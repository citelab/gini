"""~/.gini config persistence + Settings applied on startup."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from gini.app import paths
from gini.ui.main_window import MainWindow


def test_config_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path))
    paths.save_config({"theme": "Light", "llm_enabled": True, "llm_model": "gemma"})
    assert (tmp_path / "config.json").exists()
    cfg = paths.load_config()
    assert cfg["theme"] == "Light" and cfg["llm_enabled"] is True
    assert paths.projects_dir() == tmp_path / "projects"


def test_mainwindow_applies_saved_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path))
    for k in ("GINI_LLM_URL", "GINI_LLM_MODEL", "GINI_REDUCED_MOTION"):
        monkeypatch.delenv(k, raising=False)
    paths.save_config({"theme": "Light", "llm_model": "gemma", "reduced_motion": True})

    app = QApplication.instance() or QApplication([])
    w = MainWindow(app)
    assert w.theme.theme.name == "Light"            # saved theme applied at startup
    assert w.ctx.settings.llm_model == "gemma"
    assert w.ctx.settings.reduced_motion is True


def test_settings_dialog_collects_values(tmp_path, monkeypatch):
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path))
    from gini.app.context import Settings
    from gini.ui.settings_dialog import SettingsDialog
    QApplication.instance() or QApplication([])
    dlg = SettingsDialog(None, Settings())
    dlg.llm_enabled.setChecked(True)
    dlg.llm_model.setText("qwen")
    v = dlg.values()
    assert v["llm_enabled"] is True and v["llm_model"] == "qwen"
    assert v["theme"] in ("Dark", "Light", "GINI Brand", "High Contrast")


# Every key _open_settings applies before persisting. values() MUST return them all —
# a dropped key (e.g. "prices") KeyErrors the handler before save_config(), so NOTHING
# persists and all settings are silently lost on restart. (That was the bug.)
_APPLIED = ("theme", "reduced_motion", "auto_internet", "llm_enabled", "llm_url",
            "llm_model", "llm_think", "name_prefixes", "prices", "show_help_on_launch")


def test_values_returns_every_key_the_save_path_applies(tmp_path, monkeypatch):
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path))
    from gini.app.context import Settings
    from gini.ui.settings_dialog import SettingsDialog
    QApplication.instance() or QApplication([])
    v = SettingsDialog(None, Settings()).values()
    missing = [k for k in _APPLIED if k not in v]
    assert not missing, f"values() dropped {missing} — would KeyError the save"


def test_price_and_model_edits_persist_across_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path))
    from gini.app.context import Settings
    from gini.ui.settings_dialog import SettingsDialog
    QApplication.instance() or QApplication([])
    dlg = SettingsDialog(None, Settings())
    dlg.llm_model.setText("qwen2.5")
    dlg.llm_enabled.setChecked(True)
    dlg._price_edits["database"].setText("99")
    v = dlg.values()
    assert v["prices"]["database"] == 99.0 and v["llm_model"] == "qwen2.5"

    s = Settings()                                   # mirror _open_settings exactly
    for k in _APPLIED:
        setattr(s, k, v[k])
    paths.save_config({k: getattr(s, k) for k in paths.PERSISTED_KEYS})
    cfg = paths.load_config()                        # reload like a fresh launch
    assert cfg["llm_model"] == "qwen2.5"
    assert cfg["prices"]["database"] == 99.0


# -- the dialog must not LOSE what you already saved ------------------------- #
def test_opening_settings_and_saving_untouched_changes_NOTHING():
    """The bug this guards: the Theme combo listed 7 themes but its {name: index} lookup only knew
    4, so Sand/Blue/Green fell through to index 0 — the dialog silently showed "Dark", and saving
    Settings for ANY unrelated reason (editing your Teaching Center credentials, say) wrote Dark
    back and stole your theme. A settings dialog that can't round-trip its own state is a data-loss
    bug, not a cosmetic one. So: open + save with no edits must be a no-op, for EVERY theme."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gini.app.context import Settings
    from gini.ui.settings_dialog import SettingsDialog, _THEMES
    QApplication.instance() or QApplication([])

    for name in _THEMES:                       # every theme in the dropdown, not just the first four
        s = Settings()
        s.theme = name
        s.llm_enabled = True
        s.llm_url, s.llm_model = "http://box:11434", "granite4:micro"
        s.tc_url, s.tc_course = "http://localhost:8080", "cs4480-fall26"
        s.tc_student, s.tc_token = "mahesh", "tok"
        s.reduced_motion = True
        s.show_help_on_launch = False

        v = SettingsDialog(None, s).values()   # open it, touch nothing, hit Save
        assert v["theme"] == name, f"{name} came back as {v['theme']!r}"
        assert v["llm_url"] == "http://box:11434" and v["llm_model"] == "granite4:micro"
        assert v["tc_url"] == "http://localhost:8080" and v["tc_student"] == "mahesh"
        assert v["tc_course"] == "cs4480-fall26"
        # tc_token is deliberately no longer a field: a one-time claim token does not belong in a
        # config file. Not returning it is how an existing one survives untouched — the save path
        # applies only the keys the dialog hands back. Pinned in
        # test_dropping_the_token_field_does_not_wipe_an_existing_one.
        assert "tc_token" not in v
        assert v["reduced_motion"] is True and v["llm_enabled"] is True
        assert v["show_help_on_launch"] is False


