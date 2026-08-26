"""Docker-backed probe Runner — GINI's runtime as the behavioral oracle (Mac-side).

Implements the `domain.probes.Runner` seam by exec'ing checks inside the running topology's
containers via `docker compose exec` (the same compose project the orchestrator launched). This
runs only where Docker + a live stack exist (the student's Mac), so it is written defensively:
`available()` returns False whenever the stack isn't up, which keeps behavioral objectives
`pending` rather than wrongly failed. Unit-tested logic lives with `domain.probes` (FakeRunner);
this adapter is exercised in real Docker on the Mac.

Service-name resolution: the compiler names each compose service after the device name
(lowercased), so a probe's `src`/`dst` map to services; cloud-plane services reach each other by
name over the shared network.

**Networking-plane addressing.** Pinging a compose service NAME resolves through Docker DNS to the
container's `eth0` — the management bridge every container shares, which carries the fabric only in
encapsulated form. That tests Docker, not the network the student drew, and two stations on
deliberately isolated LANs would answer each other. It is the same trap the book warns about with
`tcpdump -i gini0`. Pass `addresses` (a callable returning `{device_name: ip}`, e.g. from
`compiler.address_map`) and probes target the drawn network instead. Without it the runner falls
back to name resolution and says so via `probes_by_ip`.
"""
from __future__ import annotations

import subprocess
from concurrent.futures import ThreadPoolExecutor

from ..domain import reach_strategy as _reach

# How many `docker compose exec` calls to have in flight at once. The work is almost entirely
# waiting on a subprocess, so concurrency turns the sweep's cost into a series of waves:
#
#     time ~= ceil(n / SWEEP_POOL) * per_wave
#
# Measured on Docker Desktop: per_wave is FLAT at ~0.50 s on a healthy network and ~0.91 s when
# hosts are unreachable (a sweep's wall time is the longest of its parallel pings, and an
# unreachable one burns its full `-W 1`). 9 hosts = 2 waves = 1.01 s; 20 hosts = 3 waves = 1.50 s.
#
# At 8, a 20 s observation cadence has headroom to ~176 hosts even with the network fully broken.
# Raise it for larger cohorts; there is little point going much higher otherwise, since the Docker
# daemon serialises enough that per-wave cost stops falling.
SWEEP_POOL = 8

# Cap on destinations per exec. Each becomes a backgrounded `ping` inside one shell, so a very
# large LAN would otherwise fork hundreds of processes in one container at once.
SWEEP_CHUNK = 64


