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
from .theme.manager import scale_css as _scss

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
        self._killing: set = set()      # pids with a kill in flight -> show a pending state
        self.setColumnCount(4)
        self.setHeaderLabels(["process", "pid", "state", ""])
        self.setRootIsDecorated(True)
        self.setIndentation(16)
        self.setSelectionMode(QTreeWidget.NoSelection)
        self.setFocusPolicy(Qt.NoFocus)
        self.setColumnWidth(1, 48)
        self.setColumnWidth(3, 84)
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

    def set_procs(self, procs, running_pids=(), flags=None) -> None:
        """Rebuild the tree from the current process list. `running_pids` are highlighted; `flags`
        maps pid -> a short reason string (e.g. 'starving') that badges the process."""
        t = self.theme.theme
        running = set(running_pids or [])
        flags = flags or {}
        self._killing &= {p.pid for p in procs}   # drop pending marks for procs that are now gone
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
            if p.pid in flags:                        # scheduling badge (e.g. starvation)
                reason = flags[p.pid]
                it.setText(2, f"{p.state}  ⚠ {reason}")
                it.setForeground(2, QColor(t.accent_for("red")))
                it.setToolTip(2, reason)
            items[p.pid] = it
            if self._live and p.pid > 2:          # kill affordance (never init/sh)
                self.setItemWidget(it, 3, self._kill_button(p.pid))
        self.expandAll()

    def _kill_button(self, pid: int) -> QPushButton:
        """A proper labelled Kill button (the old bare ✕ was easy to miss), with a persistent
        'killing…' pending state so the click visibly registers even while the kill lands."""
        t = self.theme.theme
        pending = pid in self._killing
        red = t.accent_for("red")
        b = QPushButton("killing…" if pending else "Kill")
        b.setFixedHeight(20)
        b.setToolTip(f"kill pid {pid}")
        b.setCursor(Qt.PointingHandCursor)
        if pending:
            b.setEnabled(False)
            b.setStyleSheet(_scss(
                f"QPushButton{{color:{t.faint};background:transparent;border:1px solid {t.line};"
                "border-radius:5px;padding:0 8px;font-size:11px;}"))
        else:
            b.setStyleSheet(_scss(
                f"QPushButton{{color:{red};background:transparent;border:1px solid {red};"
                "border-radius:5px;padding:0 8px;font-size:11px;font-weight:600;}"
                f"QPushButton:hover{{color:#ffffff;background:{red};}}"))
            b.clicked.connect(lambda _c=False, p=pid: self._on_kill_clicked(p))
        return b

    def _on_kill_clicked(self, pid: int) -> None:
        self._killing.add(pid)                 # mark pending; survives the ~0.5s tree rebuilds
        it = self._find_item(pid)
        if it is not None:                     # flip THIS button to the pending state immediately
            self.setItemWidget(it, 3, self._kill_button(pid))
        self.kill_requested.emit(pid)

    def _find_item(self, pid: int):
        def walk(it):
            if it.text(1) == str(pid):
                return it
            for i in range(it.childCount()):
                r = walk(it.child(i))
                if r is not None:
                    return r
            return None
        for i in range(self.topLevelItemCount()):
            r = walk(self.topLevelItem(i))
            if r is not None:
                return r
        return None
