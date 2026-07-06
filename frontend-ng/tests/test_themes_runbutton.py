"""Visual pass: the Sand/Blue/Green light themes, deeper canvas depth, the cue-card
light-family fallback, and the morphing circular Run/Stop power button."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from gini.ui import cue_cards
from gini.ui.main_window import MainWindow
from gini.ui.run_button import RunButton
from gini.ui.settings_dialog import _THEMES
from gini.ui.theme.manager import ThemeManager
from gini.ui.theme.tokens import get_theme


def _app():
    return QApplication.instance() or QApplication([])


def _tm(name="Dark"):
    return ThemeManager(_app(), name)


# ---- light theme family --------------------------------------------------- #
def test_new_light_themes_registered_and_light():
    for n in ("Sand", "Blue", "Green"):
        t = get_theme(n)
        assert t.name == n and t.dark is False        # they belong to the light family
        assert n in _THEMES                            # offered in Settings + toolbar
    accents = {get_theme(n).accent for n in ("Sand", "Blue", "Green")}
    assert len(accents) == 3                           # each has its own hue


def test_canvas_depth_got_deeper():
    assert get_theme("Dark").elevation >= 20           # nodes cast a bigger shadow now


def test_light_theme_nodes_pop_off_the_canvas():
    # element cards get a distinct, theme-tinted fill so they don't blend into the bg
    for n in ("Sand", "Blue", "Green", "Light"):
        t = get_theme(n)
        assert t.node and t.node_fill() != t.bg        # was identical (panel2==bg) for Sand
    # dark themes keep their panel fill (cards already read on a dark canvas)
    assert get_theme("Dark").node_fill() == get_theme("Dark").panel2


def test_theme_menu_light_to_dark_with_divider():
    w = MainWindow(_app())
    acts = w._theme_btn.menu().actions()
    labels = [a.text() for a in acts if a.text()]
    seps = [i for i, a in enumerate(acts) if a.isSeparator()]
    # light family first, then a divider, then the dark family
    assert labels == ["Light", "Sand", "Blue", "Green", "Dark", "GINI Brand", "High Contrast"]
    assert len(seps) == 1
    light = {"Light", "Sand", "Blue", "Green"}
    before = {a.text() for a in acts[:seps[0]] if a.text()}
    assert before == light                              # everything above the divider is light
    # each row carries a colour swatch, and the button shows the "colours" dots
    assert all(not a.icon().isNull() for a in acts if a.text())
    assert not w._theme_btn.icon().isNull()


def test_cue_cards_use_light_shots_for_light_themes():
    from pathlib import Path
    cand = cue_cards._cue_candidates(Path("/cue"), "sand", False, "welcome")
    names = [p.parent.name for p in cand]
    # a light theme (no 'sand' shots) reuses the LIGHT set before ever touching dark
    assert names[0] == "sand" and names[1] == "light"
    assert names.index("light") < names.index("dark")
    # dark themes stay on the dark family
    dark = cue_cards._cue_candidates(Path("/cue"), "brand", True, "welcome")
    assert dark[1].parent.name == "dark"


# ---- morphing Run/Stop power button --------------------------------------- #
def test_run_button_state_machine():
    b = RunButton(_tm()); b.resize(40, 34)
    assert b.state() == "ready" and not b._timer.isActive()
    b.set_state("booting")
    assert b._timer.isActive()                          # animates while working
    b.set_progress(2, 4)
    assert abs(b._target - 0.5) < 1e-6                  # ring reflects up/total
    b.set_state("running")
    assert b._target == 1.0
    b.set_state("ready")
    assert not b._timer.isActive() and b._target == 0.0


def test_run_button_click_emits():
    b = RunButton(_tm()); b.resize(40, 34)
    got = []
    b.clicked.connect(lambda: got.append(1))
    ev = QMouseEvent(QEvent.MouseButtonRelease, QPointF(20, 17),
                     Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    b.mouseReleaseEvent(ev)
    assert got == [1]


def test_run_button_wired_and_driven_by_lifecycle():
    w = MainWindow(_app())
    assert hasattr(w, "run_button")

    calls = []
    w._run = lambda: calls.append("run")
    w._stop = lambda: calls.append("stop")

    w.run_button.set_state("ready");   w._toggle_run(); assert calls[-1] == "run"
    w.run_button.set_state("running"); w._toggle_run(); assert calls[-1] == "stop"
    n = len(calls)
    w.run_button.set_state("stopping"); w._toggle_run()
    assert len(calls) == n                              # 'stopping' click is ignored

    # the container poller feeds the boot ring and morphs ▶ → ■
    w._running, w._stopping = True, False
    w._on_runtime_status({"a": "running", "b": "starting"})
    assert w.run_button.state() == "booting" and abs(w.run_button._target - 0.5) < 1e-6
    w._on_runtime_status({"a": "running", "b": "running"})
    assert w.run_button.state() == "running"
    w._on_runtime_status({})                            # everything down
    assert w.run_button.state() == "ready"