class DockerProbeRunner:
    def __init__(self, orchestrator, *, timeout: float = 8.0, addresses=None) -> None:
        self.orch = orchestrator            # services.orchestrator.Orchestrator (has _dc, status)
        self.timeout = timeout
        self.addresses = addresses          # () -> {device_name: ip} on the drawn network
        self._svc_cache: dict | None = None

    @property
    def probes_by_ip(self) -> bool:
        """True when probes target the drawn network rather than Docker DNS."""
        return self.addresses is not None

    # -- availability ------------------------------------------------------- #
    def available(self) -> bool:
        try:
            states = self.orch.status()
        except Exception:
            return False
        return any(str(s).startswith("running") for s in (states or {}).values())

    def _service(self, name: str) -> str:
        """Map a device name to its compose service. The orchestrator's status() keys ARE the
        service names; match case-insensitively, else fall back to the lowercased name."""
        if self._svc_cache is None:
            try:
                self._svc_cache = {k.lower(): k for k in (self.orch.status() or {})}
            except Exception:
                self._svc_cache = {}
        return self._svc_cache.get(name.lower(), name.lower())

    def _exec(self, service: str, argv: list[str]) -> tuple[int, str]:
        cmd = [*self.orch._dc, "exec", "-T", service, *argv]
        # `docker compose` (no -f) finds the project via the CWD — every orchestrator call runs in
        # the workdir, so the probe MUST too, or `exec` can't find the running stack (a false
        # negative: probes fail even though the containers are up and reachable).
        wd = getattr(self.orch, "workdir", None)
        try:
            r = subprocess.run(cmd, cwd=str(wd) if wd else None,
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=self.timeout)
            return r.returncode, (r.stdout or "") + (r.stderr or "")
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return 124, ""

    # -- probes ------------------------------------------------------------- #
    def _target(self, name: str) -> str:
        """What to actually probe for `name`: its address on the drawn network when we have one,
        else the compose service name (Docker DNS — see the module docstring's caveat)."""
        if self.addresses is not None:
            try:
                ip = (self.addresses() or {}).get(name)
            except Exception:                       # noqa: BLE001 — never fail a probe over this
                ip = None
            if ip:
                return str(ip).split("/")[0]
        return self._service(name)

    # -- the sweep: one exec per host, every destination in parallel -------- #
    def sweep(self, src: str, dsts: list) -> dict:
        """Ping every destination from `src` in ONE exec. Returns {dst: True|False|None}.

        Two levels of parallelism, for two different reasons.

        *Inside* the container the pings are backgrounded and waited on, so the sweep's worst case
        is the LONGEST timeout rather than their sum. Sequentially, nine unreachable hosts at
        `-W 1` is nine seconds — past this runner's exec timeout, so the exec is killed and the
        sweep returns nothing. That would fail exactly when the network is broken, which is when
        the measurement matters most.

        Results are echoed per destination and parsed by name, so a destination the shell never
        reported stays `None` (unknown) instead of becoming `False`. A sweep that produced nothing
        must never look like a network where nothing is reachable.

        POSIX `sh` and busybox `ping` only — the default station toolkit is Alpine, so anything
        needing bash or iputils would pass in testing and fail in a real lab.
        """
        out: dict = {d: None for d in dsts}
        svc = self._service(src)
        for i in range(0, len(dsts), SWEEP_CHUNK):
            chunk = dsts[i:i + SWEEP_CHUNK]
            pairs = " ".join(f"{d}={self._target(d)}" for d in chunk)
            script = (f'for p in {pairs}; do n=${{p%%=*}}; a=${{p#*=}}; '
                      f'( if ping -c 1 -W 1 "$a" >/dev/null 2>&1; then echo "OK $n"; '
                      f'else echo "NO $n"; fi ) & done; wait')
            _code, text = self._exec(svc, ["sh", "-c", script])
            for line in (text or "").splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[0] in ("OK", "NO") and parts[1] in out:
                    out[parts[1]] = (parts[0] == "OK")
        return out

    def sweep_all(self, hosts) -> "_reach.Relation":
        """The complete reachability relation over `hosts` — every ordered pair MEASURED.

        n execs rather than n(n-1), run `SWEEP_POOL`-way concurrently because each is almost
        entirely subprocess wait. No inference, so no assumption about filtering, no representative
        to choose, and nothing that a single `iptables` rule inside one station can quietly
        invalidate. See `domain.reach_strategy` for why inference was rejected.
        """
        plan = _reach.sweep_plan(hosts)
        if not plan:
            return _reach.measure([], lambda s, d: {})
        with ThreadPoolExecutor(max_workers=min(SWEEP_POOL, len(plan))) as pool:
            results = dict(zip((src for src, _ in plan),
                               pool.map(lambda item: self.sweep(item[0], list(item[1])), plan)))
        return _reach.measure(hosts, lambda src, dsts: results.get(src, {}))

    def reach(self, src: str, dst: str, port: int | None = None) -> bool:
        s = self._service(src)
        d = self._target(dst)
        if port is None:            # L3 reachability via ICMP
            code, _ = self._exec(s, ["ping", "-c", "1", "-W", "1", d])
            return code == 0
        # L4 reachability: a TCP connect using bash's /dev/tcp (no extra tools needed)
        code, _ = self._exec(s, ["bash", "-lc", f"exec 3<>/dev/tcp/{d}/{port}"])
        return code == 0

    def http(self, src: str, dst: str, port: int) -> bool:
        s, d = self._service(src), self._service(dst)
        code, _ = self._exec(s, ["curl", "-fsS", "-m", "4", "-o", "/dev/null",
                                 f"http://{d}:{port}/"])
        return code == 0

    def backends(self, lb: str) -> int:
        """How many backends the load balancer is REALLY serving from — i.e. upstreams it was
        configured with that are actually accepting connections right now. A config listing two
        servers proves nothing; this proves the fan-out has somewhere to go.

        The LB compiles to nginx, so read its upstream block, then probe each target from inside the
        LB container itself (that's the path traffic would take)."""
        import re
        svc = self._service(lb)
        code, out = self._exec(svc, ["sh", "-c",
                                     "cat /etc/nginx/conf.d/*.conf /etc/nginx/nginx.conf 2>/dev/null"])
        if code != 0 or not out:
            return 0
        targets = re.findall(r"^\s*server\s+([A-Za-z0-9._-]+):(\d+)\s*;", out, re.M)
        live = 0
        for host, port in targets:
            # try a few tools — the image is alpine-ish and may have any of them
            probe = (f"nc -z -w2 {host} {port} 2>/dev/null || "
                     f"wget -q -T2 -O /dev/null http://{host}:{port}/ 2>/dev/null || "
                     f"curl -fsS -m2 -o /dev/null http://{host}:{port}/ 2>/dev/null")
            c, _ = self._exec(svc, ["sh", "-c", probe])
            if c == 0:
                live += 1
        return live

    def flow(self, ovs: str, match: str = "") -> bool:
        """Did the controller ACTUALLY install flows on this switch? Reads the live OpenFlow table
        from the gRouter (the OVS runs in --openflow mode) over its control socket — the exact same
        path the Router Lab's flow view uses — and reuses the shipped parser.

        `match` empty (or 'any') = "any flow at all is installed", which is the honest test that the
        control plane did something. A non-empty match is a substring test against the entries."""
        from ..domain.flowtable import flows
        svc = self._service(ovs)
        code, out = self._exec(svc, ["python3", "/build/grouter-build/grconsole.py",
                                     f"/run/{svc}.ctl", "--once", "openflow entry all"])
        if code != 0 or not out:
            return False
        try:
            rows = flows(out)
        except Exception:                            # noqa: BLE001 — a malformed dump is just "no proof"
            return False
        if not rows:
            return False
        if not match or match.lower() in ("any", "*"):
            return True
        needle = match.lower()
        return any(needle in str(r).lower() for r in rows)


