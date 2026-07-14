"""Cue Cards feature tour + the tabbed Settings dialog."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QTabWidget

from gini.app.context import Settings
from gini.app.paths import PERSISTED_KEYS
from gini.ui.cue_cards import CardArt, CueCards, FEATURE_CARDS
from gini.ui.settings_dialog import SettingsDialog
from gini.ui.theme.manager import ThemeManager


def _ctx():
    app = QApplication.instance() or QApplication([])
    return app, ThemeManager(app, "Dark"), Settings()


def test_navigation_clamps_at_both_ends():
    _app, tm, s = _ctx()
    d = CueCards(None, tm, s, persist=lambda: None)
    assert d.title.text() == FEATURE_CARDS[0][1]
    d._go(-1)
    assert d._i == 0 and not d.back.isEnabled()         # can't go before the first
    for _ in range(len(FEATURE_CARDS) + 3):
        d._go(1)
    assert d._i == len(FEATURE_CARDS) - 1 and not d.next.isEnabled()
    assert d.title.text() == FEATURE_CARDS[-1][1]


def test_dont_show_toggle_persists():
    _app, tm, s = _ctx()
    saved = []
    d = CueCards(None, tm, s, persist=lambda: saved.append(1))
    assert s.show_help_on_launch is True
    d.dont.setChecked(True)                              # "Don't show at launch"
    assert s.show_help_on_launch is False and saved      # written + persisted


def test_no_voice_over_feature():
    # voice-over was removed — the dialog must not carry any TTS hooks
    _app, tm, s = _ctx()
    d = CueCards(None, tm, s, persist=lambda: None)
    assert not hasattr(d, "voice") and not hasattr(d, "_speak")
    assert not hasattr(s, "cue_voice")


def test_every_theme_has_a_screenshot_per_card():
    from gini.ui.cue_cards import _CUE_DIR, _theme_slug
    for theme in ("Dark", "Light", "GINI Brand", "High Contrast"):
        slug = _theme_slug(theme)
        for kind, _t, _b in FEATURE_CARDS:
            assert (_CUE_DIR / slug / f"{kind}.png").exists(), f"missing {slug}/{kind}.png"


def test_card_art_loads_the_running_theme_screenshot():
    from gini.ui.theme.manager import ThemeManager
    app = QApplication.instance() or QApplication([])
    for theme in ("Dark", "Light"):
        art = CardArt(ThemeManager(app, theme)); art.resize(480, 320)
        for kind, _t, _b in FEATURE_CARDS:
            art.set_kind(kind)
            assert art._pix is not None                       # the themed screenshot loaded
            art.render(QImage(480, 320, QImage.Format_ARGB32))


def test_settings_dialog_is_tabbed_with_new_keys():
    _app, _tm, s = _ctx()
    dlg = SettingsDialog(None, s)
    tabs = dlg.findChild(QTabWidget)
    assert tabs is not None
    labels = [tabs.tabText(i) for i in range(tabs.count())]
    assert {"Appearance", "Pricing", "Help"} <= set(labels)
    assert "show_help_on_launch" in dlg.values()
    assert "cue_voice" not in dlg.values()               # voice-over removed


def test_tour_pref_is_persisted_key():
    assert "show_help_on_launch" in PERSISTED_KEYS and "cue_voice" not in PERSISTED_KEYS


def test_theme_change_persists(tmp_path, monkeypatch):
    # regression: switching theme (e.g. via the toolbar palette menu) must survive restart
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path))
    from gini.app.paths import load_config
    from gini.ui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    w = MainWindow(app)
    w.theme.set_theme("GINI Brand")
    assert load_config().get("theme") == "GINI Brand"
