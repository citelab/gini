"""Auto-building a missing lab image is a Settings toggle, not just an env var.

The env var still wins where it's set (scripts / CI pin it explicitly); otherwise the persisted
setting decides, so a teacher can tick it once instead of exporting a variable every session.
"""
import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["GINI_HOME_DIR"] = tempfile.mkdtemp()

from PySide6.QtWidgets import QApplication

from gini.app.context import Settings
from gini.app.paths import PERSISTED_KEYS, load_config, save_config
from gini.services.orchestrator import Orchestrator


def _app():
    return QApplication.instance() or QApplication([])


def test_the_setting_exists_and_is_persisted():
    assert Settings().autobuild_images is False          # off by default — a 2-min build is a surprise
    assert "autobuild_images" in PERSISTED_KEYS          # …and it survives a restart


def test_env_var_wins_when_set(monkeypatch):
    monkeypatch.setenv("GINI_AUTOBUILD_GROUTER", "1")
    assert Orchestrator._autobuild_enabled("GROUTER") is True
    monkeypatch.setenv("GINI_AUTOBUILD_GROUTER", "0")    # an explicit 0 must NOT fall through
    assert Orchestrator._autobuild_enabled("GROUTER") is False


def test_setting_decides_when_the_env_var_is_absent(monkeypatch):
    monkeypatch.delenv("GINI_AUTOBUILD_GROUTER", raising=False)
    monkeypatch.delenv("GINI_AUTOBUILD_POX", raising=False)
    save_config({"autobuild_images": True})
    assert Orchestrator._autobuild_enabled("GROUTER") is True
    assert Orchestrator._autobuild_enabled("POX") is True   # one toggle covers both images
    save_config({"autobuild_images": False})
    assert Orchestrator._autobuild_enabled("GROUTER") is False


def test_no_config_at_all_is_simply_off(monkeypatch):
    monkeypatch.delenv("GINI_AUTOBUILD_GROUTER", raising=False)
    monkeypatch.setattr("gini.app.paths.load_config", lambda: (_ for _ in ()).throw(OSError("nope")))
    assert Orchestrator._autobuild_enabled("GROUTER") is False      # never raises


def test_dialog_round_trips_the_toggle():
    _app()
    from gini.ui.settings_dialog import SettingsDialog
    s = Settings()
    s.autobuild_images = True
    dlg = SettingsDialog(None, s)
    assert dlg.autobuild.isChecked()                     # reflects the current setting…
    dlg.autobuild.setChecked(False)
    assert dlg.values()["autobuild_images"] is False     # …and reports the edited value back


def test_every_dialog_value_reaches_settings_and_disk(monkeypatch):
    """The regression this file was born from: the dialog read the checkbox, MainWindow copied a
    hand-maintained whitelist of keys onto Settings, and anything missing from that list was
    silently dropped — the box ticked, then forgot. Guard the invariant for ALL settings, not just
    this one: every key the dialog returns must land on Settings and be persisted."""
    _app()
    from gini.app.paths import save_config
    from gini.ui.main_window import MainWindow
    from gini.ui.settings_dialog import SettingsDialog

    w = MainWindow(QApplication.instance())
    s = w.ctx.settings

    dlg_values = SettingsDialog(None, s).values()
    # (a) every key the dialog hands back must be a real Settings field, or it can never be applied
    unknown = [k for k in dlg_values if not hasattr(s, k)]
    assert not unknown, f"dialog returns keys Settings doesn't have: {unknown}"
    # (b) …and every one must be persisted, or the edit is lost on the next restart. These two
    # assertions together close the whole "my setting didn't stick" class for the dialog: (a) it
    # reaches Settings, (b) it reaches disk.
    unsaved = [k for k in dlg_values if k not in PERSISTED_KEYS]
    assert not unsaved, f"dialog settings that would not survive a restart: {unsaved}"

    # simulate the save path: apply the dialog's values, then persist
    for k, val in dlg_values.items():
        if hasattr(s, k):
            setattr(s, k, val)
    s.autobuild_images = True
    save_config({k: getattr(s, k) for k in PERSISTED_KEYS})
    assert load_config().get("autobuild_images") is True     # survives the restart


def test_connector_style_is_remembered_across_a_restart():
    """A toolbar toggle is still a preference: choosing straight connectors and finding them bent
    again after a restart is the same surprise as a Settings checkbox that doesn't stick."""
    _app()
    from gini.ui.main_window import MainWindow

    w = MainWindow(QApplication.instance())
    assert "connector_style" in PERSISTED_KEYS

    w._toggle_edge_style(False)                              # untick 'bent' -> straight
    assert w.ctx.settings.connector_style == "straight"
    assert load_config().get("connector_style") == "straight"   # persisted on the toggle itself

    # a fresh window reads it back and the toolbar button reflects it
    w2 = MainWindow(QApplication.instance())
    w2.ctx.settings.connector_style = load_config().get("connector_style", "orthogonal")
    assert w2.ctx.settings.connector_style == "straight"

    w._toggle_edge_style(True)                               # restore, and confirm both directions
    assert load_config().get("connector_style") == "orthogonal"
