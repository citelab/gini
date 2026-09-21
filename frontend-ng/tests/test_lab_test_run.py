"""Capturing what a student's program PRINTED, and attaching it to the run that printed it.

For an A-Lab the output is the deliverable: the assignment is a program that reports system
information. The chain recorded that `sysinfotest` was launched and never what it said, so a
marker received everything except the answer.

The capture is a BRACKET, and the shape of the feature follows from what a bracket needs. The
console is one long byte stream, so output belongs to a program only when both ends are known:
the cursor is read before GINI types the command, which makes the start exact, and the shell's
returning prompt closes it. Neither end exists for a program the student typed themselves or one
sent to the background — which is why a lab's test is prescribed, launched from the User Code
Lab, and run in the foreground.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from gini.domain import lab_spec as L
from gini.domain import proof_events as ev
from gini.domain.xv6 import run_finished, run_output

AGENT = Path(__file__).resolve().parents[2] / "backend" / "xv6" / "gini_agent.py"


@pytest.fixture(scope="module")
def ga():
    if not AGENT.exists():
        pytest.skip("backend/xv6/gini_agent.py not present")
    spec = importlib.util.spec_from_file_location("gini_agent", AGENT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- #
# finding the two ends of a run
# --------------------------------------------------------------------------- #

def test_the_prompt_closes_the_bracket():
    assert run_finished("sysinfotest\r\nfree memory: 4096\r\n$ ") is True
    assert run_finished("sysinfotest\r\nfree memory: 4096") is False, "still printing"
    assert run_finished("") is False


def test_the_shells_echo_is_not_the_programs_output():
    """GINI typed `sysinfotest`. A marker reading that as the first line of the program's own
    output would be reading our keystrokes back."""
    delta = "sysinfotest\r\nfree memory: 4096 bytes\r\nprocesses: 3\r\n$ "
    assert run_output(delta, "sysinfotest") == ["free memory: 4096 bytes", "processes: 3"]


def test_the_trailing_prompt_is_not_output_either():
    assert run_output("prog\n\nhello\n\n$ ", "prog") == ["hello"]


def test_a_program_that_printed_nothing_is_an_empty_list_not_a_prompt():
    assert run_output("sysinfotest\r\n$ ", "sysinfotest") == []


def test_output_survives_a_command_line_that_does_not_match():
    """If the echo came back mangled, keep the lines rather than silently dropping the first."""
    assert run_output("free memory: 4096\n$ ", "sysinfotest") == ["free memory: 4096"]


def test_blank_lines_inside_the_output_are_kept():
    """Only the leading and trailing padding goes. A blank line a student printed is theirs."""
    assert run_output("p\na\n\nb\n$ ", "p") == ["a", "", "b"]


# --------------------------------------------------------------------------- #
# the agent can run a program it has never heard of
# --------------------------------------------------------------------------- #

def test_the_program_name_is_filtered_now_that_it_is_not_from_a_fixed_list(ga):
    """`PROGRAMS` used to BE the injection guard — /run writes into a real shell and only names
    on that list reached it. A lab naming its own program removes that guarantee."""
    assert ga._safe_prog("sysinfotest") == "sysinfotest"
    assert ga._safe_prog("  sysinfotest \n") == "sysinfotest"
    assert ga._safe_prog("sysinfotest; rm -rf /") == "sysinfotestrmrf"
    assert ga._safe_prog("a`b`c") == "abc"
    assert ga._safe_prog("$(whoami)") == "whoami"
    assert ga._safe_prog("") == ""
    assert len(ga._safe_prog("x" * 200)) == 32


def test_a_students_own_program_is_runnable_once_it_is_built(ga, tmp_path, monkeypatch):
    """The image's list is fixed when the image is built; an A-Lab's test program is compiled by
    the student long afterwards. The build leaves `user/_<name>`, which is exactly what mkfs puts
    into fs.img — so the filesystem is the honest answer, not a list."""
    monkeypatch.setattr(ga, "XV6_DIR", str(tmp_path))
    (tmp_path / "user").mkdir()
    assert ga._runnable("sysinfotest") is False, "not compiled yet"
    (tmp_path / "user" / "_sysinfotest").write_bytes(b"\x7fELF")
    assert ga._runnable("sysinfotest") is True


def test_the_built_in_workloads_still_run_without_touching_the_disk(ga, tmp_path, monkeypatch):
    monkeypatch.setattr(ga, "XV6_DIR", str(tmp_path))
    for prog in ("spin", "alloc", "forktest"):
        assert ga._runnable(prog) is True


def test_a_program_that_is_neither_is_still_refused(ga, tmp_path, monkeypatch):
    monkeypatch.setattr(ga, "XV6_DIR", str(tmp_path))
    assert ga._runnable("definitely_not_a_program") is False


# --------------------------------------------------------------------------- #
# the bracket, against a fake agent
# --------------------------------------------------------------------------- #

class _FakeAgent:
    """A console that dribbles a program's output out over several polls."""

    def __init__(self, chunks, ok=True):
        self.chunks, self.ok = list(chunks), ok
        self.posted, self.buf = [], "earlier session noise\n$ "

    def post(self, q):
        self.posted.append(q)
        if self.chunks and self.ok:
            self.buf += self.chunks.pop(0)
        return '{"ok": %s}' % ("true" if self.ok else "false")

    def get_json(self, q):
        since = int(q.split("since=")[1])
        if self.chunks:
            self.buf += self.chunks.pop(0)
        return {"text": self.buf[since:], "next": len(self.buf)}


