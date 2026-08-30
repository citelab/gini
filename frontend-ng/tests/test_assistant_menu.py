"""Ask GINI panel redesign: a segmented mutually-exclusive mode switch (Chat/Explain/
Wizard), Tutor relocated as a separate 'animate' modifier, a model indicator, and the
demoted Show-a-path surfaced as a contextual chip."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from gini.ui.main_window import MainWindow


def _win():
    app = QApplication.instance() or QApplication([])
    return app, MainWindow(app)


def test_modes_are_one_exclusive_segmented_switch():
    app, w = _win()
    a = w.assistant
    assert a._chat_btn.isChecked() and not a.explain_mode and not a.wizard_mode
    a._explain_btn.setChecked(True)
    assert a.explain_mode and not a._chat_btn.isChecked()      # picking one drops the others
    a._chat_btn.setChecked(True)                               # exit Explain via Chat
    assert not a.explain_mode and a._chat_btn.isChecked()


def test_tutor_is_a_modifier_not_a_mode():
    app, w = _win()
    a = w.assistant
    assert a._tutor_box not in a._mode_group.buttons()         # animate toggle isn't a mode
    assert a._tutor_box.isChecked() and a._tutor is True       # on by default
    a._tutor_box.setChecked(False)
    assert a._tutor is False and a._chat_btn.isChecked()       # doesn't change the active mode


def test_model_indicator_and_wizard_fallback_to_chat():
    app, w = _win()
    a = w.assistant
    assert not a._wizard_btn.isEnabled()                       # Wizard needs a model
    assert w.mode_indicator._pills()[0][1] == "no model"       # shown in the toolbar now

    class _Loop:
        pass
    a.set_loop(_Loop())
    assert a._wizard_btn.isEnabled()                           # a model enables Wizard
    a._wizard_btn.setChecked(True)
    assert a.wizard_mode
    a.set_loop(None)                                           # model gone
    assert not a._wizard_btn.isEnabled()
    assert a._chat_btn.isChecked() and not a.wizard_mode       # falls back to Chat


def test_show_a_path_is_a_contextual_chip():
    app, w = _win()
    a = w.assistant
    w.api.add_device("host"); w.api.add_device("host")
    app.processEvents()
    assert "Show a path" in a._followups_for(("overview", ""))  # demoted button -> chip


def _chip_labels(a):
    return [a._follow_lay.itemAt(i).widget().text()
            for i in range(a._follow_lay.count()) if a._follow_lay.itemAt(i).widget()]


def test_coach_is_a_model_gated_exclusive_mode():
    app, w = _win()
    a = w.assistant
    assert a._coach_btn in a._mode_group.buttons()         # it's a real mode
    assert not a._coach_btn.isEnabled()                    # no model -> disabled like Wizard

    class _Loop:
        def send(self, p, on_text=None):
            return ""
    a.set_loop(_Loop())
    assert a._coach_btn.isEnabled()
    a._coach_btn.setChecked(True)
    assert a.coach_mode and not a._chat_btn.isChecked()    # picking Coach drops other modes
    a.set_loop(None)                                       # model gone
    assert not a._coach_btn.isEnabled() and a._chat_btn.isChecked()   # falls back to Chat


def test_coach_reports_a_clean_topology():
    app, w = _win()
    a = w.assistant

    class _Loop:
        def send(self, p, on_text=None):
            return ""
    a.set_loop(_Loop())
    a._coach_btn.setChecked(True)                          # empty canvas -> nothing to fix
    app.processEvents()
    assert any("no problems" in m[1].lower() for m in a._messages)


def test_coach_flags_a_lone_device_with_fix_and_recheck_chips():
    app, w = _win()
    a = w.assistant

    class _Loop:
        def send(self, p, on_text=None):
            if on_text:
                on_text("Connect R1 to something.")
            return "Connect R1 to something."
    a.set_loop(_Loop())
    w.api.add_device("router")                             # R1 with no links -> flagged
    a._coach_btn.setChecked(True)
    app.processEvents()
    labels = _chip_labels(a)
    assert "Re-check" in labels
    assert any(l.startswith("Fix") for l in labels)        # a tappable per-issue fix
