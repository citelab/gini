"""Memory Lab — the visual virtual-memory workbench for an xv6 Machine.

Shows a process's address space three ways: the region MAP (text | data | heap | guard | stack …
trapframe | trampoline), the leaf PAGE-TABLE mappings (VA → PA with R/W/X/U permissions), and the
PHYSICAL page allocator (free vs used), plus a page-FAULT log. Press “Simulate page fault” to see
lazy/demand allocation grow the stack: a fault is recorded, a physical page is allocated, and a
new mapping appears. Renders from an injected provider (offline DemoVm here; GDB-backed on Mac).
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..domain.xv6_vm import PGSIZE, DemoVm, classify_faults, region_for, shared_frames
from .theme import ThemeManager, icons
from .theme.manager import scale_css as _scss

_REGION_ACCENT = {"text": "blue", "data": "green", "heap": "cyan", "guard": "slate",
                  "stack": "amber", "trapframe": "purple", "trampoline": "red"}
_FAULT_ACCENT = {"cow-write": "purple", "lazy-alloc": "green", "stack-growth": "amber",
                 "illegal": "red"}


class ResidentMeter(QWidget):
    """A two-tone bar: RESIDENT (physically-backed) pages filled solid inside the VIRTUAL extent,
    so lazy allocation reads as a gap that fills in — virtual jumps on sbrk, resident catches up
    one page per fault."""

    def __init__(self, theme) -> None:
        super().__init__()
        self.theme = theme
        self._res = 0
        self._virt = 0
        self.setMinimumHeight(22)

    def set_values(self, resident, virtual) -> None:
        self._res, self._virt = int(resident), int(virtual)
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        t = self.theme.theme
        p.fillRect(self.rect(), QColor(t.panel))
        if self._virt <= 0:
            return
        w = self.width()
        # the virtual extent (outline) then the resident fill inside it
        p.fillRect(0, 0, w, self.height(), QColor(t.panel2))
        frac = max(0.0, min(1.0, self._res / self._virt))
        p.fillRect(0, 0, int(w * frac), self.height(), QColor(t.accent_for("green")))


class RegionStrip(QWidget):
    """The address-space map — one labelled segment per region (width weighted by page count,
    with a floor so single-page regions like the trapframe stay visible)."""

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
        floor = 0.07
        raw = [min(max(r.pages, 1), 64) for r in self._regions]   # clamp huge stacks
        tot = sum(raw)
        weights = [max(v / tot, floor) for v in raw]
        wsum = sum(weights)
        x = 0.0
        W = self.width()
        for r, w in zip(self._regions, weights):
            seg = W * (w / wsum)
            p.fillRect(int(x), 6, int(seg) - 2, self.height() - 30,
                       QColor(t.accent_for(_REGION_ACCENT.get(r.name, "slate"))))
            p.setPen(QColor(t.text))
            p.drawText(int(x) + 4, 22, r.name)
            p.setPen(QColor(t.muted))
            p.drawText(int(x) + 4, self.height() - 8, r.perms or "")
            x += seg


class PhysBar(QWidget):
    """A used/free bar for the physical page allocator."""

    def __init__(self, theme) -> None:
        super().__init__()
        self.theme = theme
        self._frac = 0.0
        self.setMinimumHeight(26)

    def set_frac(self, frac) -> None:
        self._frac = max(0.0, min(1.0, frac))
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        t = self.theme.theme
        p.fillRect(self.rect(), QColor(t.panel))
        p.fillRect(0, 0, int(self.width() * self._frac), self.height(),
                   QColor(t.accent_for("amber")))


class MemoryLab(QDialog):
    def __init__(self, parent, theme: ThemeManager, device=None, provider=None,
                 on_play=None, play_games=None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.device = device
        self.provider = provider or DemoVm()
        self._on_play = on_play             # callable(game_id) opening a game; may be None
        self._play_games = play_games or []  # [(label, game_id)]

        t = theme.theme
        self.setWindowTitle(f"Virtual Memory Lab — {getattr(device, 'name', 'xv6')}")
        self.resize(920, 700)
        self.setStyleSheet(f"QDialog{{background:{t.bg};}}")
        root = QVBoxLayout(self)
        self._build_header(root)

        self._strip = RegionStrip(theme)
        root.addWidget(self._panel("Address space  ·  regions (low → high VA)", self._strip))

        grid = QGridLayout(); grid.setSpacing(10); root.addLayout(grid, 1)
        self._pt_tbl = self._table(["VA", "PA", "perms", "region"])
        grid.addWidget(self._panel("Page table  ·  leaf mappings", self._pt_tbl, fill=True), 0, 0, 2, 1)
        grid.addWidget(self._build_phys_panel(), 0, 1)
        grid.addWidget(self._build_faults_panel(), 1, 1)
        grid.setColumnStretch(0, 3); grid.setColumnStretch(1, 2)

        # the COW / sharing view + per-process resident-vs-virtual meters (uses all_procs())
        root.addWidget(self._build_sharing_panel())

        self._render(self.provider.snapshot())
        self._render_sharing()

    # -- header/panels ---------------------------------------------------- #
    def _build_header(self, root) -> None:
        t = self.theme.theme
        head = QHBoxLayout()
        ic = QLabel(); ic.setPixmap(icons.render_pixmap("layout", t.accent_for("purple"), 22))
        title = QLabel(f"  Virtual Memory Lab — {getattr(self.device, 'name', 'xv6')}")
        title.setStyleSheet(_scss(f"color:{t.text};font-size:16px;font-weight:600;"))
        head.addWidget(ic); head.addWidget(title); head.addStretch(1)
        if self._on_play is not None:                 # in-lab games (thrashing, translate)
            for label, gid in self._play_games:
                play = QPushButton(f"  Play: {label}")
                play.setStyleSheet(
                    f"QPushButton{{color:{t.accent_for('purple')};background:{t.panel2};"
                    f"border:1px solid {t.line};border-radius:8px;padding:5px 11px;}}"
                    f"QPushButton:hover{{border-color:{t.accent};}}")
                play.clicked.connect(lambda _c=False, g=gid: self._on_play(g))
                head.addWidget(play)
        self._satp = QLabel(); self._satp.setStyleSheet(
            _scss(f"color:{t.muted};font-family:monospace;font-size:11px;"))
        head.addWidget(self._satp)
        root.addLayout(head)
        hint = QLabel("The process address space, its leaf page-table mappings (VA→PA with "
                      "R/W/X/U), and the physical allocator. Simulate a fault to watch demand "
                      "paging grow the stack.")
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
            f"gridline-color:{t.line};font-size:12px;font-family:monospace;}}"
            f"QHeaderView::section{{background:{t.panel2};color:{t.muted};border:none;"
            "padding:4px;font-family:sans-serif;}")
        return tbl

    def _build_phys_panel(self) -> QFrame:
        t = self.theme.theme
        f, v = self._framed("Physical memory  ·  page allocator")
        self._phys_bar = PhysBar(self.theme)
        v.addWidget(self._phys_bar)
        self._phys_lbl = QLabel(); self._phys_lbl.setStyleSheet(
            _scss(f"color:{t.text};font-size:12px;border:none;"))
        v.addWidget(self._phys_lbl)
        v.addStretch(1)
        return f

    def _build_faults_panel(self) -> QFrame:
        t = self.theme.theme
        f, v = self._framed("Page faults  ·  live ring (classified)")
        self._fault_tbl = self._table(["pid", "VA", "cause", "kind"])
        v.addWidget(self._fault_tbl, 1)
        self._live = not hasattr(self.provider, "simulate_fault")   # live reader can't fake a fault
        btn = QPushButton("  Refresh" if self._live else "  Simulate page fault")
        btn.setToolTip("Re-read the live fault ring + page tables (launch `alloc` from the "
                       "scheduler window to make real faults)" if self._live else
                       "Grow the stack by one page via a simulated demand fault")
        btn.setIcon(icons.icon("send", t.accent_for("amber"), 14))
        btn.clicked.connect(self._on_fault)
        btn.setStyleSheet(self._btn_css())
        v.addWidget(btn)
        return f

    def _btn_css(self) -> str:
        t = self.theme.theme
        return (f"QPushButton{{color:{t.text};background:{t.panel};border:1px solid {t.line};"
                f"border-radius:8px;padding:6px 12px;}}QPushButton:hover{{border-color:{t.accent};}}")

    def _build_sharing_panel(self) -> QFrame:
        """The COW / physical-sharing view: each process's resident-vs-virtual meter, plus the
        physical pages mapped by more than one process (shared until one writes)."""
        t = self.theme.theme
        f, v = self._framed("Copy-on-write  ·  processes & physically-shared pages")
        row = QHBoxLayout(); v.addLayout(row, 1)
        # left: per-process resident/virtual meters
        left = QWidget(); self._proc_box = QVBoxLayout(left); self._proc_box.setSpacing(4)
        self._proc_meters: dict = {}
        lw = QWidget(); lv = QVBoxLayout(lw); lv.setContentsMargins(0, 0, 0, 0)
        cap = QLabel("resident (green) within virtual (sz)")
        cap.setStyleSheet(_scss(f"color:{t.faint};font-size:10px;border:none;"))
        lv.addWidget(cap); lv.addWidget(left); lv.addStretch(1)
        row.addWidget(lw, 2)
        # right: shared physical frames
        self._share_tbl = self._table(["phys page", "shared by", "cow"])
        row.addWidget(self._panel("Shared physical pages", self._share_tbl, fill=True), 3)
        # a COW-write button in demo mode (make the child copy its shared page)
        if hasattr(self.provider, "simulate_cow_write"):
            b = QPushButton("  Simulate COW write (child writes shared page)")
            b.setIcon(icons.icon("send", t.accent_for("purple"), 14))
            b.setStyleSheet(self._btn_css())
            b.clicked.connect(self._on_cow)
            v.addWidget(b)
        return f

    def _framed(self, title) -> tuple[QFrame, QVBoxLayout]:
        t = self.theme.theme
        f = QFrame(); f.setStyleSheet(
            _scss(f"QFrame{{background:{t.panel2};border:1px solid {t.line};border-radius:10px;}}"))
        v = QVBoxLayout(f); v.setContentsMargins(10, 8, 10, 10)
        h = QLabel(title); h.setStyleSheet(
            _scss(f"color:{t.muted};font-size:11px;font-weight:600;border:none;"))
        v.addWidget(h)
        return f, v

    # -- actions ---------------------------------------------------------- #
    def _on_fault(self) -> None:
        fn = getattr(self.provider, "simulate_fault", None)
        self._render(fn() if callable(fn) else self.provider.snapshot())
        self._render_sharing()

    def _on_cow(self) -> None:
        fn = getattr(self.provider, "simulate_cow_write", None)
        if callable(fn):
            fn()
        self._render(self.provider.snapshot())
        self._render_sharing()

    def _all_procs(self) -> dict:
        fn = getattr(self.provider, "all_procs", None)
        try:
            return fn() if callable(fn) else {}
        except Exception:
            return {}

    def _fault_rows(self, snap):
        """(pid, va, cause, kind) rows. Live: the classified fault RING; demo: the simulated log."""
        if self._live:
            faults = classify_faults(self._all_procs_faults(), self._all_procs())
            return [(f.pid, f.va, f.cause, f.kind) for f in faults]
        return [(getattr(f, "pid", None), f.va, f.cause, "") for f in snap.faults]

    def _all_procs_faults(self):
        fn = getattr(self.provider, "faults", None)
        try:
            return fn() if callable(fn) else []
        except Exception:
            return []

    def _render_sharing(self) -> None:
        t = self.theme.theme
        procs = self._all_procs()
        # per-process resident-vs-virtual meters (rebuild)
        while self._proc_box.count():
            w = self._proc_box.takeAt(0).widget()
            if w:
                w.deleteLater()
        self._proc_meters = {}
        for pid in sorted(procs):
            pv = procs[pid]
            roww = QWidget(); rl = QHBoxLayout(roww); rl.setContentsMargins(0, 0, 0, 0)
            lbl = QLabel(f"pid {pid} {pv.name}")
            lbl.setStyleSheet(_scss(f"color:{t.text};font-size:11px;border:none;min-width:110px;"))
            meter = ResidentMeter(self.theme)
            meter.set_values(pv.resident_pages, max(pv.virtual_pages, pv.resident_pages))
            num = QLabel(f"{pv.resident_pages}/{max(pv.virtual_pages, pv.resident_pages)} pg")
            num.setStyleSheet(_scss(f"color:{t.muted};font-size:11px;border:none;min-width:56px;"))
            rl.addWidget(lbl); rl.addWidget(meter, 1); rl.addWidget(num)
            self._proc_box.addWidget(roww)
            self._proc_meters[pid] = meter
        # shared physical pages (mapped by >1 proc) — the COW sharing signal
        shared = shared_frames(procs)
        # is any owner's leaf for this pa a COW page? (user, read-only, RSW-marked)
        self._share_tbl.setRowCount(len(shared))
        for r, pa in enumerate(sorted(shared)):
            pids = shared[pa]
            cow = any(pte.pa == pa and pte.cow for pv in procs.values() for pte in pv.leaves)
            vals = [hex(pa), ", ".join(f"pid {x}" for x in pids), "COW" if cow else "—"]
            for c, val in enumerate(vals):
                it = QTableWidgetItem(val)
                if c == 2 and cow:
                    it.setForeground(QColor(t.accent_for("purple")))
                self._share_tbl.setItem(r, c, it)

    def _render(self, snap) -> None:
        t = self.theme.theme
        self._satp.setText(f"satp = {hex(snap.satp)}")
        self._strip.set_regions(snap.regions)
        # page table
        leaves = sorted(snap.leaves, key=lambda p: p.va)
        self._pt_tbl.setRowCount(len(leaves))
        for r, pte in enumerate(leaves):
            reg = region_for(pte.va, snap.regions)
            for c, val in enumerate([hex(pte.va), hex(pte.pa), pte.perms, reg]):
                it = QTableWidgetItem(val)
                if c == 2 and "u" in pte.perms:
                    it.setForeground(QColor(t.accent_for("green")))
                elif c == 2 and pte.perms == "----":
                    it.setForeground(QColor(t.faint))
                self._pt_tbl.setItem(r, c, it)
        # physical memory
        ph = snap.phys
        self._phys_bar.set_frac(ph.used_frac)
        self._phys_lbl.setText(
            f"{ph.used_pages:,} used / {ph.free_pages:,} free of {ph.total_pages:,} pages "
            f"({ph.used_frac * 100:.1f}% used · {ph.free_pages * 4 // 1024} MB free)")
        # faults — the live classified ring (or the simulated demo log)
        rows = self._fault_rows(snap)
        self._fault_tbl.setRowCount(len(rows))
        for r, (pid, va, cause, kind) in enumerate(rows):
            cells = ["" if pid is None else str(pid), hex(va), cause, kind]
            for c, val in enumerate(cells):
                it = QTableWidgetItem(val)
                if c == 3 and kind in _FAULT_ACCENT:
                    it.setForeground(QColor(t.accent_for(_FAULT_ACCENT[kind])))
                self._fault_tbl.setItem(r, c, it)