def _bridge(agent):
    from gini.runtime.xv6_bridge import Xv6Bridge
    b = Xv6Bridge.__new__(Xv6Bridge)
    b.agent = agent
    b.last_run_error = ""
    return b


def test_output_printed_before_the_run_is_not_attributed_to_it():
    """The cursor is read BEFORE the command is typed, which is what makes the start exact."""
    a = _FakeAgent(["sysinfotest\n", "free memory: 4096\n", "$ "])
    ok, raw = _bridge(a).run_and_capture("sysinfotest", settle=0)
    assert ok is True
    assert "free memory: 4096" in raw
    assert "earlier session noise" not in raw


def test_the_test_is_run_in_the_foreground():
    """Backgrounded, the shell hands the prompt straight back and there is no end to find."""
    a = _FakeAgent(["sysinfotest\n", "done\n", "$ "])
    _bridge(a).run_and_capture("sysinfotest", settle=0)
    assert any("fg=1" in q for q in a.posted), a.posted


def test_a_refused_launch_captures_nothing_and_says_so():
    a = _FakeAgent([], ok=False)
    b = _bridge(a)
    ok, raw = b.run_and_capture("sysinfotest", settle=0)
    assert ok is False and raw == ""
    assert b.last_run_error


def test_a_test_that_overruns_keeps_what_it_printed():
    """A missing newline should not cost a marker the output that did arrive."""
    a = _FakeAgent(["sysinfotest\n", "partial output\n"])     # no prompt, ever
    ok, raw = _bridge(a).run_and_capture("sysinfotest", timeout=1.0, settle=0)
    assert ok is True
    assert "partial output" in raw


# --------------------------------------------------------------------------- #
# cleaning and capping — console_tap's, imported rather than reimplemented
#
# This path first shipped with its own half of ConsoleTap: no escape stripping and no line cap
# at all. The escapes then broke the echo-stripping the bracket exists to get right, so the
# attribution failed in exactly the case the feature was built for.
# --------------------------------------------------------------------------- #

def _prov(raw, ok=True):
    class _P:
        last_run_error = ""

        def run_and_capture(self, prog, args=""):
            return ok, raw
    return _P()


def test_escapes_are_stripped_by_console_taps_cleaner_not_a_second_one():
    from gini.services import xv6_lab as X
    ok, out = X.run_test(_prov("sysinfotest\r\n\x1b[2Kfree memory: 4096\x07\r\n$ "),
                         "sysinfotest")
    assert ok is True
    assert out == ["free memory: 4096"]


def test_an_escape_on_the_echoed_line_no_longer_defeats_the_attribution():
    """The concrete failure: with escapes left in, the echo did not match the command, so GINI's
    own keystrokes were recorded as the first line of the student's output."""
    from gini.services import xv6_lab as X
    _ok, out = X.run_test(_prov("\x1b[Ksysinfotest\r\nhello\r\n$ "), "sysinfotest")
    assert out == ["hello"], out


def test_output_is_capped_and_says_how_much_was_dropped():
    """Unbounded `out` went straight into the proof chain. ConsoleTap's caps and its wording."""
    from gini.services.console_tap import MAX_LINES
    from gini.services import xv6_lab as X
    body = "".join(f"line {i}\r\n" for i in range(40))
    _ok, out = X.run_test(_prov(f"t\r\n{body}$ "), "t")
    assert len(out) == MAX_LINES + 1
    assert out[-1] == f"… {40 - MAX_LINES} more line(s)"


