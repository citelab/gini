"""A window's theme is fixed for its lifetime; only the main window is live-themed.

The Labs bake their colours in as they build — 151 `setStyleSheet` calls across seven files read
`theme.theme` at construction and never look again. Switching the theme underneath an open Lab
repaints the application stylesheet but not those baked colours, so the window ends up half light
and half dark: a dark dialog background with light-themed rows inside it.

Rather than rebuild open Labs (they blink and lose their page) or make 151 call sites re-runnable
(a large change to code that works), the theme menu refuses while another window is open and says
which. Labs already open with the CURRENT theme, so closing and reopening gets you there.

The canvas and the OS HUD are deliberately NOT in scope: they read the theme inside paintEvent,
so they follow a switch for free.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets")


class _Dev:
    type_key = "xv6"
    name = "M1"
    id = "m1"
    properties = {"Timeslice": "1"}


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(scope="module")
def win(app):
    from gini.ui.main_window import MainWindow
    w = MainWindow(app)
    w.show()
    return w


def _lab(win):
    from gini.ui.machine_lab import MachineLab
    lab = MachineLab(win, win.theme, _Dev())
    lab.show()
    QtWidgets.QApplication.instance().processEvents()
    return lab


def test_a_theme_switch_works_with_nothing_else_open(win):
    win._pick_theme("Green")
    assert win.theme.theme.name.lower() == "green"


def test_an_open_lab_blocks_the_switch(win):
    """The bug this rule exists for: the switch used to go through and leave the Lab half-themed."""
    win._pick_theme("Green")
    lab = _lab(win)
    try:
        assert win._open_windows() == ["Machine Lab — M1"]
        win._pick_theme("Dark")
        assert win.theme.theme.name.lower() == "green", "the theme changed under an open Lab"
    finally:
        lab.close()
        QtWidgets.QApplication.instance().processEvents()


def test_closing_the_lab_releases_the_block(win):
    lab = _lab(win)
    lab.close()
    QtWidgets.QApplication.instance().processEvents()
    assert win._open_windows() == []
    win._pick_theme("Dark")
    assert win.theme.theme.name.lower() == "dark"


def test_a_lab_opened_now_uses_the_current_theme(win):
    """The other half of the rule. Blocking switches is only acceptable because opening is right."""
    for name in ("Green", "Dark"):
        win._pick_theme(name)
        lab = _lab(win)
        try:
            assert win.theme.theme.bg in lab.styleSheet(), \
                f"a Lab opened under {name} did not use its background"
        finally:
            lab.close()
            QtWidgets.QApplication.instance().processEvents()


def test_the_refusal_says_which_window_to_close(win):
    """A refusal a student cannot act on is worse than no refusal."""
    said = []
    win.ctx.bus.log.connect(lambda lvl, msg: said.append((lvl, msg)))
    lab = _lab(win)
    try:
        win._pick_theme("Green")
        assert said, "the refusal was silent"
        lvl, msg = said[-1]
        assert lvl == "error"
        assert "Machine Lab — M1" in msg and "close" in msg.lower()
    finally:
        lab.close()
        QtWidgets.QApplication.instance().processEvents()


def test_the_menu_checkmark_follows_the_ACTIVE_theme_not_the_click(win):
    """A refused click must not leave the menu claiming a theme that is not in force."""
    win._pick_theme("Dark")
    lab = _lab(win)
    try:
        win._pick_theme("Green")                    # refused
        acts = win._theme_actions
        checked = [n for n, a in acts.items() if a.isChecked()]
        assert checked == ["Dark"], f"menu shows {checked}, active is {win.theme.theme.name}"
    finally:
        lab.close()
        QtWidgets.QApplication.instance().processEvents()


def test_popups_and_menus_do_not_count_as_windows(win):
    """Menus and tooltips are top-level widgets too. Counting them would block every switch."""
    m = QtWidgets.QMenu(win)
    m.addAction("x")
    m.popup(win.rect().center())
    QtWidgets.QApplication.instance().processEvents()
    try:
        assert win._open_windows() == [], "a popup menu was mistaken for a window"
    finally:
        m.close()
