"""Memory Lab — the visual virtual-memory workbench for an xv6 Machine.

Shows a process's address space three ways: the region MAP (text | data | heap | guard | stack …
trapframe | trampoline), the leaf PAGE-TABLE mappings (VA → PA with R/W/X/U permissions), and the
PHYSICAL page allocator (free vs used), plus a page-FAULT log. Press “Simulate page fault” to see
lazy/demand allocation grow the stack: a fault is recorded, a physical page is allocated, and a
new mapping appears. Renders from an injected provider (offline DemoVm here; GDB-backed on Mac).
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..domain.xv6_vm import (
    DemoVm,
    ad_str,
    classify_faults,
    region_for,
    shared_frames,
)
from .live_poll import LivePollMixin
from .no_data import has_data, paint_placeholder, panel_state, placeholder_for, title_for
from .theme import ThemeManager, icons
from .theme.manager import scale_css as _scss

#: How often the live face re-reads. One round is three dumps (/vm, /vmall, /faults) and the
#: serial line is the scarce resource, not the CPU — below the ~0.5 s tick so a student sees
#: motion, above the point where the wire saturates. A constant, so a term of use can tune it
#: from one place.
MEMORY_POLL_MS = 1500

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
        self._note = ""
        self.setMinimumHeight(60)

    def set_regions(self, regions, note: str = "") -> None:
        self._regions = list(regions)
        self._note = note
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        t = self.theme.theme
        p.fillRect(self.rect(), QColor(t.panel2))
        if not self._regions:
            # Was a bare `return`: an empty strip with no explanation, which reads as "this
            # process has no address space" — not a thing that can be true.
            paint_placeholder(p, self.rect(), t, self._note)
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
        self._note = ""
        self.setMinimumHeight(26)

    def set_frac(self, frac, note: str = "") -> None:
        self._frac = max(0.0, min(1.0, frac))
        self._note = note
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        t = self.theme.theme
        p.fillRect(self.rect(), QColor(t.panel))
        if self._note:                     # unknown is not the same picture as empty
            paint_placeholder(p, self.rect(), t, self._note)
            return
        p.fillRect(0, 0, int(self.width() * self._frac), self.height(),
                   QColor(t.accent_for("amber")))


class FragBar(QWidget):
    """Fragmentation, drawn honestly from what the kernel can tell us.

    The kernel reports free pages and the LARGEST CONTIGUOUS free run (from the allocation
    bitmap S3 maintains). Those two numbers are the whole story of a page allocator: plenty of
    free memory whose biggest run is small IS fragmentation, and it is why a free-list allocator
    degrades while a buddy/locality policy holds up. The bar shows total memory, the free share,
    and — as a solid inner block — how much of that free memory is in ONE run.
    """

    def __init__(self, theme) -> None:
        super().__init__()
        self.theme = theme
        self._free = self._total = self._run = 0
        self._note = ""
        self.setMinimumHeight(34)

    def set_values(self, free, total, max_run, note: str = "") -> None:
        self._free, self._total, self._run = int(free), int(total), int(max_run)
        self._note = note
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        t = self.theme.theme
        p.fillRect(self.rect(), QColor(t.panel))
        if self._total <= 0:
            paint_placeholder(p, self.rect(), t, self._note or
                              "page allocator not reported by this kernel build — "
                              "rebuild the xv6 image")
            return
        w, h = self.width(), self.height()
        used_frac = 1.0 - (self._free / self._total)
        # used memory (amber) on the left, free (dim) to the right
        p.fillRect(0, 0, int(w * used_frac), h, QColor(t.accent_for("amber")))
        # the largest contiguous free run, drawn inside the free region: a wide block means
        # healthy memory, a sliver means fragmented
        run_frac = self._run / self._total
        x = int(w * used_frac)
        p.setBrush(QColor(t.accent_for("green")))
        p.setPen(Qt.NoPen)
        p.drawRect(x + 1, 6, max(2, int(w * run_frac) - 2), h - 12)


class MemoryLab(LivePollMixin, QDialog):
    #: Carries (payload, ok) from the poll worker to the GUI thread. See live_poll.
    snap_ready = Signal(object)

    def __init__(self, parent, theme: ThemeManager, device=None, provider=None,
                 on_play=None, play_games=None, state=None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.device = device
        self.provider = provider or DemoVm()
        # The MachineState, when the lab has one. Reads go through it so every face, the OS HUD
        # and the Ask GINI card share one cache and one lock — see MachineState.refresh_vm.
        self.state = state
        # Decided here rather than in _build_faults_panel, because the header and the poll both
        # need it before that runs. A live reader cannot fake a fault; a demo one can.
        self._live = not hasattr(self.provider, "simulate_fault")
        self._on_play = on_play             # callable(game_id) opening a game; may be None
        self._play_games = play_games or []  # [(label, game_id)]

        t = theme.theme
        self.setWindowTitle(f"Virtual Memory Lab — {getattr(device, 'name', 'xv6')}")
        self.resize(920, 700)
        self.setStyleSheet(f"QDialog{{background:{t.bg};}}")
        root = QVBoxLayout(self)
        self._build_header(root)

        self._strip = RegionStrip(theme)
        self._strip_panel = self._panel("Address space  ·  regions (low → high VA)", self._strip)
        root.addWidget(self._strip_panel)

        grid = QGridLayout(); grid.setSpacing(10); root.addLayout(grid, 1)
        # A/D = the accessed + dirty bits the hardware maintains. Every page-replacement policy
        # reads them (clock sweeps A and clears it; D says an eviction needs a write-back), and
        # they were being parsed out of the PTE and thrown away.
        self._pt_tbl = self._table(["VA", "PA", "perms", "A/D", "region"])
        grid.addWidget(self._panel("Page table  ·  leaf mappings", self._pt_tbl, fill=True), 0, 0, 2, 1)
        grid.addWidget(self._build_phys_panel(), 0, 1)
        grid.addWidget(self._build_faults_panel(), 1, 1)
        grid.setColumnStretch(0, 3); grid.setColumnStretch(1, 2)

        # the COW / sharing view + per-process resident-vs-virtual meters (uses all_procs())
        root.addWidget(self._build_sharing_panel())

        # One synchronous read at open — a window appearing is when a user expects a pause, and
        # every later read is off the GUI thread.
        self._render(self._snapshot())
        self._render_sharing()
        self._init_poll(MEMORY_POLL_MS, live=self._live)

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
        # Live mode gets a rate chip and a Pause. Pause is pedagogical rather than a nicety: a
        # student reading a page table wants it to hold still while they read it.
        self._chip = QLabel("live")
        self._chip.setStyleSheet(_scss(f"color:{t.accent_for('green')};font-size:11px;"))
        self._pause = QPushButton("Pause"); self._pause.setCheckable(True)
        self._pause.setStyleSheet(self._btn_css())
        self._pause.toggled.connect(self._on_pause)
        for w in (self._chip, self._pause):
            w.setVisible(self._live)
            head.addWidget(w)
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
        f.title_label = h                  # so _render can mark it "(derived)"
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
        # S3: fragmentation — free memory vs. the largest CONTIGUOUS free run
        self._frag_bar = FragBar(self.theme)
        v.addWidget(self._frag_bar)
        self._frag_lbl = QLabel(); self._frag_lbl.setWordWrap(True)
        self._frag_lbl.setStyleSheet(_scss(f"color:{t.muted};font-size:11px;border:none;"))
        v.addWidget(self._frag_lbl)
        v.addStretch(1)
        return f

    def _build_faults_panel(self) -> QFrame:
        t = self.theme.theme
        f, v = self._framed("Page faults  ·  live ring (classified)")
        # "is my page-fault handler being called at all?" — the first question a student
        # debugging the vm shadow needs answered, parsed since the counters existed and never
        # once displayed. handled == 0 with faults falling through is THE failure state, so it
        # goes amber: legible from across a lab room.
        self._vmf_lbl = f.note_label
        self._fault_tbl = self._table(["pid", "VA", "cause", "kind"])
        v.addWidget(self._fault_tbl, 1)
        btn = QPushButton("  Refresh now" if self._live else "  Simulate page fault")
        btn.setToolTip("Re-read the live fault ring + page tables (launch `alloc` from the "
                       "scheduler window to make real faults)" if self._live else
                       "Grow the stack by one page via a simulated demand fault")
        btn.setIcon(icons.icon("send", t.accent_for("amber"), 14))
        # Live: kick the poll, which reads OFF the GUI thread. The old wiring ran the whole
        # three-dump round inline and froze the window for it.
        btn.clicked.connect((lambda: self._tick()) if self._live else self._on_fault)
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
        # Title left, an optional live note right — the fault panel puts the vm-shadow counters
        # there, where they sit beside the thing they describe rather than below it.
        note = QLabel(); note.setStyleSheet(_scss(f"color:{t.muted};font-size:11px;border:none;"))
        row = QHBoxLayout(); row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(h); row.addStretch(1); row.addWidget(note)
        v.addLayout(row)
        f.title_label, f.note_label = h, note
        return f, v

    # -- actions ---------------------------------------------------------- #
    def _on_fault(self) -> None:
        fn = getattr(self.provider, "simulate_fault", None)
        self._render(fn() if callable(fn) else self._snapshot())
        self._render_sharing()

    def _on_cow(self) -> None:
        fn = getattr(self.provider, "simulate_cow_write", None)
        if callable(fn):
            fn()
        self._render(self._snapshot())
        self._render_sharing()

    def _on_pause(self, on: bool) -> None:
        self.set_paused(on)
        self._pause.setText("Resume" if on else "Pause")
        self._chip.setText(self.poll_caption())

    def _snapshot(self):
        """One read of the VM face, through MachineState when there is one (shared cache + lock)."""
        if self.state is not None:
            return self.state.refresh_vm()
        return self.provider.snapshot()

    def _read(self):
        """OFF the GUI thread — one coalesced round: page table, all-procs, fault ring.

        These three used to be fetched from INSIDE _render and _render_sharing, i.e. on the GUI
        thread, each one a dump over the serial. Moving them here is the whole point of the poll;
        leaving one behind would defeat it.
        """
        snap = self._snapshot()
        if snap is None:
            return None
        return (snap, self._all_procs(), self._all_procs_faults())

    def _render_live(self, payload, fresh: bool) -> None:
        if payload is None:                       # nothing good has ever arrived
            self._chip.setText("no reading yet")
            return
        snap, procs, faults = payload
        self._render(snap, procs, faults)
        self._render_sharing(procs)
        # "stale" says the picture is real but old. A failed read must never blank a good one —
        # a face that flickers between data and an error is worse than one that holds still.
        self._chip.setText(self.poll_caption() + ("" if fresh else "  ·  stale"))

    def _all_procs(self) -> dict:
        fn = getattr(self.provider, "all_procs", None)
        try:
            return fn() if callable(fn) else {}
        except Exception:
            return {}

    def _fault_rows(self, snap, procs=None, faults=None):
        """(pid, va, cause, kind) rows. Live: the classified fault RING; demo: the simulated log.

        `procs`/`faults` arrive pre-read from the poll worker. None means "fetch them here",
        which is the button and demo path — and on the GUI thread, which is why the poll passes
        them in rather than letting this reach for the wire.
        """
        if self._live:
            procs = self._all_procs() if procs is None else procs
            faults = self._all_procs_faults() if faults is None else faults
            return [(f.pid, f.va, f.cause, f.kind) for f in classify_faults(faults, procs)]
        return [(getattr(f, "pid", None), f.va, f.cause, "") for f in snap.faults]

    def _all_procs_faults(self):
        fn = getattr(self.provider, "faults", None)
        try:
            return fn() if callable(fn) else []
        except Exception:
            return []

    def _render_sharing(self, procs=None) -> None:
        t = self.theme.theme
        procs = self._all_procs() if procs is None else procs
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

    def _render(self, snap, procs=None, faults=None) -> None:
        t = self.theme.theme
        self._satp.setText(f"satp = {hex(snap.satp)}")
        # -- address space ---------------------------------------------------- #
        rstate = panel_state(snap, "regions")
        self._strip.set_regions(snap.regions if has_data(snap, "regions") else [],
                                placeholder_for(rstate, "address-space map"))
        self._strip_panel.title_label.setText(
            title_for("Address space  ·  regions (low → high VA)", rstate))
        # page table
        leaves = sorted(snap.leaves, key=lambda p: p.va)
        self._pt_tbl.setRowCount(len(leaves))
        for r, pte in enumerate(leaves):
            reg = region_for(pte.va, snap.regions)
            ad = ad_str(pte.flags)
            for c, val in enumerate([hex(pte.va), hex(pte.pa), pte.perms, ad, reg]):
                it = QTableWidgetItem(val)
                if c == 2 and "u" in pte.perms:
                    it.setForeground(QColor(t.accent_for("green")))
                elif c == 2 and pte.perms == "----":
                    it.setForeground(QColor(t.faint))
                elif c == 3:                       # touched pages stand out from cold ones
                    it.setForeground(QColor(t.accent_for("amber") if "A" in ad else t.faint))
                self._pt_tbl.setItem(r, c, it)
        # -- physical memory -------------------------------------------------- #
        # NEVER a zero here. "0 used / 0 free of 0 pages" is not an empty allocator, it is an
        # unread one, and it was on screen beside a working fragmentation gauge fed by the very
        # same KA line — the two panels contradicting each other about free memory.
        ph = snap.phys
        if has_data(snap, "phys"):
            self._phys_bar.set_frac(ph.used_frac)
            self._phys_lbl.setText(
                f"{ph.used_pages:,} used / {ph.free_pages:,} free of {ph.total_pages:,} pages "
                f"({ph.used_frac * 100:.1f}% used · {ph.free_pages * 4 // 1024} MB free)")
        else:
            why = placeholder_for(panel_state(snap, "phys"), "physical memory")
            self._phys_bar.set_frac(0.0, why)
            self._phys_lbl.setText(why)
        # S3: the allocator's own bitmap counters
        free, total = getattr(snap, "free_pages", 0), getattr(snap, "total_pages", 0)
        run = getattr(snap, "max_free_run", 0)
        if has_data(snap, "frag") and total:
            self._frag_bar.set_values(free, total, run)
            frag = 1.0 - (run / free) if free else 0.0
            self._frag_lbl.setText(
                f"largest contiguous free run: {run:,} pages ({run * 4 // 1024} MB) of "
                f"{free:,} free — {frag * 100:.0f}% of free memory is NOT in that run"
                + ("   ·   healthy" if frag < 0.15 else "   ·   fragmented"))
        else:
            why = placeholder_for(panel_state(snap, "frag"), "page allocator")
            self._frag_bar.set_values(0, 0, 0, why)
            self._frag_lbl.setText(why)
        # -- the vm shadow's own scoreboard ------------------------------------ #
        if "vmfault" in (getattr(snap, "have", ()) or ()):
            hd, fell = snap.vmf_handled, snap.vmf_fell
            self._vmf_lbl.setText(f"your handler: {hd:,} handled  ·  {fell:,} fell through")
            self._vmf_lbl.setStyleSheet(_scss(
                f"color:{t.accent_for('amber') if (hd == 0 and fell) else t.muted};"
                f"font-size:11px;border:none;"))
        else:
            self._vmf_lbl.setText("")
        # faults — the live classified ring (or the simulated demo log)
        rows = self._fault_rows(snap, procs, faults)
        self._fault_tbl.setRowCount(len(rows))
        for r, (pid, va, cause, kind) in enumerate(rows):
            cells = ["" if pid is None else str(pid), hex(va), cause, kind]
            for c, val in enumerate(cells):
                it = QTableWidgetItem(val)
                if c == 3 and kind in _FAULT_ACCENT:
                    it.setForeground(QColor(t.accent_for(_FAULT_ACCENT[kind])))
                self._fault_tbl.setItem(r, c, it)
