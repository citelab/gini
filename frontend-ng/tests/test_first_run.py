"""The first-run panel, and what gets to interrupt a launch.

The rule this file defends: **setup comes before the tour.** They used to be two unconditional
timers 250ms apart — a modal `CueCards(...).exec()` at 450ms and a non-modal setup panel at 700ms
— so on the launch that mattered most, a brand-new install, "here are all the features" opened on
top of the one action that has to happen before any of those features work, and the panel
underneath it could not even be clicked. Nothing was broken; it was simply never seen.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from gini.__main__ import launch_steps
from gini.services import bootstrap
from gini.ui.first_run import FirstRunDialog, offer


def _app():
    return QApplication.instance() or QApplication([])


def _plan(state=bootstrap.PULL, **kw):
    p = {"state": state, "why": "because", "os": "macos", "arch": "amd64",
         "image_tag": "6.1.1", "refs": [], "runtime_plan": {}, "runtime_state": "ok"}
    p.update(kw)
    return p


# -- who interrupts the launch ------------------------------------------------- #
def test_a_pending_setup_suppresses_the_tour():
    """THE fix. A fresh install and a version update both land here."""
    for state in (bootstrap.PULL, bootstrap.UPDATE, bootstrap.BUILD, bootstrap.NEEDS_RUNTIME):
        steps = launch_steps(_plan(state), [])
        assert steps["setup"] is True and steps["tour"] is False, state


def test_the_tour_still_runs_when_there_is_nothing_to_set_up():
    """The other half of the pair — this must not become "the tour never opens again"."""
    steps = launch_steps(None, [])
    assert steps["tour"] is True and steps["setup"] is False


def test_demo_and_selftest_launches_never_show_the_setup_panel():
    for arg in ("--demo", "--selftest"):
        assert launch_steps(_plan(), [arg])["setup"] is False


# -- the panel's way into the tour --------------------------------------------- #
def test_the_panel_offers_the_tour_it_no_longer_lets_interrupt():
    """Suppressing the tour must not remove it: the panel is now the way in."""
    _app()
    seen = []
    dlg = FirstRunDialog(_plan(), on_tour=lambda: seen.append(1))
    assert dlg.tour.isVisibleTo(dlg)
    dlg.tour.click()
    assert seen == [1]


def test_without_a_tour_hook_the_button_stays_out_of_the_way():
    _app()
    dlg = FirstRunDialog(_plan(), on_tour=None)
    assert not dlg.tour.isVisibleTo(dlg)


def test_once_the_images_are_in_the_tour_becomes_the_next_step():
    """"Get them" has nothing left to do, and the tour stops being an interruption."""
    _app()
    dlg = FirstRunDialog(_plan(), on_tour=lambda: None)
    dlg._on_done({"ok": True, "done": ["a"], "failed": [], "message": "4 images ready."})
    assert not dlg.go.isVisibleTo(dlg)
    assert dlg.tour.isDefault()


def test_a_failed_setup_offers_a_retry_rather_than_the_tour():
    _app()
    dlg = FirstRunDialog(_plan(), on_tour=lambda: None)
    dlg._on_done({"ok": False, "done": [], "failed": ["x"], "message": "none arrived"})
    assert dlg.go.isVisibleTo(dlg) and dlg.go.isEnabled() and dlg.go.text() == "Try again"


def test_offer_returns_nothing_when_the_machine_is_ready():
    _app()
    assert offer(_plan(bootstrap.READY)) is None
    assert offer(None) is None


# --------------------------------------------------------------------------- #
# the bar actually moves
# --------------------------------------------------------------------------- #
def test_the_bar_is_determinate(qtbot):
    """It was `setRange(0, 0)` — a barber's pole that says only "something is happening", for a
    wait that can run to minutes."""
    p = FirstRunDialog(_plan())
    qtbot.addWidget(p)
    assert p.bar.maximum() > 0


def test_progress_moves_the_bar_and_names_the_image(qtbot):
    """The complaint had two halves: no bar, and the image name buried in the console. Both are
    answered in the same place, next to each other."""
    p = FirstRunDialog(_plan())
    qtbot.addWidget(p)
    p._on_progress(0.5, "Downloading gini-xv6:6.5.2…   layer 3 of 7")
    assert p.bar.value() == p.bar.maximum() // 2
    assert "gini-xv6:6.5.2" in p.detail.text()


def test_a_fraction_outside_the_range_cannot_break_the_bar(qtbot):
    p = FirstRunDialog(_plan())
    qtbot.addWidget(p)
    for f in (-1.0, 0.0, 1.0, 2.5, float("inf")):
        p._on_progress(f, "")
        assert p.bar.minimum() <= p.bar.value() <= p.bar.maximum()
