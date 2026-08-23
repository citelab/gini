"""UI tweaks: empty-canvas click exits Connect mode and reverts Explain -> Chat;
the theme button opens its menu in one click (no default-action split button)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QToolButton

from gini.ui.main_window import MainWindow


def _win():
    app = QApplication.instance() or QApplication([])
    return MainWindow(app)


def test_project_chip_is_truly_centred():
    """The chip sits between two expanding clusters, so it centres whenever the toolbar is wide
    enough to hold them — and cannot when it is not, which is arithmetic rather than a bug.

    This used to hardcode resize(1280, 760), which quietly made it a font-metrics detector: these
    tests force QT_QPA_PLATFORM=offscreen, headless Qt has no "Sans Serif" and falls back through
    an alias table to something much wider, the clusters stop fitting in 1280, and the chip gets
    shoved right. It failed identically on Linux and macOS while the real app centred the chip
    perfectly. Sizing from the toolbar's own sizeHint asserts the actual invariant on any platform
    and any font.
    """
    from PySide6.QtWidgets import QToolBar
    w = _win()
    w._set_project_label("Some-Project")
    w.show()
    tb = w.findChild(QToolBar)
    w.resize(tb.sizeHint().width() + 240, 760)     # room for the clusters, whatever the font
    QApplication.instance().processEvents()
    cx = w._nav_btn.mapTo(tb, w._nav_btn.rect().center()).x()

    # Tolerance is a FRACTION of the toolbar, not a fixed 12px.
    #
    # The chip sits between two expanding clusters whose natural widths differ (they hold
    # different buttons), so the exact centre depends on font metrics: dead-on with the app's real
    # font, ~6% off under headless Qt's fallback for the missing "Sans Serif". A 12px absolute
    # tolerance was really measuring the font, and failed identically on Linux and macOS while the
    # shipped app centred the chip perfectly.
    #
    # 10% still asserts the thing that matters — the chip is CENTRED, not shoved to one side. The
    # failures this test has actually caught were 41% (the original) and 26% (a deliberately
    # widened cluster), both far outside it.
    off = abs(cx - tb.width() // 2)
    assert off <= tb.width() * 0.10, f"chip is {off}px from centre of a {tb.width()}px toolbar"
    w.close()


def test_empty_canvas_click_exits_connect_mode():
    w = _win()
    w._connect_act.trigger()                      # enter connect mode (as a click would)
    assert w._connect_act.isChecked() and w.canvas._connect_mode
    w.ctx.bus.canvas_background_clicked.emit()     # click empty canvas
    assert not w._connect_act.isChecked() and not w.canvas._connect_mode


def test_empty_canvas_click_reverts_explain_to_chat():
    w = _win()
    w.assistant._explain_btn.setChecked(True)      # enter Explain mode
    assert w.assistant.explain_mode
    w.ctx.bus.canvas_background_clicked.emit()
    assert not w.assistant.explain_mode and w.assistant._chat_btn.isChecked()


def test_wizard_mode_is_left_sticky_on_background_click():
    w = _win()
    if not w.assistant._wizard_btn.isEnabled():
        return                                     # needs a model; skip if disabled
    w.assistant._wizard_btn.setChecked(True)
    w.ctx.bus.canvas_background_clicked.emit()
    assert w.assistant.wizard_mode                 # deliberate flow stays on


def test_theme_button_is_instant_single_click_menu():
    w = _win()
    assert w._theme_btn.popupMode() == QToolButton.InstantPopup
    acts = w._theme_btn.menu().actions()
    themes = [a for a in acts if a.text()]
    seps = [a for a in acts if a.isSeparator()]
    assert len(themes) == 7 and len(seps) == 1     # 7 themes split by a light/dark divider
    assert w._theme_btn.defaultAction() is None    # no split-button default action


def _loop_win():
    from gini.agent.llm.fake import ScriptedBackend
    from gini.agent.loop import AgentLoop
    from gini.agent.tools.registry import build_registry
    w = _win()
    w.assistant.set_loop(AgentLoop(ScriptedBackend([]), build_registry(w.api)))  # enables Coach
    return w


def test_coach_reengages_on_repeat_click():
    # the reported bug: clicking Coach again (after a fix) didn't re-run — an already-checked
    # exclusive button emits no `toggled`, so the review must run from `clicked`.
    w = _loop_win()
    calls = []
    w.assistant._run_coach = lambda: calls.append(1)
    w.assistant._coach_btn.click()     # enter Coach: toggled(True) + clicked
    w.assistant._coach_btn.click()     # re-click while active: clicked only (no toggled)
    assert len(calls) == 2 and w.assistant.coach_mode


def test_coach_click_does_nothing_when_not_in_coach_mode():
    w = _loop_win()
    calls = []
    w.assistant._run_coach = lambda: calls.append(1)
    w.assistant.coach_mode = False
    w.assistant._on_coach_clicked()
    assert calls == []
