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
