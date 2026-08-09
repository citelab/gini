"""Hardware → Flash a Board: put gBridge firmware on a GINI32 board over USB.

This is the step that was missing, and its absence made the rest of the Hardware menu
untrue. *Set Up a Board* talks to the board's `gini> ` console — which only exists once
firmware is on it. So a student holding a brand-new board could not begin in gBuilder at
all; they needed a terminal, ESP-IDF and the CLI. Now the menu covers a board's whole
life: flash it, set it up, reset it, list them.

Flashing does NOT erase the board. The images are written at their own offsets, which
leaves NVS at 0x9000 alone, so a board that was already set up keeps its id, its lab
Wi-Fi and its LED pin across a firmware update. See services/boardflash.py.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
    QMessageBox, QProgressBar, QPushButton, QVBoxLayout,
)

from ..services import boardflash as bf
from ..services import boardsetup as bs
from .worker_host import WorkerHost


def _note(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setStyleSheet("color: palette(mid);")
    return lab


class _Detect(QObject):
    """Enumerate ports and ask the selected one what chip it is."""
    done = Signal(list, str)               # [PortInfo], chip target ("" = unknown)
    failed = Signal(str)

    def __init__(self, port: str = "") -> None:
        super().__init__()
        self._port = port

    def run(self) -> None:
        try:
            ports = bs.list_ports()
            chip = bf.detect_chip(self._port) if self._port else ""
            self.done.emit(ports, chip)
        except Exception as e:             # a USB oddity must never kill the UI
            self.failed.emit(str(e))


class _Flash(QObject):
    """Write the images, off the GUI thread — this takes ten to thirty seconds."""
    done = Signal(bool, str)
    progress = Signal(str)

    def __init__(self, port: str, firmware) -> None:
        super().__init__()
        self._port, self._fw = port, firmware

    def run(self) -> None:
        try:
            res = bf.flash(self._port, self._fw, on_progress=self.progress.emit)
            self.done.emit(res.ok, res.message)
        except Exception as e:
            self.done.emit(False, str(e))


class FlashBoardDialog(WorkerHost, QDialog):
    """Pick a port, confirm the chip, write the firmware."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Flash a Board")
        self.setMinimumWidth(560)
        self._alive = True
        self._thread = None
        self._worker = None
        self._connections = []
        self.flashed_port = ""             # read by the caller to chain into Set Up

        root = QVBoxLayout(self)
        root.addWidget(_note(
            "Puts the gBridge firmware on a new board. You only need this once per "
            "board — afterwards use <b>Set Up a Board</b> to give it the lab Wi-Fi.<br><br>"
            "Re-flashing an existing board is safe: its id, Wi-Fi and pairing are stored "
            "in a separate area of flash that is not touched."))

        form = QFormLayout()
        self.port = QComboBox()
        row = QHBoxLayout()
        row.addWidget(self.port, 1)
        self.rescan = QPushButton("Rescan")
        self.rescan.clicked.connect(self.scan)
        row.addWidget(self.rescan)
        form.addRow("Board on", row)

        self.chip = QLabel("—")
        form.addRow("Chip", self.chip)
        self.firmware = QLabel("—")
        form.addRow("Firmware", self.firmware)
        root.addLayout(form)

        self.bar = QProgressBar()
        self.bar.setRange(0, 0)            # indeterminate: esptool's own % is not parsed
        self.bar.setVisible(False)
        root.addWidget(self.bar)

        self.status = _note("")
        root.addWidget(self.status)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.flash_btn = QPushButton("Flash")
        self.flash_btn.setDefault(True)
        self.buttons.addButton(self.flash_btn, QDialogButtonBox.AcceptRole)
        self.flash_btn.clicked.connect(self._flash)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)

        self.port.currentIndexChanged.connect(self._port_changed)
        self.scan()

    # ---------------------------------------------------------------- scanning

    def scan(self) -> None:
        if not bf.esptool_available():
            # Say this once, plainly, instead of letting every flash fail with a
            # traceback. esptool is a pure-Python package — no toolchain — so the fix
            # really is a single pip install.
            self._busy(False, "")
            self.status.setText(
                "<b>esptool is not installed.</b> It is a small Python package and "
                "needs no toolchain:<br><tt>pip install esptool</tt>")
            self.flash_btn.setEnabled(False)
            return
        self._busy(True, "looking for boards…")
        self._run(_Detect(), self._scanned)

    def _scanned(self, ports: list, chip: str) -> None:
        if not self._alive:
            return
        self.port.blockSignals(True)
        self.port.clear()
        for p in ports:
            self.port.addItem(p.label, p.device)
        self.port.blockSignals(False)
        if not ports:
            self._busy(False, "")
            self.status.setText(
                "No board found. Plug one in with a <b>data</b> USB cable — charge-only "
                "cables carry no data lines and look exactly like a dead board.")
            self.flash_btn.setEnabled(False)
            return
        self._busy(False, "")
        self._port_changed()

    def _port_changed(self) -> None:
        """Identify the chip, then show which firmware would be written to it."""
        if not self._alive or self.port.count() == 0:
            return
        self._busy(True, "asking the board what it is…")
        self._run(_Detect(self.port.currentData()), self._identified)

    def _identified(self, _ports: list, chip: str) -> None:
        if not self._alive:
            return
        self._busy(False, "")
        if not chip:
            self.chip.setText("could not tell")
            self.firmware.setText("—")
            self.status.setText(
                "The board did not answer. Hold <b>BOOT</b>, tap <b>RESET</b>, release "
                "BOOT, then press Rescan — some boards cannot be reset over USB alone.")
            self.flash_btn.setEnabled(False)
            return
        self.chip.setText(chip)
        fw = bf.available(chip)
        if fw is None:
            have = ", ".join(bf.available_targets()) or "none"
            self.firmware.setText("not available")
            self.status.setText(
                f"No firmware is shipped for <b>{chip}</b> (available: {have}). "
                f"Build one with <tt>GINI32_TARGET={chip} ./gini32 build</tt>.")
            self.flash_btn.setEnabled(False)
            return
        self._fw = fw
        self.firmware.setText(f"{fw.build or 'gbridge'} · {fw.total_bytes // 1024} KB")
        self.status.setText("")
        self.flash_btn.setEnabled(True)

    # ---------------------------------------------------------------- flashing

    def _flash(self) -> None:
        fw = getattr(self, "_fw", None)
        if fw is None or self.port.count() == 0:
            return
        port = self.port.currentData()
        self.bar.setVisible(True)
        self._busy(True, "flashing — do not unplug the board…")
        self.flashed_port = port
        self._run(_Flash(port, fw), self._flashed)

    def _worker_progress(self, message: str) -> None:
        if self._alive:
            self.status.setText(message)

    def _flashed(self, ok: bool, msg: str) -> None:
        if not self._alive:
            return
        self.bar.setVisible(False)
        self._busy(False, "")
        if not ok:
            self.flashed_port = ""
            QMessageBox.warning(self, "Could not flash the board", msg)
            self.status.setText(msg)
            return
        QMessageBox.information(
            self, "Board flashed",
            f"{msg}.\n\nNext: use Set Up a Board to give it the lab Wi-Fi and an id.")
        self.accept()

    def _worker_failed(self, message: str) -> None:
        if not self._alive:
            return
        self.bar.setVisible(False)
        self._busy(False, "")
        self.status.setText(message)

    # ------------------------------------------------------------------- utils

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
        self.flash_btn.setEnabled(not busy)
        self.setCursor(Qt.WaitCursor if busy else Qt.ArrowCursor)
