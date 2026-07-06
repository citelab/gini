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
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout)
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
        # A load balancer's live backend count is scheme-specific; Phase 2.x reads it from the
        # LB's admin/stats endpoint. For now, report 0 so `balances(>= n)` stays conservative.
        return 0

    def flow(self, ovs: str, match: str) -> bool:
        # Flow-table reads reuse the OVS Router-Lab OpenFlow parser (Phase 2.x). Conservative default.
        return False
