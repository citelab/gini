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

from ..runtime import HostSim, Router, make_switch
from .compiler import RuntimeConfig

# The real C gRouter runs as its own container from this prebuilt image
# (built once: `cd backend && docker build -f grouter-build/Dockerfile -t gini-grouter .`).
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

# GINI Cloud Fabric telemetry agent — one container polling every cloud service's native
# metrics. Published on a fixed host port so gBuilder can poll http://localhost:PORT.
CLOUDFABRIC_PORT = 9099
CLOUDFABRIC_HOST_PORT = 39099
# GINI32: the gbridge relay's UDP port. Boards are flashed with the host address and
# this port (gbridge_config.h: GB_DEFAULT_SERVER_PORT), so it is deliberately fixed and
# identical inside and outside the container — a student reading the firmware and a
# student reading the compose file must see the same number.
GBRIDGE_PORT = 5555
GBRIDGE_HOST_PORT = int(os.environ.get("GINI_GBRIDGE_PORT", GBRIDGE_PORT))
# Read-only status feed for the UI (board online/offline, signal, connected devices).
GBRIDGE_STATUS_PORT = 39098


def _parse_bytes(s: str) -> float:
    """A docker-stats size string ('1.2kB' / '3.4MB' / '512B') -> bytes (decimal units)."""
    s = s.strip()
    factors = {"GB": 1e9, "MB": 1e6, "kB": 1e3, "KB": 1e3, "B": 1.0,
               "GiB": 2**30, "MiB": 2**20, "KiB": 2**10}
    for unit in sorted(factors, key=len, reverse=True):
        if s.endswith(unit):
            try:
                return float(s[:-len(unit)].strip()) * factors[unit]
            except ValueError:
                return 0.0
    return 0.0


def _parse_mem_mib(s: str) -> float:
    """A docker-stats memory string ('45.2MiB' / '1.1GiB' / '512B') -> MiB."""
    s = s.strip()
    factors = {"GiB": 1024.0, "MiB": 1.0, "KiB": 1 / 1024.0, "B": 1 / 1048576.0,
               "GB": 1000.0 / 1024, "MB": 1000.0 / 1048576 * 1024, "kB": 1 / 1024.0}
    for unit in sorted(factors, key=len, reverse=True):   # match 'MiB' before 'B'
        if s.endswith(unit):
            try:
                return float(s[:-len(unit)].strip()) * factors[unit]
            except ValueError:
                return 0.0
    try:
        return float(s) / 1048576.0
    except ValueError:
        return 0.0


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
        sim._nodes.append(make_switch(s))
    for r in rt["routers"]:
        sim._nodes.append(Router(r))
    sim.start()
    return sim


# --------------------------------------------------------------------------- #
# Docker project emission
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# Machine images: two TOOLKITS, and lean is the default.
#
# A Host is the most replicated container in GINI — a ten-node topology is ten of them — so its
# image size is multiplied by ten while a Grafana is multiplied by one. It was also, historically,
# our FATTEST image: python:3.12-slim plus bind9, postfix, ettercap, tshark, nmap, haproxy… every
# host carried a mail server and a DNS server it would never run. On a lab machine slimmer than a
# developer's, that is where "GINI is slow" comes from.
#
# So a Machine now picks a toolkit:
#   lean (DEFAULT) — Alpine + python3 + the tools a student actually types: ip, ping, traceroute,
#                    tcpdump, curl, dig, nc, socat, iperf3, nmap. ~10x smaller.
#   full           — the old Debian image, for the book experiments that genuinely need the heavy
#                    services (DNS with bind9, mail with postfix, ARP/DNS spoofing with ettercap).
#                    Those tools are Debian-shaped; this is not worth porting to musl.
#
# NOTE this is a different axis from the element's SIZE (S/M/L/XL), which sets the CPU cap and the
# cost multiplier. A lean host with an XL CPU cap is a perfectly sensible thing to want, so the two
# must not be conflated: size = how much it gets, toolkit = what's installed in it.
# --------------------------------------------------------------------------- #
MACHINE_LEAN, MACHINE_FULL, MACHINE_SECURITY = "lean", "full", "security"
MACHINE_GUI = "gui"                       # a HEADFUL machine: lean tools + a light X desktop
MACHINE_TOOLKIT_DEFAULT = MACHINE_LEAN

# --------------------------------------------------------------------------------------------- #
# ttyd — the in-container terminal server the gBuilder Terminal tab connects to.
#
# IN the container rather than on the host, deliberately: the container is always Linux, so this
# behaves identically on macOS, Linux and Windows. A host-side ttyd would need a Windows build.
#
# Fetched in a throwaway Alpine stage and COPYed in, so no base image needs curl or ca-certificates
# and every image gets a byte-identical binary. It is a single static executable (~1 MB, musl,
# no runtime deps), which is why it works unchanged on both Alpine and Debian bases.
#
# `ttyd.$(uname -m)` is exactly how the project names its release assets — uname reports x86_64 and
# aarch64, matching ttyd.x86_64 and ttyd.aarch64. Both were verified to exist before this landed.
TTYD_PORT = 7681
_TTYD_VER = "1.7.7"

# Host ports for the per-element terminals. Deliberately a long way from the other fixed ports
# (GBRIDGE 39098, CLOUDFABRIC 39099) and bound to 127.0.0.1 in compose — ttyd runs writable with
# no password, so it must never be reachable off the machine.
TTYD_HOST_BASE = 37600

# Where the element -> host-port map is written, so the Terminal panel can find a container's
# terminal without re-deriving compose's numbering. Fabric elements are emitted as raw compose
# lines rather than ServiceSpecs, so there is no `ports` list for the UI to read; this file is
# that list.
TERMINALS_FILE = "gini-terminals.json"

# One tmux session per container, so re-opening an element's Terminal re-attaches to the work
# already in flight rather than starting a fresh shell. The name is fixed because each container
# runs its own tmux server — there is nothing to collide with.
TMUX_SESSION = "gini"

# element name -> {"port", "cmd"}, refreshed by every _compose(). Module level because _compose
# returns a string and has nowhere else to put it, while write_project needs it immediately
# afterwards; one write per Run, on one thread, so there is no sharing hazard.
#
# Without this the map is built inside _compose and thrown away, TERMINALS_FILE is never written,
# and the Terminal panel reports "nothing is running" forever — with the containers up and their
# ttyd ports published. Nothing else notices, which is exactly why it needs a test.
_LAST_TERMINALS: dict = {}

_TTYD_STAGE = f"""FROM alpine:3.20 AS ttyd
RUN apk add --no-cache curl \\
 && curl -fsSL -o /ttyd "https://github.com/tsl0922/ttyd/releases/download/{_TTYD_VER}/ttyd.$(uname -m)" \\
 && chmod +x /ttyd
"""

def _ttyd_layers() -> str:
    """The COPY + launcher-script layers.

    Written with printf and \\n escapes so the whole thing is ONE Dockerfile instruction — a
    literal newline inside the string would end the RUN and leave the rest as garbage.

    `${TTYD_CMD:-exec /bin/sh}` survives to runtime because it sits inside single quotes: the
    build shell leaves it alone, and the launcher expands it when a student opens a terminal. That
    is what lets routers and OVS switches share the gRouter image and still front different
    things — the value comes from compose, per element, not from the image.
    """
    # The double quotes around the expansion are PLAIN, not backslash-escaped. `\"` is not a
    # portable printf escape: GNU coreutils printf quietly turns it into `"`, but the printf a
    # Dockerfile RUN actually uses is the shell's BUILTIN (busybox ash on Alpine, dash on Debian)
    # and that one emits the backslash literally. The launcher then read
    #     ttyd ... /bin/sh -c \"${TTYD_CMD:-exec /bin/sh}\"
    # so ttyd received `"exec` and `/bin/sh"` as separate words and every terminal died with
    #     "/bin/sh": line 0: syntax error: unterminated quoted string
    # Plain quotes are safe here because the whole format string is already inside the RUN's
    # single quotes, which is also what the hand-written grouter/sdn Dockerfiles do.
    script = (r"#!/bin/sh\n"
              r"exec /usr/local/bin/ttyd -W -p %d -i 0.0.0.0 "
              r'/bin/sh -c "${TTYD_CMD:-exec /bin/sh}"\n') % TTYD_PORT
    # tmux is what makes a session OUTLIVE its browser tab: leave a ping running on M1, look at
    # M2, come back, and the ping is still there with its scrollback. Without it the view is torn
    # down on every switch, the WebSocket closes, and ttyd's child dies with it.
    #
    # Status bar off: students are here to look at a shell, not to learn tmux, and a green bar
    # across the bottom reads as part of GINI rather than part of the terminal. The prefix is left
    # at the default — nothing in the labs asks them to use it.
    conf = r"set -g status off\nset -g history-limit 10000\nset -g mouse on\n"
    return ("COPY --from=ttyd /ttyd /usr/local/bin/ttyd\n"
            f"RUN printf '{conf}' > /etc/tmux.conf "
            f"&& printf '{script}' > /usr/local/bin/gini-term "
            "&& chmod +x /usr/local/bin/gini-term\n")


def _persist(cmd: str = "") -> str:
    """A TTYD_CMD that re-attaches instead of starting over.

    `new -A` is attach-or-create: the first connection starts the session, every later one joins
    the SAME one. Plain `new` would silently start a second session per reconnect, which looks
    identical on screen and loses the work — hence the test pinning `-A`.

    Deliberately not `exec tmux`: if exec cannot find the binary the shell exits outright, so an
    image built before tmux landed would serve a dead terminal. Written this way, an old image
    prints tmux's "not found" and drops the student into a working shell — degraded, not broken.
    Same reason the trailing shell is there: quitting tmux leaves you in the container rather than
    killing the session under you.
    """
    inner = f' "{cmd}; exec /bin/sh"' if cmd else ""
    # TERM must be set or tmux refuses to start — "TERM environment variable not set." — and the
    # student silently gets the fallback shell with no persistence instead. ttyd does set TERM for
    # its PTY child, but the launcher runs `/bin/sh -c "$TTYD_CMD"` and not every sh passes it
    # through the way tmux needs; setting it here costs nothing and removes the doubt. `${TERM:-…}`
    # keeps whatever ttyd did provide.
    return (f'TERM="${{TERM:-xterm-256color}}" tmux new -A -s {TMUX_SESSION}{inner}'
            f"; exec /bin/sh")


