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
