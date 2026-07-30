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
    QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QSlider, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..domain.machine_state import MachineState
from ..domain.xv6 import DemoScheduler
from .theme import ThemeManager, icons
from .theme.manager import scale_css as _scss

# long-running programs the launcher offers (must match the agent's PROGRAMS list)
_LAUNCHABLE = ["spin", "busy", "alloc", "writer", "grind", "forktest"]


def _pid_color(pid) -> str:
    """A UNIQUE, well-separated colour per pid — golden-angle hue rotation, so no two nearby
    pids share a colour (the old 6-colour cycle collided constantly)."""
    if pid is None:
        return "#666666"
    import colorsys
    h = ((pid * 137.508) % 360) / 360.0        # golden angle -> maximally spread hues
    r, g, b = colorsys.hsv_to_rgb(h, 0.58, 0.88)
    return "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))


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
                p.drawText(x, 3, int(w), h - 6, Qt.AlignCenter, str(s.pid))
        last = self._slots[-1]                        # current pid in the reserved right gutter
        p.setPen(QColor(t.text))
        who = "idle" if last.pid is None else f"pid {last.pid}"
        if h >= 40 and last.name:
            who += f" {last.name}"
        p.drawText(self.width() - right + 6, 0, right - 8, h, Qt.AlignVCenter | Qt.AlignLeft, who)


