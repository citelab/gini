"""The in-container agent's serial reader (backend/xv6/gini_agent.py).

There is ONE serial line to the kernel and it can carry ONE dump at a time. `SerialLink.dump()`
used to release its lock before waiting for the reply, so two callers could both send, both wait,
and both then read `_last_dump` — because `_dump_seq > seq0` only proves that *a* dump arrived,
never that it was yours.

The visible symptom was an OS HUD that alternated between a perfect kernel board and
"this kernel has no board support": the polls that won the race got the board, the polls that
lost got somebody else's dump, which parses to nothing. It read like an intermittent kernel and
was in fact two readers on one wire.

These tests drive the REAL SerialLink with a stand-in socket, so they pin the actual behaviour
rather than a copy of it.
"""
import importlib.util
import sys
import threading
import time
from pathlib import Path

import pytest

AGENT = Path(__file__).resolve().parents[2] / "backend" / "xv6" / "gini_agent.py"

# what the kernel prints for each control character
BODIES = {
    0x04: b"BOARDN 14\nBSUB 0 user 700\nBSUB 10 disk 12\n",   # Ctrl-D -> gini_boarddump
    0x14: b"PROC 1 init sleep\nPROC 2 sh sleep\n",            # Ctrl-T -> gini_dump
}


@pytest.fixture(scope="module")
def ga():
    """Import the agent module without letting it start QEMU or the HTTP server."""
    if not AGENT.exists():
        pytest.skip("backend/xv6/gini_agent.py not present")
    spec = importlib.util.spec_from_file_location("gini_agent_under_test", AGENT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def make_link(ga, latency=0.01):
    """A SerialLink with no reader thread and a socket that answers asynchronously — which is the
    only thing that makes the race reproducible."""
    link = ga.SerialLink.__new__(ga.SerialLink)
    link.buf = bytearray()
    link.console = bytearray()
    link._total = 0
    link._console_total = 0
    link._console_base = 0
    link._cap = bytearray()
    link._in_dump = False
    link._last_dump = b""
    link._dump_seq = 0
    link._ever_framed = False              # mirrors __init__; the latch dump() reads
    link._lock = threading.Lock()

    class Sock:
        def sendall(self, ctrl):
            def reply():
                time.sleep(latency)
                link._ingest(bytes([ga.DUMP_START]) + BODIES[ctrl[0]] + bytes([ga.DUMP_END]))
            threading.Thread(target=reply, daemon=True).start()

    link._sock = Sock()
    return link


def _race(link, wait=0.35):
    got = {}

    def ask(name, ctrl):
        got[name] = link.dump(ctrl, wait=wait)

    ts = [threading.Thread(target=ask, args=("board", b"\x04")),
          threading.Thread(target=ask, args=("procs", b"\x14"))]
    t0 = time.time()
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=5)
    assert not any(t.is_alive() for t in ts), "dump() deadlocked"
    return got, time.time() - t0


def test_concurrent_dumps_never_cross(ga):
    """The bug, pinned. Repeated because a race that passes once proves nothing."""
    for _ in range(12):
        got, _ = _race(make_link(ga))
        assert got["board"].startswith("BOARDN"), f"/board got someone else's dump: {got['board']!r}"
        assert got["procs"].startswith("PROC"), f"/procs got someone else's dump: {got['procs']!r}"


def test_dump_returns_as_soon_as_the_reply_lands(ga):
    """Serialising the line would be ruinous with the old fixed 0.35 s sleep — the OS HUD alone
    asks for four dumps a poll, which would hold the wire for 1.4 s and starve the Machine Lab.
    The wait is a ceiling, not a cost."""
    link = make_link(ga, latency=0.01)
    t0 = time.time()
    out = link.dump(b"\x04", wait=2.0)
    dt = time.time() - t0
    assert out.startswith("BOARDN")
    assert dt < 0.25, f"waited {dt:.2f}s for a 10ms reply — the wait is not adaptive"


def test_timeout_still_bounds_a_silent_kernel(ga):
    """A kernel that never answers must not hang the request forever, and must not hold the lock
    forever either — every other reader is queued behind it."""
    link = make_link(ga)
    link._sock = type("Dead", (), {"sendall": lambda self, c: None})()
    t0 = time.time()
    out = link.dump(b"\x04", wait=0.15)
    dt = time.time() - t0
    assert out == ""                       # nothing framed, nothing raw
    assert 0.1 < dt < 0.6, f"timeout not honoured ({dt:.2f}s)"


def test_lock_is_released_between_dumps(ga):
    """Serialised, not stuck: a second dump after the first must still work."""
    link = make_link(ga)
    assert link.dump(b"\x04", wait=1.0).startswith("BOARDN")
    assert link.dump(b"\x14", wait=1.0).startswith("PROC")
    assert link.dump(b"\x04", wait=1.0).startswith("BOARDN")


# -- C1: a timeout must not hand back the console as if it were a dump --------- #
def _late(link, payload, delay=0.01):
    """A socket whose reply arrives DURING dump()'s wait — which is where the raw-byte window
    is measured from, so the bytes have to land after the send, not before it."""
    class Sock:
        def sendall(self, _ctrl):
            def reply():
                time.sleep(delay)
                link._ingest(payload)
            threading.Thread(target=reply, daemon=True).start()
    return Sock()


def test_a_timeout_on_a_framing_kernel_returns_nothing(ga):
    """The blinking process table, in one test.

    The raw-byte fallback exists for a kernel that predates the 0x1e/0x1f framing. Every kernel
    this course ships frames its dumps, so on a current image the fallback could only fire when
    the frame MISSED ITS DEADLINE — and what it then returned was the console: walker's lap
    lines, grind's output, whatever partial frame was in flight. parse_procdump found fewer PROC
    lines than there were processes and a row vanished for one poll.

    And it fired precisely when a student was doing what the lab asked, because a CPU-bound
    program is what delays the console interrupt in the first place.
    """
    link = make_link(ga)
    assert link.dump(b"\x14", wait=0.5), "one good round first"
    assert link._ever_framed, "this kernel frames its dumps, and the link now knows"

    console = b"walker: lap 3 walked in 2 ticks (expected ~3)\n"
    link._sock = _late(link, console)              # no frame this time, just program output
    out = link.dump(b"\x14", wait=0.2)
    assert out == "", "console text must never be returned as if it were kernel data"
    assert "walker" not in out


def test_a_pre_marker_kernel_still_gets_its_raw_window(ga):
    """The other half: the fallback is not deleted, it is made conditional. A kernel that has
    never framed anything is exactly the case it was written for."""
    link = make_link(ga)
    link._sock = _late(link, b"PROC 1 init sleep\nPROC 2 sh sleep\n")
    out = link.dump(b"\x14", wait=0.3)
    assert "PROC 1 init sleep" in out and not link._ever_framed


def test_console_text_around_a_frame_still_splits_correctly(ga):
    """C3 — a program's line split around a dump. Nothing is lost and the frame stays clean;
    this is expected behaviour on one hart, and it must keep working."""
    link = make_link(ga)
    link._ingest(b"walker: lap 3 wal")
    link._ingest(bytes([ga.DUMP_START]) + BODIES[0x14] + bytes([ga.DUMP_END]))
    link._ingest(b"ked in 2 ticks\n")
    assert link._last_dump == BODIES[0x14]
    console = bytes(link.console).decode()
    assert console == "walker: lap 3 walked in 2 ticks\n"
    assert "PROC" not in console, "dump bytes must never reach the console"
