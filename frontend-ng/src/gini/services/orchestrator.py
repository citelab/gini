"""Orchestrator — bring a compiled RuntimeConfig to life.

Two backends, one config:
  * simulate()  — run the gini.runtime classes in-process over localhost UDP. No Docker,
                  no privileges; used for tests and a quick "does it connect?" check.
  * up()/down() — write a self-contained Docker project and launch it via `docker compose`
                  on the user's machine (machines as containers, the fabric as one container).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from ..runtime import HostSim, LearningSwitch, Router
from .compiler import RuntimeConfig

# The real C gRouter runs as its own container from this prebuilt image
# (built once: `cd backend && docker build -f grouter-zig/Dockerfile -t gini-grouter .`).
# Override with GINI_GROUTER_IMAGE.
GROUTER_IMAGE = os.environ.get("GINI_GROUTER_IMAGE", "gini-grouter")
# The POX (Python 3) SDN controller image
# (built once: `cd backend/sdn && docker build -t gini-pox .`). Override with GINI_POX_IMAGE.
POX_IMAGE = os.environ.get("GINI_POX_IMAGE", "gini-pox")

# The external uplink network for the Internet element (NAT gateway). Fixed subnet so
# the gateway container can deterministically find its WAN interface + default route.
# Must match runtime/shuttle.py.
WAN_NET = "wan"
WAN_SUBNET = "192.168.244.0/24"
WAN_GATEWAY = "192.168.244.1"


class Sim:
    """A running in-process topology."""

    def __init__(self) -> None:
        self.machines: dict[str, HostSim] = {}
        self._nodes: list = []

    def start(self) -> None:
        for node in self._nodes:
            threading.Thread(target=node.run, daemon=True).start()
        time.sleep(0.25)

    def ping(self, src: str, dst_ip: str, timeout: float = 3.0) -> bool:
        host = self.machines[src]
        ident = (hash(src + dst_ip) & 0x7FFF) or 1
        deadline = time.time() + timeout
        seq = 0
        while time.time() < deadline:
            seq += 1
            host.ping(dst_ip, ident, seq)
            end = time.time() + 0.4
            while time.time() < end:
                if any(i == ident for i, _ in host.replies):
                    return True
                time.sleep(0.02)
        return False


def simulate(config: RuntimeConfig) -> Sim:
    rt = config.to_runtime(docker=False)
    sim = Sim()
    for m in rt["machines"]:
        h = HostSim(m)
        sim.machines[m["name"]] = h
        sim._nodes.append(h)
    for s in rt["switches"]:
        sim._nodes.append(LearningSwitch(s))
    for r in rt["routers"]:
        sim._nodes.append(Router(r))
    sim.start()
    return sim


# --------------------------------------------------------------------------- #
# Docker project emission
# --------------------------------------------------------------------------- #
# The Machine image ships "batteries included" — the common networking/diagnostic tools
# the GINI book experiments use — so students rarely need to `apt install` anything.
MACHINE_BASE = "Debian (python:3.12-slim)"
_MACHINE_TOOLS = (
    "iproute2 net-tools iputils-ping iputils-tracepath iputils-arping traceroute "
    "mtr-tiny dnsutils netcat-openbsd socat curl wget nmap tcpdump tshark iperf3 "
    "ethtool bridge-utils telnet telnetd hping3 iptables procps nano less ca-certificates "
    # services + tools the GINI book experiments stand up, so a topology runs them offline
    # (no in-container apt). DNS: bind9 (named). Mail: postfix + mailutils (the `mail` MUA).
    # Load balancing: haproxy. Security: dsniff (arpspoof/dnsspoof), ettercap, lynx.
    # DHCP: isc-dhcp-client (dhclient) to exercise the gRouter's control-plane DHCP server.
    "bind9 postfix mailutils haproxy dsniff ettercap-text-only lynx isc-dhcp-client"
)
# human-readable list for the inspector / GINI (the commands students actually type)
MACHINE_TOOLS_HUMAN = ("ip, ifconfig, ping, traceroute, mtr, tracepath, arping, "
                       "dig/nslookup/host, tcpdump, tshark, nmap, nc, socat, curl, wget, "
                       "iperf3, ethtool, brctl, telnet/telnetd, hping3, iptables; "
                       "plus experiment servers: named (bind9), postfix+mail, haproxy, "
                       "arpspoof/dnsspoof (dsniff), ettercap, lynx, dhclient (isc-dhcp-client)")

_DOCKERFILE_MACHINE = f"""FROM python:3.12-slim
ENV DEBIAN_FRONTEND=noninteractive
RUN echo "wireshark-common wireshark-common/install-setuid boolean true" \\
        | debconf-set-selections \\
 && apt-get update && apt-get install -y --no-install-recommends \\
        {_MACHINE_TOOLS} \\
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY dataplane/ /app/dataplane/
CMD ["python", "-m", "dataplane.shuttle"]
"""

_DOCKERFILE_FABRIC = """FROM python:3.12-slim
WORKDIR /app
COPY dataplane/ /app/dataplane/
COPY run_fabric.py /app/run_fabric.py
CMD ["python", "/app/run_fabric.py"]
"""

_RUN_FABRIC = '''"""Fabric supervisor: spawn each L2 switch as its own process.