def test_theme_matching_is_case_and_alias_tolerant():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gini.app.context import Settings
    from gini.ui.settings_dialog import SettingsDialog
    QApplication.instance() or QApplication([])

    s = Settings(); s.theme = "sand"                      # config.json stores it lowercased
    assert SettingsDialog(None, s).values()["theme"] == "Sand"
    s.theme = "brand"                                     # the old short alias
    assert SettingsDialog(None, s).values()["theme"] == "GINI Brand"
    s.theme = "nonsense"                                  # unknown → a sane default, not a crash
    assert SettingsDialog(None, s).values()["theme"] == "Dark"


# -- the Teaching Center tab has two audiences ---------------------------------- #
def _tc_form(dlg):
    from PySide6.QtWidgets import QFormLayout
    return next(f for f in dlg.findChildren(QFormLayout)
                if any(f.itemAt(r, QFormLayout.FieldRole)
                       and f.itemAt(r, QFormLayout.FieldRole).widget() is dlg.tc_url
                       for r in range(f.rowCount())))


def _labels(form):
    from PySide6.QtWidgets import QFormLayout
    out = []
    for r in range(form.rowCount()):
        lab = form.itemAt(r, QFormLayout.LabelRole)
        if lab and lab.widget() and lab.widget().text():
            out.append(lab.widget().text())
    return out


def _dlg():
    from gini.app.context import Settings
    from gini.ui.settings_dialog import SettingsDialog
    QApplication.instance() or QApplication([])
    return SettingsDialog(None, Settings())


def test_the_student_fields_come_first_and_the_staff_field_is_marked_as_such():
    """Two different people read this tab. Everything a student needs is the top of it; marking is
    for one person in the room and says so, rather than looking like another thing to fill in."""
    labels = _labels(_tc_form(_dlg()))
    assert labels[:2] == ["Course server", "Course"]
    assert "Staff username" in labels
    assert labels.index("Course") < labels.index("Staff username")


def test_the_one_time_claim_token_is_not_a_setting_any_more():
    """It is a one-time secret, and ~/.gini/config.json is a plain file. It is asked for where it is
    used — the sign-in inside Teacher → Open a submission — and not kept."""
    d = _dlg()
    assert not hasattr(d, "tc_token")
    assert "tc_token" not in d.values()
    assert not any("token" in lbl.lower() for lbl in _labels(_tc_form(d)))


def test_dropping_the_token_field_does_not_wipe_an_existing_one(tmp_path, monkeypatch):
    """The save path applies only the keys the dialog returns, so a value already in someone's
    config is left alone rather than cleared by an upgrade."""
    from gini.app.context import Settings
    s = Settings()
    s.tc_token = "LEFTOVER"
    for k, v in _dlg().values().items():
        if hasattr(s, k):
            setattr(s, k, v)
    assert s.tc_token == "LEFTOVER"


def test_the_tab_says_that_handing_in_needs_no_account():
    """The dialog used to invite the opposite reading — a Username and a token above a note about
    lessons — and a student would reasonably conclude they had to sign in to submit."""
    from PySide6.QtWidgets import QFormLayout, QLabel
    form = _tc_form(_dlg())
    text = " ".join(
        w.widget().text() for r in range(form.rowCount())
        for role in (QFormLayout.FieldRole, QFormLayout.SpanningRole)
        for w in [form.itemAt(r, role)] if w and isinstance(w.widget(), QLabel))
    assert "needs no account" in text
    assert "assignment code" in text
    assert "never stored" in text.lower()          # about the staff password
