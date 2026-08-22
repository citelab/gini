"""Long-running Source/Sink execution — start, stream, and cleanly stop a rider on its donor.

Unlike the one-shot `RiderRunner` (used for deterministic grading), a rider in the app runs
CONTINUOUSLY: start its tool inside the donor, stream output live, stop on demand. Two things make
this safe:

  * **streaming** — a reader thread reads the tool's stdout line by line and pushes a rolling
    {raw, measurement, running} snapshot to an `on_update` callback (the caller marshals it to the
    GUI via a Qt signal);
  * **clean teardown** — `docker exec` does not reliably kill the in-container process when the
    client goes away, so each rider is launched via a tiny wrapper that writes its PID to a file;
    `stop()` kills that PID inside the container, then terminates the local client. No orphans.

Popen + the kill call are injectable so the whole lifecycle is unit-testable without Docker.
"""
from __future__ import annotations

import shlex
import subprocess
import threading

from ..domain import riders as _riders

# donor neighbours a ping/http rider can auto-target (reachable by service name)
_TARGETABLE = {"host", "router", "instance", "container", "kinstance", "web_app", "firewall"}


class _Sess:
    def __init__(self, rider_id, type_key, service, pidfile, proc, donor, inferred) -> None:
        self.rider_id = rider_id
        self.type_key = type_key
        self.service = service
        self.pidfile = pidfile
        self.proc = proc
        self.donor = donor
        self.inferred = inferred
        self.lines: list[str] = []
        self.stop_flag = False


class RiderSessions:
    def __init__(self, orchestrator, *, popen_factory=None, timeout: float = 8.0) -> None:
        self.orch = orchestrator
        self._popen = popen_factory or subprocess.Popen
        self.timeout = timeout
        self._sessions: dict[str, _Sess] = {}
        self._svc_cache: dict | None = None

    # -- state -------------------------------------------------------------- #
    def is_running(self, rider_id: str) -> bool:
        return rider_id in self._sessions

    def running_ids(self) -> list[str]:
        return list(self._sessions)

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

    def _infer_target(self, topo, donor_id: str, rider_id: str) -> str:
        for nb in topo.neighbors(donor_id):
            if nb.id != rider_id and nb.type_key in _TARGETABLE:
                return nb.name
        for d in topo.devices.values():
            if d.id not in (donor_id, rider_id) and d.type_key in _TARGETABLE:
                return d.name
        return ""

    # -- lifecycle ---------------------------------------------------------- #
    def start(self, topo, rider_id: str, on_update) -> dict:
        """Launch the rider on its donor and stream updates. Returns a start result dict."""
        if rider_id in self._sessions:
            return {"ok": True, "running": True, "already": True}
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
        pidfile = f"/tmp/gini-rider-{rider_id}.pid"
        # write our PID, then exec the tool (so `stop` can kill exactly this process in the container)
        wrapped = ["sh", "-lc", f"echo $$ > {pidfile}; exec {shlex.join(argv)}"]
        cmd = [*self.orch._dc, "exec", "-T", service, *wrapped]
        wd = getattr(self.orch, "workdir", None)
        try:
            proc = self._popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, encoding="utf-8", errors="replace", bufsize=1, cwd=str(wd) if wd else None)
        except (FileNotFoundError, OSError) as e:
            return {"ok": False, "error": f"couldn't start: {e}"}

        sess = _Sess(rider_id, rider.type_key, service, pidfile, proc, donor.name, inferred)
        self._sessions[rider_id] = sess
        threading.Thread(target=self._reader, args=(sess, on_update), daemon=True).start()
        return {"ok": True, "running": True, "donor": donor.name, "inferred_target": inferred}

    def _snapshot(self, sess: _Sess, running: bool) -> dict:
        raw = "\n".join(sess.lines)
        m = _riders.parse_measurement(sess.type_key, raw)
        return {"ok": True, "running": running, "raw": raw, "measurement": m,
                "summary": _riders.summarize(sess.type_key, m), "donor": sess.donor,
                "inferred_target": sess.inferred}

    def _reader(self, sess: _Sess, on_update) -> None:
        import time
        last = 0.0
        try:
            for line in iter(sess.proc.stdout.readline, ""):
                if sess.stop_flag:
                    break
                sess.lines.append(line.rstrip("\n"))
                if len(sess.lines) > 300:
                    sess.lines = sess.lines[-300:]
                # THROTTLE: a high-rate rider (tcpdump under load prints per packet) would flood the
                # GUI with rider_ran signals and freeze it. Coalesce to ~4 updates/sec; the final
                # snapshot below always fires, so no data is lost — only intermediate redraws.
                now = time.monotonic()
                if now - last >= 0.25:
                    on_update(sess.rider_id, self._snapshot(sess, running=True))
                    last = now
        except Exception:                                # noqa: BLE001 — a dead pipe just ends the run
            pass
        self._sessions.pop(sess.rider_id, None)          # finite run finished, or we were stopped
        on_update(sess.rider_id, self._snapshot(sess, running=False))

    def stop(self, rider_id: str) -> None:
        sess = self._sessions.get(rider_id)
        if sess is None:
            return
        sess.stop_flag = True
        wd = getattr(self.orch, "workdir", None)
        try:                                             # kill the tool INSIDE the container
            subprocess.run(
                [*self.orch._dc, "exec", "-T", sess.service, "sh", "-lc",
                 f"kill $(cat {sess.pidfile}) 2>/dev/null; rm -f {sess.pidfile}"],
                cwd=str(wd) if wd else None, capture_output=True, timeout=self.timeout)
        except Exception:                                # noqa: BLE001
            pass
        for close in (lambda: sess.proc.terminate(), lambda: sess.proc.stdout.close()):
            try:
                close()
            except Exception:                            # noqa: BLE001
                pass
        self._sessions.pop(rider_id, None)

    def stop_all(self) -> None:
        for rid in list(self._sessions):
            self.stop(rid)
