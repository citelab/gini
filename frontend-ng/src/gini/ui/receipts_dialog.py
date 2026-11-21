"""Course → My Receipts: every receipt handed in from this computer, to read again or copy.

The "Handed in" dialog was the only place a receipt was ever shown, and students closed it without
writing the code down. The receipt is what the instructor looks work up by, so a student without it
had handed in something nobody could find — and asked the instructor, who could not find it either.

A table rather than "the last receipt": a student in a course hands in several labs, and the one
they are asked about is not always the latest. The lab CODE is on each row because that is the
thing they still have — it is on their course page — and it is how they tell the rows apart.
"""
from __future__ import annotations

import time

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..services import receipts as _receipts

SENT = "Sent"
#: Said in full because the student holding this row is the one who needs the next step.
WAITING = "Not sent yet — Course → Resubmit to Course Server"

EMPTY = ("Nothing has been handed in from this computer yet.\n\n"
         "A receipt appears here once you press Hand in. If you handed in from another computer, "
         "its receipt is on that computer.")


def _when(t: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t else ""


class ReceiptsDialog(QDialog):
    """Lists `services.receipts.handed_in()`; the selected row's receipt is one click to copy."""

    COLUMNS = ("Receipt", "Lab code", "Handed in", "Status")

    def __init__(self, parent=None, *, rows: list[dict] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("My Receipts")
        self.resize(720, 360)
        self.rows = rows if rows is not None else _receipts.handed_in()

        lay = QVBoxLayout(self)
        self.intro = QLabel(
            "Every lab handed in from this computer, newest first. Give your instructor the "
            "receipt — it is how they find your work.")
        self.intro.setWordWrap(True)
        lay.addWidget(self.intro)

        self.table = QTableWidget(len(self.rows), len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        mono = self.table.font()
        mono.setFamily("Menlo")
        mono.setStyleHint(mono.StyleHint.Monospace)
        for i, r in enumerate(self.rows):
            cells = (r["receipt"], r["code"], _when(r["when"]), SENT if r["sent"] else WAITING)
            for j, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setToolTip(r.get("path", ""))
                if j < 2:
                    item.setFont(mono)
                self.table.setItem(i, j, item)
        hdr = self.table.horizontalHeader()
        for j in range(len(self.COLUMNS) - 1):
            hdr.setSectionResizeMode(j, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(len(self.COLUMNS) - 1, QHeaderView.ResizeMode.Stretch)
        self.table.doubleClicked.connect(lambda _i: self.copy())
        lay.addWidget(self.table, 1)

        self.empty = QLabel(EMPTY)
        self.empty.setWordWrap(True)
        lay.addWidget(self.empty)
        self.table.setVisible(bool(self.rows))
        self.empty.setVisible(not self.rows)

        row = QHBoxLayout()
        self.status = QLabel("")
        row.addWidget(self.status, 1)
        self.copy_btn = QPushButton("Copy Receipt")
        self.copy_btn.clicked.connect(self.copy)
        self.copy_btn.setEnabled(bool(self.rows))
        row.addWidget(self.copy_btn)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        row.addWidget(close)
        lay.addLayout(row)

        if self.rows:
            self.table.selectRow(0)          # the latest is the one most often asked for
            self.copy_btn.setDefault(True)

    def selected(self) -> dict | None:
        i = self.table.currentRow()
        return self.rows[i] if 0 <= i < len(self.rows) else None

    def copy(self) -> str:
        """Put the selected receipt on the clipboard, and say so — a copy is otherwise invisible."""
        r = self.selected()
        if r is None:
            return ""
        QGuiApplication.clipboard().setText(r["receipt"])
        self.status.setText(f"Copied {r['receipt']}.")
        return r["receipt"]
