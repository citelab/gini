"""In-container agent console logic (backend/xv6/gini_agent.py): the single serial stream is
split at ingest into a CLEAN human console (Terminal) and captured dump blocks (Machine Lab),
and Clear moves the console baseline. Pure-Python, no container."""
import importlib.util
from pathlib import Path

import pytest

AGENT = Path(__file__).resolve().parents[2] / "backend" / "xv6" / "gini_agent.py"


@pytest.fixture(scope="module")
def ga():
    if not AGENT.exists():
        pytest.skip("backend/xv6/gini_agent.py not present")
    spec = importlib.util.spec_from_file_location("gini_agent", AGENT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def _close_links():
    """Close every SerialLink a test builds.

    `SerialLink.__init__` starts a reader thread, and with nothing listening it retried the
    connection for the rest of the session — one per test. Harmless-looking, and part of what was
    crashing the suite: leaked threads and Qt teardown do not mix.
    """
    made = []
    yield made
    for sl in made:
        sl.close()


def _serial(ga, keep=None):
    sl = ga.SerialLink(("127.0.0.1", 65533))         # nothing to connect to; we feed _ingest
    if keep is not None:
        keep.append(sl)
    return sl


def test_dump_block_kept_out_of_console(ga, _close_links):
    sl = _serial(ga, _close_links)
    sl._ingest(b"$ ls\nREADME\n")
    sl._ingest(bytes([0x1e]) + b"1 run spin\nSCHED policy 0 quantum 3\n" + bytes([0x1f]))
    sl._ingest(b"$ ")
    assert sl.tail() == "$ ls\nREADME\n$ "           # the bracketed dump is hidden


def test_native_procdump_survives(ga, _close_links):
    # xv6's own Ctrl-P procdump is NOT bracketed, so it shows in the console
    sl = _serial(ga, _close_links)
    sl._ingest(b"1 sleep init\n2 sleep sh\n")
    assert sl.tail() == "1 sleep init\n2 sleep sh\n"


def test_captured_dump_is_available_for_the_lab(ga, _close_links):
    sl = _serial(ga, _close_links)
    sl._ingest(bytes([0x1e]) + b"1 run spin\n" + bytes([0x1f]))
    assert sl._last_dump.decode() == "1 run spin\n"


def test_stream_is_append_only(ga, _close_links):
    sl = _serial(ga, _close_links)
    sl._ingest(b"$ ls\n")
    t0, n0 = sl.stream(0)
    assert t0 == "$ ls\n"
    sl._ingest(b"README\n")
    t1, n1 = sl.stream(n0)
    assert t1 == "README\n" and n1 > n0              # only the NEW bytes


def test_clear_console_moves_baseline(ga, _close_links):
    sl = _serial(ga, _close_links)
    sl._ingest(b"$ hello\nworld\n")
    sl.clear_console()
    sl._ingest(b"after\n")
    assert sl.tail() == "after\n"                    # only bytes since Clear