class MachineLab(QDialog):
    """Scheduler face of an xv6 Machine (the only face today; linux/kata later)."""

    snap_ready = Signal(object)   # a Snapshot pushed from a worker thread (live mode)

    def __init__(self, parent, theme: ThemeManager, device, state: MachineState | None = None,
                 live=False, on_console=None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.device = device
        self.on_console = on_console
        # The shared MachineState is the bridge (owns provider + timeline + watcher); the Lab
        # only renders from it. Offline we spin up a demo-backed one so the lab is explorable.
        self.state = state or MachineState(
            DemoScheduler(timeslice=int((device.properties or {}).get("Timeslice", "1") or "1")),
            device_id=getattr(device, "id", ""))
        self.live = live
        self._running = False

        t = theme.theme
        self.setWindowTitle(f"Machine Lab — {device.name}")
        self.resize(900, 680)
        self.setStyleSheet(f"QDialog{{background:{t.bg};}}")

        root = QVBoxLayout(self)
        self._build_header(root)
        self._build_controls(root)
        if self.live:
            self._build_launcher(root)   # launch/kill programs to give the scheduler real work
        self._build_panels(root)

        self._busy = False
        self.snap_ready.connect(self._on_snap)
        self._poll = QTimer(self)
        self._poll.timeout.connect(self._on_poll)

        self._render()   # initial paint (state's first snapshot is taken when it's created)
        if self.live:
            self._fetch(step=False)   # kick an async read so the table populates on open

    # -- header ----------------------------------------------------------- #
    def _build_header(self, root) -> None:
        t = self.theme.theme
        head = QHBoxLayout()
        ic = QLabel(); ic.setPixmap(icons.render_pixmap("host", t.accent_for("red"), 24))
        title = QLabel(f"  xv6 Scheduler — {self.device.name}")
        title.setStyleSheet(_scss(f"color:{t.text};font-size:16px;font-weight:600;"))
        head.addWidget(ic); head.addWidget(title); head.addStretch(1)
        mode = QLabel("live (GDB)" if self.live else "offline demo")
        mode.setStyleSheet(
            f"color:{t.success if self.live else t.muted};"
            f"background:{t.panel2};border:1px solid {t.line};border-radius:9px;"
            "padding:2px 10px;font-size:11px;")
        head.addWidget(mode)
        memory = QPushButton("  Memory")
        memory.setIcon(icons.icon("layout", t.accent_for("purple"), 14))
        memory.setToolTip("Virtual memory — page tables, the allocator, and page faults")
        memory.clicked.connect(self._open_memory_lab)
        memory.setStyleSheet(self._btn_css())
        head.addWidget(memory)
        storage = QPushButton("  Storage")
        storage.setIcon(icons.icon("database", t.accent_for("cyan"), 14))
        storage.setToolTip("File system — inodes, buffer cache, and the write-ahead log")
        storage.clicked.connect(self._open_storage_lab)
        storage.setStyleSheet(self._btn_css())
        head.addWidget(storage)
        syscalls = QPushButton("  Syscall Builder")
        syscalls.setIcon(icons.icon("compile", t.accent_for("red"), 14))
        syscalls.setToolTip("Add your own system call to xv6 — generate the real kernel edits")
        syscalls.clicked.connect(self._open_syscall_builder)
        syscalls.setStyleSheet(self._btn_css())
        head.addWidget(syscalls)
        # No Console button here — the console is a *peripheral*: drop a Terminal on the canvas,
        # wire it to this xv6 Machine, and double-click it.
        root.addLayout(head)
        hint = QLabel("Watch the kernel move the CPU between processes. Slow the time-slice or "
                      "Step one context switch at a time to see it happen.")
        hint.setWordWrap(True)
        hint.setStyleSheet(_scss(f"color:{t.muted};font-size:11px;"))
        root.addWidget(hint)

    def _open_memory_lab(self) -> None:
        from .memory_lab import MemoryLab
        # render from the shared MachineState's VM reader (offline demo or the Mac GDB bridge),
        # so the Memory face and the Ask GINI card see one source.
        self._memory = MemoryLab(self, self.theme, device=self.device, provider=self.state.vm)
        self._memory.show()
        self._memory.raise_()

    def _open_storage_lab(self) -> None:
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
        launch = QPushButton("  Launch")
        launch.setIcon(icons.icon("play", t.accent_for("green"), 14))
        launch.setStyleSheet(self._btn_css())
        launch.clicked.connect(self._launch)
        lay.addWidget(launch)
        hint = QLabel("spin = CPU loop · alloc = grows memory · writer = file writes. "
                      "Use ✕ in the table to kill one.")
        hint.setStyleSheet(_scss(f"color:{t.faint};font-size:11px;"))
        lay.addWidget(hint); lay.addStretch(1)
        root.addWidget(bar)

    def _update_slice_lbl(self) -> None:
        v = self._slice.value()
        self._slice_lbl.setText(f"{v} tick{'s' if v != 1 else ''}  (~{v * 0.5:.1f}s slice)")

    _REG_ROWS = ["pc", "sp", "ra", "satp", "a0", "a7"]
    _MEM_ROWS = ["page table (satp)", "user pc", "stack ptr", "address space"]

    # -- the four state panels -------------------------------------------- #
    def _build_panels(self, root) -> None:
        grid = QGridLayout(); grid.setSpacing(10)
        self._proc_tbl = self._make_proc_table()
        grid.addWidget(self._panel("Processes  ·  proc[]", self._proc_tbl, fill=True), 0, 0)
        # per-CPU tables: one column per CPU (so both cores' registers/memory show on SMP)
        self._reg_tbl = self._make_cpu_table(self._REG_ROWS)
        grid.addWidget(self._panel("CPU registers  ·  per core", self._reg_tbl, fill=True), 0, 1)
        self._mem_tbl = self._make_cpu_table(self._MEM_ROWS)
        grid.addWidget(self._panel("Memory  ·  address space (per core)", self._mem_tbl,
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

    def _launch(self) -> None:
        prog = self._prog_combo.currentText()
        self._bg(lambda: self._act_then_refresh(lambda: self.state.provider.run(prog)))

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
            time.sleep(0.4)
            try:
                self.state.refresh()      # blocks until the read completes (worker thread)
                self.snap_ready.emit(None)  # repaint on the GUI thread
            except (Exception, RuntimeError):
                return                    # dialog closed mid-refresh -> stop quietly

    def _open_console(self) -> None:
        if self.live and hasattr(self.state.provider, "console"):
            self._console = Xv6Console(self, self.theme, self.state.provider, self.device)
            self._console.show(); self._console.raise_()
        elif self.on_console:
            self.on_console()

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
        if self._busy:
            return
        self._busy = True
        import threading

        def work():
            try:
                self.state.step() if step else self.state.refresh()
                self.snap_ready.emit(None)  # marshal back to the GUI thread
            except (Exception, RuntimeError):
                pass                        # incl. dialog closed mid-read
        threading.Thread(target=work, daemon=True).start()

    def _on_snap(self, _obj) -> None:
        self._busy = False
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
        snap = self.state.latest
        if snap is None:
            return
        t = self.theme.theme
        # process table
        self._proc_tbl.setRowCount(len(snap.procs))
        run = snap.running_pid
        for r, p in enumerate(snap.procs):
            for c, val in enumerate((str(p.pid), p.state, p.name)):
                it = QTableWidgetItem(val)
                if p.pid == run:
                    it.setForeground(QColor(_pid_color(p.pid)))
                    f = it.font(); f.setBold(True); it.setFont(f)
                elif p.state == "sleeping":
                    it.setForeground(QColor(t.faint))
                self._proc_tbl.setItem(r, c, it)
            # a kill button for user processes (never init/sh); live only
            if self.live and p.pid > 2:
                b = QPushButton("✕")
                b.setToolTip(f"kill pid {p.pid}")
                b.setFixedSize(28, 22)
                b.setStyleSheet(
                    f"QPushButton{{color:{t.muted};background:transparent;border:none;}}"
                    f"QPushButton:hover{{color:{t.accent_for('red')};}}")
                b.clicked.connect(lambda _c=False, pid=p.pid: self._kill(pid))
                self._proc_tbl.setCellWidget(r, 3, b)
            else:
                self._proc_tbl.removeCellWidget(r, 3)
        # per-CPU registers + memory (a column per core; falls back to one column single-CPU)
        cpu_regs = snap.cpu_regs or ({0: snap.cpu} if snap.cpu else {})
        cids = sorted(cpu_regs)
        self._reg_tbl.setColumnCount(len(cids))
        self._mem_tbl.setColumnCount(len(cids))
        heads = [f"CPU {c} · pid {cpu_regs[c].key('pid')}" for c in cids]
        self._reg_tbl.setHorizontalHeaderLabels(heads)
        self._mem_tbl.setHorizontalHeaderLabels(heads)
        for col, c in enumerate(cids):
            cs = cpu_regs[c]
            for row, key in enumerate(self._REG_ROWS):
                self._reg_tbl.setItem(row, col, QTableWidgetItem(cs.key(key)))
            mem_vals = [cs.key("satp"), cs.key("pc"), cs.key("sp"),
                        f"pid {cs.key('pid')} · asid {cs.key('satp')[-3:]}"]
            for row, val in enumerate(mem_vals):
                self._mem_tbl.setItem(row, col, QTableWidgetItem(val))
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

    def closeEvent(self, e) -> None:  # noqa: N802
        self._poll.stop()
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