def _with_ttyd(cmd: str) -> str:
    """Wrap an image's CMD so the terminal server runs beside the real workload.

    The workload keeps PID 1 semantics via `exec`, so container stop/restart behave exactly as
    before; ttyd is a background child that dies with it.
    """
    return f'CMD ["sh", "-c", "gini-term & exec {cmd}"]\n'

# Alpine package names (musl). Deliberately NOT: bind9, postfix, ettercap, haproxy, dsniff, lynx,
# telnetd — those are what made the old image huge, and they belong to the `full` toolkit.
_MACHINE_TOOLS_LEAN = (
    "python3 iproute2 iputils busybox-extras tcpdump curl wget bind-tools "
    "netcat-openbsd socat iperf3 nmap ethtool traceroute mtr bridge-utils iptables tmux"
)

_DOCKERFILE_MACHINE_LEAN = f"""{_TTYD_STAGE}FROM alpine:3.20
RUN apk add --no-cache {_MACHINE_TOOLS_LEAN}
WORKDIR /app
COPY dataplane/ /app/dataplane/
{_ttyd_layers()}CMD ["sh", "-c", "gini-term & exec python3 -m dataplane.shuttle"]
"""

# The FULL image ships "batteries included" — the heavy services the GINI book experiments stand
# up — so a student running those chapters never needs to `apt install` inside a container.
MACHINE_BASE = "Debian (python:3.12-slim)"
_MACHINE_TOOLS = (
    "iproute2 net-tools iputils-ping iputils-tracepath iputils-arping traceroute "
    "mtr-tiny dnsutils netcat-openbsd socat curl wget nmap tcpdump tshark iperf3 "
    "ethtool bridge-utils telnet telnetd hping3 iptables procps nano less ca-certificates tmux "
    # services + tools the GINI book experiments stand up, so a topology runs them offline
    # (no in-container apt). DNS: bind9 (named). Mail: postfix + mailutils (the `mail` MUA).
    # Web caching: squid (forward proxy). Load balancing: haproxy. Security: dsniff
    # (arpspoof/dnsspoof), ettercap, lynx.
    # DHCP: isc-dhcp-client (dhclient) to exercise the gRouter's control-plane DHCP server.
    "bind9 postfix mailutils squid haproxy dsniff ettercap-text-only lynx isc-dhcp-client"
)
# The SECURITY tier = the FULL toolkit PLUS the heavy security engines the Security part (Part VI)
# stands up: openssl (TLS/PKI), wireguard-tools (encrypted tunnels), isc-dhcp-server (rogue-DHCP
# lab), suricata (IDS), tcpreplay (replay captures into the IDS). Opt-in per host, so ordinary hosts
# never carry it. WireGuard needs the in-kernel module (present in modern Docker Desktop kernels) or
# a userspace fallback; the VPN lab notes this.
_MACHINE_TOOLS_SECURITY = _MACHINE_TOOLS + (
    " openssl wireguard-tools isc-dhcp-server suricata tcpreplay"
)
# human-readable lists for the inspector / GINI (the commands students actually type)
MACHINE_TOOLS_LEAN_HUMAN = ("ip, ifconfig, ping, traceroute, mtr, arping, dig/nslookup/host, "
                            "tcpdump, nmap, nc, socat, curl, wget, iperf3, ethtool, brctl, "
                            "iptables")
MACHINE_TOOLS_HUMAN = ("ip, ifconfig, ping, traceroute, mtr, tracepath, arping, "
                       "dig/nslookup/host, tcpdump, tshark, nmap, nc, socat, curl, wget, "
                       "iperf3, ethtool, brctl, telnet/telnetd, hping3, iptables; "
                       "plus experiment servers: named (bind9), postfix+mail, squid (web cache), "
                       "haproxy, arpspoof/dnsspoof (dsniff), ettercap, lynx, dhclient (isc-dhcp-client)")
MACHINE_TOOLS_SECURITY_HUMAN = (MACHINE_TOOLS_HUMAN +
                       "; plus security engines: openssl (TLS/PKI), wg (WireGuard VPN), "
                       "suricata (IDS), isc-dhcp-server, tcpreplay")


# The GUI toolkit = the lean Alpine host PLUS a light X desktop, served to gBuilder over noVNC:
# Xvfb (a virtual screen), fluxbox (window manager), PCManFM (desktop + file manager), an xterm,
# and Dillo (a tiny browser to hit web servers in the topology). x11vnc + noVNC publish the screen.
# It's a REAL fabric host (runs dataplane.shuttle), so it also has the lean networking tools.
MACHINE_TOOLS_GUI_HUMAN = (MACHINE_TOOLS_LEAN_HUMAN +
                       "; plus a graphical desktop (fluxbox WM, PCManFM file manager, xterm, "
                       "Dillo browser) opened over noVNC")


def machine_tools(toolkit: str) -> str:
    if toolkit == MACHINE_SECURITY:
        return MACHINE_TOOLS_SECURITY_HUMAN
    if toolkit == MACHINE_GUI:
        return MACHINE_TOOLS_GUI_HUMAN
    return MACHINE_TOOLS_HUMAN if toolkit == MACHINE_FULL else MACHINE_TOOLS_LEAN_HUMAN


_DOCKERFILE_MACHINE = f"""{_TTYD_STAGE}FROM python:3.12-slim
ENV DEBIAN_FRONTEND=noninteractive
RUN echo "wireshark-common wireshark-common/install-setuid boolean true" \\
        | debconf-set-selections \\
 && apt-get update && apt-get install -y --no-install-recommends \\
        {_MACHINE_TOOLS} \\
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY dataplane/ /app/dataplane/
{_ttyd_layers()}CMD ["sh", "-c", "gini-term & exec python -m dataplane.shuttle"]
"""

_DOCKERFILE_MACHINE_SECURITY = f"""{_TTYD_STAGE}FROM python:3.12-slim
ENV DEBIAN_FRONTEND=noninteractive
RUN echo "wireshark-common wireshark-common/install-setuid boolean true" \\
        | debconf-set-selections \\
 && apt-get update && apt-get install -y --no-install-recommends \\
        {_MACHINE_TOOLS_SECURITY} \\
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY dataplane/ /app/dataplane/
{_ttyd_layers()}CMD ["sh", "-c", "gini-term & exec python -m dataplane.shuttle"]
"""

# The HEADFUL machine: lean Alpine host + a light X desktop, served over noVNC on :6080. The
# entrypoint starts the desktop stack in the background, then execs dataplane.shuttle in the
# foreground — so the container is a real fabric node (networking) that also has a GUI.
_GUI_START = (
    "Xvfb :0 -screen 0 1280x800x24 -nolisten tcp >/dev/null 2>&1 & sleep 1; "
    "fluxbox >/dev/null 2>&1 & pcmanfm --desktop >/dev/null 2>&1 & "
    "x11vnc -display :0 -forever -shared -nopw -rfbport 5900 -bg -quiet -noxdamage >/dev/null 2>&1; "
    "websockify --web=/usr/share/novnc 6080 localhost:5900 >/dev/null 2>&1 & "
    "exec python3 -m dataplane.shuttle"
)
_DOCKERFILE_MACHINE_GUI = f"""{_TTYD_STAGE}FROM alpine:3.20
RUN apk add --no-cache {_MACHINE_TOOLS_LEAN} \\
        xvfb x11vnc fluxbox xterm pcmanfm dillo novnc websockify font-dejavu
# Ready-made desktop shortcuts so students see a Terminal and Dillo on the desktop at startup
# (pcmanfm --desktop shows ~/Desktop/*.desktop as clickable icons; +x avoids a trust prompt).
RUN mkdir -p /root/Desktop \\
 && printf '[Desktop Entry]\\nType=Application\\nName=Terminal\\nExec=xterm\\nTerminal=false\\n' > /root/Desktop/Terminal.desktop \\
 && printf '[Desktop Entry]\\nType=Application\\nName=Dillo Browser\\nExec=dillo\\nTerminal=false\\n' > /root/Desktop/Dillo.desktop \\
 && chmod +x /root/Desktop/*.desktop
ENV DISPLAY=:0 HOME=/root
WORKDIR /app
COPY dataplane/ /app/dataplane/
{_ttyd_layers()}CMD ["sh", "-c", "gini-term & {_GUI_START}"]
"""

# toolkit -> (compose image name, dockerfile path). Explicit image names so N hosts of one tier
# resolve to ONE image and compose builds it once.
_MACHINE_IMAGE = {
    MACHINE_LEAN:     ("gini-machine-lean",     "docker/Dockerfile.machine-lean"),
    MACHINE_FULL:     ("gini-machine-full",     "docker/Dockerfile.machine"),
    MACHINE_SECURITY: ("gini-machine-security", "docker/Dockerfile.machine-security"),
    MACHINE_GUI:      ("gini-machine-gui",      "docker/Dockerfile.machine-gui"),
}

_DOCKERFILE_FABRIC = """FROM python:3.12-slim
WORKDIR /app
COPY dataplane/ /app/dataplane/
COPY run_fabric.py /app/run_fabric.py
CMD ["python", "/app/run_fabric.py"]
"""

