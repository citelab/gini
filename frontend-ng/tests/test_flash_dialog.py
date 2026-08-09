"""The Flash and Reset dialogs, driven headless.

Running these headless is not ceremony: the three bugs that made the Set Up dialog abort
the whole process (a garbage-collected worker, closure connections with no thread
affinity, and a QThread parented to the dialog) were ALL found this way and none of them
were visible by reading the code. The threading rules now live in ui/worker_host.py, so
these tests guard the shared copy.
"""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from gini.services import boardflash as bf
from gini.services import boardsetup as bs
from gini.ui import worker_host


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _reap_threads():
    """Reap worker threads after every test.

    Without this the suite ends with "QThread: Destroyed while thread is still running" —
    harmless-looking noise that is in fact the exact abort this module guards against,
    and which would mask a real one. It also proves `drain()` works, since the app calls
    the same function when the window closes.
    """
    yield
    QApplication.processEvents()
    worker_host.drain()


@pytest.fixture
def no_esptool(monkeypatch):
    monkeypatch.setattr(bf, "esptool_available", lambda *a, **k: False)


@pytest.fixture
def quiet_serial(monkeypatch):
    """No real USB in a test run, and no waiting on one either."""
    monkeypatch.setattr(bs, "list_ports", lambda: [])
    monkeypatch.setattr(bs, "detect_boards", lambda ports=None: ([], []))


def test_missing_esptool_is_explained_rather_than_crashed_into(app, no_esptool):
    from gini.ui.flash_dialog import FlashBoardDialog
    dlg = FlashBoardDialog()
    assert not dlg.flash_btn.isEnabled()
    assert "pip install esptool" in dlg.status.text()
    dlg.close()


def test_no_board_plugged_in_says_so_and_blames_the_cable(app, monkeypatch, quiet_serial):
    """A charge-only USB cable is indistinguishable from a dead board, and is far more
    common — so the message has to name it."""
    monkeypatch.setattr(bf, "esptool_available", lambda *a, **k: True)
    from gini.ui.flash_dialog import FlashBoardDialog
    dlg = FlashBoardDialog()
    QApplication.processEvents()
    dlg._scanned([], "")
    assert not dlg.flash_btn.isEnabled()
    assert "cable" in dlg.status.text().lower()
    dlg.close()


def test_a_chip_with_no_shipped_image_is_named_not_just_refused(app, monkeypatch,
                                                               quiet_serial):
    monkeypatch.setattr(bf, "esptool_available", lambda *a, **k: True)
    monkeypatch.setattr(bf, "available", lambda *a, **k: None)
    monkeypatch.setattr(bf, "available_targets", lambda *a, **k: ["esp32s3"])
    from gini.ui.flash_dialog import FlashBoardDialog
    dlg = FlashBoardDialog()
    dlg._identified([], "esp32c6")
    assert not dlg.flash_btn.isEnabled()
    assert "esp32c6" in dlg.status.text()
    assert "esp32s3" in dlg.status.text()      # says what IS available
    dlg.close()


def test_a_recognised_chip_shows_the_build_about_to_be_written(app, monkeypatch,
                                                              quiet_serial):
    """Naming the build is what stops a stale flash masquerading as a fresh one."""
    monkeypatch.setattr(bf, "esptool_available", lambda *a, **k: True)
    fw = bf.Firmware(target="esp32s3", directory=None, files=[], build="gbridge-17 (x)")
    monkeypatch.setattr(bf, "available", lambda *a, **k: fw)
    from gini.ui.flash_dialog import FlashBoardDialog
    dlg = FlashBoardDialog()
    dlg._identified([], "esp32s3")
    assert dlg.flash_btn.isEnabled()
    assert "gbridge-17" in dlg.firmware.text()
    dlg.close()


def test_closing_mid_flash_does_not_take_the_process_with_it(app, monkeypatch,
                                                            quiet_serial):
    """The original crash: a worker still running when the dialog is destroyed delivers
    a queued call to a freed C++ object. _detach must make it harmless."""
    monkeypatch.setattr(bf, "esptool_available", lambda *a, **k: True)
    from gini.ui.flash_dialog import FlashBoardDialog
    dlg = FlashBoardDialog()
    dlg.close()
    QApplication.processEvents()
    assert dlg._connections == []
    assert dlg._alive is False


def test_workers_are_held_by_the_module_not_the_dialog(app, monkeypatch, quiet_serial):
    """Rule 1: PySide will not keep a worker alive just because a signal is connected."""
    monkeypatch.setattr(bf, "esptool_available", lambda *a, **k: True)
    from gini.ui.flash_dialog import FlashBoardDialog
    dlg = FlashBoardDialog()
    # the scan kicked off in __init__ registered its (thread, worker) pair
    assert all(isinstance(e, tuple) and len(e) == 2 for e in worker_host._LIVE_THREADS)
    dlg.close()


def test_the_flash_thread_is_not_parented_to_the_dialog(app, monkeypatch, quiet_serial):
    """Rule 3: destroying a running QThread makes Qt abort the process."""
    monkeypatch.setattr(bf, "esptool_available", lambda *a, **k: True)
    from gini.ui.flash_dialog import FlashBoardDialog
    dlg = FlashBoardDialog()
    if dlg._thread is not None:
        assert dlg._thread.parent() is None
    dlg.close()


# --------------------------------------------------------------- reset dialog

def test_reset_dialog_offers_nothing_when_no_board_is_present(app, quiet_serial):
    from gini.ui.reset_dialog import ResetBoardDialog
    dlg = ResetBoardDialog()
    dlg._scanned([], [])
    assert not dlg.reset_btn.isEnabled()
    dlg.close()


def test_reset_dialog_shows_who_holds_the_claim(app, quiet_serial):
    """The whole point of Reset: a board claimed by someone else looks broken, so the
    dialog has to say plainly that it is claimed, and by whom."""
    from gini.ui.reset_dialog import ResetBoardDialog
    dlg = ResetBoardDialog()
    dlg._scanned([bs.BoardInfo(port="/dev/ttyUSB0", board_id="gini-5",
                               owner="laptop-abc")], [])
    assert "laptop-abc" in dlg.owner.text()
    assert dlg.reset_btn.isEnabled()
    dlg.close()


def test_an_unclaimed_board_can_still_be_released(app, quiet_serial):
    """Releasing an unclaimed board is harmless, and someone who SUSPECTS a stuck claim
    should be able to just try it rather than having the button greyed out."""
    from gini.ui.reset_dialog import ResetBoardDialog
    dlg = ResetBoardDialog()
    dlg._scanned([bs.BoardInfo(port="/dev/ttyUSB0", board_id="gini-5", owner="")], [])
    assert "not claimed" in dlg.owner.text()
    assert dlg.reset_btn.isEnabled()
    dlg.close()
