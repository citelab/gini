"""Per-element 'Log in' / 'View logs' route to the right container.

Regression: a serverless Function has no container of its own (it's a handler in the
shared `faas` runtime), so its console must target `faas` — not fall through to the
switch/`fabric` branch (which produced 'service "fabric" is not running')."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import gini.services as services
from gini.ui.main_window import MainWindow


def _win():
    app = QApplication.instance() or QApplication([])
    return app, MainWindow(app)


def _capture(monkey_target, calls):
    def fake(title, workdir, cmd):
        calls.append((title, cmd))
        return True, "ok"
    return fake


def _run(w, tmp):
    w._running = True
    w._workdir = tmp


def test_function_login_targets_the_faas_runtime(tmp_path, monkeypatch):
    app, w = _win()
    _run(w, str(tmp_path))
    calls = []
    monkeypatch.setattr(services, "open_terminal", _capture(None, calls))
    fid = w.api.add_device("function")["id"]
    w._open_terminal(fid)
    assert calls, "no terminal opened"
    _title, cmd = calls[-1]
    assert "faas" in cmd and "fabric" not in cmd       # the bug was a fabric fall-through


def test_function_logs_tail_the_faas_container(tmp_path, monkeypatch):
    app, w = _win()
    _run(w, str(tmp_path))
    calls = []
    monkeypatch.setattr(services, "open_terminal", _capture(None, calls))
    fid = w.api.add_device("function")["id"]
    w._open_logs(fid)
    assert calls
    _title, cmd = calls[-1]
    assert "logs" in cmd and cmd.strip().endswith("faas")


def test_api_gateway_still_uses_its_own_container(tmp_path, monkeypatch):
    # the API Gateway IS a real Traefik container (role 'service') — unchanged
    app, w = _win()
    _run(w, str(tmp_path))
    calls = []
    monkeypatch.setattr(services, "open_terminal", _capture(None, calls))
    gid = w.api.add_device("api_gateway")["id"]
    w._open_terminal(gid)
    _title, cmd = calls[-1]
    assert "faas" not in cmd and "fabric" not in cmd


# --- run-gated actions: console/logs/login only when the lab is up -------------- #
def test_menu_action_gates():
    from gini.ui.canvas import NodeItem
    stopped = NodeItem.action_gates(running=False, is_router=False)
    assert stopped == {"console": False, "logs": False, "login": False}
    up = NodeItem.action_gates(running=True, is_router=False)
    assert up == {"console": True, "logs": True, "login": True}
    # a Router can open its (offline) Router Lab even when stopped
    assert NodeItem.action_gates(running=False, is_router=True)["login"] is True


def test_inspector_login_button_gated_on_running(tmp_path):
    app, w = _win()
    db = w.api.add_device("database")["id"]
    w.ctx.select(db)
    app.processEvents()
    assert not w.inspector.login_btn.isEnabled()       # stopped -> can't log in
    w.inspector.set_live_running(True)
    assert w.inspector.login_btn.isEnabled()           # lab up -> enabled
    w.inspector.set_live_running(False)
    assert not w.inspector.login_btn.isEnabled()


def test_router_login_enabled_offline(tmp_path):
    app, w = _win()
    r = w.api.add_device("router")["id"]
    w.ctx.select(r)
    app.processEvents()
    assert w.inspector.login_btn.isEnabled()           # Router Lab opens without Docker