Routers are NOT here anymore — each router runs as its own `gini-grouter` container
(the real C gRouter). The fabric container is now just the switched L2 substrate.
"""
import json, os, subprocess, sys, time
PY = sys.executable
os.environ.setdefault("GINI_CTRL_DIR", "/run/gini")   # per-element control sockets
os.makedirs(os.environ["GINI_CTRL_DIR"], exist_ok=True)
cfg = json.loads(os.environ["FABRIC_CONFIG"])
procs = {}
def spawn(module, conf, env_key):
    env = dict(os.environ); env[env_key] = json.dumps(conf)
    return subprocess.Popen([PY, "-m", module], env=env, cwd="/app")
for s in cfg.get("switches", []):
    procs[("switch", s["name"])] = (spawn("dataplane.switch", s, "SWITCH_CONFIG"),
                                    "dataplane.switch", s, "SWITCH_CONFIG")
print("[fabric] up:", len(cfg.get("switches", [])), "switches", file=sys.stderr)
while True:
    time.sleep(1)
    for k, (p, mod, conf, key) in list(procs.items()):
        if p.poll() is not None:
            print("[fabric] restarting", k, file=sys.stderr)
            procs[k] = (spawn(mod, conf, key), mod, conf, key)
'''


def write_project(config: RuntimeConfig, workdir: str | Path, runtime_dir: str | Path,
                  auto_internet: bool = True) -> Path:
    """Write a self-contained Docker project that runs this topology."""
    work = Path(workdir)
    (work / "dataplane").mkdir(parents=True, exist_ok=True)
    (work / "docker").mkdir(exist_ok=True)
    # copy the runtime data-plane modules
    for py in Path(runtime_dir).glob("*.py"):
        shutil.copy(py, work / "dataplane" / py.name)
    (work / "docker" / "Dockerfile.machine").write_text(_DOCKERFILE_MACHINE)
    (work / "docker" / "Dockerfile.fabric").write_text(_DOCKERFILE_FABRIC)
    (work / "run_fabric.py").write_text(_RUN_FABRIC)
    # generated service config (e.g. observability: prometheus.yml, grafana provisioning)
    for s in config.services:
        for rel, content in s.files.items():
            dst = work / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(content)
    (work / "docker-compose.yml").write_text(_compose(config, auto_internet))
    return work


def _compose(config: RuntimeConfig, auto_internet: bool = True) -> str:
    rt = config.to_runtime(docker=True)
    # The `gini` bridge is a normal (non-internal) network so the HOST can reach
    # published web consoles (Grafana/MinIO/…). "Faithful mode" (auto_internet off) does
    # NOT isolate the network — that would also block published ports. Instead each host's
    # shuttle drops its docker-eth0 default route (see `cut_default` below), so a Machine
    # has no path to the internet unless one is routed through a drawn Internet element.
    net = ["  gini:", "    driver: bridge"]
    # if the canvas has an Internet element (a NAT gateway machine), add an external
    # `wan` bridge with a fixed subnet. Only the gateway joins it; that container NATs
    # the fabric out to the world. This is the lab's egress in faithful mode.
    gw_machines = [m for m in rt["machines"] if m.get("gateway")]
    if gw_machines:
        net += [f"  {WAN_NET}:", "    driver: bridge",
                "    ipam:", "      config:",
                f"        - subnet: {WAN_SUBNET}", f"          gateway: {WAN_GATEWAY}"]
    lines = ["name: gini-lab", "networks:", *net, "services:"]

    # fabric = the L2 switch substrate only (skip entirely if there are no switches)
    if rt["switches"]:
        fabric = {"switches": rt["switches"]}
        lines += [
            "  fabric:",
            "    build: { context: ., dockerfile: docker/Dockerfile.fabric }",
            "    networks: [gini]",
            "    environment:",
            f"      FABRIC_CONFIG: '{json.dumps(fabric)}'",
        ]

    # each router = its own real C gRouter container (prebuilt image). The gRouter's
    # `tun` links are pure userspace UDP, so no NET_ADMIN / /dev/net/tun needed.
    for r in rt["routers"]:
        lines += [
            f"  {r['name']}:",
            f"    image: {GROUTER_IMAGE}",
            "    pull_policy: never",       # the image is built locally, never from a registry
            "    networks: [gini]",
            "    environment:",
            f"      ROUTER_CONFIG: '{json.dumps(r)}'",
        ]

    # SDN controllers = POX (Python 3) containers, one per controller
    for c in rt["controllers"]:
        lines += [
            f"  {c['name']}:",
            f"    image: {POX_IMAGE}",
            "    pull_policy: never",
            "    networks: [gini]",
            "    environment:",
            f"      POX_APP: '{c['app']}'",
            f"      POX_PORT: '{c['port']}'",
        ]

    # SDN switches = the real gRouter in OpenFlow mode (own container), pointed at
    # their controller via GINI_OF_CONTROLLER. Same image as routers.
    for o in rt["ovs"]:
        of_cfg = {"name": o["name"], "openflow": {"port": o["controller_port"]},
                  "ifaces": o["ports"]}
        env = ["    environment:", f"      ROUTER_CONFIG: '{json.dumps(of_cfg)}'",
               "      GINI_OF_CONNECT_DELAY: '2'"]
        if o.get("controller"):
            env.append(f"      GINI_OF_CONTROLLER: '{o['controller']}:{o['controller_port']}'")
        depends = ([f"    depends_on: [{o['controller']}]"] if o.get("controller") else [])
        lines += [
            f"  {o['name']}:",
            f"    image: {GROUTER_IMAGE}",
            "    pull_policy: never",
            "    networks: [gini]",
            *depends,
            *env,
        ]

    # managed cloud services = ordinary containers from public images on the bridge,
    # reachable by service name (cloud-style discovery). Web consoles are published.
    for s in rt["services"]:
        lines += [
            f"  {s['name']}:",
            f"    image: {s['image']}",
            "    networks: [gini]",
        ]
        if s.get("command"):
            lines.append("    command: " + json.dumps(s["command"]))
        if s.get("env"):
            lines.append("    environment:")
            for k, v in s["env"].items():
                lines.append(f"      {k}: '{v}'")
        if s.get("ports"):
            lines.append("    ports:")
            for p in s["ports"]:
                lines.append(f'      - "{p["host"]}:{p["container"]}"')
        if s.get("privileged"):
            lines.append("    privileged: true")
        if s.get("volumes"):
            lines.append("    volumes:")
            for v in s["volumes"]:
                lines.append(f'      - "{v}"')

    for m in rt["machines"]:
        m = dict(m)
        # faithful mode: a plain host with no internet path of its own drops its docker
        # default route, so it genuinely can't reach the world (the management NIC isn't a
        # back door). Hosts that route to a drawn Internet element keep/replace it instead.
        m["cut_default"] = (not auto_internet
                            and not m.get("fabric_default") and not m.get("gateway"))
        nets = f"[gini, {WAN_NET}]" if m.get("gateway") else "[gini]"
        lines += [
            f"  {m['name']}:",
            "    build: { context: ., dockerfile: docker/Dockerfile.machine }",
            "    cap_add: [NET_ADMIN]",
            '    devices: ["/dev/net/tun:/dev/net/tun"]',
            f"    networks: {nets}",
            "    environment:",
            f"      NODE_CONFIG: '{json.dumps(m)}'",
        ]
    return "\n".join(lines) + "\n"


class Orchestrator:
    """Manages the Docker lifecycle of a compiled topology on the user's machine."""

    def __init__(self, runtime_dir: str | Path) -> None:
        self.runtime_dir = Path(runtime_dir)
        self.workdir: Path | None = None

    def up(self, config: RuntimeConfig, workdir: str | Path,
           auto_internet: bool = True) -> tuple[bool, str]:
        if config.routers or config.ovs_switches:   # routers & OVS use the gRouter image
            ok, msg = self._ensure_grouter_image()
            if not ok:
                return False, msg
        if config.controllers:                       # SDN controllers use the POX image
            ok, msg = self._ensure_pox_image()
            if not ok:
                return False, msg
        self.workdir = write_project(config, workdir, self.runtime_dir, auto_internet)
        # --remove-orphans clears containers from a previous run that are no longer in
        # this compose, so stale services (e.g. an old web app) can't linger on the
        # network and shadow / break name resolution.
        return self._compose("up", "--build", "-d", "--remove-orphans")

    def _ensure_grouter_image(self) -> tuple[bool, str]:
        """The real gRouter runs from a locally-built image. Check it exists and, if we
        can find the backend, offer to build it — otherwise return the exact command."""
        if shutil.which("docker") is None:
            return False, "docker not found — is Docker installed and running?"
        present = subprocess.run(["docker", "image", "inspect", GROUTER_IMAGE],
                                 capture_output=True, text=True)
        if present.returncode == 0:
            return True, "image present"
        # locate the backend (repo_root/backend) relative to this file
        backend = Path(__file__).resolve().parents[4] / "backend"
        dockerfile = backend / "grouter-zig" / "Dockerfile"
        build_cmd = (f"cd {backend} && docker build -f grouter-zig/Dockerfile "
                     f"-t {GROUTER_IMAGE} .")
        if not dockerfile.exists():
            return False, (f"The real gRouter image '{GROUTER_IMAGE}' isn't built yet, and "
                           f"I can't find the backend to build it.\nBuild it once:\n  {build_cmd}")
        if os.environ.get("GINI_AUTOBUILD_GROUTER") != "1":
            return False, (f"The real gRouter image '{GROUTER_IMAGE}' isn't built yet.\n"
                           f"Build it once (takes a couple of minutes):\n  {build_cmd}\n"
                           f"…then press Run again. (Set GINI_AUTOBUILD_GROUTER=1 to let the "
                           f"app build it automatically.)")
        # opt-in auto-build
        b = subprocess.run(["docker", "build", "-f", "grouter-zig/Dockerfile",
                            "-t", GROUTER_IMAGE, "."], cwd=str(backend),
                           capture_output=True, text=True, timeout=1800)
        if b.returncode != 0:
            return False, f"Building {GROUTER_IMAGE} failed:\n{(b.stderr or b.stdout)[-800:]}"
        return True, f"built {GROUTER_IMAGE}"

    def _ensure_pox_image(self) -> tuple[bool, str]:
        """The SDN controller runs from a locally-built POX image."""
        if shutil.which("docker") is None:
            return False, "docker not found — is Docker installed and running?"
        present = subprocess.run(["docker", "image", "inspect", POX_IMAGE],
                                 capture_output=True, text=True)
        if present.returncode == 0:
            return True, "image present"
        sdn = Path(__file__).resolve().parents[4] / "backend" / "sdn"
        build_cmd = f"cd {sdn} && docker build -t {POX_IMAGE} ."
        if not (sdn / "Dockerfile").exists():
            return False, (f"The POX image '{POX_IMAGE}' isn't built yet, and I can't find "
                           f"backend/sdn to build it.\nBuild it once:\n  {build_cmd}")
        if os.environ.get("GINI_AUTOBUILD_POX") != "1":
            return False, (f"The SDN controller image '{POX_IMAGE}' isn't built yet.\n"
                           f"Build it once:\n  {build_cmd}\n…then press Run again. "
                           f"(Set GINI_AUTOBUILD_POX=1 to let the app build it automatically.)")
        b = subprocess.run(["docker", "build", "-t", POX_IMAGE, "."], cwd=str(sdn),
                           capture_output=True, text=True, timeout=1800)
        if b.returncode != 0:
            return False, f"Building {POX_IMAGE} failed:\n{(b.stderr or b.stdout)[-800:]}"
        return True, f"built {POX_IMAGE}"

    def down(self) -> tuple[bool, str]:
        if not self.workdir:
            return True, "nothing running"
        return self._compose("down")

    def status(self, workdir: str | Path | None = None) -> dict[str, str]:
        """Map service -> state ('running' / 'exited' / ...) via `docker compose ps`."""
        wd = workdir or self.workdir
        if not wd:
            return {}
        try:
            r = subprocess.run(["docker", "compose", "ps", "--format", "json"],
                               cwd=str(wd), capture_output=True, text=True, timeout=20)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return {}
        out = (r.stdout or "").strip()
        if not out:
            return {}
        rows: list = []
        try:
            data = json.loads(out)
            rows = data if isinstance(data, list) else [data]
        except json.JSONDecodeError:
            for line in out.splitlines():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        states: dict[str, str] = {}
        for row in rows:
            svc = row.get("Service") or row.get("Name")
            raw = str(row.get("State") or row.get("Status", "")).lower()
            if svc:
                states[svc] = "running" if "running" in raw or "up" in raw else raw
        return states

    def _compose(self, *args: str) -> tuple[bool, str]:
        try:
            r = subprocess.run(["docker", "compose", *args], cwd=str(self.workdir),
                               capture_output=True, text=True, timeout=600)
            return r.returncode == 0, (r.stderr or r.stdout).strip()
        except FileNotFoundError:
            return False, "docker not found — is Docker installed and running?"
        except subprocess.TimeoutExpired:
            return False, "docker compose timed out"
