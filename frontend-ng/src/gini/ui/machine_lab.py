"""Machine Lab — the visual OS workbench for an xv6 Machine.

Open it by double-clicking an xv6 Machine. The scheduler face is the flagship: four live
panels — process table, CPU registers, memory (address space / SATP), and the kernel stack —
that update on each context switch, plus a Gantt strip of which process held the CPU and a
switch counter. Controls let you slow the time-slice to watch switches happen, Step one
context switch at a time, or free-run.

The lab reads state through an injected `provider` (the same Snapshot shape whether it comes
from QEMU's GDB stub on the Mac or the offline DemoScheduler here), so all of the UI is
testable offscreen against a fake feed — the same split as the Router Lab.

Mirror of the Router Lab: there you step a *packet* through a pipeline; here you step the
*CPU* through context switches.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox, QStackedWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..domain.machine_state import MachineState
from ..domain.xv6 import DemoScheduler, policy_name, ready_queue, short_pid
from .theme import ThemeManager, icons
from .theme.manager import scale_css as _scss

# scheduler policies the selector offers (must match domain POLICY_NAMES / kernel gini_pick)
_POLICIES = ["round-robin", "priority", "lottery"]

# long-running programs the launcher offers (must match the agent's PROGRAMS list).
#
# `grind` earns its place: it is the only workload that spends real time in KERNEL mode ("run
# random system calls in parallel forever" — fork/exec/pipe/open/write/link/mkdir), so it is what
# makes the CPU Journey's kernel band, the Trap Lab taxonomy and the syscall histogram show
# anything interesting. spin/busy are user-mode loops; alloc/writer touch the kernel in bursts.
#
# KNOWN INTERACTION (not grind's fault): user programs write to the same serial console the Lab
# parses framed state dumps from, so a chatty program (grind prints an "A"/"B" progress byte every
# 500 iterations) can corrupt a dump — a mangled process line drops that process from the table,
# a mangled frame marker leaves the table showing the last good snapshot. Any student program
# that prints does this too; the fix is a dedicated dump channel, not dropping the program.
# Ordered by which resource each one presses on, so the menu reads as a tour of the machine:
# CPU · CPU-with-a-moving-PC · memory-by-pattern · memory-lazily · storage · storage-under-pressure
# · allocator-under-pressure · everything-at-once · the process table.
# Must stay in step with PROGRAMS in backend/xv6/gini_agent.py.
_LAUNCHABLE = ["spin", "busy", "walker", "toucher", "alloc", "writer", "sgrind", "mgrind",
               "grind", "forktest"]

# Programs whose argument carries the lesson, and what to show in the argument box for each. A
# program absent from this map takes no argument, and its box is disabled rather than ignored —
# a box that accepts text it then discards is worse than no box.
#
# Each entry mirrors the program's own argv handling in _UPROGS (backend/xv6/gini_patch.py); the
# default shown here is the one the C code falls back to when the argument is absent.
_PROG_ARGS = {
    "spin":    "seconds",            # argv[1] seconds, else forever
    "busy":    "seconds",            # argv[1] seconds, else forever
    "walker":  "pages ticks laps",   # argv[1] pages (64), argv[2] dwell (1), argv[3] laps (0=∞)
    "toucher": "pages [rand]",       # argv[1] pages (48), argv[2] seq|rand|stride
    "alloc":   "pages",              # argv[1] pages (48)
    "sgrind":  "blocks (30 fits)",   # argv[1] K blocks (20); the cache holds 30
    "mgrind":  "pages",              # argv[1] pages per round (16)
}


def _pid_color(pid) -> str:
    """A UNIQUE, well-separated colour per pid — golden-angle hue rotation, so no two nearby
    pids share a colour (the old 6-colour cycle collided constantly)."""
    if pid is None:
        return "#666666"
    import colorsys
    h = ((pid * 137.508) % 360) / 360.0        # golden angle -> maximally spread hues
    r, g, b = colorsys.hsv_to_rgb(h, 0.58, 0.88)
    return "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))


class LayerCard(QFrame):
    """A clickable card in the layered overview: title + one-line description + a live mini-stat.
    Cards are grouped into OS layers (user / syscall interface / kernel / hardware) so the front
    door reads like the textbook stack — the student drills into a component instead of being
    dropped straight into the dense scheduler view."""

    clicked = Signal()

    def __init__(self, theme, title, desc, accent_key) -> None:
        super().__init__()
        self.theme = theme
        self._accent = accent_key
        t = theme.theme
        acc = t.accent_for(accent_key)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setMinimumHeight(92)
        self.setStyleSheet(
            f"LayerCard{{background:{t.panel};border:1px solid {t.line};border-radius:12px;}}"
            f"LayerCard:hover{{border-color:{acc};background:{t.panel2};}}")
        v = QVBoxLayout(self); v.setContentsMargins(12, 10, 12, 10); v.setSpacing(3)
        row = QHBoxLayout(); row.setSpacing(6)
        dot = QLabel("●"); dot.setStyleSheet(f"color:{acc};font-size:11px;border:none;")
        ttl = QLabel(title)
        ttl.setStyleSheet(_scss(f"color:{t.text};font-size:14px;font-weight:600;border:none;"))
        row.addWidget(dot); row.addWidget(ttl); row.addStretch(1)
        v.addLayout(row)
        d = QLabel(desc); d.setWordWrap(True)
        d.setStyleSheet(_scss(f"color:{t.muted};font-size:11px;border:none;"))
        v.addWidget(d)
        v.addStretch(1)
        self.stat = QLabel("")
        self.stat.setStyleSheet(
            _scss(f"color:{acc};font-family:monospace;font-size:12px;font-weight:600;border:none;"))
        v.addWidget(self.stat)

    def set_stat(self, text) -> None:
        self.stat.setText(text or "")

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class SchedulingPanel(QWidget):
    """The scheduler's decision, made legible: the READY QUEUE (who's waiting, ordered the way the
    policy favours) plus the CPU SHARE each process actually got. The Gantt shows who-ran-over-time;
    this shows who's-next-and-why + how fairly the CPU was split — the evidence a lottery/priority
    assignment is graded on. Read-only: students change priority/tickets from their own program."""

    def __init__(self, theme: ThemeManager) -> None:
        super().__init__()
        self.theme = theme
        t = theme.theme
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(4)
        self._tbl = QTableWidget(0, 6)
        self._tbl.setHorizontalHeaderLabels(["pid", "name", "pri", "tickets", "wait", "CPU share"])
        self._tbl.verticalHeader().setVisible(False)
        self._tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        self._tbl.setSelectionMode(QTableWidget.NoSelection)
        self._tbl.horizontalHeader().setStretchLastSection(True)
        self._tbl.setStyleSheet(
            f"QTableWidget{{background:{t.panel};color:{t.text};border:none;"
            f"gridline-color:{t.line};font-family:monospace;font-size:11px;}}"
            f"QHeaderView::section{{background:{t.panel2};color:{t.muted};border:none;"
            "padding:3px;font-family:sans-serif;}")
        v.addWidget(self._tbl)

    @staticmethod
    def _bar(frac: float, width: int = 8) -> str:
        fill = max(0, min(width, round(frac * width)))
        return "█" * fill + "░" * (width - fill) + f"  {frac * 100:.0f}%"

    def update_view(self, procs, shares) -> None:
        t = self.theme.theme
        running = [p for p in procs if p.state == "running"]
        rows = running + ready_queue(procs)         # running first, then the ready queue
        self._tbl.setRowCount(len(rows))
        for i, p in enumerate(rows):
            share = shares.get(p.pid, 0.0)
            vals = [str(p.pid), p.name,
                    "—" if p.priority is None else str(p.priority),
                    "—" if p.tickets is None else str(p.tickets),
                    "—" if p.wait_ticks is None else str(p.wait_ticks),
                    self._bar(share)]
            for c, val in enumerate(vals):
                item = QTableWidgetItem(val)
                if p.state == "running":
                    item.setForeground(QColor(t.accent_for("green")))
                self._tbl.setItem(i, c, item)


class GanttStrip(QWidget):
    """A horizontal strip of recent scheduling slots — one cell per snapshot, coloured by the
    running pid, so context switches read as colour changes across time."""

    def __init__(self, theme, label="") -> None:
        super().__init__()
        self.theme = theme
        self.label = label            # e.g. "CPU 0" (shown at the left on SMP)
        self._slots: list = []
        self.setMinimumHeight(46)

    def set_slots(self, slots) -> None:
        self._slots = list(slots)
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        t = self.theme.theme
        h = self.height()
        p.fillRect(self.rect(), QColor(t.panel2))
        left = 44 if self.label else 4              # left gutter for the "CPU i" label
        right = 96                                  # right gutter for the running-pid text
        if self.label:
            p.setPen(QColor(t.muted))
            p.drawText(0, 0, 40, h, Qt.AlignVCenter | Qt.AlignRight, self.label)
        area = self.width() - left - right
        if not self._slots or area <= 4:
            p.setPen(QColor(t.muted))
            p.drawText(self.rect().adjusted(left, 0, 0, 0), Qt.AlignCenter, "— press Run —")
            return
        n = len(self._slots)
        w = max(2.0, area / max(n, 1))
        for i, s in enumerate(self._slots):          # the colour band fills the full strip height
            x = left + int(i * w)
            p.fillRect(x, 3, int(w) + 1, h - 6, QColor(_pid_color(s.pid)))
            if w >= 16 and s.pid is not None and (i == 0 or self._slots[i - 1].pid != s.pid):
                p.setPen(QColor("#111111"))
                # last two digits, so a narrow segment never centre-clips a full pid to a
                # misleading middle (3615 & 3613 both showed '61'); full pid is in the right gutter.
                p.drawText(x, 3, int(w), h - 6, Qt.AlignCenter, short_pid(s.pid))
        last = self._slots[-1]                        # current pid in the reserved right gutter
        p.setPen(QColor(t.text))
        who = "idle" if last.pid is None else f"pid {last.pid}"
        if h >= 40 and last.name:
            who += f" {last.name}"
        p.drawText(self.width() - right + 6, 0, right - 8, h, Qt.AlignVCenter | Qt.AlignLeft, who)


class MachineLab(QDialog):
    """Scheduler face of an xv6 Machine (the only face today; linux/kata later)."""

    snap_ready = Signal(object)   # a Snapshot pushed from a worker thread (live mode)
    load_result = Signal(bool, str, str)   # (ok, log, action) from a Load/Revert worker thread
    shadows_ready = Signal(object)         # {name: ShadowStatus} from a worker thread
    launch_failed = Signal(str)            # a refused launch, from the worker thread that tried it

    def __init__(self, parent, theme: ThemeManager, device, state: MachineState | None = None,
                 live=False, on_console=None, on_log=None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.device = device
        self.on_console = on_console
        self.on_log = on_log                  # (level, message) -> mirror to the GINI Console
        self._shadows: dict = {}              # last shadow manifest (name -> ShadowStatus)
        self._shadow_present_seen = False     # for the "shadow detected" one-shot console line
        self._reenable_shadow = None          # after a Load, re-run the shadow once the kernel is back
        # The shared MachineState is the bridge (owns provider + timeline + watcher); the Lab
        # only renders from it. With no state we spin up a demo-backed one so the lab is explorable.
        self.state = state or MachineState(
            DemoScheduler(timeslice=int((device.properties or {}).get("Timeslice", "1") or "1")),
            device_id=getattr(device, "id", ""), mode="demo")
        # `live` (behaviour: async reads, launcher, poll-vs-step) follows the DATA MODE, which is
        # a user choice on the state — Real means live kernel, Demo means the stand-in feed.
        self.live = (self.state.mode == "real")
        self._running = False

        t = theme.theme
        self.setWindowTitle(f"Machine Lab — {device.name}")
        self.resize(940, 700)
        self.setStyleSheet(f"QDialog{{background:{t.bg};}}")

        root = QVBoxLayout(self)
        self._build_topbar(root)
        # wedge banner sits at the TOP level, not on the scheduler face: a hang is a property of
        # the machine (and can happen with no shadow enabled at all), so it must be visible
        # whichever component the student is looking at — right next to the Reboot button.
        self._wedge_lbl = QLabel()
        self._wedge_lbl.setWordWrap(True)
        self._wedge_lbl.setVisible(False)
        self._wedge_lbl.setStyleSheet(_scss(
            f"color:{t.accent_for('amber')};font-size:12px;background:{t.panel};"
            f"border:1px solid {t.accent_for('amber')};border-radius:8px;padding:6px 10px;"))
        root.addWidget(self._wedge_lbl)

        # Two rooms behind one door: the layered OVERVIEW (page 0, shown first) and the dense
        # SCHEDULER view (page 1). The scheduler page is built eagerly so its widgets exist for
        # rendering + tests even while the overview is on screen.
        self._stack = QStackedWidget()
        root.addWidget(self._stack, 1)

        self._sched_page = QWidget()
        spl = QVBoxLayout(self._sched_page); spl.setContentsMargins(0, 0, 0, 0)
        shdr = QHBoxLayout()             # title header, consistent with the other Labs' windows
        sic = QLabel(); sic.setPixmap(icons.render_pixmap("host", t.accent_for("red"), 22))
        stitle = QLabel(f"  Process Scheduler Lab — {device.name}")
        stitle.setStyleSheet(_scss(f"color:{t.text};font-size:16px;font-weight:600;"))
        shdr.addWidget(sic); shdr.addWidget(stitle); shdr.addStretch(1)
        spl.addLayout(shdr)
        self._build_banner(spl)          # shown in Real mode when there's no live data (never fake)
        self._build_controls(spl)
        if self.live:
            self._build_launcher(spl)   # launch/kill programs to give the scheduler real work
            self._build_sched_controls(spl)  # per-proc priority/tickets (control-plane) setters
            self._build_shadow_bar(spl)  # the shadow: toggle + Load/Revert + inline result
        self._build_panels(spl)

        self._overview = self._build_overview()
        self._stack.addWidget(self._overview)     # index 0 — the hub is the permanent Machine Lab
        # the scheduler page opens in its OWN window (like every other card) so the hub stays up and
        # subsystems can be open concurrently; built eagerly (widgets exist for rendering + tests),
        # reparented into its window on first open.
        self._sched_win = None

        self._busy = False
        self._read_fails = 0                      # consecutive failed reads (see _on_poll's worker)
        self._closed = False                      # set on close; guards worker-thread callbacks
        self.snap_ready.connect(self._on_snap)
        self.load_result.connect(self._on_load_result)
        self.shadows_ready.connect(self._on_shadows)
        self._poll = QTimer(self)
        self._poll.timeout.connect(self._on_poll)
        self._ov_poll = QTimer(self)              # slow refresh so overview mini-stats stay live
        self._ov_poll.timeout.connect(self._on_ov_poll)
        self._shadow_poll = QTimer(self)          # slow poll for shadow status (catches file edits)
        self._shadow_poll.timeout.connect(self._on_shadow_poll)

        self._show_overview()
        self._render()   # initial paint (state's first snapshot is taken when it's created)
        if self.live:
            self._fetch(step=False)   # kick an async read so the stats/table populate on open

    # -- top bar (persistent across pages) -------------------------------- #
    def _build_topbar(self, root) -> None:
        t = self.theme.theme
        head = QHBoxLayout()
        self._back_btn = QPushButton("  ← Overview")
        self._back_btn.setStyleSheet(self._btn_css())
        self._back_btn.clicked.connect(self._show_overview)
        self._back_btn.setVisible(False)
        head.addWidget(self._back_btn)
        ic = QLabel(); ic.setPixmap(icons.render_pixmap("host", t.accent_for("red"), 24))
        self._title_lbl = QLabel(f"  Machine Lab — {self.device.name}")
        self._title_lbl.setStyleSheet(_scss(f"color:{t.text};font-size:16px;font-weight:600;"))
        head.addWidget(ic); head.addWidget(self._title_lbl); head.addStretch(1)
        # Real/Demo is a user choice (never auto-switched). Real = live kernel; Demo = the stand-in
        # feed for exploring the interface with nothing running.
        from PySide6.QtWidgets import QButtonGroup
        dlbl = QLabel("data"); dlbl.setStyleSheet(_scss(f"color:{t.faint};font-size:11px;"))
        head.addWidget(dlbl)
        self._mode_group = QButtonGroup(self); self._mode_group.setExclusive(True)
        self._mode_btns = {}
        for key, label in (("real", "Real"), ("demo", "Demo")):
            b = QPushButton(label); b.setCheckable(True)
            b.setChecked(self.state.mode == key)
            b.setStyleSheet(self._mode_btn_css())
            b.clicked.connect(lambda _c=False, k=key: self._set_data_mode(k))
            self._mode_group.addButton(b); self._mode_btns[key] = b
            head.addWidget(b)
        # Reboot is a MACHINE-level action, not a shadow one: reset the box on the current kernel
        # (no rebuild) whenever you want a clean boot — after a wedge, or just to start an
        # experiment from a known state. Lives beside Real/Demo because it is about the machine.
        head.addSpacing(10)
        self._reboot_btn = QPushButton()
        self._reboot_btn.setIcon(icons.icon("power", t.accent_for("amber"), 16))
        self._reboot_btn.setToolTip(
            "Reboot the machine — reset on the CURRENT kernel, no rebuild.\n"
            "Shadows come back OFF (your shadow file is kept), so this is also\n"
            "the way out of a hang caused by a shadow.")
        self._reboot_btn.setStyleSheet(self._btn_css())
        self._reboot_btn.clicked.connect(self._reboot_machine)
        self._reboot_btn.setEnabled(self.live)      # nothing to reboot in the offline stand-in
        head.addWidget(self._reboot_btn)
        root.addLayout(head)

    def _build_banner(self, root) -> None:
        """A 'no live data' banner for Real mode with nothing running — an explicit error that
        offers Demo, instead of silently painting fake data."""
        t = self.theme.theme
        self._banner = QFrame()
        self._banner.setStyleSheet(
            f"QFrame{{background:{t.panel2};border:1px solid {t.accent_for('amber')};"
            "border-radius:10px;}")
        lay = QHBoxLayout(self._banner); lay.setContentsMargins(12, 8, 12, 8)
        self._banner_lbl = QLabel()
        self._banner_lbl.setWordWrap(True)
        self._banner_lbl.setStyleSheet(_scss(f"color:{t.text};font-size:12px;border:none;"))
        lay.addWidget(self._banner_lbl, 1)
        use_demo = QPushButton("  Use Demo")
        use_demo.setStyleSheet(self._btn_css())
        use_demo.clicked.connect(lambda: self._set_data_mode("demo"))
        lay.addWidget(use_demo)
        self._banner.setVisible(False)
        root.addWidget(self._banner)

    def _mode_btn_css(self) -> str:
        t = self.theme.theme
        return (f"QPushButton{{color:{t.muted};background:{t.panel2};border:1px solid {t.line};"
                f"border-radius:8px;padding:4px 12px;font-size:12px;}}"
                f"QPushButton:checked{{color:{t.accent_for('green')};border-color:{t.accent};"
                f"background:{t.panel};font-weight:600;}}"
                f"QPushButton:hover{{border-color:{t.accent};}}")

    def _set_data_mode(self, mode: str) -> None:
        """User flipped Real/Demo. Swap the state's data plane, realign behaviour, and repaint.
        NEVER called automatically — the state also never auto-falls-back, so Real stays Real."""
        if self._running:
            self._toggle_run()                    # stop the poll loop before swapping the source
        self.state.set_mode(mode)
        self.live = (mode == "real")
        for k, b in self._mode_btns.items():
            b.setChecked(k == mode)
        self._render()
        if self.live and self.state.has_real():
            self._fetch(step=False)               # kick a live read on entering Real

    # -- overview: the layered OS stack of drill-down cards --------------- #
    def _build_overview(self) -> QWidget:
        t = self.theme.theme
        page = QWidget()
        outer = QVBoxLayout(page); outer.setContentsMargins(0, 0, 0, 0)
        intro = QLabel("The xv6 machine, layer by layer. Pick a component to open it — you drill "
                       "into detail as you need it.")
        intro.setWordWrap(True)
        intro.setStyleSheet(_scss(f"color:{t.muted};font-size:12px;"))
        outer.addWidget(intro)

        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        body = QWidget(); col = QVBoxLayout(body); col.setContentsMargins(2, 2, 2, 2)
        col.setSpacing(6)
        self._ov_cards: dict = {}

        # top of the stack: what the student's programs are, running in user mode
        col.addWidget(self._layer_band("USER SPACE", [
            ("programs", "Programs & Shell", "The processes you launch — running in user mode.",
             "green", self._open_console),
            ("games", "Games", "Diagnose-from-the-signature challenges.",
             "purple", self._open_games)]))
        col.addWidget(self._boundary("ecall  ▾  trap into the kernel  ·  sret  ▴  back to user"))
        # the system-call interface: the door between user and kernel
        col.addWidget(self._layer_band("SYSTEM-CALL INTERFACE", [
            ("syscalls", "System Calls", "Live histogram (last 60s) + strace-style trace.",
             "blue", self._open_syscall_lab),
            ("builder", "Syscall Builder", "Add your own syscall — real kernel edits generated.",
             "red", self._open_syscall_builder),
            ("fingerprints", "Process Fingerprints",
             "Each process's behavioral signature + a classify game.",
             "purple", self._open_fingerprints)]))
        col.addWidget(self._boundary("supervisor mode  ·  the kernel"))
        # the kernel's core subsystems
        col.addWidget(self._layer_band("KERNEL", [
            ("scheduler", "Process Scheduler", "Watch the CPU move between processes.",
             "red", self._show_scheduler),
            ("memory", "Virtual Memory", "Page tables, the allocator, and page faults.",
             "purple", self._open_memory_lab),
            ("storage", "File System", "Inodes, buffer cache, and the write-ahead log.",
             "cyan", self._open_storage_lab),
            ("journey", "Traps & Interrupts", "Live trap mix (syscall/fault/timer) + step one.",
             "amber", self._open_trap_lab),
            ("locks", "Locks & Contention", "Which locks cores are spinning on, live.",
             "green", self._open_lock_lab)]))
        col.addWidget(self._boundary("registers · trapframe · timer interrupts"))
        # the hardware the kernel drives
        col.addWidget(self._layer_band("HARDWARE", [
            ("cpu", "CPU & Registers", "Per-core registers, satp, and the kernel stack.",
             "red", self._open_cpu)]))
        col.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        return page

    def _layer_band(self, name, cards) -> QFrame:
        t = self.theme.theme
        band = QFrame()
        band.setStyleSheet(
            f"QFrame{{background:{t.panel2};border:1px solid {t.line};border-radius:12px;}}")
        v = QVBoxLayout(band); v.setContentsMargins(12, 8, 12, 12); v.setSpacing(8)
        lbl = QLabel(name)
        lbl.setStyleSheet(_scss(f"color:{t.faint};font-size:10px;font-weight:700;"
                                "letter-spacing:2px;border:none;"))
        v.addWidget(lbl)
        row = QHBoxLayout(); row.setSpacing(8)
        for key, title, desc, accent, handler in cards:
            card = LayerCard(self.theme, title, desc, accent)
            card.clicked.connect(handler)
            self._ov_cards[key] = card
            row.addWidget(card)
        v.addLayout(row)
        return band

    def _boundary(self, text) -> QLabel:
        t = self.theme.theme
        lbl = QLabel(text); lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet(_scss(f"color:{t.faint};font-size:10px;letter-spacing:1px;"))
        return lbl

    # -- page navigation -------------------------------------------------- #
    def _show_overview(self) -> None:
        self._stack.setCurrentWidget(self._overview)
        self._back_btn.setVisible(False)          # vestigial now the hub is permanent
        self._title_lbl.setText(f"  Machine Lab — {self.device.name}")
        if self.live:
            self._ov_poll.start(2000)             # keep the mini-stats fresh while the hub is up
        self._update_overview()

    def _ensure_sched_window(self) -> None:
        """Lazily wrap the eagerly-built scheduler page in its own window (reparents it once)."""
        if self._sched_win is not None:
            return
        from PySide6.QtWidgets import QDialog
        t = self.theme.theme
        w = QDialog(self)
        w.setWindowTitle(f"Process Scheduler Lab — {self.device.name}")
        w.resize(940, 700)
        w.setStyleSheet(f"QDialog{{background:{t.bg};}}")
        lay = QVBoxLayout(w); lay.setContentsMargins(10, 10, 10, 10)
        lay.addWidget(self._sched_page)
        w.finished.connect(lambda _r=0: self._shadow_poll.stop())
        self._sched_win = w

    def _show_scheduler(self) -> None:
        # opens the scheduler in its OWN window; the hub (and its mini-stat poll) stays live, so the
        # scheduler can be open alongside Memory/CPU/etc.
        self._ensure_sched_window()
        self._sched_win.show()
        self._sched_win.raise_()
        if self.live:
            self._shadow_poll.start(3000)         # keep the shadow status fresh; catch external edits
            self._refresh_shadows()

    def _on_ov_poll(self) -> None:
        """Slow overview refresh: read current state (live) so the cards' mini-stats update."""
        if self.live and not self._closed:
            self._fetch(step=False)

    def _require_data(self) -> bool:
        """Guard the data-driven faces: in Real mode with nothing running there's no provider to
        read, so surface the banner (on the scheduler page) instead of opening an empty dialog."""
        if self.state.mode == "real" and not self.state.has_real():
            self._show_scheduler()       # the banner lives here and explains + offers Demo
            self._update_banner()
            return False
        return True

    def _open_syscall_lab(self) -> None:
        if not self._require_data():
            return
        from .syscall_lab import SyscallLab
        # live /sc over the serial when running; DemoScheduler.sc() offline
        src = getattr(self.state.provider, "sc", None)
        self._sclab = SyscallLab(self, self.theme, device=self.device,
                                 sc_source=src if callable(src) else None)
        self._sclab.show(); self._sclab.raise_()

    def _open_lock_lab(self) -> None:
        """Contention: the one kernel phenomenon nothing else here can show. Needs 2+ harts to be
        meaningful, which is why xv6 now boots multi-core; the panel says so if it is not."""
        from .lock_lab import LockLab
        self._locklab = LockLab(self, self.theme, device=self.device,
                                provider=self.state.provider, live=self.live)
        self._locklab.show(); self._locklab.raise_()

    def _open_trap_lab(self) -> None:
        # The Traps room: the live trap-cause histogram + feed (observational front), with a
        # "Step a trap" button into the frame-by-frame journey. Not gated on live data — the
        # histogram shows an empty state, but the (authored) journey is always reachable.
        from .trap_lab import TrapLab
        src = getattr(self.state.provider, "traps", None)
        catch = getattr(self.state.provider, "catch_trap", None)   # live gdb freeze (Phase 2/4)
        alarms = getattr(self.state.provider, "alarms", None)      # sigalarm-lab strip (Phase 3)
        self._traplab = TrapLab(self, self.theme, device=self.device,
                                traps_source=src if callable(src) else None,
                                catch_source=catch if callable(catch) else None,
                                alarm_source=alarms if callable(alarms) else None,
                                on_step=self._open_journey,
                                on_play=lambda: self._play_game("trap-cause"))
        self._traplab.show(); self._traplab.raise_()

    def _open_journey(self, frame=None) -> None:
        # Seed the journey with a frozen trap (real scause/sepc/stval + saved regs) when we have
        # one; otherwise fall back to the running proc's registers at the dispatch stage.
        from .cpu_journey import CpuJourney
        cpu = self.state.latest.cpu if (self.state.latest and self.state.latest.cpu) else None
        self._journey = CpuJourney(self, self.theme, device=self.device, cpu=cpu, frame=frame)
        self._journey.show(); self._journey.raise_()

    def _open_games(self) -> None:
        from .games_lab import GamesLab
        self._games = GamesLab(self, self.theme, self.device, self.state, live=self.live)
        self._games.show()
        self._games.raise_()

    def _play_game(self, game_id: str) -> None:
        from .games_lab import open_game
        self._game_win = open_game(self, self.theme, self.device, self.state, game_id, self.live)

    def _open_fingerprints(self) -> None:
        # Cross-cutting behavioral view (syscalls + traps + scheduling). Not gated on live data:
        # in Demo it uses canned fingerprints so the panel + classify game work offline.
        from .fingerprint_lab import FingerprintLab
        self._fplab = FingerprintLab(self, self.theme, self.device, self.state, live=self.live)
        self._fplab.show()
        self._fplab.raise_()

    def _open_cpu(self) -> None:
        # The HARDWARE face: per-core register file, decoded satp, kernel stack. Distinct from the
        # Process Scheduler (which process runs) — this is the registers the CPU runs *with*.
        if not self._require_data():
            return
        from .cpu_lab import CpuLab
        self._cpulab = CpuLab(self, self.theme, self.device, self.state, live=self.live)
        self._cpulab.show()
        self._cpulab.raise_()

    def _open_memory_lab(self) -> None:
        if not self._require_data():
            return
        from .memory_lab import MemoryLab
        # render from the shared MachineState's VM reader (demo stand-in or the Mac GDB bridge),
        # so the Memory face and the Ask GINI card see one source.
        self._memory = MemoryLab(self, self.theme, device=self.device, provider=self.state.vm,
                                 on_play=self._play_game,
                                 play_games=[("diagnose thrashing", "thrash-diagnose"),
                                             ("translate an address", "addr-translate")])
        self._memory.show()
        self._memory.raise_()

    def _open_storage_lab(self) -> None:
        if not self._require_data():
            return
        from .storage_lab import StorageLab
        self._storage = StorageLab(self, self.theme, device=self.device, provider=self.state.fs)
        self._storage.show()
        self._storage.raise_()

    def _open_syscall_builder(self) -> None:
        from .syscall_builder import SyscallBuilder
        # If the live provider knows how to write+recompile (Mac-side), let Apply drive it;
        # offline the builder still generates and previews the exact code.
        apply_fn = getattr(self.state.provider, "apply_syscall", None)
        self._syscalls = SyscallBuilder(
            self, self.theme, device=self.device,
            on_apply=apply_fn if callable(apply_fn) else None)
        self._syscalls.show()
        self._syscalls.raise_()

    def _btn_css(self) -> str:
        t = self.theme.theme
        return (f"QPushButton{{color:{t.text};background:{t.panel2};border:1px solid {t.line};"
                f"border-radius:8px;padding:6px 12px;}}"
                f"QPushButton:hover{{border-color:{t.accent};}}"
                f"QPushButton:disabled{{color:{t.faint};}}")

    # -- controls (time-slice + step/run) --------------------------------- #
    def _build_controls(self, root) -> None:
        t = self.theme.theme
        hint = QLabel("Watch the kernel move the CPU between processes. Slow the time-slice or "
                      "Step one context switch at a time to see it happen.")
        hint.setWordWrap(True)
        hint.setStyleSheet(_scss(f"color:{t.muted};font-size:11px;"))
        root.addWidget(hint)
        bar = QFrame(); bar.setStyleSheet(
            _scss(f"QFrame{{background:{t.panel2};border:1px solid {t.line};border-radius:10px;}}"))
        lay = QHBoxLayout(bar); lay.setContentsMargins(12, 8, 12, 8)

        lbl = QLabel("Time-slice"); lbl.setStyleSheet(_scss(f"color:{t.muted};font-size:12px;"))
        lay.addWidget(lbl)
        self._slice = QSlider(Qt.Horizontal)
        # quantum in timer ticks (~0.5s each) -> 1..10 ticks = 0.5..5s slices.
        self._slice.setMinimum(1); self._slice.setMaximum(10)
        self._slice.setValue(min(self.state.timeslice, 10))
        self._slice.setFixedWidth(180)
        # label tracks the drag smoothly; the actual (blocking, kernel-halting) quantum write
        # happens ONCE on release, off the GUI thread — dragging must not flood the kernel.
        self._slice.valueChanged.connect(self._update_slice_lbl)
        self._slice.sliderReleased.connect(self._apply_slice)
        lay.addWidget(self._slice)
        self._slice_lbl = QLabel(); self._slice_lbl.setStyleSheet(
            _scss(f"color:{t.text};font-size:12px;min-width:64px;"))
        lay.addWidget(self._slice_lbl)

        # scheduler policy selector — switches RR/priority/lottery LIVE (over the serial). The
        # kernel confirmation next to it shows the policy the kernel actually reports.
        plbl = QLabel("   Policy"); plbl.setStyleSheet(_scss(f"color:{t.muted};font-size:12px;"))
        lay.addWidget(plbl)
        self._policy_combo = QComboBox()
        self._policy_combo.addItems(_POLICIES)
        cur = getattr(self.state, "policy", "round-robin")
        if cur in _POLICIES:
            self._policy_combo.setCurrentText(cur)
        self._policy_combo.setStyleSheet(
            f"QComboBox{{color:{t.text};background:{t.panel};border:1px solid {t.line};"
            "border-radius:6px;padding:3px 8px;}")
        self._policy_combo.currentTextChanged.connect(self._apply_policy)   # connect AFTER setting
        lay.addWidget(self._policy_combo)
        self._policy_kernel_lbl = QLabel()
        self._policy_kernel_lbl.setStyleSheet(_scss(f"color:{t.faint};font-size:11px;"))
        lay.addWidget(self._policy_kernel_lbl)
        # in-lab game: read a Gantt, name the policy
        self._policy_play = QPushButton("  Play")
        self._policy_play.setToolTip("Guess-the-scheduler game: name the policy from a timeline")
        self._policy_play.setIcon(icons.icon("robot", t.accent_for("purple"), 14))
        self._policy_play.setStyleSheet(self._btn_css())
        self._policy_play.clicked.connect(lambda: self._play_game("guess-policy"))
        lay.addWidget(self._policy_play)
        lay.addStretch(1)

        self._step_btn = QPushButton("  Step switch")
        self._step_btn.setIcon(icons.icon("send", t.accent_for("red"), 15))
        self._step_btn.clicked.connect(self._on_step)
        self._step_btn.setStyleSheet(self._btn_css())
        lay.addWidget(self._step_btn)

        self._run_btn = QPushButton("  Run")
        self._run_btn.setIcon(icons.icon("play", t.accent_for("green"), 15))
        self._run_btn.clicked.connect(self._toggle_run)
        self._run_btn.setStyleSheet(self._btn_css())
        lay.addWidget(self._run_btn)

        self._switch_lbl = QLabel("0 switches")
        self._switch_lbl.setStyleSheet(_scss(f"color:{t.muted};font-size:12px;min-width:88px;"))
        lay.addWidget(self._switch_lbl)
        root.addWidget(bar)
        self._update_slice_lbl()

        # one Gantt strip per CPU (SMP). Built on demand from the per-CPU timelines.
        self._gantt_box = QVBoxLayout(); self._gantt_box.setSpacing(3)
        holder = QWidget(); holder.setLayout(self._gantt_box)
        root.addWidget(holder)
        self._gantts: dict = {}                 # cpu_index -> GanttStrip
        self._gantt = GanttStrip(self.theme)    # the default single strip (cpu 0)
        self._gantts[0] = self._gantt
        self._gantt_box.addWidget(self._gantt)

    def _sync_gantts(self) -> None:
        """Ensure there's a strip per CPU the kernel reports; shrink them when there are many."""
        cpus = sorted(self.state.cpu_timelines) or [0]
        for ci in cpus:
            if ci not in self._gantts:
                g = GanttStrip(self.theme, label=f"CPU {ci}")
                self._gantts[ci] = g
                self._gantt_box.addWidget(g)
        multi = len(cpus) > 1
        for ci, g in self._gantts.items():
            g.label = f"CPU {ci}" if multi else ""
            g.setMinimumHeight(30 if multi else 46)
            g.setMaximumHeight(30 if multi else 46)
            tl = self.state.cpu_timelines.get(ci)
            g.set_slots(tl.recent() if tl else [])

    def _build_launcher(self, root) -> None:
        t = self.theme.theme
        bar = QFrame(); bar.setStyleSheet(
            _scss(f"QFrame{{background:{t.panel2};border:1px solid {t.line};border-radius:10px;}}"))
        lay = QHBoxLayout(bar); lay.setContentsMargins(12, 6, 12, 6)
        lbl = QLabel("Launch a program"); lbl.setStyleSheet(_scss(f"color:{t.muted};font-size:12px;"))
        lay.addWidget(lbl)
        self._prog_combo = QComboBox()
        self._prog_combo.addItems(_LAUNCHABLE)
        self._prog_combo.setStyleSheet(
            f"QComboBox{{color:{t.text};background:{t.panel};border:1px solid {t.line};"
            "border-radius:6px;padding:3px 8px;}")
        lay.addWidget(self._prog_combo)
        # Argument box. Three of these programs are parameterised and the parameter IS the lesson:
        # sgrind 20 fits the buffer cache and sgrind 60 does not, and that contrast is the whole
        # exercise. Without this the launcher could only ever run the default.
        self._prog_args = QLineEdit()
        self._prog_args.setFixedWidth(124)   # fits "pages ticks laps"
        self._prog_args.setStyleSheet(
            f"QLineEdit{{color:{t.text};background:{t.panel};border:1px solid {t.line};"
            "border-radius:6px;padding:3px 8px;}")
        self._prog_args.returnPressed.connect(self._launch)
        lay.addWidget(self._prog_args)
        self._prog_combo.currentTextChanged.connect(self._sync_args_hint)
        self._sync_args_hint(self._prog_combo.currentText())
        launch = QPushButton("  Launch")
        launch.setIcon(icons.icon("play", t.accent_for("green"), 14))
        launch.setStyleSheet(self._btn_css())
        launch.clicked.connect(self._launch)
        lay.addWidget(launch)
        hint = QLabel("spin = CPU loop (PC parks on one instruction) · busy = varied CPU work "
                      "(PC moves) · walker = the PC itself walks a corridor of NOPs, one page "
                      "at a time, slowly enough to watch · "
                      "toucher = touches N pages TWICE, second pass faults zero times · "
                      "alloc = grows memory lazily · writer = file writes · "
                      "sgrind = reads K blocks round and round (cache holds 30) · "
                      "mgrind = hammers the page allocator and fork · "
                      "grind = heavy KERNEL-mode syscall mix · "
                      "forktest = fills the process table, then exits. "
                      "Use ✕ in the table to kill one.")
        # WRAPPED, and on its own row beneath the launcher rather than inside it. Every word of
        # the text above is kept — it is the only in-app description of these programs — but a
        # QLabel in a HORIZONTAL layout hands its full single-line width to that layout as a
        # MINIMUM, and this one measures 2856 px. That alone put the Process Scheduler panel's
        # minimum at 3325 px, wider than the screen, dragging the dropdown, the argument box, the
        # pid setters and the shadow bar off the edge with it. Below the bar it folds to whatever
        # width the panel has, and the panel is free to be narrow: 943 px.
        #
        # Fixed once before by moving the descriptions into dropdown tooltips. That is a defensible
        # design and it was not what was asked for — from outside it is text that vanished.
        hint.setWordWrap(True)
        hint.setStyleSheet(_scss(f"color:{t.faint};font-size:11px;padding:2px 12px;"))
        lay.addStretch(1)
        root.addWidget(bar)
        root.addWidget(hint)
        # Refusals land here rather than nowhere. Hidden until something actually fails.
        self._launch_msg = QLabel(); self._launch_msg.setWordWrap(True)
        self._launch_msg.setStyleSheet(
            _scss(f"color:{t.accent_for('red')};font-size:11px;padding:2px 12px;"))
        self._launch_msg.setVisible(False)
        root.addWidget(self._launch_msg)
        self.launch_failed.connect(self._on_launch_failed)

    def _build_sched_controls(self, root) -> None:
        """Per-process priority + ticket setters (control-plane) — so priority/lottery have real
        differences to schedule on. Pick a pid, set its priority (lower = higher) and lottery
        tickets, then Set."""
        t = self.theme.theme
        bar = QFrame(); bar.setStyleSheet(
            _scss(f"QFrame{{background:{t.panel2};border:1px solid {t.line};border-radius:10px;}}"))
        lay = QHBoxLayout(bar); lay.setContentsMargins(12, 6, 12, 6)
        lay.addWidget(QLabel("Scheduling for pid"))
        self._sc_pid = QComboBox(); self._sc_pid.setMinimumWidth(64)
        self._sc_pid.setStyleSheet(
            f"QComboBox{{color:{t.text};background:{t.panel};border:1px solid {t.line};"
            "border-radius:6px;padding:3px 8px;}")
        self._sc_pid.currentIndexChanged.connect(self._load_sched_control)
        lay.addWidget(self._sc_pid)
        lay.addWidget(QLabel("priority"))
        self._sc_prio = QSpinBox(); self._sc_prio.setRange(0, 30); self._sc_prio.setValue(10)
        self._sc_prio.setToolTip("lower = higher priority")
        lay.addWidget(self._sc_prio)
        lay.addWidget(QLabel("tickets"))
        self._sc_tickets = QSpinBox(); self._sc_tickets.setRange(1, 100); self._sc_tickets.setValue(1)
        self._sc_tickets.setToolTip("lottery weight")
        lay.addWidget(self._sc_tickets)
        setb = QPushButton("Set"); setb.setStyleSheet(self._btn_css())
        setb.clicked.connect(self._apply_sched_control)
        lay.addWidget(setb)
        for w in (self._sc_pid, self._sc_prio, self._sc_tickets):
            pass
        for lbl in bar.findChildren(QLabel):
            lbl.setStyleSheet(_scss(f"color:{t.muted};font-size:12px;"))
        lay.addStretch(1)
        root.addWidget(bar)

    def _sched_user_pids(self) -> list:
        snap = self.state.latest
        return [p for p in (snap.procs if snap else []) if p.pid > 2]

    def _refresh_sched_pids(self) -> None:
        """Keep the pid dropdown in step with the live process list (preserve the selection)."""
        if not hasattr(self, "_sc_pid"):
            return
        cur = self._sc_pid.currentData()
        procs = self._sched_user_pids()
        wanted = [(f"{p.pid} {p.name}", p.pid) for p in procs]
        have = [(self._sc_pid.itemText(i), self._sc_pid.itemData(i))
                for i in range(self._sc_pid.count())]
        if have == wanted:
            return
        self._sc_pid.blockSignals(True)
        self._sc_pid.clear()
        for text, pid in wanted:
            self._sc_pid.addItem(text, pid)
        if cur is not None:                       # keep pointing at the same pid if it's still there
            idx = self._sc_pid.findData(cur)
            if idx >= 0:
                self._sc_pid.setCurrentIndex(idx)
        self._sc_pid.blockSignals(False)
        self._load_sched_control()

    def _load_sched_control(self) -> None:
        """Load the selected pid's current priority/tickets into the spinboxes."""
        pid = self._sc_pid.currentData()
        for p in self._sched_user_pids():
            if p.pid == pid:
                if p.priority is not None:
                    self._sc_prio.setValue(p.priority)
                if p.tickets is not None:
                    self._sc_tickets.setValue(p.tickets)
                return

    def _apply_sched_control(self) -> None:
        pid = self._sc_pid.currentData()
        if pid is None:
            return
        prio, tk = self._sc_prio.value(), self._sc_tickets.value()
        self._bg(lambda: (self.state.provider.set_priority(pid, prio),
                          self.state.provider.set_tickets(pid, tk)))

    def _update_slice_lbl(self) -> None:
        v = self._slice.value()
        self._slice_lbl.setText(f"{v} tick{'s' if v != 1 else ''}  (~{v * 0.5:.1f}s slice)")

    _REG_ROWS = ["pc", "sp", "ra", "s0", "satp", "a0", "a7"]   # s0 = frame pointer (backtrace lab)
    _MEM_ROWS = ["page table (satp)", "user pc", "stack ptr", "address space"]

    # -- the four state panels -------------------------------------------- #
    def _build_panels(self, root) -> None:
        grid = QGridLayout(); grid.setSpacing(10)
        from .process_tree import ProcessTree
        self._proc_tree = ProcessTree(self.theme)
        self._proc_tree.set_live(self.live)
        self._proc_tree.kill_requested.connect(self._kill)
        grid.addWidget(self._panel("Processes  ·  tree", self._proc_tree, fill=True), 0, 0)
        # per-CPU tables: one column per CPU (so both cores' registers/memory show on SMP)
        self._reg_tbl = self._make_cpu_table(self._REG_ROWS)
        grid.addWidget(self._panel("CPU registers  ·  per core", self._reg_tbl, fill=True), 0, 1)
        # the scheduler flagship: ready queue + CPU share (the per-core memory view lives behind
        # the Memory card on the hub, so it doesn't need a duplicate mini-panel here).
        self._sched_panel = SchedulingPanel(self.theme)
        grid.addWidget(self._panel("Scheduling  ·  ready queue & CPU share", self._sched_panel,
                                   fill=True), 1, 0)
        self._stack_lbl = QLabel(); self._stack_lbl.setAlignment(Qt.AlignTop)
        self._stack_lbl.setTextFormat(Qt.RichText)
        t = self.theme.theme
        self._stack_lbl.setStyleSheet(_scss(f"color:{t.text};font-family:monospace;font-size:12px;"))
        grid.addWidget(self._panel("Kernel stack  ·  bt (Step)", self._stack_lbl), 1, 1)
        grid.setColumnStretch(0, 1); grid.setColumnStretch(1, 1)
        root.addLayout(grid, 1)

    def _make_cpu_table(self, rows) -> QTableWidget:
        t = self.theme.theme
        tbl = QTableWidget(len(rows), 0)
        tbl.setVerticalHeaderLabels(rows)
        tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        tbl.setSelectionMode(QTableWidget.NoSelection)
        tbl.horizontalHeader().setStretchLastSection(True)
        tbl.setStyleSheet(
            f"QTableWidget{{background:{t.panel};color:{t.text};border:none;"
            f"gridline-color:{t.line};font-family:monospace;font-size:11px;}}"
            f"QHeaderView::section{{background:{t.panel2};color:{t.muted};border:none;"
            "padding:3px;font-family:sans-serif;}")
        return tbl

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
        if not fill:                 # keep content pinned to the top, not centered
            v.addStretch(1)
        return f

    def _make_proc_table(self) -> QTableWidget:
        t = self.theme.theme
        tbl = QTableWidget(0, 4)
        tbl.setHorizontalHeaderLabels(["PID", "State", "Name", ""])
        tbl.verticalHeader().setVisible(False)
        tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        tbl.setSelectionMode(QTableWidget.NoSelection)
        tbl.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        tbl.setColumnWidth(3, 44)
        tbl.setStyleSheet(
            f"QTableWidget{{background:{t.panel};color:{t.text};border:none;"
            f"gridline-color:{t.line};font-size:12px;}}"
            f"QHeaderView::section{{background:{t.panel2};color:{t.muted};border:none;"
            "padding:4px;}")
        return tbl

    def _make_kv_panel(self, keys) -> tuple[dict, QWidget]:
        t = self.theme.theme
        w = QWidget(); g = QGridLayout(w); g.setContentsMargins(0, 4, 0, 0); g.setSpacing(4)
        fields = {}
        for i, k in enumerate(keys):
            kl = QLabel(k); kl.setStyleSheet(_scss(f"color:{t.muted};font-size:11px;border:none;"))
            vl = QLabel("—"); vl.setStyleSheet(
                _scss(f"color:{t.text};font-family:monospace;font-size:12px;border:none;"))
            vl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            g.addWidget(kl, i, 0); g.addWidget(vl, i, 1)
            fields[k] = vl
        g.setColumnStretch(1, 1)
        return fields, w

    # -- programs: launch / kill (all off the GUI thread) ----------------- #
    def _bg(self, fn) -> None:
        import threading
        threading.Thread(target=fn, daemon=True).start()

    def _sync_args_hint(self, prog: str) -> None:
        """Show what this program's argument means, in the box itself. The placeholder is the only
        clue a student gets that `sgrind` even takes a number."""
        self._prog_args.setPlaceholderText(_PROG_ARGS.get(prog, ""))
        self._prog_args.setEnabled(bool(_PROG_ARGS.get(prog)))
        if not _PROG_ARGS.get(prog):
            self._prog_args.clear()

    def _launch(self) -> None:
        prog = self._prog_combo.currentText()
        args = self._prog_args.text().strip() if _PROG_ARGS.get(prog) else ""

        def go():
            ok = self.state.provider.run(prog, args)
            if not ok:
                # Do not let this vanish. The commonest cause is an xv6 image built before the
                # program existed, and a silent failure is indistinguishable from a slow launch.
                why = getattr(self.state.provider, "last_run_error", "") or f"could not launch {prog}"
                self.launch_failed.emit(why)
        self._bg(lambda: self._act_then_refresh(go))

    def _on_launch_failed(self, why: str) -> None:
        self._launch_msg.setText(why)
        self._launch_msg.setVisible(True)
        if self.on_log:
            self.on_log("error", f"xv6: {why}")

    def _kill(self, pid: int) -> None:
        self._bg(lambda: self._act_then_refresh(lambda: self.state.provider.kill(pid)))

    def _act_then_refresh(self, action) -> None:
        """Run a serial action (launch/kill), then RE-READ the kernel several times (SEQUENTIALLY,
        each read blocking to completion in this worker thread) so the new/removed process shows
        up — the shell needs a beat to fork+exec (or reap). Sequential (not busy-guarded) so every
        attempt actually fires; empty reads are ignored by MachineState, so nothing blanks."""
        import time
        try:
            action()
        except Exception:
            pass
        for _ in range(6):
            if self._closed:
                return
            time.sleep(0.4)
            try:
                self.state.refresh()      # blocks until the read completes (worker thread)
                if not self._closed:
                    self.snap_ready.emit(None)  # repaint on the GUI thread
            except (Exception, RuntimeError):
                return                    # dialog closed mid-refresh -> stop quietly

    def _open_console(self) -> None:
        if self.live and hasattr(self.state.provider, "console"):
            self._console = Xv6Console(self, self.theme, self.state.provider, self.device)
            self._console.show(); self._console.raise_()
        elif self.on_console:
            self.on_console()
        else:
            # no live console (offline demo) — the process tree IS the list of programs
            self._show_scheduler()

    # -- control handlers ------------------------------------------------- #
    def _apply_slice(self) -> None:
        """Commit the time-slice ONCE, when the slider is released — off the GUI thread for the
        live bridge (the write halts the kernel via gdb, so we must not do it inline or on every
        drag tick)."""
        v = self._slice.value()
        self._update_slice_lbl()
        if self.live:
            self._bg(lambda: self.state.set_timeslice(v))
        else:
            self.state.set_timeslice(v)

    def _apply_policy(self, name) -> None:
        """Switch the scheduler policy live. For the live bridge the write goes over the serial
        off the GUI thread; offline it re-picks against the demo so the change is visible at once."""
        if self.live:
            self._bg(lambda: self.state.set_policy(name))
        else:
            self.state.set_policy(name)
            self._render()
        if hasattr(self, "_shadow_status"):
            self._update_shadow_bar()             # the bar tracks the CURRENT policy's shadow

    def _sync_policy_combo(self) -> None:
        """Populate the policy selector from the kernel's live roster (POLICY lines) so a policy the
        kernel ships auto-appears — no hardcoded list. Offline / no roster: keep the built-in list."""
        combo = getattr(self, "_policy_combo", None)
        if combo is None or not self.live:
            return
        roster = self.state.policies()
        if not roster:
            return
        names = [roster[i] for i in sorted(roster)]
        if names == [combo.itemText(i) for i in range(combo.count())]:
            return                                # already in sync
        cur = combo.currentText()
        combo.blockSignals(True)                  # repopulate without firing _apply_policy
        combo.clear(); combo.addItems(names)
        if cur in names:
            combo.setCurrentText(cur)
        combo.blockSignals(False)

    # -- the shadow bar: toggle + Load/Revert + inline result (live only) --------- #
    _POLICY_SHADOW = {"round-robin": "rr_sched", "priority": "prio_sched",
                      "lottery": "lottery_sched"}

    def _build_shadow_bar(self, root) -> None:
        t = self.theme.theme
        bar = QFrame(); bar.setStyleSheet(
            _scss(f"QFrame{{background:{t.panel2};border:1px solid {t.line};border-radius:10px;}}"))
        lay = QVBoxLayout(bar); lay.setContentsMargins(12, 8, 12, 8); lay.setSpacing(6)
        row = QHBoxLayout()
        lbl = QLabel("Shadow"); lbl.setStyleSheet(_scss(f"color:{t.muted};font-size:12px;"))
        row.addWidget(lbl)
        self._shadow_status = QLabel("—")
        self._shadow_status.setStyleSheet(
            _scss(f"color:{t.text};font-size:12px;font-family:monospace;"))
        row.addWidget(self._shadow_status); row.addStretch(1)
        self._shadow_toggle = QCheckBox("Use my shadow")
        self._shadow_toggle.setStyleSheet(_scss(f"color:{t.text};font-size:12px;"))
        self._shadow_toggle.toggled.connect(self._toggle_shadow)
        row.addWidget(self._shadow_toggle)
        self._load_btn = QPushButton("  Load")
        self._load_btn.setIcon(icons.icon("compile", t.accent_for("green"), 14))
        self._load_btn.setToolTip("Rebuild the kernel with your shadow and restart")
        self._load_btn.clicked.connect(self._load_shadow)
        self._load_btn.setStyleSheet(self._btn_css())
        row.addWidget(self._load_btn)
        self._revert_btn = QPushButton("  Revert")
        self._revert_btn.setToolTip("Restore the shipped shadow and rebuild")
        self._revert_btn.clicked.connect(self._revert_shadow)
        self._revert_btn.setStyleSheet(self._btn_css())
        row.addWidget(self._revert_btn)
        lay.addLayout(row)
        self._shadow_result = QPlainTextEdit(); self._shadow_result.setReadOnly(True)
        self._shadow_result.setVisible(False)
        lay.addWidget(self._shadow_result)
        root.addWidget(bar)

    def _current_shadow_name(self) -> str:
        combo = getattr(self, "_policy_combo", None)
        pol = combo.currentText() if combo else "priority"
        return self._POLICY_SHADOW.get(pol, "prio_sched")

    def _refresh_shadows(self) -> None:
        if not self.live:
            return
        self._bg(self._fetch_shadows)

    def _fetch_shadows(self) -> None:
        try:
            sh = self.state.shadows()
        except (Exception, RuntimeError):
            return
        if not self._closed:
            self.shadows_ready.emit(sh)

    def _on_shadow_poll(self) -> None:
        if self.live and not self._closed:
            self._refresh_shadows()
            self._check_wedge()

    # -- wedge reporting: we TELL the student, we never reboot for them ------------ #
    def _hide_wedge(self) -> None:
        lbl = getattr(self, "_wedge_lbl", None)
        if lbl is not None:
            lbl.setVisible(False)

    def _show_wedge(self, msg: str) -> None:
        lbl = getattr(self, "_wedge_lbl", None)
        if lbl is None:
            return
        lbl.setText("⚠  " + msg)
        lbl.setVisible(True)

    def _check_wedge(self) -> None:
        """Two different hangs, two different messages:

        HARD — the kernel stopped answering dumps (panic, or a loop with interrupts off). Only the
        agent can see this, because from here it just looks like stale data.
        SOFT — dumps keep working but nothing is ever scheduled; a picker that never returns a
        process looks exactly like this. Detected here, from the timeline we already keep.
        """
        import time as _t
        hard = {}
        prov = getattr(self.state, "provider", None)
        fn = getattr(prov, "wedge", None)
        if callable(fn):
            try:
                hard = fn() or {}
            except Exception:
                hard = {}
        if hard.get("wedged"):
            who = ", ".join(hard.get("blamed") or []) or "a shadow"
            why = "panicked" if hard.get("panic") else "stopped responding"
            self._show_wedge(
                f"The machine {why} {hard.get('quiet_s', '?')}s ago — {who} was enabled. "
                "Press Reboot: the machine comes back with shadows off, and your file is kept.")
            return
        soft = ""
        try:
            soft = self.state.stall(_t.monotonic())
        except Exception:
            soft = ""
        if soft:
            self._show_wedge(soft)
        else:
            self._hide_wedge()

    def _on_shadows(self, sh) -> None:
        if self._closed:
            return
        self._shadows = sh or {}
        self._update_shadow_bar()
        # reconcile a pending "run my shadow after Load": the rebuilt kernel booted on the primary,
        # so once its manifest is readable again, re-enable the shadow; confirm only when it's active.
        name = self._reenable_shadow
        if name:
            s = self._shadows.get(name)
            if s is None:
                return                                  # kernel still booting — retry next poll
            if s.active:
                self._reenable_shadow = None
                self._show_result("✓ Running your shadow", "green")
                self._log("info", "xv6: your shadow is now running")
            elif not s.enabled:
                self._bg(lambda n=name: self._provider_call("set_shadow", n, True))

    def _update_shadow_bar(self) -> None:
        if not hasattr(self, "_shadow_status"):
            return
        t = self.theme.theme
        name = self._current_shadow_name()
        s = self._shadows.get(name)
        if s is None:
            self._shadow_status.setText(f"{name}: (not reported)")
        else:
            # one line per distinguishable situation — the same four the Coach reasons about
            if s.faults:
                txt, col = (f"{name}: wedged the machine {s.faults}× — rebooted without it",
                            t.accent_for("red"))
            elif s.rejects:
                txt, col = (f"{name}: {s.rejects} answer(s) REJECTED — the kernel refused them and "
                            f"used the primary", t.accent_for("amber"))
            elif s.active:
                txt, col = f"{name}: active ✓", t.success
            elif s.enabled and s.calls:
                txt, col = (f"{name}: on, asked {s.calls}× but never answers (stub returns 0)",
                            t.muted)
            elif s.enabled:
                txt, col = f"{name}: on (stub → primary)", t.muted
            else:
                txt, col = f"{name}: off (primary running)", t.muted
            if s.hash and s.hash != "baseline":
                txt += f"  [{s.hash}]"
            self._shadow_status.setText(txt)
            self._shadow_status.setStyleSheet(
                _scss(f"color:{col};font-size:12px;font-family:monospace;"))
            self._shadow_toggle.blockSignals(True)
            self._shadow_toggle.setChecked(bool(s.enabled))
            self._shadow_toggle.blockSignals(False)
        # "shadow detected" — one-shot console line when the file first differs from the stub
        present = any(getattr(v, "is_student", False) for v in self._shadows.values())
        if present and not self._shadow_present_seen:
            self._log("info", "xv6 shadow detected — gini_sched.c edited (Load to run it)")
        self._shadow_present_seen = present

    def _toggle_shadow(self, on) -> None:
        name = self._current_shadow_name()
        self._bg(lambda: self._provider_call("set_shadow", name, bool(on)))
        self._refresh_shadows()

    def _provider_call(self, method, *args) -> None:
        fn = getattr(self.state.provider, method, None)
        if callable(fn):
            try:
                fn(*args)
            except Exception:
                pass

    def _load_shadow(self) -> None:
        self._run_build("load", "Building your shadow…")

    def _revert_shadow(self) -> None:
        self._run_build("revert", "Reverting to the shipped shadow…")

    def _reboot_machine(self) -> None:
        """Reset the machine on the current kernel. Never automatic: when a shadow hangs the
        machine the student is TOLD to press this, so the hang is something they see and learn
        from. The kernel boots with shadows disabled, so this always returns to a working box."""
        self._reenable_shadow = None          # never auto-re-enable across a reboot
        self._run_build("reboot", "Rebooting the machine…")

    _BUILD_BTNS = ("_load_btn", "_revert_btn", "_reboot_btn")

    def _set_build_btns(self, on: bool) -> None:
        """Load/Revert live on the scheduler face (live mode only); Reboot lives in the top bar and
        always exists — so every one of them is looked up defensively."""
        for name in self._BUILD_BTNS:
            b = getattr(self, name, None)
            if b is not None:
                b.setEnabled(on)

    def _run_build(self, action, busy_msg) -> None:
        self._set_build_btns(False)
        self._show_result(busy_msg, "muted")

        def work():
            fn = getattr(self.state.provider, action, None)
            ok, log = (fn() if callable(fn) else (False, "no live kernel"))
            if not self._closed:
                self.load_result.emit(bool(ok), str(log), action)
        self._bg(work)

    def _on_load_result(self, ok, log, action) -> None:
        if self._closed:
            return
        self._set_build_btns(True)
        if action == "reboot" and ok:
            self._hide_wedge()                    # a fresh boot clears the warning
            self.state.new_episode()              # and the old boot's Gantt/watcher state
        if not ok:
            self._show_result(log or "build failed", "red")
            self._log("error", f"xv6 shadow {action} failed — compile error (see Machine Lab)")
            self._reenable_shadow = None
        elif action == "revert":
            # the shipped stub is back; a rebooted kernel boots on the primary
            self._show_result("✓ Reverted — shipped shadow restored (primary running)", "green")
            self._log("info", "xv6: reverted to the shipped shadow")
            self._reenable_shadow = None
        else:  # load — the kernel is rebuilt, but a fresh boot starts on the PRIMARY (shadow off)
            if self._shadow_toggle.isChecked():
                # honour the intent: re-run the shadow once the rebooted kernel is back (reconciled
                # in _on_shadows). Don't claim it's running until it actually is.
                self._reenable_shadow = self._current_shadow_name()
                self._show_result("✓ Loaded — bringing up your shadow…", "muted")
            else:
                self._reenable_shadow = None
                self._show_result("✓ Loaded — kernel rebuilt. Tick “Use my shadow” to run it.",
                                  "green")
            self._log("info", "xv6: loaded a rebuilt kernel")
        if self.live:
            self._fetch(step=False)      # QEMU restarted -> read fresh state
            self._refresh_shadows()

    def _show_result(self, text, tone) -> None:
        t = self.theme.theme
        col = {"green": t.success, "red": t.accent_for("red"),
               "muted": t.muted}.get(tone, t.text)
        if getattr(self, "_shadow_result", None) is None:
            self._log("info", text)        # no shadow bar (Reboot from the top bar) -> log it
            return
        self._shadow_result.setPlainText(text)
        self._shadow_result.setVisible(True)
        multiline = ("\n" in text) or tone == "red"
        self._shadow_result.setFixedHeight(130 if multiline else 30)
        self._shadow_result.setStyleSheet(
            f"QPlainTextEdit{{background:{t.panel};color:{col};border:1px solid {t.line};"
            "border-radius:6px;font-family:monospace;font-size:11px;padding:4px;}")

    def _log(self, level, msg) -> None:
        if self.on_log:
            try:
                self.on_log(level, msg)
            except Exception:
                pass

    def _on_poll(self) -> None:
        """The Run loop = OBSERVE the free-running kernel: just read current state on a timer.
        Crucially NOT step() — on a live idle kernel, stepping waits for a context switch that
        never comes and would hang. The demo has no real kernel, so it advances its round-robin
        to animate."""
        if self.live:
            self._fetch(step=False)         # refresh (read), never halt-and-wait
        else:
            try:
                self.state.step()           # demo: animate the fake round-robin
            except Exception:
                return
            self._render()

    def _on_step(self) -> None:
        """The manual 'Step switch' button = advance exactly one context switch (halts on
        swtch). On a live idle kernel this may time out harmlessly (async, so no freeze)."""
        if self.live:
            self._fetch(step=True)          # live: read gdb off the GUI thread
            return
        try:
            self.state.step()               # demo: instant, run inline
        except Exception:
            return
        self._render()

    def _fetch(self, step: bool) -> None:
        """Pull a snapshot from the live bridge on a worker thread (gdb-over-HTTP can block for
        a moment or time out), then repaint on the GUI thread. Skips if a read is in flight so
        Run/Step can't pile up and freeze the UI."""
        if self._busy or self._closed:
            return
        self._busy = True
        import threading

        def work():
            # ALWAYS signal, even on failure. `_busy` is cleared in _on_snap, so a read that
            # raised and returned without emitting used to leave it set forever — every later
            # poll then returned at the guard above and the Lab silently stopped updating until
            # it was closed and reopened. One timed-out read was enough, and under load (the
            # agent is single-threaded, so dumps queue) a timeout is exactly what happens.
            err = ""
            try:
                self.state.step() if step else self.state.refresh()
            except (Exception, RuntimeError) as e:   # incl. dialog closed mid-read
                err = f"{type(e).__name__}: {e}"
            try:
                if not self._closed:            # don't signal a dialog that's being torn down
                    # Carry the outcome WITH the signal. The count used to be updated here, after
                    # the emit — so `_busy` was already cleared, the next poll could start, and two
                    # workers then read-modify-wrote `_read_fails` from separate threads while the
                    # GUI thread read it. Consequences, in order of how much they matter:
                    #   * the "readings are failing" warning triggers on `== 5` exactly, and a lost
                    #     update steps straight past 5 — so nobody is told the machine is gone,
                    #     which is the entire purpose of the counter;
                    #   * a caller that observes the lab right after a read sees a stale count.
                    # Counting on the GUI thread makes it single-writer and orders it BEFORE
                    # `_busy` drops, so a finished read is fully finished.
                    self.snap_ready.emit(err)   # marshal back to the GUI thread — clears _busy
            except RuntimeError:
                return                          # dialog went away between the check and the emit
        threading.Thread(target=work, daemon=True).start()

    def _on_snap(self, err=None) -> None:
        """Always on the GUI thread. `err` is "" for a good read, a message for a failed one, and
        None from callers that are only asking for a repaint (they must not touch the counter)."""
        if err is not None:
            # Report a run of failures once rather than every poll: a single dropped read is
            # normal under load, a sustained run means the machine is gone.
            self._read_fails = (self._read_fails + 1) if err else 0
            if err and self._read_fails == 5:
                self._log("error", f"Machine Lab: readings are failing — {err}")
        self._busy = False                      # cleared LAST: the read is now wholly accounted for
        if self._closed:
            return
        self._render()

    def _toggle_run(self) -> None:
        t = self.theme.theme
        self._running = not self._running
        if self._running:
            self._run_btn.setText("  Pause")
            self._run_btn.setIcon(icons.icon("stop", t.accent_for("amber"), 15))
            self._poll.start(500 if self.live else 700)   # fast procdump reads -> sample often
        else:
            self._run_btn.setText("  Run")
            self._run_btn.setIcon(icons.icon("play", t.accent_for("green"), 15))
            self._poll.stop()

    # -- render from the shared state ------------------------------------- #
    def _render(self, *_a) -> None:
        self._update_banner()
        snap = self.state.latest
        if snap is None:
            self._update_overview()      # keep the hub's mini-stats honest (blank) with no data
            return
        t = self.theme.theme
        # process TREE — highlight whatever is running on any CPU (SMP-aware)
        run = snap.running_pid
        running_pids = set(snap.cpus.values()) if snap.cpus else ({run} if run else set())
        # scheduling badges: mark starving / CPU-monopolising procs in the tree
        sf = self.state.scheduling_flags()
        flags = {pid: "starving" for pid in sf.get("starvation", ())}
        for pid in sf.get("cpu_monopoly", ()):
            flags.setdefault(pid, "monopolising CPU")
        self._proc_tree.set_procs(snap.procs, running_pids, flags=flags)
        if hasattr(self, "_sc_pid"):
            self._refresh_sched_pids()          # keep the priority/tickets pid list live
        # per-CPU registers (a column per core; falls back to one column single-CPU)
        cpu_regs = snap.cpu_regs or ({0: snap.cpu} if snap.cpu else {})
        cids = sorted(cpu_regs)
        self._reg_tbl.setColumnCount(len(cids))
        heads = [f"CPU {c} · pid {cpu_regs[c].key('pid')}" for c in cids]
        self._reg_tbl.setHorizontalHeaderLabels(heads)
        for col, c in enumerate(cids):
            cs = cpu_regs[c]
            for row, key in enumerate(self._REG_ROWS):
                self._reg_tbl.setItem(row, col, QTableWidgetItem(cs.key(key)))
        # scheduling panel: ready queue + CPU share (share from the aggregate timeline)
        self._sched_panel.update_view(snap.procs, self.state.timeline.shares())
        # kernel stack (from gdb on Step; user procs are in user mode during Run)
        if snap.stack:
            rows = "<br>".join(
                f"<span style='color:{t.muted}'>#{i}</span> {f.fn}"
                + (f" <span style='color:{t.faint}'>{f.loc}</span>" if f.loc else "")
                for i, f in enumerate(snap.stack))
            self._stack_lbl.setText(rows)
        else:
            self._stack_lbl.setText(
                f"<span style='color:{t.faint}'>Press <b>Step switch</b> to capture the kernel "
                "backtrace at a context switch. (Running processes are in user mode.)</span>")
        # gantt + counter + a switches/second meter (drops as the time-slice grows -> shows the
        # slider actually taking effect, which coarse sampling otherwise hides)
        import time as _time
        if not hasattr(self, "_rate"):
            import collections
            self._rate = collections.deque(maxlen=40)
        self._rate.append((_time.monotonic(), run))
        _chg = sum(1 for i in range(1, len(self._rate))
                   if self._rate[i][1] != self._rate[i - 1][1])
        _win = (self._rate[-1][0] - self._rate[0][0]) if len(self._rate) > 1 else 0.0
        self._switch_rate = (_chg / _win) if _win > 0.5 else 0.0
        self._sync_gantts()                     # per-CPU strips (SMP) from the CPU dump lines
        n = self.state.timeline.switches()
        kq = getattr(self.state.provider, "kernel_quantum", None)   # the kernel's ACTUAL quantum
        qtxt = f" · Q{kq}" if kq else ""
        self._switch_lbl.setText(f"{n} sw · {self._switch_rate:.1f}/s{qtxt}")
        self._sync_policy_combo()                # data-driven selector from the kernel roster
        # policy confirmation: what the kernel actually reports (live id) or the demo's policy
        kp = getattr(self.state.provider, "kernel_policy", None)
        kp_name = policy_name(kp) if kp is not None else getattr(
            self.state.provider, "policy", None) or self.state.policy
        self._policy_kernel_lbl.setText(f"kernel: {kp_name}")
        self._update_overview()

    def _update_banner(self) -> None:
        if not hasattr(self, "_banner"):
            return
        no_data = (self.state.mode == "real" and self.state.latest is None)
        self._banner.setVisible(no_data)
        if no_data:
            self._banner_lbl.setText(
                "No live data from the xv6 machine. It doesn't look like it's running — start the "
                "topology (Run), or switch to Demo to explore the interface."
                if not self.state.has_real()
                else "Real mode is selected but no data has arrived yet — waiting for the xv6 "
                     "machine…")

    def _configured_cores(self) -> int:
        """The number of cores QEMU was launched with (derived from the Size tier), so the count is
        correct even when cores are idle — idle cores emit no per-core dump line, which is why a
        live-sample would read '1 core'."""
        try:
            from ..services.compiler import _xv6_harts
            return max(1, _xv6_harts(self.device))
        except Exception:
            return 1

    def _update_overview(self) -> None:
        """Refresh the overview cards' live mini-stats from the (cheap) scheduler snapshot.
        Only the always-cheap cards carry a live number; Memory / File system / Builder keep a
        descriptive line so the hub never hammers the serial just to draw the front door."""
        cards = getattr(self, "_ov_cards", None)
        if not cards:
            return
        snap = self.state.latest
        procs = snap.procs if snap else []
        user = [p for p in procs if p.pid and p.pid > 2]     # everything past init(1)+sh(2)
        active = [p for p in procs if p.state in ("running", "runnable", "sleeping")]
        rate = getattr(self, "_switch_rate", 0.0)
        ncpu = self._configured_cores()          # the cores QEMU was launched with (Size), not the
        run = snap.running_pid if snap else None  # idle live-sample (idle cores emit no dump lines)
        if "programs" in cards:
            cards["programs"].set_stat(
                f"{len(user)} user program{'s' if len(user) != 1 else ''}" if user
                else "shell ready")
        if "scheduler" in cards:
            cards["scheduler"].set_stat(f"{len(active)} procs · {rate:.1f} sw/s")
        if "journey" in cards:
            cards["journey"].set_stat(f"{self.state.timeline.switches()} switches so far")
        if "cpu" in cards:
            cards["cpu"].set_stat(
                f"{ncpu} core{'s' if ncpu != 1 else ''}"
                + (f" · running pid {run}" if run else " · idle"))
        # descriptive (no cheap live number without an extra serial read)
        cards.get("syscalls") and cards["syscalls"].set_stat("open for live histogram →")
        cards.get("builder") and cards["builder"].set_stat("generate kernel edits →")
        cards.get("memory") and cards["memory"].set_stat("page tables & faults →")
        cards.get("storage") and cards["storage"].set_stat("inodes & the log →")

    def closeEvent(self, e) -> None:  # noqa: N802
        self._closed = True            # stop any in-flight worker from signalling a dead dialog
        self._poll.stop()
        self._ov_poll.stop()
        self._shadow_poll.stop()
        try:
            self.snap_ready.disconnect(self._on_snap)
        except (RuntimeError, TypeError):
            pass
        super().closeEvent(e)