# the GINI Cloud Fabric telemetry agent image (psycopg2 for the Postgres adapter; the
# rest is stdlib). Same `dataplane/` copy as the machine image, so the agent ships with it.
_DOCKERFILE_CLOUDFABRIC = """FROM python:3.12-slim
RUN pip install --no-cache-dir psycopg2-binary
WORKDIR /app
COPY dataplane/ /app/dataplane/
CMD ["python", "-m", "dataplane.cloudfabric_agent"]
"""

_DOCKERFILE_SG = """FROM alpine:3.20
RUN apk add --no-cache iptables
"""

_DOCKERFILE_FAAS = """FROM python:3.12-slim
# event-trigger clients: RabbitMQ (queue), NATS (pub/sub), Kafka/Redpanda (stream).
# kafka-python-ng is the maintained fork that supports Python 3.12.
RUN pip install --no-cache-dir pika nats-py kafka-python-ng
WORKDIR /app
COPY run_faas.py /app/run_faas.py
CMD ["python", "/app/run_faas.py"]
"""

# The GINI serverless runtime: ONE process hosts every Function as a handler (multiplexed,
# like real FaaS). Reachable at http://faas:8000/<name>; meters each invocation. Stdlib only.
_RUN_FAAS = '''"""GINI serverless runtime — hosts every Function as a handler in one process.

A Function node is NOT its own container; it is a handler registered here and reachable
at http://faas:8000/<name>. This mirrors real FaaS: no servers to run, the platform
multiplexes your functions, runs them on demand, and meters each invocation. Config
arrives as FAAS_CONFIG (JSON): {"functions": [{"name", "handler", "code"}]}.
"""
import inspect, json, os, random, threading, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CFG = json.loads(os.environ.get("FAAS_CONFIG", "{}"))
FUNCS = {f["name"]: f for f in CFG.get("functions", [])}
STATS = {n: {"invocations": 0, "errors": 0, "events": 0, "last_ms": 0.0,
             "cold": True, "count": 0} for n in FUNCS}
LOCK = threading.Lock()


class Context:
    """The invocation context passed to a handler — the GINI analogue of AWS Lambda's
    `context`: who is running, a unique id per call, and how long is left."""
    def __init__(self, name):
        self.function_name = name
        self.invocation_id = uuid.uuid4().hex
        self.aws_request_id = self.invocation_id      # alias for AWS-style code
        self.remaining_ms = 30000

    def get_remaining_time_in_millis(self):
        return self.remaining_ms


def _compile_custom(code):
    """Compile a student's `def handle(event, context)` (one arg also accepted) into a
    normalized 2-arg callable, or None if the code doesn't define a callable `handle`."""
    ns = {}
    try:
        exec(code, ns)
    except Exception:
        return None
    fn = ns.get("handle")
    if not callable(fn):
        return None
    try:
        nparams = len(inspect.signature(fn).parameters)
    except (TypeError, ValueError):
        nparams = 2
    return fn if nparams >= 2 else (lambda event, context, _f=fn: _f(event))


CUSTOM = {n: _compile_custom(f.get("code", "")) for n, f in FUNCS.items()
          if f.get("handler") == "custom"}


def run_handler(name, event, context):
    f = FUNCS[name]
    h = f.get("handler", "echo")
    cold = STATS[name]["cold"]
    if cold and h != "slow":
        time.sleep(0.25)                         # cold start: the first call pays init latency
    if h == "slow":
        time.sleep(1.5 if cold else 0.05)        # an extra-slow function: always sleeps
        return 200, {"function": name, "cold": cold}
    if h == "fail":
        if random.random() < 0.3:
            raise RuntimeError("simulated failure (shows retries/error handling)")
        return 200, {"function": name, "ok": True}
    if h == "transform":
        body = event.get("body", "")
        return 200, {"function": name, "input": body, "output": body.upper()}
    if h == "counter":
        with LOCK:
            STATS[name]["count"] += 1
            c = STATS[name]["count"]
        return 200, {"function": name, "count": c,
                     "note": "resets when the runtime restarts (functions are stateless)"}
    if h == "custom":
        fn = CUSTOM.get(name)
        if fn is None:
            return 500, {"error": "custom handler did not define handle(event, context)"}
        result = fn(event, context)
        # AWS proxy-style: a {statusCode, body} return sets the HTTP status + raw body.
        if isinstance(result, dict) and "statusCode" in result:
            return int(result["statusCode"]), result.get("body", "")
        return 200, {"function": name, "result": result}
    return 200, {"function": name, "method": event.get("method"),
                 "path": event.get("path"), "body": event.get("body")}   # echo (default)


def invoke(name, event):
    """Run a function and meter it. Both HTTP requests and event triggers go through
    here, so a queue/stream message counts as a real invocation just like an HTTP call."""
    context = Context(name)
    t0 = time.time()
    try:
        code, result = run_handler(name, event, context)
    except Exception as e:
        code, result = 500, {"error": str(e)}
    ms = (time.time() - t0) * 1000.0
    with LOCK:
        s = STATS[name]
        s["invocations"] += 1
        s["last_ms"] = round(ms, 1)
        s["cold"] = False
        if event.get("source") and event.get("source") != "http":
            s["events"] += 1
        if code >= 500:
            s["errors"] += 1
    return code, result


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj, ctype=None):
        # bytes -> raw; str -> text (a handler's {statusCode, body} returns a string body);
        # dict/list -> JSON.
        if isinstance(obj, bytes):
            body, ctype = obj, ctype or "application/json"
        elif isinstance(obj, str):
            body, ctype = obj.encode(), ctype or "text/plain"
        else:
            body, ctype = json.dumps(obj).encode(), ctype or "application/json"
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _dispatch(self, method):
        path = self.path.split("?", 1)[0]
        if path in ("/_gini/health", "/healthz"):
            return self._send(200, {"ok": True})
        if path == "/_gini/metrics":
            with LOCK:
                snap = {n: {"invocations": s["invocations"], "errors": s["errors"],
                            "events": s["events"], "last_ms": s["last_ms"]}
                        for n, s in STATS.items()}
            return self._send(200, {"functions": snap,
                                    "total": sum(s["invocations"] for s in STATS.values())})
        if path in ("/", "/_gini/console"):
            rows = "".join("<li><b>%s</b> [%s] - %d calls</li>"
                           % (n, FUNCS[n].get("handler", "echo"), STATS[n]["invocations"])
                           for n in FUNCS)
            html = ("<h2>GINI Functions</h2><ul>" + (rows or "<i>none</i>") + "</ul>").encode()
            return self._send(200, html, "text/html")
        fn = path.strip("/").split("/", 1)[0]
        if fn not in FUNCS:
            return self._send(404, {"error": "no such function", "name": fn})
        n = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(n).decode("utf-8", "replace") if n else ""
        from urllib.parse import urlsplit, parse_qs
        parts = urlsplit(self.path)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        event = {"method": method, "path": self.path, "rawPath": parts.path,
                 "query": query, "headers": dict(self.headers.items()),
                 "body": body, "source": "http"}
        code, result = invoke(fn, event)
        self._send(code, result)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")


# --- event triggers: subscribe to each Function's sources and invoke per message ---
# The queue/topic/subject is named after the function, so "publish to <fn>" invokes it.
# Each subscriber runs in its own thread with a reconnect loop (the broker may start late);
# the client import lives inside so a missing client only disables that one trigger type.
def _sub_queue(name, host, port):
    """Message Queue (RabbitMQ / AMQP): consume the queue named after the function."""
    import pika
    creds = pika.PlainCredentials("guest", "guest")
    while True:
        try:
            conn = pika.BlockingConnection(pika.ConnectionParameters(
                host=host, port=port, credentials=creds, heartbeat=30,
                connection_attempts=1, socket_timeout=5))
            ch = conn.channel()
            ch.queue_declare(queue=name, durable=False)
            print("[faas] queue trigger ready:", name, "@", host, flush=True)
            for method, _props, body in ch.consume(name, inactivity_timeout=1):
                if body is None:
                    continue
                invoke(name, {"method": "EVENT", "source": "queue",
                              "body": body.decode("utf-8", "replace")})
                ch.basic_ack(method.delivery_tag)
        except Exception as e:
            print("[faas] queue trigger retry:", name, e, flush=True)
            time.sleep(3)


def _sub_stream(name, host, port):
    """Event Stream (Redpanda / Kafka API): consume the topic named after the function."""
    from kafka import KafkaConsumer
    while True:
        try:
            c = KafkaConsumer(name, bootstrap_servers="%s:%d" % (host, port),
                              auto_offset_reset="latest", consumer_timeout_ms=1000,
                              group_id="gini-faas-" + name)
            print("[faas] stream trigger ready:", name, "@", host, flush=True)
            for msg in c:
                invoke(name, {"method": "EVENT", "source": "stream",
                              "body": (msg.value or b"").decode("utf-8", "replace")})
        except Exception as e:
            print("[faas] stream trigger retry:", name, e, flush=True)
            time.sleep(3)


def _sub_pubsub(name, host, port):
    """Pub/Sub (NATS): subscribe to the subject named after the function."""
    import asyncio
    import nats
    async def run():
        nc = await nats.connect("nats://%s:%d" % (host, port))
        print("[faas] pubsub trigger ready:", name, "@", host, flush=True)
        async def cb(m):
            invoke(name, {"method": "EVENT", "source": "pubsub",
                          "body": m.data.decode("utf-8", "replace")})
        await nc.subscribe(name, cb=cb)
        while True:
            await asyncio.sleep(3600)
    while True:
        try:
            asyncio.run(run())
        except Exception as e:
            print("[faas] pubsub trigger retry:", name, e, flush=True)
            time.sleep(3)


_SUBS = {"queue": _sub_queue, "stream": _sub_stream, "messaging": _sub_pubsub}


def start_triggers():
    """Spawn a subscriber thread for every event trigger declared on a function."""
    for name, f in FUNCS.items():
        for trig in f.get("triggers", []):
            sub = _SUBS.get(trig.get("type"))
            if not sub:
                continue
            threading.Thread(target=sub, name="trig-%s-%s" % (trig["type"], name),
                             args=(name, trig["host"], int(trig["port"])),
                             daemon=True).start()


if __name__ == "__main__":
    print("[faas] hosting", len(FUNCS), "function(s):", ", ".join(FUNCS), flush=True)
    start_triggers()
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
'''

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


