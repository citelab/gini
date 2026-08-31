"""About GINI — the version question, answered from inside the app.

There was no way to tell which build was running without leaving gBuilder, which is the first thing
any bug report needs.

An earlier draft of this listed all three distributions and warned when gini-toolkit and gini-core
disagreed. That was machinery for a failure `pyproject.toml` already prevents — the toolkit
declares `gini-core>=<release>` as a floor, with a comment saying exactly why — so it asked a user
to check something pip cannot get wrong.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QLabel                 # noqa: E402

from gini.ui.about_dialog import HOME, TAGLINE, AboutDialog, where  # noqa: E402


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


def test_it_names_where_it_comes_from(about):
    """A student should know whose lab built the thing they are learning on, and "GINI" alone is
    a name nobody can look up."""
    assert "McGill" in _text(about)


def test_it_reports_one_version_not_a_dependency_audit(about):
    """gini-core is a managed dependency with a declared floor. Listing it invited a user to check
    something pip cannot resolve wrongly."""
    shown = _text(about)
    assert "gini-core" not in shown and "gini-teaching-center" not in shown


def test_it_says_where_the_package_is_loaded_from(about):
    """The other half of "which version": an editable checkout and an installed wheel look
    identical from the outside, and only one of them changes when you edit a file."""
    loc = where()
    assert loc and loc in _text(about)


def test_the_version_can_be_selected_and_pasted(about):
    """It exists to be copied into a bug report, not transcribed from a screen."""
    from PySide6.QtCore import Qt
    rows = [w for w in about.findChildren(QLabel) if w.text().startswith("version ")]
    assert rows
    assert all(w.textInteractionFlags() & Qt.TextSelectableByMouse for w in rows)


def test_it_opens_on_a_source_checkout_with_no_installed_metadata(about):
    """It runs while someone is working out why things are broken. It is the last thing that
    should throw."""
    assert HOME in _text(about)
    assert where()


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


def test_it_states_the_licence(about):
    """MIT is the intent, and the About box is where someone looks for it. No copyright line —
    nothing requires the notice in the UI; MIT's condition is that it ship with the software,
    which the LICENSE file does."""
    shown = _text(about)
    assert "MIT" in shown
    assert "Copyright" not in shown
