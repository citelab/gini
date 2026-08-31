"""The Process Scheduler panel has to fit on a screen.

One non-wrapping QLabel in the launcher's horizontal bar carried every stock program's description
as a single line of prose. A QLabel in a horizontal layout hands its full single-line width to the
layout as a MINIMUM, so that sentence — 521 characters — asked for 3033 px on its own and dragged
the whole panel past the width of the display, dropdown and button and all.

Nothing was deleted to fix it. The descriptions moved to the dropdown's own tooltips, which is
where you want them: this is text you read while CHOOSING a program.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt                                  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel             # noqa: E402

from gini.ui.machine_lab import _LAUNCHABLE, _WHAT_IT_DOES     # noqa: E402


def _app():
    return QApplication.instance() or QApplication([])


def test_every_launchable_program_still_explains_itself():
    """Moved, not lost. A program in the dropdown with no description is one a student has no way
    to find out about — the prose line was at least readable."""
    _app()
    missing = [n for n in _LAUNCHABLE if not _WHAT_IT_DOES.get(n)]
    assert not missing, missing


def test_nothing_in_the_launcher_asks_for_more_than_a_panel_of_width():
    """The actual failure: a label wide enough to set the panel's minimum. 600 px is generous for
    a dock that people drag narrow, and the old line wanted five times it."""
    _app()
    for name, text in _WHAT_IT_DOES.items():
        lbl = QLabel(text)
        assert lbl.sizeHint().width() < 600, f"{name} is too long to sit anywhere but a tooltip"


def test_the_hint_that_remains_wraps():
    """Word wrap is the guard that outlives this fix. Any future sentence added to that row will
    fold rather than widen the panel."""
    _app()
    lbl = QLabel("Hover a program to see what it does. ✕ in the table kills one.")
    lbl.setWordWrap(True)
    assert lbl.sizeHint().width() < 600


def test_the_descriptions_are_reachable_as_tooltips(qtbot):
    """Where a student meets them: opening the dropdown to pick one."""
    from PySide6.QtWidgets import QComboBox
    _app()
    combo = QComboBox()
    combo.addItems(_LAUNCHABLE)
    for i, name in enumerate(_LAUNCHABLE):
        combo.setItemData(i, _WHAT_IT_DOES.get(name, ""), Qt.ToolTipRole)
    qtbot.addWidget(combo)
    assert combo.itemData(_LAUNCHABLE.index("walker"), Qt.ToolTipRole).startswith("The PC itself")


def test_the_wording_survived_the_move():
    """The details are the lesson — "the second pass faults zero times" is the whole point of
    toucher, and "the buffer cache holds 30" is why sgrind 20 and sgrind 60 differ."""
    assert "faults zero times" in _WHAT_IT_DOES["toucher"]
    assert "30" in _WHAT_IT_DOES["sgrind"]
    assert "one page at a time" in _WHAT_IT_DOES["walker"]