def _seed_examples(scripts: Path, shared: Path) -> None:
    """Copy the examples GINI ships (packaged under gini/data/examples) into the user's
    ~/.gini dirs, so the book's instructions work out of the box: the reference Lua
    control-plane modules appear in ~/.gini/scripts (loadable as /scripts/<name>.lua on
    every router), and the Multicast File Distribution starter kit in
    ~/.gini/shared/multicast_fs (visible at /shared/multicast_fs on every station).

    Note the reference modules are deliberately NOT named after the files the book asks a
    student to write (the routing experiment writes rip.lua), so seeding never lands the
    finished answer on top of an exercise. Never overwrites a file the user edited."""
    ex = Path(__file__).resolve().parents[1] / "data" / "examples"
    try:
        for name in ("mcast_tree.lua", "rip_reference.lua"):
            src = ex / name
            if src.exists() and not (scripts / name).exists():
                shutil.copy(src, scripts / name)
        kit = ex / "multicast_fs"
        if kit.is_dir():
            dst = shared / "multicast_fs"
            dst.mkdir(parents=True, exist_ok=True)
            for f in kit.iterdir():
                if f.is_file() and not (dst / f.name).exists():
                    shutil.copy(f, dst / f.name)
    except Exception:
        pass    # examples are a convenience; never block a Run on them


def write_project(config: RuntimeConfig, workdir: str | Path, runtime_dir: str | Path,
                  auto_internet: bool = True, laptop_id: str = "") -> Path:
    """Write a self-contained Docker project that runs this topology."""
    from ..app.paths import captures_dir, scripts_dir, shared_dir
    captures_dir().mkdir(parents=True, exist_ok=True)   # host dir bind-mounted for tap .pcaps
    scripts_dir().mkdir(parents=True, exist_ok=True)    # host dir bind-mounted for Lua VNFs
    shared_dir().mkdir(parents=True, exist_ok=True)     # host dir bind-mounted into machines
    _seed_examples(scripts_dir(), shared_dir())         # ship mcast_tree.lua + the C starter kit
    work = Path(workdir)
    (work / "dataplane").mkdir(parents=True, exist_ok=True)
    (work / "docker").mkdir(exist_ok=True)
    # copy the runtime data-plane modules
    for py in Path(runtime_dir).glob("*.py"):
        shutil.copy(py, work / "dataplane" / py.name)
    # Everything written here is consumed INSIDE Linux containers, so pin the bytes:
    # UTF-8 (Windows' locale default is cp1252 — an em-dash in a comment became byte
    # 0x97 and killed run_fabric.py with a SyntaxError) and \n newlines (CRLF breaks sh).
    def _put(path: Path, text: str) -> None:
        path.write_text(text, encoding="utf-8", newline="\n")

    _put(work / "docker" / "Dockerfile.machine", _DOCKERFILE_MACHINE)
    _put(work / "docker" / "Dockerfile.machine-lean", _DOCKERFILE_MACHINE_LEAN)
    _put(work / "docker" / "Dockerfile.machine-security", _DOCKERFILE_MACHINE_SECURITY)
    _put(work / "docker" / "Dockerfile.machine-gui", _DOCKERFILE_MACHINE_GUI)
    _put(work / "docker" / "Dockerfile.fabric", _DOCKERFILE_FABRIC)
    _put(work / "docker" / "Dockerfile.cloudfabric", _DOCKERFILE_CLOUDFABRIC)
    _put(work / "docker" / "Dockerfile.faas", _DOCKERFILE_FAAS)
    _put(work / "docker" / "Dockerfile.sg", _DOCKERFILE_SG)
    _put(work / "run_fabric.py", _RUN_FABRIC)
    _put(work / "run_faas.py", _RUN_FAAS)
    # security-group iptables scripts, bind-mounted into each member's firewall sidecar
    for fw in config.firewalls:
        d = work / "sg"
        d.mkdir(exist_ok=True)
        _put(d / f"{fw['member']}.sh", fw["script"])
    # generated service config (e.g. observability: prometheus.yml, grafana provisioning)
    for s in config.services:
        for rel, content in s.files.items():
            dst = work / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            _put(dst, content)
    # kubernetes manifests, bind-mounted into each k3s cluster container for kubectl apply
    for k in config.k8s:
        (work / "k8s" / k.svc).mkdir(parents=True, exist_ok=True)
        _put(work / "k8s" / k.svc / "manifests.yaml", k.manifests or "")
    _put(work / "docker-compose.yml", _compose(config, auto_internet, laptop_id))
    # The element -> terminal-port map, written AFTER _compose has populated it. Fabric elements
    # are emitted as raw compose lines rather than ServiceSpecs, so the UI has no `ports` list to
    # read back; this file is that list.
    _put(work / TERMINALS_FILE, json.dumps(_LAST_TERMINALS, indent=2, sort_keys=True))
    return work


def _hostpath(p) -> str:
    """A host path for use inside the compose file: always forward slashes.

    A Windows path lands in a *double-quoted* YAML scalar (`"C:\\Users\\...:/captures"`),
    where `\\U` and `\\x` are escape sequences — compose's YAML loader then fails with
    "did not find expected hexadecimal number" and nothing starts. Docker accepts forward
    slashes on every platform, so normalizing here keeps one code path.
    """
    return str(p).replace("\\", "/")


