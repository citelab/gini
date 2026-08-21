"""Docker-backed rider execution (Mac-side).

Runs a Source/Sink INSIDE its donor container via `docker compose exec` — the same seam
`probe_runner` uses — then reduces the raw output to a measurement with `domain.riders`. A rider has
no container of its own, so there is nothing to launch: we exec its tool in the donor the student
attached it to. Written defensively: `available()` is False when no stack is up, and a missing donor
or a broken command returns an error dict rather than raising, so the UI can show it inline.
"""
from __future__ import annotations

import subprocess

from ..domain import riders as _riders


class RiderRunner:
    def __init__(self, orchestrator, *, timeout: float = 15.0) -> None:
        self.orch = orchestrator            # services.orchestrator.Orchestrator (has _dc, status, workdir)
        self.timeout = timeout
        self._svc_cache: dict | None = None

    def available(self) -> bool:
        try:
            states = self.orch.status()
        except Exception:
            return False
        return any(str(s).startswith("running") for s in (states or {}).values())

    def _service(self, name: str) -> str:
        if self._svc_cache is None:
            try:
                self._svc_cache = {k.lower(): k for k in (self.orch.status() or {})}
            except Exception:
                self._svc_cache = {}
        return self._svc_cache.get(name.lower(), name.lower())

    def _exec(self, service: str, argv: list[str]) -> tuple[int, str]:
        cmd = [*self.orch._dc, "exec", "-T", service, *argv]
        wd = getattr(self.orch, "workdir", None)
        try:
            r = subprocess.run(cmd, cwd=str(wd) if wd else None,
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=self.timeout)
            return r.returncode, (r.stdout or "") + (r.stderr or "")
        except subprocess.TimeoutExpired as e:            # keep partial output, flag the timeout
            partial = e.stdout if isinstance(e.stdout, str) else ""
            return (0 if partial else 124), partial
        except (FileNotFoundError, OSError):
            return 124, ""

    # pingable/curlable donor neighbours we can auto-target (reachable by service name)
    _TARGETABLE = {"host", "router", "instance", "container", "kinstance", "web_app", "firewall"}

    def _infer_target(self, topo, donor_id: str, rider_id: str) -> str:
        """When a ping/http rider has no Target, pick a reachable name: a directly-wired neighbour of
        the donor first (excluding riders), else any other targetable node on the board."""
        donor = topo.devices.get(donor_id)
        for nb in topo.neighbors(donor_id):            # prefer a direct network neighbour
            if nb.id != rider_id and nb.type_key in self._TARGETABLE:
                return nb.name
        for d in topo.devices.values():                # else anything else pingable by name
            if d.id not in (donor_id, rider_id) and d.type_key in self._TARGETABLE:
                return d.name
        return ""

    def run(self, topo, rider_id: str) -> dict:
        """Execute the rider on its donor and return
        {ok, raw, measurement, summary, donor} — or {ok: False, error} on any problem."""
        rider = topo.devices.get(rider_id)
        if rider is None:
            return {"ok": False, "error": "no such rider"}
        donor = topo.donor_of(rider_id)
        if donor is None:
            return {"ok": False, "error": f"{rider.name} isn't attached to a donor yet."}

        props = dict(rider.properties)
        inferred = ""
        if rider.type_key in ("ping_probe", "http_probe") and not (props.get("Target") or "").strip():
            inferred = self._infer_target(topo, donor.id, rider_id)
            if inferred:
                props["Target"] = inferred
        try:
            argv = _riders.build_command(rider.type_key, props)
        except _riders.RiderError as e:
            return {"ok": False, "error": str(e)}
        service = self._service(donor.name)
        code, raw = self._exec(service, argv)
        if not (raw or "").strip():
            # never show a blank "no output" — say what ran and how it exited, so the cause is
            # visible (missing tool, unresolved target, timeout, wrong donor service, …)
            hint = "timed out" if code == 124 else f"exit {code}"
            raw = (f"(no output — {hint})\n"
                   f"$ docker compose exec {service} {' '.join(argv)}")
        m = _riders.parse_measurement(rider.type_key, raw)
        return {"ok": True, "code": code, "raw": raw, "measurement": m,
                "summary": _riders.summarize(rider.type_key, m), "donor": donor.name,
                "target": props.get("Target", ""), "inferred_target": inferred}
