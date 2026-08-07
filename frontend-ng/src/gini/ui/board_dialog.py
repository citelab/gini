"""Hardware → Set Up a Board: give a GINI32 board the lab Wi-Fi, over USB.

This is the only step in the whole GINI32 story that needs a cable, and a student
does it once per board. Everything the dialog writes is the minimum a board
cannot work out for itself; its address, gateway, hotspot and subnet all still
come from the canvas when it checks in.

The serial work happens on a worker thread. Talking to a board takes a couple of
seconds — it reboots when the port is opened — and freezing the window for that
long makes a working setup look like a hang.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from ..services import boardsetup as bs


# A worker and its thread outlive the dialog that started them (see _run), so they
# need an owner that is not the dialog. Both matter: garbage-collecting a running
# QThread makes Qt abort, and freeing the worker out from under a thread that is
# about to invoke it is a segfault. Entries retire themselves when the thread ends.
_LIVE_THREADS: set = set()          # {(QThread, worker)}


def _note(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setStyleSheet("color: palette(mid);")
    return lab


class _Scan(QObject):
    """Finds boards on a worker thread (opening a port resets and waits for it)."""
    done = Signal(list, list)          # [BoardInfo], [PortInfo]
    failed = Signal(str)

    def run(self) -> None:
        try:
            boards, others = bs.detect_boards()
            self.done.emit(boards, others)
        except Exception as e:                       # never let a USB oddity kill the UI
            self.failed.emit(str(e))


class _Apply(QObject):
    """Writes settings to one board, off the GUI thread."""
    done = Signal(bool, str)

    def __init__(self, port: str, ssid: str, password: str, board_id: str) -> None:
        super().__init__()
        self._args = (port, ssid, password, board_id)

    def run(self) -> None:
        port, ssid, password, board_id = self._args
        try:
            with bs.BoardConsole(port) as con:
                if not con.wait_for_prompt():
                    self.done.emit(False, "the board never reached its prompt — unplug "
                                          "it, plug it back in, and try again")
                    return
                ok, msg = con.apply(ssid, password, board_id)
                self.done.emit(ok, msg)
        except Exception as e:
            self.done.emit(False, str(e))


class BoardSetupDialog(QDialog):
    """Pick a plugged-in board, confirm the details, write them."""

    def __init__(self, parent, settings, known_ids: list[str] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Set Up a GINI32 Board")
        self.setMinimumWidth(560)
        self._settings = settings
        self._known_ids = list(known_ids or [])
        self._boards: list[bs.BoardInfo] = []
        self._thread: QThread | None = None
        self._worker: QObject | None = None   # kept alive; see _run()
        self._connections: list = []          # (signal, slot) pairs cut on close
        self._alive = True          # cleared on close: workers must stop touching us
        self.applied_id = ""

        root = QVBoxLayout(self)
        root.addWidget(_note(
            "Plug a board into this computer with a USB cable. This is the only step "
            "that needs a cable — after it, the board works over Wi-Fi."))

        form = QFormLayout()
        row = QHBoxLayout()
        self.board = QComboBox()
        self.board.setMinimumWidth(320)
        row.addWidget(self.board, 1)
        self.rescan = QPushButton("Rescan")
        self.rescan.clicked.connect(self.scan)
        row.addWidget(self.rescan)
        holder = QWidget()
        holder.setLayout(row)
        form.addRow("Board", holder)

        self.ssid = QLineEdit(getattr(settings, "board_wifi_ssid", "") or "")
        self.ssid.setPlaceholderText("the Wi-Fi this laptop is on — 2.4 GHz")
        form.addRow("Lab Wi-Fi name", self.ssid)
        self.password = QLineEdit(getattr(settings, "board_wifi_password", "") or "")
        self.password.setEchoMode(QLineEdit.Password)
        form.addRow("Lab Wi-Fi password", self.password)
        self.board_id = QLineEdit()
        self.board_id.setPlaceholderText("gini-1")
        form.addRow("Name this board", self.board_id)
        root.addLayout(form)

        self.status = _note("")
        root.addWidget(self.status)
        root.addWidget(_note(
            "The name you give the board is what you type into the GINI32 element's "
            "BoardID on the canvas — pick something short you can read off a sticker."))

        self.buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.apply_btn = self.buttons.addButton("Set Up Board",
                                                QDialogButtonBox.AcceptRole)
        self.apply_btn.clicked.connect(self._apply)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)

        self.board.currentIndexChanged.connect(self._board_changed)
        self.scan()

    # ---------------------------------------------------------------- scanning

    def scan(self) -> None:
        self._busy(True, "Looking for boards…")
        self.board.clear()
        self._run(_Scan(), self._scanned)

    def _worker_failed(self, message: str) -> None:
        if self._alive:
            self._busy(False, f"Could not look for boards: {message}")

    def _scanned(self, boards: list, others: list) -> None:
        if not self._alive:          # the dialog was closed while we were scanning
            return
        self._boards = boards
        self.board.clear()
        for b in boards:
            label = b.board_id or "(unnamed board)"
            if b.configured:
                label += f"   — set up for '{b.ssid}'"
            else:
                label += "   — not set up yet"
            self.board.addItem(f"{label}   [{b.port}]", b.port)

        if boards:
            self._busy(False, f"Found {len(boards)} board"
                              f"{'s' if len(boards) != 1 else ''}.")
        elif others:
            names = ", ".join(o.device for o in others[:3])
            self._busy(False,
                       f"Serial devices found ({names}), but none answered as a GINI32 "
                       f"board. Is the gBridge firmware flashed on it?")
        else:
            self._busy(False,
                       "No board found. Check the cable is plugged in at both ends — "
                       "and that it is a DATA cable: many USB cables carry power only, "
                       "which looks exactly like a dead board.")
        self._board_changed()

    def _board_changed(self) -> None:
        """Suggest a name that does not collide with one already in use."""
        b = self._selected()
        if b is None:
            return
        if b.board_id and b.board_id not in ("gini32-1",):   # keep a real, chosen name
            self.board_id.setText(b.board_id)
        else:
            taken = self._known_ids + [x.board_id for x in self._boards
                                       if x is not b and x.board_id]
            self.board_id.setText(bs.suggest_board_id(taken))
        if b.ssid and not self.ssid.text().strip():
            self.ssid.setText(b.ssid)

    def _selected(self) -> bs.BoardInfo | None:
        port = self.board.currentData()
        return next((b for b in self._boards if b.port == port), None)

    # ---------------------------------------------------------------- applying

    def _apply(self) -> None:
        b = self._selected()
        if b is None:
            QMessageBox.information(self, "No board selected",
                                    "Plug a board in and press Rescan.")
            return
        ssid = self.ssid.text().strip()
        pw = self.password.text()
        bid = self.board_id.text().strip()
        if not ssid:
            QMessageBox.warning(self, "Wi-Fi name needed",
                                "Enter the name of the Wi-Fi network this laptop is on. "
                                "The board joins it to reach gBuilder.")
            return
        if not bid:
            QMessageBox.warning(self, "Name needed",
                                "Give the board a short name — it is how the canvas "
                                "refers to it.")
            return
        self._busy(True, f"Setting up '{bid}'…")
        self._run(_Apply(b.port, ssid, pw, bid), self._applied)

    def _applied(self, ok: bool, msg: str) -> None:
        if not self._alive:
            return
        self._busy(False, "")
        if not ok:
            QMessageBox.warning(self, "Setup failed", msg)
            return
        # Remember the lab Wi-Fi so the next board is a single click.
        try:
            self._settings.board_wifi_ssid = self.ssid.text().strip()
            self._settings.board_wifi_password = self.password.text()
        except Exception:
            pass
        self.applied_id = self.board_id.text().strip()
        QMessageBox.information(
            self, "Board is set up",
            f"{msg}.\n\nThe board is restarting and will join the Wi-Fi on its own. "
            f"You can unplug it now — it only needs power from here on.\n\n"
            f"Use '{self.applied_id}' as the BoardID on the canvas.")
        self.accept()

    # ------------------------------------------------------------------- utils

    def _run(self, worker: QObject, on_done) -> None:
        """Run a worker on its own thread and clean both up when it finishes.

        The thread is deliberately NOT parented to this dialog. Serial work is slow —
        a board reboots when its port is opened — so a student can easily press Cancel
        mid-scan; a parented QThread would then be destroyed while still running, and
        Qt aborts the whole process when that happens. Unparented, it is held by the
        module-level registry until it finishes on its own, and the callbacks simply
        stop touching a dialog that has gone away.
        """
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        # Hold a Python reference to BOTH. PySide does not keep the worker alive just
        # because a signal is connected to it, so a worker passed in as a temporary is
        # garbage-collected the moment this call returns — the thread then sits in its
        # event loop forever and nothing is ever emitted.
        entry = (thread, worker)
        _LIVE_THREADS.add(entry)
        self._worker = worker

        # Connect to BOUND METHODS of this dialog, never to a closure. A closure has no
        # thread affinity, so Qt picks a direct connection and the "handler" would run
        # on the worker thread — touching widgets from off the GUI thread, which hangs
        # or crashes. A bound method of a QObject living in the GUI thread makes the
        # connection queued, which is the whole point of doing this work on a thread.
        worker.done.connect(on_done)
        worker.done.connect(thread.quit)
        self._connections = [(worker.done, on_done)]
        if hasattr(worker, "failed"):
            worker.failed.connect(self._worker_failed)
            worker.failed.connect(thread.quit)
            self._connections.append((worker.failed, self._worker_failed))
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(lambda: _LIVE_THREADS.discard(entry))
        thread.finished.connect(thread.deleteLater)
        self._thread = thread
        thread.start()

    def _detach(self) -> None:
        """Cut every path from a running worker back into this dialog.

        The `_alive` flag is not enough on its own: if the dialog is destroyed while a
        worker is still going, the queued call lands on a freed C++ object and takes the
        process with it. Disconnecting our own handlers (and only ours — `thread.quit`
        stays connected, or the thread would never stop) makes the worker harmless.
        """
        import warnings
        self._alive = False
        for signal, slot in getattr(self, "_connections", []):
            # A worker that already finished has dropped its connections, and PySide
            # reports that as a warning rather than an exception. Closing a dialog
            # after a successful scan is the common case, so this must be silent.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                try:
                    signal.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
        self._connections = []

    def closeEvent(self, event) -> None:
        self._detach()
        super().closeEvent(event)

    def reject(self) -> None:
        self._detach()
        super().reject()

    def _busy(self, busy: bool, message: str) -> None:
        if not self._alive:
            return
        self.status.setText(message)
        self.rescan.setEnabled(not busy)
        self.apply_btn.setEnabled(not busy)
        self.setCursor(Qt.WaitCursor if busy else Qt.ArrowCursor)
