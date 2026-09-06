"""Storage Lab — the visual file-system workbench for an xv6 Machine.

Reads the FS state (domain.xv6_fs) and shows it four ways: the on-disk region MAP (boot | super
| log | inodes | bitmap | data), the INODES and the directory tree they realize, the BUFFER
CACHE with its hit rate, and the write-ahead LOG — where you can Simulate a write and watch a
transaction fill, commit, and install, which is how xv6 survives a crash. Renders from an
injected provider (offline DemoDisk here; a GDB-backed reader on the Mac later).
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..domain.xv6_fs import DemoDisk
from .live_poll import LivePollMixin
from .no_data import ABSENT, paint_placeholder, panel_state, placeholder_for
from .theme import ThemeManager, icons
from .theme.manager import scale_css as _scss

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


class CacheGrid(QWidget):
    """The buffer cache as a grid of cells — one per buffer, laid out like the array it is.

    A table of numbers cannot show a replacement POLICY; motion can. Fill = recency heat (bright
    = just used, fading to cold), a ring = the buffer is in use (never a legal victim), and a
    recycled buffer FLASHES red for a moment. Run `writer` with an LRU shadow and then with a
    random one and the difference is visible without reading a single number.
    """

    FLASH_MS = 1200

    def __init__(self, theme) -> None:
        super().__init__()
        self.theme = theme
        self._bufs: list = []
        self._prev: dict = {}                 # index -> blockno, to spot a recycle
        self._flash: dict = {}                # index -> monotonic time of the last eviction
        self._note = ""                       # why there is nothing to draw, when there isn't
        self.setMinimumHeight(96)

    def set_bufs(self, bufs, note: str = "") -> None:
        import time
        self._note = note
        now = time.monotonic()
        for b in bufs:                        # a slot whose block changed was just recycled
            was = self._prev.get(b.index)
            if was is not None and was != b.blockno:
                self._flash[b.index] = now
            self._prev[b.index] = b.blockno
        self._bufs = list(bufs)
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        import time
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        t = self.theme.theme
        p.fillRect(self.rect(), QColor(t.panel))
        if not self._bufs:
            paint_placeholder(p, self.rect(), t, self._note or placeholder_for(
                ABSENT, "buffer cache"))
            return
        now = time.monotonic()
        n = len(self._bufs)
        cols = max(1, min(10, n))
        rows = (n + cols - 1) // cols
        cw = max(18, (self.width() - 12) // cols)
        ch = max(18, min(34, (self.height() - 12) // max(rows, 1)))
        # recency is relative: the newest stamp is "hot", the oldest "cold"
        stamps = [b.lastuse for b in self._bufs if b.lastuse] or [0]
        lo, hi = min(stamps), max(stamps)
        span = (hi - lo) or 1
        warm = QColor(t.accent_for("cyan"))
        for i, b in enumerate(self._bufs):
            x = 6 + (i % cols) * cw
            y = 6 + (i // cols) * ch
            r = QRectF(x, y, cw - 4, ch - 4)
            heat = ((b.lastuse - lo) / span) if b.lastuse else 0.0
            fill = QColor(warm)
            fill.setAlpha(int(40 + 180 * heat) if b.valid else 24)
            flashed = now - self._flash.get(b.index, -99) < self.FLASH_MS / 1000.0
            p.setBrush(QColor(t.accent_for("red")) if flashed else fill)
            p.setPen(QPen(QColor(t.accent_for("amber") if b.in_use else t.line),
                          2 if b.in_use else 1))
            p.drawRoundedRect(r, 4, 4)
            p.setPen(QColor(t.text if (heat > 0.5 or flashed) else t.muted))
            p.setFont(QFont(self.font().family(), 8))
            p.drawText(r, Qt.AlignCenter, str(b.blockno))


class BlockMap(QWidget):
    """The disk's data region as a live free/used map, drawn from the on-disk bitmap.

    Fragmentation is a *shape*, not a number: a first-fit allocator leaves speckle as files are
    created and deleted, while a locality-aware policy keeps a file's blocks in solid runs. Each
    cell aggregates several blocks (a 2000-block disk would be unreadable one cell per block), so
    a partly-used group shades proportionally. The newest allocation is ringed, which makes the
    allocator's choice visible the moment it happens.
    """

    def __init__(self, theme) -> None:
        super().__init__()
        self.theme = theme
        self._used: list = []
        self._last = 0
        self._note = ""                       # why there is nothing to draw, when there isn't
        self.setMinimumHeight(70)

    def set_map(self, used, last_alloc=0, note: str = "") -> None:
        self._note = note
        self._used = list(used or [])
        self._last = int(last_alloc or 0)
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        t = self.theme.theme
        p.fillRect(self.rect(), QColor(t.panel))
        n = len(self._used)
        if not n:
            paint_placeholder(p, self.rect(), t, self._note or placeholder_for(
                ABSENT, "block map"))
            return
        cols = max(1, self.width() // 9)
        rows = max(1, min(6, (n + cols - 1) // cols))
        per = max(1, (n + cols * rows - 1) // (cols * rows))     # blocks aggregated per cell
        cw = self.width() / cols
        ch = (self.height() - 6) / rows
        base = QColor(t.accent_for("cyan"))
        for c in range(cols * rows):
            lo = c * per
            if lo >= n:
                break
            grp = self._used[lo:lo + per]
            frac = sum(1 for u in grp if u) / len(grp)
            x, y = (c % cols) * cw, 3 + (c // cols) * ch
            col = QColor(base)
            col.setAlpha(int(25 + 205 * frac))                   # solid = fully allocated
            p.fillRect(QRectF(x, y, cw - 1, ch - 1), col)
            if lo <= self._last < lo + per:                      # the block just handed out
                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(QColor(t.accent_for("amber")), 2))
                p.drawRect(QRectF(x, y, cw - 1, ch - 1))


#: One dump a round (/fs), but it is the largest of them — so a slower cadence than the Memory
#: face despite doing less work. A constant so a term of use can tune it from one place.
STORAGE_POLL_MS = 2000


class StorageLab(LivePollMixin, QDialog):
    #: Carries (payload, ok) from the poll worker to the GUI thread. See live_poll.
    snap_ready = Signal(object)

    def __init__(self, parent, theme: ThemeManager, device=None, provider=None,
                 state=None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.device = device
        self.provider = provider or DemoDisk()
        self.state = state                    # MachineState: one cache, one lock, every reader
        self._live = not hasattr(self.provider, "simulate_write")   # can't fake a txn on a real fs

        t = theme.theme
        self.setWindowTitle(f"File System Lab — {getattr(device, 'name', 'xv6')}")
        self.resize(920, 720)
        self.setStyleSheet(f"QDialog{{background:{t.bg};}}")
        root = QVBoxLayout(self)
        self._build_header(root)

        self._strip = DiskStrip(theme)
        root.addWidget(self._panel("On-disk layout  ·  block regions", self._strip))
        # S4: the data region as a live free/used map — fragmentation as a picture
        mapbox = QWidget(); mapcol = QVBoxLayout(mapbox)
        mapcol.setContentsMargins(0, 0, 0, 0); mapcol.setSpacing(4)
        self._block_map = BlockMap(theme)
        mapcol.addWidget(self._block_map)
        self._alloc_lbl = QLabel(); self._alloc_lbl.setWordWrap(True)
        self._alloc_lbl.setStyleSheet(_scss(f"color:{t.muted};font-size:11px;"))
        mapcol.addWidget(self._alloc_lbl)
        root.addWidget(self._panel("Block allocator  ·  free / used map", mapbox))

        grid = QGridLayout(); grid.setSpacing(10); root.addLayout(grid, 1)
        self._inode_tbl = self._table(["inum", "type", "nlink", "size", "blocks"])
        grid.addWidget(self._panel("Inodes", self._inode_tbl, fill=True), 0, 0)
        self._tree_tbl = self._table(["", "inum", "name"])
        grid.addWidget(self._panel("Directory tree", self._tree_tbl, fill=True), 0, 1)
        # the cache as a GRID (policy is motion, not numbers) with the table beneath it
        bufbox = QWidget(); bufcol = QVBoxLayout(bufbox)
        bufcol.setContentsMargins(0, 0, 0, 0); bufcol.setSpacing(6)
        self._cache_grid = CacheGrid(theme)
        bufcol.addWidget(self._cache_grid)
        self._buf_tbl = self._table(["block", "ref", "valid", "last use"])
        bufcol.addWidget(self._buf_tbl, 1)
        self._buf_panel = self._panel("Buffer cache", bufbox, fill=True)
        grid.addWidget(self._buf_panel, 1, 0)
        grid.addWidget(self._build_log_panel(), 1, 1)
        grid.setColumnStretch(0, 1); grid.setColumnStretch(1, 1)

        self._render(self._snapshot())
        self._init_poll(STORAGE_POLL_MS, live=self._live)

    # -- the poll --------------------------------------------------------- #
    def _snapshot(self):
        """One read of the FS face, through MachineState when there is one."""
        if self.state is not None:
            return self.state.refresh_fs()
        return self.provider.snapshot()

    def _read(self):
        """OFF the GUI thread. One dump — /fs carries the superblock, log, bcache and block map."""
        return self._snapshot()

    def _render_live(self, snap, fresh: bool) -> None:
        if snap is None:
            self._chip.setText("no reading yet")
            return
        self._render(snap)
        self._chip.setText(self.poll_caption() + ("" if fresh else "  ·  stale"))

    def _on_pause(self, on: bool) -> None:
        self.set_paused(on)
        self._pause.setText("Resume" if on else "Pause")
        self._chip.setText(self.poll_caption())

    # -- header/panels ---------------------------------------------------- #
    def _build_header(self, root) -> None:
        t = self.theme.theme
        head = QHBoxLayout()
        ic = QLabel(); ic.setPixmap(icons.render_pixmap("database", t.accent_for("cyan"), 22))
        title = QLabel(f"  File System Lab — {getattr(self.device, 'name', 'xv6')}")
        title.setStyleSheet(_scss(f"color:{t.text};font-size:16px;font-weight:600;"))
        head.addWidget(ic); head.addWidget(title); head.addStretch(1)
        self._chip = QLabel("live")
        self._chip.setStyleSheet(_scss(f"color:{t.accent_for('green')};font-size:11px;"))
        self._pause = QPushButton("Pause"); self._pause.setCheckable(True)
        self._pause.setStyleSheet(
            f"QPushButton{{color:{t.text};background:{t.panel};border:1px solid {t.line};"
            f"border-radius:8px;padding:4px 10px;}}QPushButton:hover{{border-color:{t.accent};}}")
        self._pause.toggled.connect(self._on_pause)
        for w in (self._chip, self._pause):
            w.setVisible(self._live)
            head.addWidget(w)
        root.addLayout(head)
        hint = QLabel("The on-disk regions, the inodes and the tree they build, the buffer "
                      "cache, and the write-ahead log that makes writes crash-safe.")
        hint.setWordWrap(True); hint.setStyleSheet(_scss(f"color:{t.muted};font-size:11px;"))
        root.addWidget(hint)

    def _panel(self, title, inner, fill=False) -> QFrame:
        t = self.theme.theme
        f = QFrame(); f.setStyleSheet(
            _scss(f"QFrame{{background:{t.panel2};border:1px solid {t.line};border-radius:10px;}}"))
        v = QVBoxLayout(f); v.setContentsMargins(10, 8, 10, 10)
        h = QLabel(title); h.setStyleSheet(
            _scss(f"color:{t.muted};font-size:11px;font-weight:600;border:none;"))
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
            _scss(f"QFrame{{background:{t.panel2};border:1px solid {t.line};border-radius:10px;}}"))
        v = QVBoxLayout(f); v.setContentsMargins(10, 8, 10, 10)
        top = QHBoxLayout()
        h = QLabel("Write-ahead log  ·  journal"); h.setStyleSheet(
            _scss(f"color:{t.muted};font-size:11px;font-weight:600;border:none;"))
        top.addWidget(h); top.addStretch(1)
        self._log_phase = QLabel(); self._log_phase.setStyleSheet(
            f"color:{t.text};background:{t.panel};border:1px solid {t.line};"
            "border-radius:9px;padding:2px 10px;font-size:11px;")
        top.addWidget(self._log_phase)
        v.addLayout(top)
        self._log_body = QLabel(); self._log_body.setWordWrap(True); self._log_body.setAlignment(Qt.AlignTop)
        self._log_body.setStyleSheet(_scss(f"color:{t.text};font-family:monospace;font-size:12px;border:none;"))
        v.addWidget(self._log_body, 1)
        live = self._live
        btn = QPushButton("  Refresh now" if live else "  Simulate write")
        btn.setToolTip("Re-read the live file-system state (launch `writer` from the scheduler "
                       "window to make real log transactions)" if live else
                       "Advance a simulated write-ahead-log transaction")
        btn.setIcon(icons.icon("save", t.accent_for("green"), 14))
        # Live: kick the poll, which reads off the GUI thread rather than inline.
        btn.clicked.connect((lambda: self._tick()) if live else self._on_write)
        btn.setStyleSheet(
            f"QPushButton{{color:{t.text};background:{t.panel};border:1px solid {t.line};"
            f"border-radius:8px;padding:6px 12px;}}QPushButton:hover{{border-color:{t.accent};}}")
        v.addWidget(btn)
        return f

    # -- actions ---------------------------------------------------------- #
    def _on_write(self) -> None:
        fn = getattr(self.provider, "simulate_write", None)
        self._render(fn() if callable(fn) else self._snapshot())

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
        # S4: block allocator map + locality score
        self._block_map.set_map(getattr(snap, "block_used", []), getattr(snap, "last_alloc", 0),
                                placeholder_for(panel_state(snap, "blockmap"), "block map"))
        allocs, gap = getattr(snap, "allocs", 0), getattr(snap, "mean_gap", 0)
        if allocs:
            self._alloc_lbl.setText(
                f"{allocs} allocations  ·  mean gap {gap} blocks between consecutive ones "
                f"(lower = better locality = fewer seeks)  ·  last block {getattr(snap, 'last_alloc', 0)}")
        else:
            self._alloc_lbl.setText("no allocations yet — run `writer` to grow a file")
        # buffer cache — the grid shows the policy at work, the table the exact state
        # A container that is not answering is not a kernel too old to report — the two get
        # different sentences, so nobody rebuilds an image to fix a stopped machine.
        self._cache_grid.set_bufs(snap.bufs,
                                  placeholder_for(panel_state(snap, "bcache"), "buffer cache"))
        self._buf_tbl.setRowCount(len(snap.bufs))
        for r, b in enumerate(snap.bufs):
            for c, val in enumerate([str(b.blockno), str(b.refcnt),
                                     "✓" if b.valid else "", str(b.lastuse or "")]):
                it = QTableWidgetItem(val)
                if c == 1 and b.in_use:          # in use = cannot be evicted
                    it.setForeground(QColor(t.accent_for("amber")))
                self._buf_tbl.setItem(r, c, it)
        ev = getattr(snap, "evicts", 0)
        self._buf_panel.findChild(QLabel).setText(
            f"Buffer cache  ·  {snap.hits} hits / {snap.misses} miss "
            f"({snap.hit_rate * 100:.0f}% hit)  ·  {ev} evictions")
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
