"""The A-Labs run on the real kernel, and a student is told when their files have not arrived.

What students hit, on A-Lab 03: they opened the Machine Lab BEFORE pressing Run. With nothing
running it opened in Demo, and a kernel starting never moved it ("never force a demo user to
Real"), so it stayed on the stand-in for the whole session. Arming an A-Lab from it was gated on
`live`, so it copied no files and said nothing, and Load stayed greyed out with "Start the
machine" over a machine that was running. Only some students saw it, because only some opened
the Lab first — which is why it looked random.

So: a Demo nobody chose turns Real when the kernel attaches; the A-Lab windows read the REAL
plane whatever the toggle says; arming always asks to link; and after a link GINI checks which
files actually reached the student's folder and says which are missing and why.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from gini.domain import lab_spec as L
from gini.domain.machine_state import MachineState
from gini.domain.xv6 import DemoScheduler
from gini.services import xv6_lab as X

QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path / "gini"))
    monkeypatch.delenv("GINI_LAB", raising=False)
    return tmp_path / "gini"


class _Dev:
    id = "d1"
    type_key = "xv6"
    name = "M1"
    properties = {"Timeslice": "1"}


def _theme(app):
    from gini.ui.theme import ThemeManager
    return ThemeManager(app)


def _kernel():
    """Stands in for the live bridge: all the state needs from it is a snapshot()."""
    return DemoScheduler(timeslice=1)


def _demo_state():
    """What a Machine Lab opened before Run is given: Demo, because nothing was running."""
    return MachineState(DemoScheduler(timeslice=1), device_id="d1", mode="demo")


# --------------------------------------------------------------------------- #
# the state
# --------------------------------------------------------------------------- #

def test_a_default_demo_turns_real_when_the_kernel_attaches():
    ms = _demo_state()
    k = _kernel()
    assert ms.attach_real(k) is True
    assert ms.mode == "real" and ms.provider is k


def test_a_demo_the_student_chose_stays_demo():
    ms = _demo_state()
    ms.set_mode("demo")                          # pressed Demo themselves
    assert ms.attach_real(_kernel()) is False
    assert ms.mode == "demo"


def test_the_real_plane_is_reachable_whatever_the_toggle_shows():
    ms = _demo_state()
    assert ms.real_plane() is None               # nothing running yet
    ms.set_mode("demo")
    k = _kernel()
    ms.attach_real(k)
    assert ms.mode == "demo" and ms.real_plane()[0] is k


# --------------------------------------------------------------------------- #
# the windows
# --------------------------------------------------------------------------- #

def test_a_lab_opened_before_run_goes_live_and_so_do_its_a_lab_windows(home, app):
    from gini.ui.machine_lab import MachineLab
    spec = L.get("a-lab-03")
    ms = _demo_state()
    lab = MachineLab(None, _theme(app), _Dev(), state=ms)
    assert lab.live is False
    lab._open_user_code()
    hub = lab._usercode
    hub._choose(spec)
    panel = hub._panel
    assert not panel._load_btn.isEnabled()       # "Start the machine to build your code."

    ms.attach_real(_kernel())                    # Run: the main window attaches the bridge...
    lab.kernel_attached()                        # ...and tells the open Lab
    assert lab.live is True and lab._mode_btns["real"].isChecked()
    assert hub.live is True
    assert panel.live is True and panel._load_btn.isEnabled()
    assert all(b.isEnabled() for b in panel._check_btns)
    panel.close(); hub.close(); lab.close()


def test_the_a_lab_windows_use_the_real_kernel_even_when_the_lab_shows_demo(home, app):
    from gini.ui.machine_lab import MachineLab
    ms = _demo_state()
    ms.set_mode("demo")
    k = _kernel()
    ms.attach_real(k)
    lab = MachineLab(None, _theme(app), _Dev(), state=ms)
    assert lab.live is False                     # the student's own Demo is kept for the Lab...
    lab._open_user_code()
    assert lab._usercode.live is True and lab._usercode.provider is k   # ...not for the A-Labs
    lab._usercode.close(); lab.close()


def test_arming_asks_to_link_even_when_the_window_thinks_nothing_is_running(home, app):
    """The window's `live` is not the judge of whether a machine is running — that is what
    dropped the files. The owner of the orchestrator is asked every time and decides."""
    from gini.ui.user_code_lab import UserCodeLab
    asked = []
    hub = UserCodeLab(None, _theme(app), device=_Dev(), live=False,
                      on_relink=lambda n, s: asked.append((n, s.id)))
    hub._choose(L.get("a-lab-03"))
    assert asked == [("M1", "a-lab-03")]
    hub.close()
    if hub._panel is not None:
        hub._panel.close()


# --------------------------------------------------------------------------- #
# did the files arrive?
# --------------------------------------------------------------------------- #

def _fill(spec, names):
    d = X.lab_dir("M1", spec.id)
    d.mkdir(parents=True, exist_ok=True)
    for n in names:
        (d / n).write_text("x", encoding="utf-8")


def test_a_complete_folder_reports_nothing(home):
    spec = L.get("a-lab-03")
    _fill(spec, [f.name for f in spec.files])
    assert X.link_report(spec, "M1", "gini-lab-linked\n") == ""


def test_a_file_the_image_lacks_is_blamed_on_the_image(home):
    spec = L.get("a-lab-03")
    _fill(spec, ["kalloc.c", "vm.c"])
    out = "".join(f"{X.MISSING}{n}\n" for n in ("buddy.h", "buddytest.c", "megatest.c"))
    msg = X.link_report(spec, "M1", out + "gini-lab-linked\n")
    assert "buddy.h, buddytest.c, megatest.c" in msg and "image" in msg
    assert "sharing" not in msg


def test_a_file_the_machine_has_but_this_computer_does_not_is_blamed_on_file_sharing(home):
    """The container wrote it, the student's folder does not have it: the machine is writing
    somewhere this computer cannot see. More Runs will not fix that, so it must not say so."""
    spec = L.get("a-lab-03")
    _fill(spec, ["kalloc.c"])
    msg = X.link_report(spec, "M1", "gini-lab-linked\n")
    assert "buddy.h" in msg and "file-sharing" in msg
    assert str(X.lab_dir("M1", spec.id)) in msg


def test_the_link_script_says_which_files_it_could_not_provide():
    spec = L.get("a-lab-03")
    script = X.link_script(spec)
    for f in spec.files:
        assert f"echo {X.MISSING}{f.name}" in script or f"'{X.MISSING}{f.name}'" in script
    assert script.rstrip().endswith("echo gini-lab-linked")


def test_the_panel_names_the_missing_files_and_what_brings_them(home, app):
    from gini.ui.user_code import UserCode, missing_note
    spec = L.get("a-lab-03")
    X.arm("M1", spec.id)
    X.seed_host_files(spec, "M1")               # kalloc.c only: the rest come from the machine
    w = UserCode(None, _theme(app), device=_Dev(), provider=None, spec=spec, live=False)
    assert w._missing.isVisibleTo(w)
    assert w._missing.text() == missing_note(
        ["buddy.h", "vm.c", "buddytest.c", "megatest.c"], False)
    assert "press Run" in w._missing.text()
    _fill(spec, ["buddy.h", "vm.c", "buddytest.c", "megatest.c"])
    w.refresh()
    assert not w._missing.isVisibleTo(w)
    w.close()


def test_the_note_changes_once_the_machine_is_running():
    from gini.ui.user_code import missing_note
    assert missing_note(["vm.c"], True).startswith("Not in your folder: vm.c. Stop and Run again")
    assert missing_note([], True) == ""


# --------------------------------------------------------------------------- #
# arming with the machine stopped
# --------------------------------------------------------------------------- #

def test_arming_a_stopped_machine_says_to_press_run_and_seeds_what_it_can(home, app):
    from PySide6.QtWidgets import QApplication

    from gini.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    w = MainWindow(app)
    said = []
    w.ctx.log = lambda msg, level="info": said.append((level, msg))
    w._running = False
    spec = L.get("a-lab-03")
    w._relink_xv6_lab("M1", spec)
    assert (X.lab_dir("M1", spec.id) / "kalloc.c").is_file()
    assert any("Press Run" in m for _lvl, m in said)
    w.close()
