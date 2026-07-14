"""When connected to a GINI server, the main window routes Run/Stop to the remote client
instead of the local Docker path."""
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from gini.ui.main_window import MainWindow


class FakeRemote:
    def __init__(self):
        self.ran = self.stopped = False

    def kata_available(self):
        return True

    def run(self, topology):
        self.ran = True
        return True, "starting"

    def wait_until_running(self, *a, **k):
        return True, "running"

    def run_state(self):
        return {"state": "running"}

    def stop(self):
        self.stopped = True
        return True, "stopped"

    def metrics(self):
        return {"startup": {"k1": 1840.0}, "stats": {}}


def _win():
    app = QApplication.instance() or QApplication([])
    return app, MainWindow(app)


def _pump(app, cond, tries=80):
    for _ in range(tries):
        if cond():
            return True
        app.processEvents(); time.sleep(0.01)
    return cond()


def test_connect_then_run_and_stop_go_to_the_server():
    app, w = _win()
    fr = FakeRemote()
    assert w._connect_server(client=fr) is True
    assert w._remote is fr and w.ctx.settings.backend == "gini-server"

    w.api.add_device("kinstance")
    w._run()                                          # remote backend -> server, not local Docker
    assert _pump(app, lambda: fr.ran)                 # topology was sent to the server
    assert _pump(app, lambda: w._running)             # remote run-state marked the lab running

    w._stop()
    assert _pump(app, lambda: fr.stopped)             # Stop went to the server too


def test_toggle_backend_disconnects_back_to_local():
    app, w = _win()
    w._connect_server(client=FakeRemote())
    assert w._remote is not None
    w._toggle_backend()                               # toggle off -> local
    assert w._remote is None and w.ctx.settings.backend == "local"
