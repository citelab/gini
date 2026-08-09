"""Live process tree for the Machine Lab — the process hierarchy as a tree, not a flat table.

Built from each process's parent link (ppid, from gini_dump). fork() adds a child, exec() relabels,
exit() shows a zombie, wait() reaps it, and an exiting parent's children re-parent to init — all
visible as the tree changes. State is colour-coded; the running process(es) are highlighted; user
processes get a kill affordance. Driven entirely by the live serial dump (no gdb).
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QHeaderView, QPushButton, QTreeWidget, QTreeWidgetItem

from ..domain.xv6 import build_process_tree
from .theme import ThemeManager

_STATE_ACCENT = {"running": "green", "runnable": "amber", "sleeping": "slate",
                 "zombie": "red", "used": "slate", "unused": "slate"}


class ProcessTree(QTreeWidget):
    """A QTreeWidget rendering of the process hierarchy. `kill_requested(pid)` fires when the
    user clicks a process's kill affordance (never shown for init/sh)."""

    kill_requested = Signal(int)

    def __init__(self, theme: ThemeManager) -> None:
        super().__init__()
        self.theme = theme
        self._live = False
        self.setColumnCount(4)
        self.setHeaderLabels(["process", "pid", "state", ""])
        self.setRootIsDecorated(True)
        self.setIndentation(16)
        self.setSelectionMode(QTreeWidget.NoSelection)
        self.setFocusPolicy(Qt.NoFocus)
        self.setColumnWidth(1, 48)
        self.setColumnWidth(3, 40)
        self.header().setSectionResizeMode(0, QHeaderView.Stretch)
        t = theme.theme
        self.setStyleSheet(
            f"QTreeWidget{{background:{t.panel};color:{t.text};border:none;font-size:12px;"
            f"outline:none;}}"
            f"QHeaderView::section{{background:{t.panel2};color:{t.muted};border:none;"
            "padding:4px;}"
            f"QTreeWidget::item{{padding:2px 0;}}")

    def set_live(self, live: bool) -> None:
        self._live = live

    def set_procs(self, procs, running_pids=()) -> None:
        """Rebuild the tree from the current process list. `running_pids` are highlighted."""
        t = self.theme.theme
        running = set(running_pids or [])
        self.clear()
        items: dict = {}          # pid -> QTreeWidgetItem
        for node in build_process_tree(procs):
            p = node.proc
            parent_item = items.get(p.parent)
            it = QTreeWidgetItem(parent_item if parent_item is not None else self)
            it.setText(0, p.name)
            it.setText(1, str(p.pid))
            it.setText(2, p.state)
            color = QColor(t.accent_for(_STATE_ACCENT.get(p.state, "slate")))
            it.setForeground(2, color)
            if p.pid in running:
                it.setForeground(0, color)
                f = it.font(0); f.setBold(True); it.setFont(0, f)
            elif p.state == "zombie":
                for c in (0, 1, 2):
                    it.setForeground(c, QColor(t.faint))
            items[p.pid] = it
            if self._live and p.pid > 2:          # kill affordance (never init/sh)
                b = QPushButton("✕")
                b.setToolTip(f"kill pid {p.pid}")
                b.setFixedSize(26, 20)
                b.setStyleSheet(
                    f"QPushButton{{color:{t.muted};background:transparent;border:none;}}"
                    f"QPushButton:hover{{color:{t.accent_for('red')};}}")
                b.clicked.connect(lambda _c=False, pid=p.pid: self.kill_requested.emit(pid))
                self.setItemWidget(it, 3, b)
        self.expandAll()
