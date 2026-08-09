"""Hardware → Set Up a Board: the dialog's threading and its reporting.

Serial work is slow (a board reboots when its port is opened), so it runs on a
worker thread. Both regressions guarded here were found by actually running the
dialog headlessly, and both aborted the whole process rather than failing softly.
"""
import time

import pytest

from PySide6.QtWidgets import QApplication

from gini.app.context import Settings
from gini.services import boardsetup as bs
from gini.ui import board_dialog as bd


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _settle(app, limit: float = 10.0) -> None:
    """Pump the GUI thread until every worker thread has retired."""
    t0 = time.time()
    while bd._LIVE_THREADS and time.time() - t0 < limit:
        app.processEvents()
        time.sleep(0.02)


def test_scan_finishes_and_the_thread_retires(app):
    """Regression: the worker was passed as a temporary, so PySide garbage-collected
    it the moment _run() returned. The thread then sat in its event loop forever,
    the scan never completed, and the dialog said 'Looking for boards…' for good."""
    dlg = bd.BoardSetupDialog(None, Settings())
    _settle(app)
    assert not bd._LIVE_THREADS, "worker thread never retired — is the worker held?"
    assert "Looking for boards" not in dlg.status.text()
    dlg.close()


def test_closing_mid_scan_does_not_abort(app):
    """Regression: the QThread was parented to the dialog, so closing during a scan
    destroyed a running thread and Qt called abort() on the whole process."""
    dlg = bd.BoardSetupDialog(None, Settings())
    dlg.close()                              # while the scan is still in flight
    assert dlg._alive is False
    _settle(app)
    assert not bd._LIVE_THREADS


def test_repeated_open_close_leaks_nothing(app):
    for _ in range(5):
        bd.BoardSetupDialog(None, Settings()).close()
    _settle(app, 15.0)
    assert not bd._LIVE_THREADS


def test_no_board_message_mentions_the_data_cable(app):
    """A power-only USB cable is indistinguishable from a dead board, and wastes
    an extraordinary amount of lab time. Say it before they go hunting."""
    dlg = bd.BoardSetupDialog(None, Settings())
    _settle(app)
    text = dlg.status.text().lower()
    assert "no board found" in text
    assert "data cable" in text
    dlg.close()


def test_lab_wifi_is_prefilled_from_settings(app):
    s = Settings()
    s.board_wifi_ssid = "dept-wifi"
    s.board_wifi_password = "hunter2"
    dlg = bd.BoardSetupDialog(None, s)
    assert dlg.ssid.text() == "dept-wifi"
    assert dlg.password.text() == "hunter2"
    _settle(app)
    dlg.close()


def test_applying_without_a_board_is_refused_not_crashed(app, monkeypatch):
    """No board selected must produce a message, never an exception."""
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.information", lambda *a, **k: None)
    dlg = bd.BoardSetupDialog(None, Settings())
    _settle(app)
    dlg._apply()                     # nothing detected in a test environment
    dlg.close()


# ------------------------------------------------------- port filtering (unit)

@pytest.mark.parametrize("device,ok", [
    ("/dev/cu.usbserial-120", True),
    ("/dev/cu.SLAB_USBtoUART", True),
    ("/dev/cu.wchusbserial5410", True),
    ("/dev/cu.usbmodem14201", True),
    ("/dev/ttyUSB0", True),
    ("/dev/ttyACM0", True),
    ("/dev/ttyS0", False),                       # motherboard UART, 32 of them on Linux
    ("/dev/ttyS31", False),
    ("/dev/cu.Bluetooth-Incoming-Port", False),
    ("/dev/cu.debug-console", False),
    ("/dev/random", False),
])
def test_only_plausible_devices_are_offered(device, ok):
    """A Linux box exposes 32 legacy /dev/ttyS* ports; offering them would bury the
    one device the student actually plugged in."""
    assert bs._plausible(device) is ok
