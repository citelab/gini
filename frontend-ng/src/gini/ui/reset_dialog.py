"""Hardware → Reset a Board: release a board's pairing, over USB.

A claimed board answers only the laptop that owns it and is invisible to every other
gBuilder. That is what stops thirty laptops in a room from stealing each other's
hardware — but it means a board claimed by the wrong laptop looks, from the student's
side, exactly like a board that is simply broken: it powers up, joins the Wi-Fi, and
never appears on anyone's canvas.

There is deliberately no timed auto-release: a board must never re-open itself while its
owner is at lunch. Physical possession is the authority instead — and USB *is* what
physical possession means in software. Hence this dialog: whoever is holding the board
can always take it back, and nobody else can.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
    QMessageBox, QPushButton, QVBoxLayout,
)

from ..services import boardsetup as bs
from .worker_host import WorkerHost


def _note(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setStyleSheet("color: palette(mid);")
    return lab


class _Scan(QObject):
    done = Signal(list, list)
    failed = Signal(str)

    def run(self) -> None:
        try:
            boards, others = bs.detect_boards()
            self.done.emit(boards, others)
        except Exception as e:
            self.failed.emit(str(e))


class _Unpair(QObject):
    done = Signal(bool, str)

    def __init__(self, port: str) -> None:
        super().__init__()
        self._port = port

    def run(self) -> None:
        try:
            with bs.BoardConsole(self._port) as con:
                if not con.wait_for_prompt():
                    self.done.emit(False, "the board never reached its prompt — unplug "
                                          "it, plug it back in, and try again")
                    return
                ok, msg = con.unpair()
                self.done.emit(ok, msg)
        except Exception as e:
            self.done.emit(False, str(e))


class ResetBoardDialog(WorkerHost, QDialog):
    """Pick a plugged-in board and release whoever owns it."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Reset a Board")
        self.setMinimumWidth(560)
        self._alive = True
        self._thread = None
        self._worker = None
        self._connections = []

        root = QVBoxLayout(self)
        root.addWidget(_note(
            "Releases a board's pairing, so any gBuilder can claim it again.<br><br>"
            "Use this when a board is online and healthy but never appears on the "
            "canvas — that usually means another laptop claimed it. Settings, id and "
            "Wi-Fi are kept; only the pairing is cleared."))

        form = QFormLayout()
        self.board = QComboBox()
        row = QHBoxLayout()
        row.addWidget(self.board, 1)
        self.rescan = QPushButton("Rescan")
        self.rescan.clicked.connect(self.scan)
        row.addWidget(self.rescan)
        form.addRow("Board", row)
        self.owner = QLabel("—")
        form.addRow("Claimed by", self.owner)
        root.addLayout(form)

        self.status = _note("")
        root.addWidget(self.status)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.reset_btn = QPushButton("Release")
        self.reset_btn.setDefault(True)
        self.buttons.addButton(self.reset_btn, QDialogButtonBox.AcceptRole)
        self.reset_btn.clicked.connect(self._reset)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)

        self.board.currentIndexChanged.connect(self._board_changed)
        self.scan()

    def scan(self) -> None:
        self._busy(True, "looking for boards…")
        self._run(_Scan(), self._scanned)

    def _scanned(self, boards: list, others: list) -> None:
        if not self._alive:
            return
        self._boards = boards
        self.board.blockSignals(True)
        self.board.clear()
        for b in boards:
            self.board.addItem(f"{b.board_id or '(no id)'} — {b.port}", b.port)
        self.board.blockSignals(False)
        self._busy(False, "")
        if not boards:
            self.owner.setText("—")
            self.reset_btn.setEnabled(False)
            self.status.setText(
                "No board found. Plug one in with a <b>data</b> USB cable."
                + (f" ({len(others)} other serial device(s) seen.)" if others else ""))
            return
        self._board_changed()

    def _board_changed(self) -> None:
        if not self._alive or self.board.currentIndex() < 0:
            return
        b = self._boards[self.board.currentIndex()]
        owner = (getattr(b, "owner", "") or "").strip()
        self.owner.setText(owner or "not claimed")
        # Releasing an unclaimed board is harmless, so it stays enabled: the point is to
        # let someone who suspects a stuck claim just try it and see.
        self.reset_btn.setEnabled(True)

    def _reset(self) -> None:
        if self.board.currentIndex() < 0:
            return
        self._busy(True, "releasing…")
        self._run(_Unpair(self.board.currentData()), self._done)

    def _done(self, ok: bool, msg: str) -> None:
        if not self._alive:
            return
        self._busy(False, "")
        if not ok:
            QMessageBox.warning(self, "Could not release the board", msg)
            self.status.setText(msg)
            return
        QMessageBox.information(self, "Board released", msg)
        self.accept()

    def _worker_failed(self, message: str) -> None:
        if self._alive:
            self._busy(False, "")
            self.status.setText(message)

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
        self.reset_btn.setEnabled(not busy)
        self.setCursor(Qt.WaitCursor if busy else Qt.ArrowCursor)