class Xv6Console(QDialog):
    """An in-app xv6 serial console. The agent owns the single QEMU serial (a second raw client
    would be refused), so this reads the console tail via /console and sends input via /input —
    all off the GUI thread."""

    tail_ready = Signal(str)

    def __init__(self, parent, theme: ThemeManager, provider, device=None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.provider = provider
        t = theme.theme
        self.setWindowTitle(f"xv6 console — {getattr(device, 'name', 'xv6')}")
        self.resize(720, 460)
        self.setStyleSheet(f"QDialog{{background:{t.bg};}}")
        v = QVBoxLayout(self)
        self.view = QPlainTextEdit(); self.view.setReadOnly(True)
        self.view.setStyleSheet(
            f"QPlainTextEdit{{background:{t.panel};color:{t.text};border:1px solid {t.line};"
            "border-radius:6px;font-family:monospace;font-size:12px;}")
        v.addWidget(self.view, 1)
        row = QHBoxLayout()
        self.input = QLineEdit(); self.input.setPlaceholderText("type a command and press Enter…")
        self.input.setStyleSheet(
            f"QLineEdit{{background:{t.panel};color:{t.text};border:1px solid {t.line};"
            "border-radius:6px;padding:6px;font-family:monospace;}")
        self.input.returnPressed.connect(self._send)
        row.addWidget(self.input, 1)
        # xv6 has no `ps`; Ctrl-P dumps the process table. This button sends that control char
        # (awkward to type) and refreshes — a reliable manual process listing.
        pbtn = QPushButton("  List processes  ⌃P")
        pbtn.setToolTip("Send Ctrl-P — xv6's process dump (its 'ps')")
        pbtn.setStyleSheet(
            f"QPushButton{{color:{t.text};background:{t.panel2};border:1px solid {t.line};"
            f"border-radius:6px;padding:6px 12px;}}QPushButton:hover{{border-color:{t.accent};}}")
        pbtn.clicked.connect(self._procdump)
        row.addWidget(pbtn)
        v.addLayout(row)
        hint = QLabel("xv6 has no `ps` — use ⌃P (above) to list processes.")
        hint.setStyleSheet(_scss(f"color:{t.faint};font-size:11px;"))
        v.addWidget(hint)
        self.tail_ready.connect(self._show)
        self._poll = QTimer(self); self._poll.timeout.connect(self._refresh)
        self._poll.start(1200)
        self._refresh()

    def _bg(self, fn):
        import threading
        threading.Thread(target=fn, daemon=True).start()

    def _procdump(self):
        # send Ctrl-P (xv6's process dump), then pull the console so it shows up
        import time

        def work():
            try:
                self.provider.send_input("\x10")
                time.sleep(0.4)
                txt = self.provider.console()
            except Exception:
                txt = ""
            self.tail_ready.emit(txt or "")
        self._bg(work)

    def _refresh(self):
        def work():
            try:
                txt = self.provider.console()
            except Exception:
                txt = ""
            self.tail_ready.emit(txt or "")
        self._bg(work)

    def _show(self, txt):
        if txt and txt != self.view.toPlainText():
            sb = self.view.verticalScrollBar()
            at_bottom = sb.value() >= sb.maximum() - 4
            self.view.setPlainText(txt)
            if at_bottom:
                sb.setValue(sb.maximum())

    def _send(self):
        line = self.input.text()
        self.input.clear()
        self._bg(lambda: self.provider.send_input(line + "\n"))

    def closeEvent(self, e):  # noqa: N802
        self._poll.stop()
        super().closeEvent(e)
