"""Quitting is blocked while a topology is running (⌘Q / menu / window close)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication

from gini.ui.main_window import MainWindow


def _win():
    app = QApplication.instance() or QApplication([])
    return app, MainWindow(app)


def test_close_is_blocked_while_running():
    app, w = _win()
    logs = []
    w.ctx.bus.log.connect(lambda lvl, msg: logs.append(msg))
    w._running = True
    ev = QCloseEvent(); ev.accept()
    w.closeEvent(ev)                                    # ⌘Q / File▸Quit route here (via self.close)
    assert not ev.isAccepted()                          # quit vetoed
    assert any("Stop" in m for m in logs)               # told the user in the console
    w.close  # noqa: B018


def test_close_proceeds_when_idle():
    app, w = _win()
    w._running = False
    ev = QCloseEvent(); ev.accept()
    w.closeEvent(ev)
    assert ev.isAccepted()                              # idle → normal quit


def test_event_filter_blocks_app_quit_while_running():
    app, w = _win()
    w._running = True
    assert w.eventFilter(app, QEvent(QEvent.Type.Quit)) is True    # ⌘Q backstop consumes the quit
    w._running = False
    assert w.eventFilter(app, QEvent(QEvent.Type.Quit)) is False   # idle → let it through
