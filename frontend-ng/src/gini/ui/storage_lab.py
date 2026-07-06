"""Storage Lab — the visual file-system workbench for an xv6 Machine.

Reads the FS state (domain.xv6_fs) and shows it four ways: the on-disk region MAP (boot | super
| log | inodes | bitmap | data), the INODES and the directory tree they realize, the BUFFER
CACHE with its hit rate, and the write-ahead LOG — where you can Simulate a write and watch a
transaction fill, commit, and install, which is how xv6 survives a crash. Renders from an
injected provider (offline DemoDisk here; a GDB-backed reader on the Mac later).
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..domain.xv6_fs import DemoDisk
from .theme import ThemeManager, icons

_REGION_ACCENT = {"boot": "slate", "super": "blue", "log": "amber", "inodes": "green",
                  "bitmap": "purple", "data": "cyan"}
_ITYPE_ACCENT = {"dir": "blue", "file": "green", "dev": "amber", "free": "slate"}


class DiskStrip(QWidget):
    """The on-disk region map — one labelled segment per region, widths weighted by block count
    (with a floor so the tiny regions stay visible next to the huge data region)."""

    def __init__(self, theme) -> None:
        super().__init__()
        self.theme = theme
        self._regions = []
        self.setMinimumHeight(60)

    def set_regions(self, regions) -> None:
        self._regions = list(regions)
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        t = self.theme.theme
        p.fillRect(self.rect(), QColor(t.panel2))
        if not self._regions:
            return
        floor = 0.06
        raw = [max(r.count, 1) for r in self._regions]
        tot = sum(raw)
        weights = [max(v / tot, floor) for v in raw]
        wsum = sum(weights)
        x = 0.0
        W = self.width()
        for r, w in zip(self._regions, weights):
            seg = W * (w / wsum)
            col = QColor(t.accent_for(_REGION_ACCENT.get(r.name, "slate")))
            p.fillRect(int(x), 6, int(seg) - 2, self.height() - 28, col)
            p.setPen(QColor(t.text))
            p.drawText(int(x) + 4, 22, r.name)
            p.setPen(QColor(t.muted))
            rng = f"{r.start}" if r.count <= 1 else f"{r.start}–{r.end}"
            p.drawText(int(x) + 4, self.height() - 8, rng)
            x += seg


class StorageLab(QDialog):
    def __init__(self, parent, theme: ThemeManager, device=None, provider=None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.device = device
        self.provider = provider or DemoDisk()

        t = theme.theme
        self.setWindowTitle(f"Storage Lab — {getattr(device, 'name', 'xv6')}")
        self.resize(920, 720)
        self.setStyleSheet(f"QDialog{{background:{t.bg};}}")
        root = QVBoxLayout(self)
        self._build_header(root)

        self._strip = DiskStrip(theme)
        root.addWidget(self._panel("On-disk layout  ·  block regions", self._strip))

        grid = QGridLayout(); grid.setSpacing(10); root.addLayout(grid, 1)
        self._inode_tbl = self._table(["inum", "type", "nlink", "size", "blocks"])
        grid.addWidget(self._panel("Inodes", self._inode_tbl, fill=True), 0, 0)
        self._tree_tbl = self._table(["", "inum", "name"])
        grid.addWidget(self._panel("Directory tree", self._tree_tbl, fill=True), 0, 1)
        self._buf_tbl = self._table(["block", "valid", "dirty", "ref"])
        self._buf_panel = self._panel("Buffer cache", self._buf_tbl, fill=True)
        grid.addWidget(self._buf_panel, 1, 0)
        grid.addWidget(self._build_log_panel(), 1, 1)
        grid.setColumnStretch(0, 1); grid.setColumnStretch(1, 1)

        self._render(self.provider.snapshot())

    # -- header/panels ---------------------------------------------------- #
    def _build_header(self, root) -> None:
        t = self.theme.theme
        head = QHBoxLayout()
        ic = QLabel(); ic.setPixmap(icons.render_pixmap("database", t.accent_for("cyan"), 22))
        title = QLabel(f"  File system — {getattr(self.device, 'name', 'xv6')}")
        title.setStyleSheet(f"color:{t.text};font-size:16px;font-weight:600;")
        head.addWidget(ic); head.addWidget(title); head.addStretch(1)
        root.addLayout(head)
        hint = QLabel("The on-disk regions, the inodes and the tree they build, the buffer "
                      "cache, and the write-ahead log that makes writes crash-safe.")
        hint.setWordWrap(True); hint.setStyleSheet(f"color:{t.muted};font-size:11px;")
        root.addWidget(hint)

    def _panel(self, title, inner, fill=False) -> QFrame:
        t = self.theme.theme
        f = QFrame(); f.setStyleSheet(
            f"QFrame{{background:{t.panel2};border:1px solid {t.line};border-radius:10px;}}")
        v = QVBoxLayout(f); v.setContentsMargins(10, 8, 10, 10)
        h = QLabel(title); h.setStyleSheet(
            f"color:{t.muted};font-size:11px;font-weight:600;border:none;")
        v.addWidget(h)
        inner.setStyleSheet((inner.styleSheet() or "") + "border:none;")
        v.addWidget(inner, 1 if fill else 0)
        return f

    def _table(self, cols) -> QTableWidget:
        t = self.theme.theme
        tbl = QTableWidget(0, len(cols))
        tbl.setHorizontalHeaderLabels(cols)
        tbl.verticalHeader().setVisible(False)
        tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        tbl.setSelectionMode(QTableWidget.NoSelection)
        tbl.horizontalHeader().setSectionResizeMode(len(cols) - 1, QHeaderView.Stretch)
        tbl.setStyleSheet(
            f"QTableWidget{{background:{t.panel};color:{t.text};border:none;"
            f"gridline-color:{t.line};font-size:12px;}}"
            f"QHeaderView::section{{background:{t.panel2};color:{t.muted};border:none;"
            "padding:4px;}")
        return tbl

    def _build_log_panel(self) -> QFrame:
        t = self.theme.theme
        f = QFrame(); f.setStyleSheet(
            f"QFrame{{background:{t.panel2};border:1px solid {t.line};border-radius:10px;}}")
        v = QVBoxLayout(f); v.setContentsMargins(10, 8, 10, 10)
        top = QHBoxLayout()
        h = QLabel("Write-ahead log  ·  journal"); h.setStyleSheet(
            f"color:{t.muted};font-size:11px;font-weight:600;border:none;")
        top.addWidget(h); top.addStretch(1)
        self._log_phase = QLabel(); self._log_phase.setStyleSheet(
            f"color:{t.text};background:{t.panel};border:1px solid {t.line};"
            "border-radius:9px;padding:2px 10px;font-size:11px;")
        top.addWidget(self._log_phase)
        v.addLayout(top)
        self._log_body = QLabel(); self._log_body.setWordWrap(True); self._log_body.setAlignment(Qt.AlignTop)
        self._log_body.setStyleSheet(f"color:{t.text};font-family:monospace;font-size:12px;border:none;")
        v.addWidget(self._log_body, 1)
        live = not hasattr(self.provider, "simulate_write")   # live reader can't fake a txn
        btn = QPushButton("  Refresh" if live else "  Simulate write")
        btn.setToolTip("Re-read the live file-system state (launch `writer` from the scheduler "
                       "window to make real log transactions)" if live else
                       "Advance a simulated write-ahead-log transaction")
        btn.setIcon(icons.icon("save", t.accent_for("green"), 14))
        btn.clicked.connect(self._on_write)
        btn.setStyleSheet(
            f"QPushButton{{color:{t.text};background:{t.panel};border:1px solid {t.line};"
            f"border-radius:8px;padding:6px 12px;}}QPushButton:hover{{border-color:{t.accent};}}")
        v.addWidget(btn)
        return f

    # -- actions ---------------------------------------------------------- #
    def _on_write(self) -> None:
        fn = getattr(self.provider, "simulate_write", None)
        self._render(fn() if callable(fn) else self.provider.snapshot())

    def _render(self, snap) -> None:
        t = self.theme.theme
        self._strip.set_regions(snap.regions)
        # inodes
        self._inode_tbl.setRowCount(len(snap.inodes))
        for r, ino in enumerate(snap.inodes):
            vals = [str(ino.inum), ino.type, str(ino.nlink), str(ino.size), str(len(ino.blocks))]
            for c, val in enumerate(vals):
                it = QTableWidgetItem(val)
                if c == 1:
                    it.setForeground(QColor(t.accent_for(_ITYPE_ACCENT.get(ino.type, "slate"))))
                self._inode_tbl.setItem(r, c, it)
        # tree
        self._tree_tbl.setRowCount(len(snap.tree))
        for r, d in enumerate(snap.tree):
            leaf = d.name.split("/")[-1]
            name = ("    " * d.depth) + leaf + ("/" if d.is_dir else "")
            for c, val in enumerate(["", str(d.inum), name]):
                self._tree_tbl.setItem(r, c, QTableWidgetItem(val))
        # buffer cache
        self._buf_tbl.setRowCount(len(snap.bufs))
        for r, b in enumerate(snap.bufs):
            for c, val in enumerate([str(b.blockno), "✓" if b.valid else "",
                                     "●" if b.dirty else "", str(b.refcnt)]):
                it = QTableWidgetItem(val)
                if c == 2 and b.dirty:
                    it.setForeground(QColor(t.accent_for("amber")))
                self._buf_tbl.setItem(r, c, it)
        self._buf_panel.findChild(QLabel).setText(
            f"Buffer cache  ·  {snap.hits} hits / {snap.misses} miss "
            f"({snap.hit_rate * 100:.0f}% hit)")
        # log
        lg = snap.log
        self._log_phase.setText(lg.phase)
        col = {"idle": t.muted, "building": t.accent_for("amber"),
               "committing": t.accent_for("green")}.get(lg.phase, t.muted)
        self._log_phase.setStyleSheet(
            f"color:{col};background:{t.panel};border:1px solid {t.line};"
            "border-radius:9px;padding:2px 10px;font-size:11px;")
        if lg.blocks:
            self._log_body.setText(
                f"transaction: {len(lg.blocks)} block(s) staged in the log\n"
                f"dest blocks: {', '.join(map(str, lg.blocks))}\n"
                + ("committing → installing to their home locations…" if lg.committing
                   else "logged but not yet committed (a crash here loses nothing)."))
        else:
            self._log_body.setText("log idle — no transaction in flight.\n"
                                   "Press “Simulate write” to stage one and watch it commit.")