def test_a_very_long_line_is_clipped_to_console_taps_width():
    from gini.services.console_tap import MAX_LINE
    from gini.services import xv6_lab as X
    _ok, out = X.run_test(_prov("t\r\n" + "x" * 900 + "\r\n$ "), "t")
    assert len(out[0]) == MAX_LINE


def test_a_refused_run_yields_nothing_to_record():
    from gini.services import xv6_lab as X
    assert X.run_test(_prov("", ok=False), "t") == (False, [])
    assert X.run_test(None, "t") == (False, [])
    assert X.run_test(_prov("x"), "") == (False, [])


def test_an_agent_without_the_runner_is_refused_not_crashed():
    """An image built before the test runner has no `run_and_capture` on its provider."""
    from gini.services import xv6_lab as X
    assert X.run_test(object(), "t") == (False, [])


# --------------------------------------------------------------------------- #
# what reaches the chain
# --------------------------------------------------------------------------- #

def test_the_entry_carries_the_output_and_says_it_was_the_test():
    kind, d = ev.spawn("M1", "sysinfotest", out=["free memory: 4096"], test=True)
    assert kind == ev.SPAWN
    assert d["out"] == ["free memory: 4096"]
    assert d["test"] is True


def test_an_ordinary_workload_launch_is_unchanged():
    """`out` and `test` are absent, not empty — an old reader sees exactly what it always saw."""
    _kind, d = ev.spawn("M1", "spin")
    assert "out" not in d and "test" not in d


def test_a_long_line_is_truncated_not_dropped():
    _kind, d = ev.spawn("M1", "t", out=["x" * 500], test=True)
    assert 0 < len(d["out"][0]) <= 210


def test_the_assignment_names_its_own_test_program():
    spec = L.get("syscall-sysinfo")
    assert spec.test_prog == "sysinfotest"
    named = L.from_yaml("id: x\ntitle: X\ntest_program: mytest\n"
                        "files: [{name: a.c, tree: user/a.c, uprog: other}]\n")
    assert named.test_prog == "mytest", "an explicit name beats the first uprog"
    none = L.from_yaml("id: x\ntitle: X\nfiles: [{name: a.h, tree: kernel/a.h}]\n")
    assert none.test_prog == ""


def test_the_panel_records_the_run_with_its_output(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    from gini.services import xv6_lab as X
    from gini.ui.theme import ThemeManager
    from gini.ui.user_code import UserCode
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path))
    app = QApplication.instance() or QApplication([])
    spec = L.get("syscall-sysinfo")
    X.arm("M1", spec.id)
    X.seed_host_files(spec, "M1")

    said = []

    class _Rec:
        def note_spawn(self, *a):
            said.append(a)

    class _Dev:
        name, type_key, properties = "M1", "xv6", {}

    class _Prov:
        last_run_error = ""

        def run_and_capture(self, prog, args="", **kw):
            return True, ["free memory: 4096 bytes"]

    w = UserCode(None, ThemeManager(app), device=_Dev(), provider=_Prov(), spec=spec,
                 live=True, recorder=_Rec())
    assert "sysinfotest" in w._test_btn.text()
    w._on_test_result(True, ["free memory: 4096 bytes"])
    assert said == [("M1", "sysinfotest", "launch", None, ["free memory: 4096 bytes"], True)]
    assert "free memory: 4096 bytes" in w._log.toPlainText()


def test_a_test_that_would_not_start_is_recorded_too(tmp_path, monkeypatch):
    """"It never ran" is the student's evidence about their own code, and a chain that keeps
    only the successes cannot tell that from "never tried"."""
    from PySide6.QtWidgets import QApplication

    from gini.services import xv6_lab as X
    from gini.ui.theme import ThemeManager
    from gini.ui.user_code import UserCode
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path))
    app = QApplication.instance() or QApplication([])
    spec = L.get("syscall-sysinfo")
    X.arm("M1", spec.id)
    X.seed_host_files(spec, "M1")
    said = []

    class _Rec:
        def note_spawn(self, *a):
            said.append(a)

    class _Dev:
        name, type_key, properties = "M1", "xv6", {}

    class _Prov:
        last_run_error = "this kernel has no program called 'sysinfotest'"

    w = UserCode(None, ThemeManager(app), device=_Dev(), provider=_Prov(), spec=spec,
                 live=True, recorder=_Rec())
    w._on_test_result(False, [])
    assert len(said) == 1 and said[0][5] is True
    assert "no program called" in w._log.toPlainText()