class RuntimeRunner(DockerProbeRunner):
    """DockerProbeRunner + `measure()` — the runtime half of an output check. It resolves
    `measure(rider_type, metric)` by reading the LIVE measurement of the matching attached Source/Sink
    from `get_results` (the streaming rider snapshots the grader has already started + let run). We
    read the streaming session's reading rather than doing a one-shot capture, because `docker exec`
    block-buffers output — a timeout-killed one-shot comes back empty even when the manual streaming
    view sees packets. `get_results()` → {rider_id: snapshot}; `get_topology()` finds the rider."""

    def __init__(self, orchestrator, get_topology, get_results, *, timeout: float = 8.0) -> None:
        super().__init__(orchestrator, timeout=timeout)
        self._get_topology = get_topology
        self._get_results = get_results

    def measure(self, rider_type: str, metric: str):
        from ..domain.objectives import slot_match
        base, _, slot = str(rider_type).partition("@")       # measure(packet_view@A, packets)
        topo = self._get_topology()
        results = self._get_results() or {}
        for d in getattr(topo, "devices", {}).values():
            if getattr(d, "type_key", None) != base:
                continue
            if not slot_match(getattr(d, "slot", ""), slot):
                continue
            snap = results.get(d.id)
            m = (snap or {}).get("measurement") or {}
            if metric in m:
                return m.get(metric)
        return None
