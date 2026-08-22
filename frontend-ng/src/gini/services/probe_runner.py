"""Docker-backed probe Runner — GINI's runtime as the behavioral oracle (Mac-side).

Implements the `domain.probes.Runner` seam by exec'ing checks inside the running topology's
containers via `docker compose exec` (the same compose project the orchestrator launched). This
runs only where Docker + a live stack exist (the student's Mac), so it is written defensively:
`available()` returns False whenever the stack isn't up, which keeps behavioral objectives
`pending` rather than wrongly failed. Unit-tested logic lives with `domain.probes` (FakeRunner);
this adapter is exercised in real Docker on the Mac.

Service-name resolution: the compiler names each compose service after the device name
(lowercased), so a probe's `src`/`dst` map to services; cloud-plane services reach each other by
name over the shared network. Networking-plane addressing (by IP) is a Phase-2.x refinement.
"""
from __future__ import annotations

import subprocess


class DockerProbeRunner:
    def __init__(self, orchestrator, *, timeout: float = 8.0) -> None:
        self.orch = orchestrator            # services.orchestrator.Orchestrator (has _dc, status)
        self.timeout = timeout
        self._svc_cache: dict | None = None

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
    def reach(self, src: str, dst: str, port: int | None = None) -> bool:
        s, d = self._service(src), self._service(dst)
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