def _compose(config: RuntimeConfig, auto_internet: bool = True,
             laptop_id: str = "") -> str:
    from ..app.paths import captures_dir, scripts_dir
    cap_host = _hostpath(captures_dir())    # host path bind-mounted into routers at /captures
    scr_host = _hostpath(scripts_dir())     # student Lua modules, mounted read-only at /scripts
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
    # VPC/subnet networks. A VPC's shared net is `internal` (the implicit VPC fabric — no
    # internet of its own); the per-VPC `_egress` net is a normal bridge that public-subnet
    # members also join for real internet + host-published consoles. Elements with no VPC
    # stay on the flat `gini` bridge above.
    vpc_nets = rt.get("networks", [])
    for vnet in vpc_nets:
        net += [f"  {vnet['name']}:", "    driver: bridge"]
        if vnet.get("internal"):
            net.append("    internal: true")
        if vnet.get("cidr"):
            net += ["    ipam:", "      config:", f"        - subnet: {vnet['cidr']}"]
    # One terminal per element. The port is allocated in emission order and recorded so the
    # Terminal panel can look it up by element name; `TTYD_CMD` decides what the terminal
    # FRONTS, which differs per element even when the image is shared (a gRouter and an OVS
    # switch are both the gRouter image, but a student wants the router CLI on one).
    _term: dict = {}

    def _term_port(name: str, cmd: str = "") -> str:
        """Publish this element's terminal and remember the port. Returns the ENTRY only.

        Deliberately NOT the `ports:` header: a service can publish more than one thing — a GUI
        host also publishes noVNC — and emitting a header here produced a second `ports:` key,
        which compose rejects outright ("mapping key \\"ports\\" already defined"). Call sites
        gather every entry and emit ONE block. Same reasoning as `_term_env`: never emit a
        top-level service key that somebody else might also emit.
        """
        port = TTYD_HOST_BASE + len(_term)
        _term[name] = {"port": port, "cmd": cmd}
        return f'      - "127.0.0.1:{port}:{TTYD_PORT}"'

    def _term_env(name: str) -> list:
        cmd = _term.get(name, {}).get("cmd", "")
        return [f"      TTYD_CMD: '{cmd}'"] if cmd else []


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

    # gbridge = the one container that talks to REAL hardware. It holds the fabric end of
    # every drawn GINI32 board's link and relays frames to the board over the physical LAN.
    # This is the only service with a published port: boards send to the host's LAN address,
    # which is what lets a $5 chip on the desk reach a topology running inside Docker (and
    # is why this works on macOS, where containers cannot see the LAN directly).
    if rt.get("gbridge"):
        gcfg = {"name": "gbridge", "listen_port": GBRIDGE_PORT,
                "status_port": GBRIDGE_STATUS_PORT,
                # Who this install is to a board. A board that has been claimed by a
                # different laptop ignores us, and we ignore it.
                "laptop_id": laptop_id or "", "boards": rt["gbridge"]}
        lines += [
            "  gbridge:",
            "    build: { context: ., dockerfile: docker/Dockerfile.fabric }",
            "    command: [\"python\", \"-m\", \"dataplane.gbridge\"]",
            "    networks: [gini]",
            "    ports:",
            f'      - "{GBRIDGE_HOST_PORT}:{GBRIDGE_PORT}/udp"',
            f'      - "{GBRIDGE_STATUS_PORT}:{GBRIDGE_STATUS_PORT}"',
            "    environment:",
            f"      GBRIDGE_CONFIG: '{json.dumps(gcfg)}'",
        ]

    # each router = its own real C gRouter container (prebuilt image). The gRouter's
    # `tun` links are pure userspace UDP, so no NET_ADMIN / /dev/net/tun needed.
    for r in rt["routers"]:
        # A router's terminal fronts the gRouter CLI, not a shell — that is what a student came
        # for. grconsole is a CLIENT of the running daemon and needs the control socket path:
        # <confdir>/<name>.ctl, and the image sets GINI_HOME=/run. Without the argument it prints
        # its usage to stderr and exits 2, so the tab showed "usage: grconsole.py <socket>" and
        # then ttyd's "Press Enter to Reconnect". Same form the external-terminal button uses.
        # No `exec`: when the student quits the CLI they land in a shell in the router's own
        # container rather than having the session die under them.
        term = _term_port(r["name"], _persist(
            f"python3 /build/grouter-build/grconsole.py /run/{r['name']}.ctl"))
        lines += [
            f"  {r['name']}:",
            f"    image: {GROUTER_IMAGE}",
            "    pull_policy: never",       # the image is built locally, never from a registry
            "    networks: [gini]",
            "    volumes:",
            f'      - "{cap_host}:/captures"',   # tap VNF .pcap files land on the host here
            f'      - "{scr_host}:/scripts:ro"', # student Lua modules: `gpipe add lua /scripts/x.lua`
            "    ports:", term,
            "    environment:",
            f"      ROUTER_CONFIG: '{json.dumps(r)}'",
            *_term_env(r["name"]),
        ]

    # SDN controllers = POX (Python 3) containers, one per controller
    for c in rt["controllers"]:
        term = _term_port(c["name"], _persist())   # a shell: POX itself owns the foreground
        lines += [
            f"  {c['name']}:",
            f"    image: {POX_IMAGE}",
            "    pull_policy: never",
            "    networks: [gini]",
            "    ports:", term,
            "    environment:",
            f"      POX_APP: '{c['app']}'",
            f"      POX_PORT: '{c['port']}'",
            *_term_env(c["name"]),
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
        # Same image as a router, but a SHELL: on a switch the student wants ovs-style
        # inspection and the gRouter's `openflow …` CLI, not to be dropped straight into one.
        term = _term_port(o["name"], _persist())
        env += _term_env(o["name"])       # AFTER _term_port: that call is what registers it
        lines += [
            f"  {o['name']}:",
            f"    image: {GROUTER_IMAGE}",
            "    pull_policy: never",
            "    networks: [gini]",
            "    volumes:",
            f'      - "{cap_host}:/captures"',   # tap VNF .pcap files land on the host here
            f'      - "{scr_host}:/scripts:ro"', # student Lua modules: `gpipe add lua /scripts/x.lua`
            "    ports:", term,
            *depends,
            *env,
        ]

    # managed cloud services = ordinary containers from public images on the bridge,
    # reachable by service name (cloud-style discovery). Web consoles are published.
    for s in rt["services"]:
        lines += [
            f"  {s['name']}:",
            f"    image: {s['image']}",
            f"    networks: [{', '.join(s.get('networks') or ['gini'])}]",   # VPC/subnet nets, or flat gini
        ]
        # locally-built images (xv6 kernel, OS Zoo emulators) are never on a registry, so tell
        # compose not to try to pull them — otherwise `docker compose up` fails with access denied.
        if str(s.get("image", "")).startswith(("gini-xv6", "gini-oszoo")):
            lines.append("    pull_policy: never")
        if s.get("runtime"):                                  # Kata Instance -> VM isolation
            lines.append(f"    runtime: {s['runtime']}")
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
        lines += _cpu_limit_lines(s.get("cpus"))
        if s.get("volumes"):
            lines.append("    volumes:")
            for v in s["volumes"]:
                lines.append(f'      - "{_hostpath(v)}"')   # may carry an absolute host path

    # the GINI Cloud Fabric agent — watches every cloud service, serves normalized
    # app-level metrics to gBuilder on a fixed host port.
    fab = rt.get("fabric")
    if fab:
        fcfg = {"services": fab["services"]}
        # the agent polls every cloud service for the dashboard, so it must reach into each
        # VPC — multi-home it onto gini + every VPC network (GINI's own infra, not a tenant
        # resource, so crossing VPC boundaries here is intentional).
        # the agent reaches members over each VPC's internal fabric net (private members
        # live ONLY there); it doesn't need the public egress nets.
        fab_nets = ", ".join(["gini"] + [v["name"] for v in vpc_nets if v.get("internal")])
        lines += [
            "  cloudfabric:",
            "    build: { context: ., dockerfile: docker/Dockerfile.cloudfabric }",
            f"    networks: [{fab_nets}]",
            "    environment:",
            f"      FABRIC_CONFIG: '{json.dumps(fcfg)}'",
            f"      FABRIC_PORT: '{fab['port']}'",
            "    ports:",
            f'      - "{CLOUDFABRIC_HOST_PORT}:{fab["port"]}"',
        ]

    # serverless: ONE faas runtime container hosts every Function (multiplexed handlers),
    # reachable by name at http://faas:8000/<name>. Only emitted if the canvas has Functions.
    faas_funcs = rt.get("faas")
    if faas_funcs:
        lines += [
            "  faas:",
            "    build: { context: ., dockerfile: docker/Dockerfile.faas }",
            "    networks: [gini]",
            "    environment:",
            f"      FAAS_CONFIG: '{json.dumps({'functions': faas_funcs})}'",
        ]

    # security groups: a tiny iptables sidecar per firewalled member. It shares the member's
    # network namespace (network_mode: service:<member>) so its rules filter the member's
    # traffic without changing the member's image; it installs the rules once and exits.
    for fw in rt.get("firewalls", []):
        member = fw["member"]
        lines += [
            f"  {member}_fw:",
            "    build: { context: ., dockerfile: docker/Dockerfile.sg }",
            f"    network_mode: \"service:{member}\"",
            "    cap_add: [NET_ADMIN]",
            f"    depends_on: [{member}]",
            "    restart: \"no\"",
            "    volumes:",
            f'      - "./sg/{member}.sh:/sg/run.sh:ro"',
            '    command: ["sh", "/sg/run.sh"]',
        ]

    # real Kubernetes clusters — k3s in a container (privileged). Pods are scheduled by
    # k3s inside it; gBuilder applies manifests + reads state via `kubectl exec`.
    for k in rt.get("k8s", []):
        lines += [
            f"  {k['name']}:",
            f"    image: {k['image']}",
            "    command: server --disable=traefik --snapshotter=native",
            "    privileged: true",
            "    tmpfs: [/run, /var/run]",
            "    environment:",
            '      K3S_KUBECONFIG_MODE: "644"',
            "    networks: [gini]",
            "    volumes:",
            f"      - ./k8s/{k['name']}:/gini-manifests:ro",
        ]

    for m in rt["machines"]:
        m = dict(m)
        # faithful mode: a plain host with no internet path of its own drops its docker
        # default route, so it genuinely can't reach the world (the management NIC isn't a
        # back door). Hosts that route to a drawn Internet element keep/replace it instead.
        m["cut_default"] = (not auto_internet and not m.get("fabric_default")
                            and not m.get("gateway") and not m.get("nf"))  # VNFs are transit
        nets = f"[gini, {WAN_NET}]" if m.get("gateway") else "[gini]"
        # Which toolkit this host was built with. `image:` is given EXPLICITLY so that ten hosts
        # sharing a toolkit resolve to ONE image and compose builds it once — without it, compose
        # tags a separate <project>-<service> image per host and walks the build for each.
        tk = m.get("toolkit", MACHINE_TOOLKIT_DEFAULT)
        image, dockerfile = _MACHINE_IMAGE.get(tk, _MACHINE_IMAGE[MACHINE_LEAN])
        term = _term_port(m["name"], _persist())   # a plain shell — it is a host
        lines += [
            f"  {m['name']}:",
            f"    hostname: {m.get('hostname', m['name'])}",   # `hostname` = the canvas label
            f"    image: {image}",
            f"    build: {{ context: ., dockerfile: {dockerfile} }}",
            "    cap_add: [NET_ADMIN]",
            '    devices: ["/dev/net/tun:/dev/net/tun"]',
            f"    networks: {nets}",
            "    environment:",
            f"      NODE_CONFIG: '{json.dumps(m)}'",
            *_term_env(m["name"]),
        ]
        from ..app.paths import captures_dir, shared_dir
        # every station mounts ~/.gini/shared at /shared: students edit sources on the
        # host and compile in the container (the Multicast File Distribution capstone),
        # and a station can drop results back for the host to inspect.
        vols = [f'      - "{_hostpath(shared_dir())}:/shared"']
        if tk == MACHINE_SECURITY:               # an IDS host reads the router's Tap FIFO here
            vols.append(f'      - "{_hostpath(captures_dir())}:/captures"')
        lines += ['    volumes:'] + vols
        # ONE ports block per service: the terminal, plus noVNC on a headful host. Two
        # separate blocks is a duplicate YAML key and compose refuses to start the lab.
        pubs = [term]
        if tk == MACHINE_GUI and m.get("novnc_port"):   # headful host: publish its noVNC console
            pubs.append(f'      - "{m["novnc_port"]}:6080"')
        lines += ["    ports:", *pubs]
        lines += _cpu_limit_lines(m.get("cpus"))
    _LAST_TERMINALS.clear()                  # hand the port map to write_project
    _LAST_TERMINALS.update(_term)
    return "\n".join(_cap_service_logs(lines)) + "\n"


def _cap_service_logs(lines: list[str]) -> list[str]:
    """Insert a json-file log cap into every service block (right after its `image:` or
    `build:` line — each service has exactly one, at 4-space indent). Without caps,
    Docker keeps EVERY log line forever; chatty containers (per-packet router logging)
    poured unbounded logs into the VM and fed the OOM pressure that killed a lab."""
    cap = ["    logging:",
           "      driver: json-file",
           '      options: {max-size: "5m", max-file: "2"}']
    out: list[str] = []
    capped = False          # once per service block — some services emit BOTH build: AND image:
    for ln in lines:
        if ln and not ln.startswith("    "):    # a new service header (or top-level key)
            capped = False
        out.append(ln)
        if not capped and (ln.startswith("    image:") or ln.startswith("    build:")):
            out.extend(cap)
            capped = True
    return out


def _cpu_limit_lines(cpus) -> list[str]:
    """Compose lines for a per-container CPU cap from the element's size tier.
    Uses `deploy.resources.limits.cpus`, which `docker compose up` (v2) enforces."""
    try:
        c = float(cpus or 0)
    except (TypeError, ValueError):
        c = 0.0
    if c <= 0:
        return []
    return ["    deploy:", "      resources:", "        limits:", f'          cpus: "{c:g}"']


def _startup_ms(created: str, started: str) -> float | None:
    """Milliseconds from a container's Created to its StartedAt — the headline
    VM-vs-container signal (a Kata microVM boots a guest kernel, so it starts much slower
    than a plain container). Tolerates Docker's RFC3339 nanosecond timestamps."""
    import re
    from datetime import datetime

    def parse(ts: str):
        ts = (ts or "").strip()
        if not ts or ts.startswith("0001"):          # Docker's zero value = never started
            return None
        ts = ts.replace("Z", "+00:00")
        m = re.match(r"(.*\.\d{6})\d*([+-]\d\d:\d\d)?$", ts)   # trim ns -> microseconds
        if m:
            ts = m.group(1) + (m.group(2) or "")
        try:
            return datetime.fromisoformat(ts)
        except ValueError:
            return None

    c, s = parse(created), parse(started)
    if c is None or s is None:
        return None
    return round((s - c).total_seconds() * 1000.0, 1)


class Orchestrator:
    """Manages the Docker lifecycle of a compiled topology on the user's machine."""

    def __init__(self, runtime_dir: str | Path, project: str | None = None) -> None:
        self.runtime_dir = Path(runtime_dir)
        self.workdir: Path | None = None
        self.project = project        # docker compose -p <project> (per-student namespacing)
        # mDNS announcer for GINI32 boards. Lives here (on the host) and not in a
        # container because multicast is link-local and does not cross Docker's bridge.
        self._advertiser = None

    @property
    def _dc(self) -> list:
        """`docker compose` (+ `-p <project>` when namespaced). Use for EVERY compose call
        so read-backs hit the same project the stack was launched under."""
        return ["docker", "compose"] + (["-p", self.project] if self.project else [])

    def up(self, config: RuntimeConfig, workdir: str | Path,
           auto_internet: bool = True, laptop_id: str = "") -> tuple[bool, str]:
        if config.routers or config.ovs_switches:   # routers & OVS use the gRouter image
            ok, msg = self._ensure_grouter_image()
            if not ok:
                return False, msg
        if config.controllers:                       # SDN controllers use the POX image
            ok, msg = self._ensure_pox_image()
            if not ok:
                return False, msg
        self.workdir = write_project(config, workdir, self.runtime_dir,
                                     auto_internet, laptop_id)
        # --remove-orphans clears containers from a previous run that are no longer in
        # this compose, so stale services (e.g. an old web app) can't linger on the
        # network and shadow / break name resolution.
        ok, msg = self._compose("up", "--build", "-d", "--remove-orphans")
        if ok:
            self._start_advertiser(config)
        return ok, msg

    # ---- GINI32 discovery ------------------------------------------------- #

    def _start_advertiser(self, config: RuntimeConfig) -> None:
        """Announce this lab on the local link, but only while boards are drawn.

        Advertising unconditionally would let a board latch onto a laptop that is running
        nothing, so the announcement is tied to the lifetime of a topology that actually
        has hardware in it."""
        self._stop_advertiser()
        self.advertise_error = ""
        if not getattr(config, "gbridge", None):
            return
        try:
            from .discovery import GiniAdvertiser
            adv = GiniAdvertiser(
                port=GBRIDGE_HOST_PORT,
                txt={"boards": str(len(config.gbridge)), "port": str(GBRIDGE_HOST_PORT)})
            if adv.start():
                self._advertiser = adv
            else:
                self.advertise_error = adv.error or "could not open port 5353"
        except Exception as exc:
            # discovery is a convenience: a board can always be given an address by hand,
            # so nothing here may take the lab down. It is recorded, never hidden.
            self._advertiser = None
            self.advertise_error = f"{exc.__class__.__name__}: {exc}"

    def _stop_advertiser(self) -> None:
        if self._advertiser is not None:
            try:
                self._advertiser.stop()
            except Exception:
                pass
            self._advertiser = None

    @property
    def advertising(self) -> str | None:
        """"<ip>:<port>" while announcing, else None — shown in the UI/diagnostics."""
        a = self._advertiser
        return f"{a.address}:{a.port}" if a is not None and a.running else None

    def redeploy_faas(self, config: RuntimeConfig, auto_internet: bool = True
                      ) -> tuple[bool, str]:
        """Re-deploy ONLY the serverless runtime with the current function code — the GINI
        analogue of AWS 'Deploy'. Regenerates the project files (new FAAS_CONFIG / run_faas.py)
        and recreates just the `faas` container; everything else in the lab keeps running
        (databases, queues, their data). Needs a lab already up (self.workdir set)."""
        if not self.workdir:
            return False, "the lab isn't running — press Run first"
        if not config.faas:
            return False, "no Functions on the canvas to deploy"
        write_project(config, self.workdir, self.runtime_dir, auto_internet)
        # --no-deps: don't touch the function's dependencies (queues/DBs stay up);
        # --force-recreate: pick up the new FAAS_CONFIG even though the image is cached.
        return self._compose("up", "-d", "--no-deps", "--force-recreate", "--build", "faas")

    @staticmethod
    def _autobuild_enabled(kind: str) -> bool:
        """May we build a missing backend image ourselves? `GINI_AUTOBUILD_<KIND>` wins when set
        (scripts / CI pin it explicitly); otherwise the persisted Settings toggle
        (Settings → Networking → "Build missing lab images automatically") decides."""
        env = os.environ.get(f"GINI_AUTOBUILD_{kind}")
        if env is not None:
            return env == "1"
        try:
            from ..app.paths import load_config
            return bool(load_config().get("autobuild_images", False))
        except Exception:                                # noqa: BLE001 — no config = not enabled
            return False

    @staticmethod
    def _image_present(name: str, tries: int = 3, pause: float = 1.5) -> bool:
        """Whether Docker holds this image — asked more than once, and that is the whole point.

        Docker Desktop's Resource Saver pauses the VM when the machine goes idle. The first command
        after it wakes reaches a daemon that ANSWERS — so this is not a connection failure and no
        "is Docker running?" check catches it — out of an image store that has not finished
        loading, and `docker image inspect gini-grouter` comes back "No such image" for an image
        that is sitting right there. Seconds later the same command succeeds.

        Concluding absence from one answer turned a two-second wake into "the gRouter image isn't
        built yet", followed by a `docker build` command pointing into a `backend/` that a wheel
        does not contain — so the advice was impossible to follow as well as wrong. Intermittently,
        which is the worst way for it to be wrong.
        """
        for attempt in range(max(1, tries)):
            r = subprocess.run(["docker", "image", "inspect", name],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace")
            if r.returncode == 0:
                return True
            if attempt + 1 < tries:
                time.sleep(pause)
        # Still no. Before believing it, ask a DIFFERENT question — see `_retag_if_orphaned`.
        return Orchestrator._retag_if_orphaned(name)

    @staticmethod
    def _retag_if_orphaned(name: str) -> bool:
        """Repair a name Docker has lost track of, and say whether the image is really here.

        The work is in `setup.images.repair_tag`, because the SAME defect strikes twice from two
        directions: Run refuses to start a topology, and `missing_locally` decides the images are
        gone and re-downloads all four on every launch. One repair, one place, one explanation.

        It matters here beyond the check: compose resolves `image: gini-grouter` through the same
        index, so a run waved past a laxer test would fail deeper in with a far less legible
        message.
        """
        try:
            from ..setup.images import repair_tag
            return repair_tag(name)
        except Exception:                            # noqa: BLE001 — a repair must not raise
            return False

    @staticmethod
    def _no_image_advice(image: str, build_cmd: str, have_backend: bool) -> str:
        """What to actually do about a missing image, which depends on how GINI was installed.

        From a wheel (pip or pipx) there is no `backend/` and never was, so telling someone to cd
        into one is advice they cannot take. `gini-setup` is the answer there: it pulls the
        published images and tags them under the plain names the runtime resolves.
        """
        if have_backend:
            return (f"The image '{image}' isn't built yet.\n"
                    f"Build it once (takes a couple of minutes):\n  {build_cmd}\n"
                    f"…then press Run again.\n"
                    f"(Or tick Settings → Networking → \"Build missing lab images "
                    f"automatically\" and press Run — GINI will build it for you.)")
        return (f"The image '{image}' is not on this machine.\n"
                f"Fetch the lab images once:\n  gini-setup\n"
                f"…then press Run again.")

    def _docker_not_ready(self) -> str:
        """"" when Docker can serve a lab, otherwise what is wrong with it.

        Checked only on the failure path, and only to tell two different problems apart: a daemon
        that is down needs starting, and a missing image needs fetching. The same distinction
        `setup/runtime.docker_state` already draws, for the same reason — telling somebody who has
        Docker to install Docker sends them off to fix the wrong thing.
        """
        try:
            from ..setup.runtime import docker_state
            state = docker_state()
        except Exception:                                # noqa: BLE001
            return ""
        if state == "missing":
            return "docker not found — is Docker installed?"
        if state == "stopped":
            return ("Docker is installed but its engine is not answering.\n"
                    "Start Docker Desktop (or `colima start`), give it a moment, and press Run "
                    "again.")
        return ""

    def _ensure_grouter_image(self) -> tuple[bool, str]:
        """The real gRouter runs from a locally-built image. Check it exists and, if we
        can find the backend, offer to build it — otherwise return the exact command."""
        if shutil.which("docker") is None:
            return False, "docker not found — is Docker installed and running?"
        if self._image_present(GROUTER_IMAGE):
            return True, "image present"
        # locate the backend (repo_root/backend) relative to this file
        backend = Path(__file__).resolve().parents[4] / "backend"
        dockerfile = backend / "grouter-build" / "Dockerfile"
        build_cmd = (f"cd {backend} && docker build -f grouter-build/Dockerfile "
                     f"-t {GROUTER_IMAGE} .")
        # Asked only now that the image looks absent, because a daemon that cannot answer looks
        # exactly like one that has nothing — and the two need opposite advice.
        down = self._docker_not_ready()
        if down:
            return False, down
        if not dockerfile.exists() or not self._autobuild_enabled("GROUTER"):
            return False, self._no_image_advice(GROUTER_IMAGE, build_cmd, dockerfile.exists())
        # opt-in auto-build
        b = subprocess.run(["docker", "build", "-f", "grouter-build/Dockerfile",
                            "-t", GROUTER_IMAGE, "."], cwd=str(backend),
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
        if b.returncode != 0:
            return False, f"Building {GROUTER_IMAGE} failed:\n{(b.stderr or b.stdout)[-800:]}"
        return True, f"built {GROUTER_IMAGE}"

    def _ensure_pox_image(self) -> tuple[bool, str]:
        """The SDN controller runs from a locally-built POX image."""
        if shutil.which("docker") is None:
            return False, "docker not found — is Docker installed and running?"
        if self._image_present(POX_IMAGE):
            return True, "image present"
        sdn = Path(__file__).resolve().parents[4] / "backend" / "sdn"
        build_cmd = f"cd {sdn} && docker build -t {POX_IMAGE} ."
        down = self._docker_not_ready()
        if down:
            return False, down
        if not (sdn / "Dockerfile").exists() or not self._autobuild_enabled("POX"):
            return False, self._no_image_advice(POX_IMAGE, build_cmd, (sdn / "Dockerfile").exists())
        b = subprocess.run(["docker", "build", "-t", POX_IMAGE, "."], cwd=str(sdn),
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
        if b.returncode != 0:
            return False, f"Building {POX_IMAGE} failed:\n{(b.stderr or b.stdout)[-800:]}"
        return True, f"built {POX_IMAGE}"

    def down(self) -> tuple[bool, str]:
        self._stop_advertiser()      # stop announcing before the relay goes away
        if not self.workdir:
            return True, "nothing running"
        return self._compose("down")

    def status(self, workdir: str | Path | None = None) -> dict[str, str]:
        """Map service -> state ('running' / 'exited' / ...) via `docker compose ps`."""
        wd = workdir or self.workdir
        if not wd:
            return {}
        try:
            r = subprocess.run([*self._dc, "ps", "--format", "json"],
                               cwd=str(wd), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
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

    def set_controller_app(self, service: str, app: str,
                           workdir: str | Path | None = None) -> tuple[bool, str]:
        """Point a RUNNING controller at a different POX app, restarting only it.

        POX loads its components once at process start (`pox.py openflow.of_01 <modules>`)
        and has no hot-reload, so a new App means a new controller process. What it does
        NOT need is a full topology restart: the switches reconnect on their own, which
        makes trying a different controller app a few seconds' work instead of a
        stop/start cycle.

        The app is baked into the compose file as POX_APP, and `docker compose restart`
        re-runs a container with its EXISTING config — so the value has to be rewritten
        first and the container recreated. Only that one service's environment line is
        touched (not a full project regeneration), so nothing else in a running lab can
        shift underneath the containers already up.
        """
        wd = workdir or self.workdir
        if not wd:
            return False, "not running"
        compose = Path(wd) / "docker-compose.yml"
        if not compose.exists():
            return False, "no docker-compose.yml in the project directory"

        # Rewrite POX_APP inside THIS service's block only. The file is generated with a
        # fixed two-space service indent, so the block runs from "  <service>:" to the
        # next line at that indent.
        out, in_block, replaced = [], False, False
        for line in compose.read_text(encoding="utf-8").splitlines():
            if line.startswith("  ") and line.rstrip().endswith(":") and not line.startswith("   "):
                in_block = (line.strip().rstrip(":") == service)
            if in_block and line.strip().startswith("POX_APP:"):
                out.append(f"      POX_APP: '{app}'")
                replaced = True
                continue
            out.append(line)
        if not replaced:
            return False, f"no POX_APP entry found for service '{service}'"
        compose.write_text("\n".join(out) + "\n", encoding="utf-8")

        # --no-deps so only the controller is touched; --force-recreate so the new
        # environment is actually picked up rather than the old container restarted.
        return self._compose("up", "-d", "--force-recreate", "--no-deps", service)

    def update_cpus(self, service: str, cpus: float,
                    workdir: str | Path | None = None) -> tuple[bool, str]:
        """Live-change a running container's CPU cap (vertical scaling), no restart, via
        `docker update --cpus`. Resolves the container id through `docker compose ps`."""
        wd = workdir or self.workdir
        if not wd:
            return False, "not running"
        try:
            r = subprocess.run([*self._dc, "ps", "-q", service],
                               cwd=str(wd), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
            ids = (r.stdout or "").strip().splitlines()
            if not ids or not ids[0]:
                return False, f"{service}: no running container"
            u = subprocess.run(["docker", "update", "--cpus", f"{cpus:g}", ids[0]],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
            return u.returncode == 0, (u.stderr or u.stdout).strip()
        except FileNotFoundError:
            return False, "docker not found — is Docker installed and running?"
        except subprocess.TimeoutExpired:
            return False, "docker update timed out"

    def stats(self, service: str, workdir: str | Path | None = None) -> dict | None:
        """One cheap sample of a running container's CPU% and memory (MiB) via
        `docker stats --no-stream` (just reads cgroup counters). None if unavailable."""
        wd = workdir or self.workdir
        if not wd:
            return None
        try:
            r = subprocess.run([*self._dc, "ps", "-q", service],
                               cwd=str(wd), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
            ids = (r.stdout or "").strip().splitlines()
            if not ids or not ids[0]:
                return None
            s = subprocess.run(
                ["docker", "stats", "--no-stream", "--format",
                 "{{.CPUPerc}}|{{.MemUsage}}|{{.NetIO}}", ids[0]],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
            line = (s.stdout or "").strip()
            if s.returncode != 0 or not line:
                return None
            parts = line.split("|")
            cpu = float(parts[0].strip().rstrip("%") or 0)
            mem = _parse_mem_mib(parts[1].split("/")[0]) if len(parts) > 1 else 0.0
            net = 0.0
            if len(parts) > 2:                  # "1.2kB / 3.4kB" (rx / tx) -> total bytes
                rx, _, tx = parts[2].partition("/")
                net = _parse_bytes(rx) + _parse_bytes(tx)
            return {"cpu": cpu, "mem_used": mem, "net_bytes": net}
        except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
            return None

    def k8s_apply(self, service: str) -> tuple[bool, str]:
        """Wait for the k3s API to be Ready, then `kubectl apply` the generated manifests
        (run inside the cluster container — k3s ships kubectl, so no host kubectl needed)."""
        if not self.workdir:
            return False, "not running"
        base = [*self._dc, "exec", "-T", service, "kubectl"]
        for _ in range(40):                  # k3s takes ~15-30s to come up
            try:
                r = subprocess.run(base + ["get", "nodes", "--no-headers"],
                                   cwd=str(self.workdir), capture_output=True,
                                   text=True, encoding="utf-8", errors="replace", timeout=20)
            except (FileNotFoundError, subprocess.TimeoutExpired):
                return False, "docker/cluster not reachable"
            if r.returncode == 0 and " Ready" in (" " + r.stdout):
                break
            time.sleep(3)
        else:
            return False, "k3s API did not become Ready in time"
        a = subprocess.run(base + ["apply", "-f", "/gini-manifests/"],
                           cwd=str(self.workdir), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        return a.returncode == 0, (a.stderr or a.stdout).strip()

    def k8s_pods(self, service: str) -> list:
        """`kubectl get pods -o json` inside the cluster -> [{name,app,phase,node,restarts}]."""
        if not self.workdir:
            return []
        try:
            r = subprocess.run(
                [*self._dc, "exec", "-T", service, "kubectl", "get", "pods",
                 "-A", "-o", "json"], cwd=str(self.workdir),
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return []
        if r.returncode != 0:
            return []
        try:
            data = json.loads(r.stdout)
        except json.JSONDecodeError:
            return []
        pods = []
        for it in data.get("items", []):
            md, st = it.get("metadata", {}), it.get("status", {})
            if md.get("namespace") in ("kube-system",):     # hide cluster infra pods
                continue
            pods.append({"name": md.get("name"),
                         "app": (md.get("labels") or {}).get("app"),
                         "phase": st.get("phase"),
                         "node": (it.get("spec") or {}).get("nodeName")})
        return pods

    def k8s_metrics(self, service: str) -> dict:
        """Per-deployment K8s metrics for the Live view: replicas, CPU% vs HPA target,
        min/max. From `kubectl get hpa,deploy -o json` (one exec). {deployments:{name:{…}}}."""
        if not self.workdir:
            return {}
        try:
            r = subprocess.run(
                [*self._dc, "exec", "-T", service, "kubectl",
                 "get", "hpa,deploy", "-o", "json"], cwd=str(self.workdir),
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return {}
        if r.returncode != 0:
            return {}
        try:
            data = json.loads(r.stdout)
        except json.JSONDecodeError:
            return {}
        deps: dict = {}
        for it in data.get("items", []):
            md = it.get("metadata", {}) or {}
            if md.get("namespace") in ("kube-system", "kube-public", "kube-node-lease"):
                continue
            kind, nm = it.get("kind"), md.get("name")
            if kind == "Deployment":
                d = deps.setdefault(nm, {})
                d["replicas"] = (it.get("status") or {}).get("readyReplicas", 0)
                d["desired"] = (it.get("spec") or {}).get("replicas", 0)
            elif kind == "HorizontalPodAutoscaler":
                sp, st = it.get("spec", {}) or {}, it.get("status", {}) or {}
                tgt = (sp.get("scaleTargetRef") or {}).get("name", nm)
                d = deps.setdefault(tgt, {})
                d["min"], d["max"] = sp.get("minReplicas"), sp.get("maxReplicas")
                d["hpa"] = nm
                if st.get("currentReplicas") is not None:
                    d["replicas"] = st["currentReplicas"]
                for m in sp.get("metrics", []) or []:
                    res = m.get("resource") or {}
                    if res.get("name") == "cpu":
                        d["target_pct"] = (res.get("target") or {}).get("averageUtilization")
                for cm in st.get("currentMetrics", []) or []:
                    res = cm.get("resource") or {}
                    if res.get("name") == "cpu":
                        d["cpu_pct"] = (res.get("current") or {}).get("averageUtilization")
                if d.get("target_pct") is None:
                    d["target_pct"] = sp.get("targetCPUUtilizationPercentage")
                if d.get("cpu_pct") is None:
                    d["cpu_pct"] = st.get("currentCPUUtilizationPercentage")
        pods = sum(int(d.get("desired") or d.get("replicas") or 0) for d in deps.values())
        return {"deployments": deps, "pods": pods}

    def k8s_scale(self, service: str, deployment: str, replicas) -> tuple[bool, str]:
        """Live `kubectl scale` a Deployment (the Pod Replicas slider)."""
        if not self.workdir:
            return False, "not running"
        try:
            r = subprocess.run(
                [*self._dc, "exec", "-T", service, "kubectl", "scale",
                 f"deployment/{deployment}", f"--replicas={int(replicas)}"],
                cwd=str(self.workdir), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
            return r.returncode == 0, (r.stderr or r.stdout).strip()
        except (FileNotFoundError, subprocess.TimeoutExpired, ValueError) as e:
            return False, str(e)

    def k8s_set_hpa(self, service: str, hpa: str, target=None, mn=None,
                    mx=None) -> tuple[bool, str]:
        """Live-patch an HPA's target CPU% / min / max (the Autoscaling Group sliders)."""
        if not self.workdir:
            return False, "not running"
        spec: dict = {}
        if mn is not None:
            spec["minReplicas"] = int(mn)
        if mx is not None:
            spec["maxReplicas"] = int(mx)
        if target is not None:
            spec["metrics"] = [{"type": "Resource", "resource": {"name": "cpu",
                "target": {"type": "Utilization", "averageUtilization": int(target)}}}]
        try:
            r = subprocess.run(
                [*self._dc, "exec", "-T", service, "kubectl", "patch",
                 f"hpa/{hpa}", "--type", "merge", "-p", json.dumps({"spec": spec})],
                cwd=str(self.workdir), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
            return r.returncode == 0, (r.stderr or r.stdout).strip()
        except (FileNotFoundError, subprocess.TimeoutExpired, ValueError) as e:
            return False, str(e)

    def stats_all(self, workdir: str | Path | None = None) -> dict:
        """One `docker stats --no-stream` for ALL containers -> {service: {cpu,mem_used,
        net_bytes}}. One call keeps every element's history live regardless of selection."""
        wd = workdir or self.workdir
        if not wd:
            return {}
        try:
            s = subprocess.run(
                ["docker", "stats", "--no-stream", "--format",
                 "{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}|{{.NetIO}}"],
                cwd=str(wd), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return {}
        if s.returncode != 0:
            return {}
        import re
        out: dict = {}
        for line in (s.stdout or "").strip().splitlines():
            parts = line.split("|")
            if len(parts) < 4:
                continue
            m = re.search(r"[-_]([a-z0-9]+)[-_]\d+$", parts[0].strip())   # …_<svc>_1
            if not m:
                continue
            try:
                cpu = float(parts[1].strip().rstrip("%") or 0)
            except ValueError:
                cpu = 0.0
            rx, _, tx = parts[3].partition("/")
            out[m.group(1)] = {"cpu": cpu,
                               "mem_used": _parse_mem_mib(parts[2].split("/")[0]),
                               "net_bytes": _parse_bytes(rx) + _parse_bytes(tx)}
        return out

    _vm_mem_mib: float | None = None      # cached — the VM's size doesn't change mid-run

    def vm_memory_mib(self) -> float | None:
        """The Docker VM's total memory (MiB) via `docker info` — the budget every lab
        container shares on macOS. None if the daemon is unreachable. Cached."""
        if Orchestrator._vm_mem_mib is not None:
            return Orchestrator._vm_mem_mib
        try:
            r = subprocess.run(["docker", "info", "--format", "{{.MemTotal}}"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
            if r.returncode == 0 and r.stdout.strip().isdigit():
                Orchestrator._vm_mem_mib = int(r.stdout.strip()) / (1024 * 1024)
                return Orchestrator._vm_mem_mib
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return None

    def runtime_available(self, name: str, workdir: str | Path | None = None) -> bool:
        """Whether the active Docker daemon has an OCI runtime registered under `name`
        (e.g. 'kata'). Used to gate the Kata Instance element + warn on Run."""
        wd = workdir or self.workdir
        try:
            r = subprocess.run(["docker", "info", "--format", "{{json .Runtimes}}"],
                               cwd=(str(wd) if wd else None),
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
            if r.returncode != 0:
                return False
            import json
            return name in (json.loads(r.stdout or "{}") or {})
        except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
            return False

    def startup_times(self, workdir: str | Path | None = None) -> dict:
        """Per-element startup time in ms (Created -> StartedAt) for the running stack —
        the VM-vs-container experiment's headline metric. {service: ms}."""
        wd = workdir or self.workdir
        if not wd:
            return {}
        import re
        try:
            ids = subprocess.run([*self._dc, "ps", "-q"], cwd=str(wd),
                                 capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20).stdout.split()
            if not ids:
                return {}
            r = subprocess.run(
                ["docker", "inspect", "--format",
                 "{{.Name}}\t{{.Created}}\t{{.State.StartedAt}}", *ids],
                cwd=str(wd), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return {}
        out: dict = {}
        for line in (r.stdout or "").splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            m = re.search(r"[-_]([a-z0-9]+)[-_]\d+$", parts[0].strip().lstrip("/"))
            ms = _startup_ms(parts[1], parts[2])
            if m and ms is not None:
                out[m.group(1)] = ms
        return out

    def drive_load(self, host_port: int, url: str, qps, conns=8,
                   dur: str = "3600s") -> tuple[bool, str]:
        """Drive a Fortio load generator via its REST API: (re)start a continuous run at
        `qps` against `url`. Stops any current run first, so this doubles as the throttle."""
        import urllib.parse
        import urllib.request
        base = f"http://localhost:{host_port}/fortio/rest"
        try:
            urllib.request.urlopen(base + "/stop", timeout=5)
        except Exception:                       # noqa: BLE001 — nothing running yet is fine
            pass
        q = urllib.parse.urlencode({"url": url, "qps": qps, "t": dur,
                                    "c": conns, "async": "on"})
        try:
            urllib.request.urlopen(f"{base}/run?{q}", timeout=5).read()
            return True, f"load → {url} @ {qps} req/s"
        except Exception as e:                  # noqa: BLE001
            return False, str(e)

    def stop_load(self, host_port: int) -> tuple[bool, str]:
        import urllib.request
        try:
            urllib.request.urlopen(f"http://localhost:{host_port}/fortio/rest/stop",
                                   timeout=5)
            return True, "stopped"
        except Exception as e:                  # noqa: BLE001
            return False, str(e)

    def fabric_metrics(self) -> dict | None:
        """Poll the GINI Cloud Fabric agent's normalized app-level metrics for the lab.
        None if the lab isn't running or the agent isn't reachable yet."""
        if not self.workdir:
            return None
        try:
            import urllib.request
            url = f"http://localhost:{CLOUDFABRIC_HOST_PORT}/metrics.json"
            with urllib.request.urlopen(url, timeout=3) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception:                       # noqa: BLE001 — agent may not be up yet
            return None

    def board_action(self, action: str, board: str) -> bool:
        """claim / release / blink a real board. Queued by the relay and delivered on
        the board's next contact, which is when we actually know where it is."""
        if not self.workdir or action not in ("claim", "release", "blink"):
            return False
        try:
            import urllib.request
            req = urllib.request.Request(
                f"http://localhost:{GBRIDGE_STATUS_PORT}/{action}",
                data=json.dumps({"board": board}).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=2) as r:
                return bool(json.loads(r.read().decode()).get("ok"))
        except Exception:                    # noqa: BLE001 — relay may not be up
            return False

    def board_status(self) -> dict | None:
        """Live state of every real GINI32 board: online, signal, connected devices.

        The UI calls only this, never the relay directly, so moving the relay out of
        the container later is invisible above this line.
        """
        if not self.workdir:
            return None
        try:
            import urllib.request
            url = f"http://localhost:{GBRIDGE_STATUS_PORT}/"
            with urllib.request.urlopen(url, timeout=2) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception:            # noqa: BLE001 — relay may not be up yet, or no boards
            return None

    def _compose(self, *args: str) -> tuple[bool, str]:
        try:
            r = subprocess.run([*self._dc, *args], cwd=str(self.workdir),
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
            return r.returncode == 0, (r.stderr or r.stdout).strip()
        except FileNotFoundError:
            return False, "docker not found — is Docker installed and running?"
        except subprocess.TimeoutExpired:
            return False, "docker compose timed out"
