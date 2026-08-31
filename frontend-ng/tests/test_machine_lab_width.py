"""The Process Scheduler panel has to fit on a screen — with everything still on it.

The launcher's hint carries the only in-app description of the stock programs, as one line of
prose. A QLabel in a HORIZONTAL layout hands its full single-line width to that layout as a
MINIMUM, and this one measures 2856 px — which set the whole panel's minimum to 3325 px and
dragged the dropdown, the argument box, the pid setters and the shadow bar off the screen with it.

Wrapping it and putting it on its own row fixes that without touching a word of it.

The second half of this file exists because of how it was fixed the FIRST time: the descriptions
were moved into dropdown tooltips, which is a defensible design and was not what was asked for —
from the outside it is text that vanished. So these tests assert the panel is narrow AND that
nothing has left it.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QLabel                 # noqa: E402

from gini.app import AppContext                                    # noqa: E402
from gini.domain.machine_state import MachineState                 # noqa: E402
from gini.ui.machine_lab import _LAUNCHABLE, MachineLab            # noqa: E402
from gini.ui.theme import ThemeManager                             # noqa: E402

#: A 13" laptop is 1440 logical pixels, and the panel opens in its own window beside the canvas.
#: Generous, and still a fifth of what the unwrapped hint demanded.
FITS_ON_A_SCREEN = 1400


@pytest.fixture
def lab(qtbot):
    """The panel as it is really built. `live` alone is not enough — the launcher and the pid
    setters only exist when the machine state says "real", so a panel built any other way does not
    contain the thing under test at all."""
    app = QApplication.instance() or QApplication([])
    ctx = AppContext()
    dev = ctx.add_device("xv6", x=0, y=0)
    state = MachineState(provider=None)
    state.mode = "real"
    w = MachineLab(None, ThemeManager(app, "Light"), dev, state=state, live=True)
    qtbot.addWidget(w)
    return w


def _hint(lab):
    for w in lab._sched_page.findChildren(QLabel):
        if w.text().startswith("spin = CPU loop"):
            return w
    return None


# ---- it fits ------------------------------------------------------------------- #
def test_the_scheduler_panel_fits_on_a_screen(lab):
    got = lab._sched_page.minimumSizeHint().width()
    assert got <= FITS_ON_A_SCREEN, f"the panel demands {got} px before anything can be resized"


def test_no_single_label_can_set_the_panels_width(lab):
    """The failure, stated as the rule that prevents it: any label long enough to matter must be
    able to fold. One that cannot hands its whole length to the layout."""
    for w in lab._sched_page.findChildren(QLabel):
        if w.sizeHint().width() > 600:
            assert w.wordWrap(), f"{w.text()[:48]!r} cannot wrap and is {w.sizeHint().width()} px"


# ---- and nothing left it -------------------------------------------------------- #
def test_the_hint_still_says_everything_it_said(lab):
    """Wrapped, not shortened, and not moved into a tooltip. This is the only place in the app
    that explains what these programs do."""
    h = _hint(lab)
    assert h is not None, "the launcher hint is gone"
    assert h.wordWrap() is True
    for name in _LAUNCHABLE:
        assert name in h.text(), f"{name} is offered in the menu but no longer described"
    for detail in ("second pass faults zero times",   # the whole point of toucher
                   "cache holds 30",                  # why sgrind 20 and sgrind 60 differ
                   "one page at a time",              # why walker is watchable
                   "Use ✕ in the table to kill one"):
        assert detail in h.text(), f"lost from the hint: {detail!r}"


def test_every_launcher_control_is_still_there(lab):
    for name in ("_prog_combo", "_prog_args", "_launch_msg"):
        assert hasattr(lab, name), f"{name} left the launcher"
    assert [lab._prog_combo.itemText(i) for i in range(lab._prog_combo.count())] == _LAUNCHABLE


def test_the_scheduling_and_shadow_controls_are_still_there(lab):
    """Priority, tickets and the shadow bar share the panel with the launcher, so a width fix
    that reached too far would take them out — and they are the point of the lab."""
    text = " ".join(w.text() for w in lab._sched_page.findChildren(QLabel))
    for label in ("Scheduling for pid", "priority", "tickets", "Shadow"):
        assert label in text, f"{label!r} is missing from the scheduler panel"
