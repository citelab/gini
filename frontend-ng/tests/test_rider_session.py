"""Continuous rider sessions — start, stream, auto-finish, and clean stop, all without Docker.

A rider runs as a long-lived process whose stdout is streamed line by line; stopping must kill the
in-container process (via its pidfile) and drop the session. A fake Popen drives the whole lifecycle
deterministically.
"""
import os
import tempfile
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["GINI_HOME_DIR"] = tempfile.mkdtemp()

from gini.domain.topology import Topology
from gini.services import rider_session as RS
from gini.services.rider_session import RiderSessions


class _FakeOrch:
    _dc = ["docker", "compose"]
    workdir = None

    def status(self):
        return {"m1": "running"}


class _FakeStdout:
    """Yields the given lines, then blocks until released (simulating a live process), then EOF."""
    def __init__(self, lines, release: threading.Event):
        self._lines = list(lines)
        self._release = release
        self.closed = False

    def readline(self):
        if self._lines:
            return self._lines.pop(0)
        self._release.wait(2)                 # emulate "still running" until stop releases us
        return ""                             # EOF

    def close(self):
        self.closed = True
        self._release.set()


class _FakeProc:
    def __init__(self, lines, release):
        self.stdout = _FakeStdout(lines, release)
        self.terminated = False
        self._release = release

    def terminate(self):
        self.terminated = True
        self._release.set()


def _topo():
    t = Topology()
    m = t.add_device("host", "M1")
    ping = t.add_device("ping_probe", "PING1", properties={"Target": "M2", "Count": "0"})
    t.add_attach(ping.id, m.id)
    return t, ping.id


def test_start_streams_lines_then_stop_kills_and_drops_the_session(monkeypatch):
    kills = []
    monkeypatch.setattr(RS.subprocess, "run",
                        lambda *a, **k: kills.append(a[0]) or None)   # capture the kill exec

    release = threading.Event()
    proc = _FakeProc(["64 bytes from M2: icmp_seq=0 time=0.1 ms\n",
                      "64 bytes from M2: icmp_seq=1 time=0.2 ms\n"], release)
    sessions = RiderSessions(_FakeOrch(), popen_factory=lambda *a, **k: proc)

    updates = []
    t, rid = _topo()
    res = sessions.start(t, rid, lambda i, snap: updates.append(snap))
    assert res["ok"] and res["running"] and sessions.is_running(rid)

    time.sleep(0.3)                                     # let the reader consume both lines
    sessions.stop(rid)
    # the final snapshot (emitted on stop) carries all lines with the rolled-up measurement
    for _ in range(100):
        if any(not u.get("running") for u in updates):
            break
        time.sleep(0.02)
    final = [u for u in updates if not u.get("running")][-1]
    assert final["measurement"]["received"] == 2
    assert final["measurement"]["rtt_avg_ms"] == 0.15

    assert not sessions.is_running(rid)                 # session dropped
    assert proc.terminated                              # local client terminated
    assert kills and "kill $(cat" in " ".join(kills[0])  # in-container process killed via pidfile


def test_a_finite_run_finishes_on_its_own_and_reports_not_running(monkeypatch):
    monkeypatch.setattr(RS.subprocess, "run", lambda *a, **k: None)
    release = threading.Event(); release.set()          # EOF immediately after the lines
    proc = _FakeProc(["5 packets transmitted, 5 packets received, 0% packet loss\n"], release)
    sessions = RiderSessions(_FakeOrch(), popen_factory=lambda *a, **k: proc)

    done = threading.Event()
    finals = []
    def on_update(i, snap):
        if not snap.get("running"):
            finals.append(snap); done.set()
    t, rid = _topo()
    sessions.start(t, rid, on_update)
    assert done.wait(2)                                 # reader reached EOF and emitted a final snap
    assert finals[-1]["running"] is False
    assert not sessions.is_running(rid)                 # auto-dropped, no manual stop needed
