"""xv6 peripherals on the canvas: hard link-blocking + double-click resolves to the wired xv6."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from gini.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_peripheral_resolves_to_its_wired_xv6(app):
    w = MainWindow(app)
    k = w.api.add_device("xv6", x=0, y=0)["id"]
    term = w.api.add_device("terminal", x=200, y=0)["id"]
    w.ctx.add_link(k, term)
    assert w._xv6_for_peripheral(term) == k


def test_unwired_peripheral_resolves_to_none(app):
    w = MainWindow(app)
    term = w.api.add_device("terminal", x=0, y=0)["id"]
    assert w._xv6_for_peripheral(term) is None


def test_open_unwired_peripheral_logs_hint_not_crash(app):
    # double-clicking a peripheral with no xv6 should log a friendly hint, not raise
    w = MainWindow(app)
    term = w.api.add_device("terminal", x=0, y=0)["id"]
    w._open_peripheral(term)            # must not raise
