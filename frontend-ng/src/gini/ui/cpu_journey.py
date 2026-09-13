"""CPU journey — what the CPU does on each kind of TRAP, one step at a time.

The modes are the SIX CAUSES the kernel sorts traps into, the same six the Traps & Interrupts Lab
charts and offers in its catch menu: syscall, pagefault, timer, device, illegal, other. One
vocabulary for one subject. Every one of them is a trap — in RISC-V and in xv6 `trap` is the
umbrella, and `scause` bit 63 says whether it was an INTERRUPT (timer, device) or an EXCEPTION
(the rest).

At each stage the view shows the privilege band and process lane the CPU is in, and which save area
is touched: the TRAPFRAME (31 user registers plus the user pc, written by uservec on a trap from
user mode) or the CONTEXT (ra, sp, s0–s11, written by swtch). A kernel-mode trap writes neither,
and both cards stay dark for it.

With a real capture the CAPTURE picks the path — where the trap was taken, which scause, whether
the quantum was reached — so a caption never has to hedge about which case it is describing, and
the lanes show the actual pid rather than "process A".
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from ..domain.cpu_journey import (
    INTERRUPT_MODES, JOURNEY_HEADLINE, JOURNEY_SHORT, JOURNEY_TITLES, JOURNEYS, MODES, mode_for,
    stages_for,
)
from .theme import ThemeManager, icons
from .windowing import standalone


class CpuJourney(QDialog):
    def __init__(self, parent, theme: ThemeManager, device=None, cpu=None, frame=None,
                 procs=None) -> None:
        super().__init__(parent)
        # Its own window, not an owned dialog: on Windows an owned dialog gets no
        # taskbar button and Alt+Tab skips it. See ui/windowing.
        standalone(self)
        self.theme = theme
        self.device = device
        self.cpu = cpu                      # a CpuState (real regs) to seed the syscall path
        self.frame = frame                  # a TrapFrame from /trapcatch (a real frozen trap)
        # pid -> name, so a captured lane can say "pid 7 (spin)" instead of "process A". From the
        # live process table the Lab already holds; empty in reference mode (falls back to "process
        # A"). Accepts a {pid: name} dict or a list of Proc(.pid/.name).
        self._names = {}
        if isinstance(procs, dict):
            self._names = {int(k): v for k, v in procs.items()}
        else:
            for p in (procs or []):
                pid = getattr(p, "pid", None)
                if pid is not None:
                    self._names[int(pid)] = getattr(p, "name", "")
        # Derive the journey from what was CAUGHT, not a hardcoded "syscall". A timer opens the
        # preemption walkthrough, not a system-call one — the old default narrated every captured
        # trap as `ecall → syscall() → sret`, which is what "does not make sense" meant.
        self._mode = self._mode_for(frame)
        self._i = 0

        t = theme.theme
        self.setWindowTitle(f"CPU journey — {getattr(device, 'name', 'xv6')}")
        self.resize(820, 500)
        self.setStyleSheet(f"QDialog{{background:{t.bg};}}")
        root = QVBoxLayout(self)

        head = QLabel("Every one of these is a TRAP; scause bit 63 says which kind. "
                      "Step through and watch which save-area moves.")
        head.setWordWrap(True); head.setStyleSheet(f"color:{t.muted};font-size:12px;")
        self._head = head                   # retitled per walkthrough in _set_mode
        root.addWidget(head)

        # a live banner when we froze a real trap (Phase 2): its actual scause/sepc/stval
        self._live = QLabel(); self._live.setWordWrap(True)
        self._live.setVisible(bool(frame))
        if frame is not None and getattr(frame, "ok", False):
            acc = t.accent_for("amber")
            self._live.setStyleSheet(
                f"color:{acc};background:{t.panel2};border:1px solid {acc};border-radius:8px;"
                "padding:6px 10px;font-family:monospace;font-size:12px;")
            msg = f"● froze a live trap: {frame.kind_name} · scause {frame.scause} · sepc {frame.sepc}"
            if frame.stval and frame.stval not in ("0x0", "0x0000000000000000"):
                msg += f" · stval {frame.stval}"
            if frame.pid is not None and frame.pid >= 0:
                msg += f" · pid {frame.pid}"
            if not getattr(frame, "from_user", True):
                msg += "  ·  from KERNEL mode (no trapframe written)"
            if not self._mode:
                # Nothing describes this trap. Say so — and, critically, open NOTHING. Substituting
                # a walkthrough here is what told a student that pid 4 executed an `ecall` when a
                # device had interrupted it.
                msg += "  —  no step-by-step matches this trap; the values above are the real trap"
            self._live.setText(msg)
        elif frame is not None:
            self._live.setStyleSheet(f"color:{t.faint};font-size:12px;")
            self._live.setText("● couldn't freeze a trap (kernel idle) — showing the reference "
                               "walkthrough. Launch a program and try again.")
        root.addWidget(self._live)

        modes = QHBoxLayout()
        self._mode_group = QButtonGroup(self); self._mode_group.setExclusive(True)
        self._mode_btns = {}
        # Every walkthrough, not just the three that existed: a student who caught a device
        # interrupt should be able to read the page-fault story too. Short labels because seven
        # full titles do not fit one row; the full title is the tooltip.
        for key in MODES:
            b = QPushButton(JOURNEY_SHORT.get(key, key))
            b.setToolTip(JOURNEY_TITLES.get(key, key))
            b.setCheckable(True); b.setChecked(key == self._mode)
            b.setStyleSheet(self._btn_css())
            b.clicked.connect(lambda _c=False, k=key: self._set_mode(k))
            self._mode_group.addButton(b); self._mode_btns[key] = b
            modes.addWidget(b)
        modes.addStretch(1)
        root.addLayout(modes)

        self._stage_row = QHBoxLayout(); self._stage_row.setSpacing(4)
        sw = QWidget(); sw.setLayout(self._stage_row)
        root.addWidget(sw)

        self._band = QLabel(); self._band.setStyleSheet(f"color:{t.muted};font-size:12px;")
        root.addWidget(self._band)

        self._caption = QLabel(); self._caption.setWordWrap(True)
        self._caption.setStyleSheet(
            f"color:{t.text};font-size:13px;background:{t.panel2};border:1px solid {t.line};"
            "border-radius:10px;padding:12px;")
        self._caption.setMinimumHeight(90)
        root.addWidget(self._caption, 1)

        save = QHBoxLayout()
        self._tf = self._save_card("trapframe",
                                   "31 user registers + the user pc · saved on every trap from user mode")
        self._ctx = self._save_card("context", "ra, sp, s0–s11 — 14 registers · saved on every swtch")
        save.addWidget(self._tf); save.addWidget(self._ctx)
        root.addLayout(save)

        nav = QHBoxLayout()
        self._prev = QPushButton("  Prev"); self._prev.setStyleSheet(self._btn_css())
        self._prev.clicked.connect(lambda: self._step(-1))
        self._next = QPushButton("  Step ▸"); self._next.setStyleSheet(self._btn_css())
        self._next.clicked.connect(lambda: self._step(1))
        self._pos = QLabel(); self._pos.setStyleSheet(f"color:{t.muted};font-size:12px;")
        nav.addWidget(self._pos); nav.addStretch(1)
        nav.addWidget(self._prev); nav.addWidget(self._next)
        root.addLayout(nav)

        self._set_mode(self._mode)          # the mode DERIVED from the caught trap, not a default

    def _btn_css(self) -> str:
        t = self.theme.theme
        return (f"QPushButton{{color:{t.text};background:{t.panel2};border:1px solid {t.line};"
                f"border-radius:8px;padding:6px 12px;}}"
                f"QPushButton:checked{{border-color:{t.accent};color:{t.accent_for('blue')};}}"
                f"QPushButton:hover{{border-color:{t.accent};}}"
                f"QPushButton:disabled{{color:{t.faint};}}")

    def _save_card(self, name, sub) -> QFrame:
        t = self.theme.theme
        f = QFrame(); f.setObjectName(name)
        v = QVBoxLayout(f); v.setContentsMargins(10, 8, 10, 8)
        title = QLabel(name); title.setObjectName("t")
        sublbl = QLabel(sub); sublbl.setObjectName("s")
        v.addWidget(title); v.addWidget(sublbl)
        f._title, f._sub = title, sublbl
        return f

    def _resolve_stages(self, mode):
        """The stage list to show for a mode. Preempt has two paths: the full trap+switch, and the
        short 'quantum not reached -> same process returns' path, chosen from the captured counters.
        Reference mode (no capture) always shows the full canonical path."""
        fr = self.frame
        if fr is not None and getattr(fr, "ok", False) and mode == mode_for(
                getattr(fr, "kind", -1)):
            # This IS the captured trap's own mode, so the capture decides the path: where it was
            # taken, which scause, whether the quantum was reached. Prose never has to hedge.
            return stages_for(getattr(fr, "kind", -1),
                              bool(getattr(fr, "from_user", True)),
                              getattr(fr, "scause", None),
                              getattr(fr, "qticks", None), getattr(fr, "quantum", None))
        # A mode the student picked to read as a reference, or no capture at all.
        return JOURNEYS.get(mode) or []

    def _lane_label(self, slot: str) -> str:
        """A process lane -> display text. With a real capture, lane A is the captured pid; lane B
        is never known at capture time (the switch target is picked afterwards). Reference mode
        (no capture) falls back to the generic 'process A'."""
        if slot == "sched":
            return "the scheduler"
        if slot == "B":
            return "the next runnable process"
        fr = self.frame                                  # slot "A" — the process the story is about
        if fr is None or not getattr(fr, "ok", False):
            return "process A"
        pid = getattr(fr, "pid", None)
        if pid is None or pid < 0:
            return "process A"
        if pid == 0:
            return "the kernel (no process on this core)"
        name = self._names.get(pid)
        return f"pid {pid}" + (f" ({name})" if name else "")

    def _set_mode(self, key) -> None:
        self._mode = key
        self._i = 0
        self._stages = self._resolve_stages(key)
        if getattr(self, "_head", None) is not None:
            kindword = "INTERRUPT" if key in INTERRUPT_MODES else "EXCEPTION"
            self._head.setText(
                (f"<b>{key}</b> — a trap, and an {kindword} (scause bit 63 "
                 f"{'set' if key in INTERRUPT_MODES else 'clear'}). "
                 + JOURNEY_HEADLINE.get(key, "")) if key in JOURNEY_HEADLINE else
                "Pick a walkthrough to read how that kind of trap is handled.")
        for k, b in self._mode_btns.items():
            b.setChecked(k == key)
        # rebuild the stage chips
        while self._stage_row.count():
            w = self._stage_row.takeAt(0).widget()
            if w:
                w.deleteLater()
        self._chips = []
        t = self.theme.theme
        for s in self._stages:
            c = QLabel(s.title)
            c.setAlignment(Qt.AlignCenter)
            c.setStyleSheet(
                f"color:{t.muted};background:{t.panel2};border:1px solid {t.line};"
                "border-radius:6px;padding:4px 6px;font-size:11px;")
            self._chips.append(c)
            self._stage_row.addWidget(c)
        self._render()

    def _step(self, d) -> None:
        stages = self._stages
        self._i = max(0, min(len(stages) - 1, self._i + d))
        self._render()

    def _mode_for(self, frame) -> str:
        """Which journey to open for a caught trap.

        Delegates to the domain's `journey_for`, which decides on the kind, `from_user` AND the
        scause. With no capture at all (reference mode, or a catch that timed out) the system-call
        walkthrough is the honest default: there is no real trap for it to contradict.

        What this must never do again is substitute a walkthrough for a trap it does not describe.
        A captured device interrupt used to open the SYSTEM CALL journey and tell the student that
        pid 4 executed an `ecall` — with the real pid attached, under a banner that said the
        opposite. `journey_for` returns "" when nothing fits, and `_render` says so.
        """
        if frame is None or not getattr(frame, "ok", False):
            return "syscall"
        return mode_for(getattr(frame, "kind", 0))

    #: Stage titles that open a walkthrough on the instant the trap was taken. The captured CSRs
    #: belong there, on whichever path the capture chose — a page fault without its stval on screen
    #: is missing the one number the student came for.
    _ENTRY_TITLES = frozenset({
        "ecall", "the access faults", "instruction fetch faults", "timer fires",
        "timer fires in the kernel", "device fires", "device fires in the kernel",
        "the instruction traps", "an exception in the kernel",
    })

    def _live_note(self, title) -> str:
        """The real-values line appended to a stage caption.

        Everything here comes FROM the capture, never from the mode: the same entry stage serves a
        user-mode and a kernel-mode path, and the values are whatever was frozen. That is the rule
        the captions follow too — prose asserts only what is true of every trap routed to it, and
        anything that varies is read off the frame.
        """
        fr = self.frame
        if fr is None or not getattr(fr, "ok", False):
            # no frozen trap — seed the dispatch stage from the running proc, as before
            if title == "syscall()" and self.cpu is not None:
                return (f"live: a7={self.cpu.key('a7')}  a0={self.cpu.key('a0')}  "
                        f"pc={self.cpu.key('pc')}")
            return ""
        # A capture only describes the walkthrough for its OWN cause. Reading another one as a
        # reference must not have this trap's numbers pasted into it.
        if self._mode != mode_for(getattr(fr, "kind", -1)):
            return ""
        r = fr.regs
        if title in self._ENTRY_TITLES:
            s = f"scause={fr.scause} → {fr.kind_name}   ·   sepc={fr.sepc}"
            if fr.stval and fr.stval not in ("0x0", "0x0000000000000000"):
                s += f"   ·   stval (faulting address) = {fr.stval}"
            if self._mode == "device" and fr.pid is not None and fr.pid >= 0:
                s += (f"   ·   interrupted pid {fr.pid} — a BYSTANDER, not necessarily whose "
                      f"device this was")
            return s
        if title == "yield()":
            n, q = int(getattr(fr, "qticks", 0) or 0) + 1, int(getattr(fr, "quantum", 0) or 0)
            if not q:
                return ""
            if n >= q:
                return (f"quantum reached ({n} of {q}) → this tick DID preempt: "
                        f"yield() → sched() → swtch")
            return (f"quantum NOT reached ({n} of {q}) → returned to the SAME process; "
                    f"no swtch this tick")
        if title == "yield, or not":
            who = "no process on this core (pid 0)" if not fr.pid else f"pid {fr.pid}"
            return f"myproc() at the time: {who}"
        if title == "kernelvec":
            return "no trapframe was written for this trap — sstatus.SPP was 1 (kernel)"
        if title == "uservec":
            parts = [f"{k}={r[k]}" for k in ("ra", "sp", "a0", "a7") if k in r]
            return ("this trap's user registers, saved into the trapframe:  " + "  ".join(parts)
                    if parts else "this trap's user registers are saved into the trapframe")
        if title == "usertrap":
            s = f"scause={fr.scause} → {fr.kind_name}   ·   sepc={fr.sepc}"
            if fr.stval and fr.stval not in ("0x0", "0x0000000000000000"):
                s += f"   ·   stval (faulting address) = {fr.stval}"
            return s
        if title == "syscall()":
            a7, a0 = r.get("a7", ""), r.get("a0", "")
            return f"live: a7={a7}  a0={a0}" if (a7 or a0) else ""
        return ""

    def _render(self) -> None:
        t = self.theme.theme
        stages = self._stages
        if not stages:
            # Nothing describes this trap (see _mode_for). An empty pane that says why beats a
            # confident walkthrough of something that did not happen — the banner above carries the
            # REAL captured values, and the buttons stay live so a reference can be read on purpose.
            self._band.setText("")
            self._caption.setText(
                "No step-by-step walkthrough matches this trap.\n\n"
                "The banner above shows what was actually captured — that part is real. Pick a "
                "walkthrough above to read it as a reference; it will describe that kind of trap, "
                "not this one.")
            for card, kind in ((self._tf, "trapframe"), (self._ctx, "context")):
                card._title.setText(kind)
            self._pos.setText("")
            self._prev.setEnabled(False)
            self._next.setEnabled(False)
            return
        s = stages[self._i]
        for idx, c in enumerate(self._chips):
            active = idx == self._i
            done = idx < self._i
            col = t.accent_for("blue") if active else (t.text if done else t.muted)
            border = t.accent if active else t.line
            c.setStyleSheet(
                f"color:{col};background:{t.panel2};border:1px solid {border};"
                f"border-radius:6px;padding:4px 6px;font-size:11px;"
                f"{'font-weight:600;' if active else ''}")
        lane_a, lane_b = self._lane_label("A"), self._lane_label("B")
        lane = {"A": lane_a, "sched": "the scheduler", "B": lane_b}.get(s.lane, s.lane)
        # Band: a context switch never leaves S; a trap CAPTURED in kernel mode never entered user
        # (so it shows KERNEL throughout, not a user->kernel crossing — §1.6); a from-user trap
        # crosses U<->S per the stage.
        fr = self.frame
        kmode = fr is not None and getattr(fr, "ok", False) and not getattr(fr, "from_user", True)
        band_disp = "KERNEL" if kmode else s.band.upper()
        if self._mode == "context":
            # A context switch is NOT a trap, so gini_traprec() never sees one and no capture can
            # drive this — badge it so the empty live values read as intent, not a bug (§1.2 C6).
            suffix = "   · never leaves S  ·  reference walkthrough (a context switch is not a trap)"
        elif kmode:
            suffix = "   · kernel-mode trap · no user/kernel crossing"
        else:
            suffix = f"   · privilege {'change' if s.band == 'user' else 'stays S'}"
        self._band.setText(f"CPU is in: {band_disp} mode · {lane}{suffix}")
        # seed the trap-entry captions with real values — from a frozen trap if we have one,
        # else the running proc's registers (the old behaviour), else nothing. Captions are {a}/{b}
        # templates; format BEFORE appending the live note (which may itself contain no braces).
        note = self._live_note(s.title)
        self._caption.setText(s.caption.format(a=lane_a, b=lane_b) + (f"\n\n{note}" if note else ""))
        # highlight the active save-area — but a trap CAPTURED in kernel mode writes no trapframe,
        # so both cards stay unlit for it (§1.5/§1.6; the banner already says "no trapframe written").
        for card, kind in ((self._tf, "trapframe"), (self._ctx, "context")):
            on = (s.save == kind) and not kmode
            accent = t.accent_for("green" if kind == "trapframe" else "amber")
            card.setStyleSheet(
                f"QFrame#{kind}{{background:{t.panel2};border:1px solid "
                f"{accent if on else t.line};border-radius:10px;}}")
            card._title.setStyleSheet(
                f"color:{accent if on else t.text};font-size:13px;font-weight:600;border:none;"
                + (f"" if on else ""))
            card._sub.setStyleSheet(f"color:{t.muted};font-size:11px;border:none;")
            card._title.setText(("▸ " if on else "") + kind + ("  (writing)" if on else ""))
        self._pos.setText(f"stage {self._i + 1} / {len(stages)}")
        self._prev.setEnabled(self._i > 0)
        self._next.setEnabled(self._i < len(stages) - 1)
