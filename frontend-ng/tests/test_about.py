"""About GINI — the version question, answered from inside the app.

There was no way to tell which build was running without leaving gBuilder, which is the first thing
any bug report needs. One number would not have been enough: `gini` is a namespace package shared
by THREE separately-installed distributions, and a toolkit newer than its core fails at import —
that is not hypothetical, it produced `ModuleNotFoundError: No module named 'gini.services'` in a
teacher's marking window.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QLabel                 # noqa: E402

from gini.ui.about_dialog import (                                 # noqa: E402
    DISTRIBUTIONS, TAGLINE, AboutDialog, mismatch, versions, where,
)


def _app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def about(qtbot):
    from gini.ui.theme import ThemeManager
    d = AboutDialog(None, ThemeManager(_app(), "Dark"))
    qtbot.addWidget(d)
    return d


def _text(d):
    return "\n".join(w.text() for w in d.findChildren(QLabel))


def test_it_says_what_gini_stands_for(about):
    """The first thing anyone asks about the name."""
    assert TAGLINE == "GINI Is Not Internet"
    assert TAGLINE in _text(about)


def test_it_reports_a_version(about):
    """The whole reason it exists."""
    from gini.version import gini_version
    assert gini_version() in _text(about)


def test_it_reports_every_distribution_separately(about):
    """One number would hide the failure that matters: they are installed separately and can
    disagree."""
    shown = _text(about)
    for dist in DISTRIBUTIONS:
        assert dist in shown


def test_a_missing_teaching_center_is_missing_not_broken():
    """It is legitimately absent on a student's machine."""
    v = dict.fromkeys(DISTRIBUTIONS, "6.4.0")
    v["gini-teaching-center"] = ""
    assert mismatch(v) == "", "an absent server is not a mismatch"


def test_a_toolkit_out_of_step_with_its_core_is_called_out():
    """They share the `gini` namespace, so this fails at IMPORT — in front of whoever is using it,
    which is how it was found the first time."""
    warn = mismatch({"gini-toolkit": "6.5.0", "gini-core": "6.4.0", "gini-teaching-center": ""})
    assert "gini-toolkit 6.5.0" in warn and "gini-core 6.4.0" in warn
    assert "Upgrade both" in warn


def test_a_lagging_teaching_center_is_not_called_out():
    """It is a separate server on a separate machine and may lag freely."""
    assert mismatch({"gini-toolkit": "6.4.0", "gini-core": "6.4.0",
                     "gini-teaching-center": "6.1.0"}) == ""


def test_it_says_where_the_package_is_loaded_from(about):
    """The other half of "which version": an editable checkout and an installed wheel look
    identical from the outside, and only one of them changes when you edit a file."""
    loc = where()
    assert loc and loc in _text(about)


def test_the_versions_can_be_selected_and_pasted(about):
    """It exists to be copied into a bug report."""
    from PySide6.QtCore import Qt
    rows = [w for w in about.findChildren(QLabel) if w.text().startswith("gini-")]
    assert rows
    assert all(w.textInteractionFlags() & Qt.TextSelectableByMouse for w in rows)


def test_versions_never_raises_on_a_broken_install():
    """It runs while someone is trying to work out why things are broken. It is the last thing
    that should throw."""
    v = versions()
    assert set(v) == set(DISTRIBUTIONS)
    assert all(isinstance(x, str) for x in v.values())


def test_the_menu_item_is_wired(qtbot):
    """It lives in File beside Settings — NoRole, or macOS hoists it into the application menu,
    away from where someone told to "look in File" will look."""
    from gini.ui.main_window import MainWindow
    w = MainWindow(_app())
    qtbot.addWidget(w)
    acts = [a for a in w.findChildren(type(w.menuBar().actions()[0]))
            if "About" in a.text()]
    assert acts, "no About item in the menus"
    from PySide6.QtGui import QAction
    assert acts[0].menuRole() == QAction.MenuRole.NoRole
    assert hasattr(w, "_open_about")
    w.close()
