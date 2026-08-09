"""CPU journey — a step-driven walkthrough of a system call (trap) vs a context switch (swtch).

Pick a mode, then Step through the stages. At each stage the view shows the privilege band
(user/kernel) and process lane the CPU is in, and highlights which save-area is touched — the
TRAPFRAME (all user registers, on a trap) or the CONTEXT (14 callee-saved, on a swtch). Real
register values from the running process seed the syscall path.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from ..domain.cpu_journey import JOURNEY_TITLES, JOURNEYS
from .theme import ThemeManager, icons


class CpuJourney(QDialog):
    def __init__(self, parent, theme: ThemeManager, device=None, cpu=None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.device = device
        self.cpu = cpu                      # a CpuState (real regs) to seed the syscall path
        self._mode = "syscall"
        self._i = 0

        t = theme.theme
        self.setWindowTitle(f"CPU journey — {getattr(device, 'name', 'xv6')}")
        self.resize(820, 480)
        self.setStyleSheet(f"QDialog{{background:{t.bg};}}")
        root = QVBoxLayout(self)

        head = QLabel("A system call is a TRAP (same process, user↔kernel, saves the trapframe). "
                      "A context switch is swtch (a different process, kernel↔kernel, saves the "
                      "context). Preemption is both. Step through and watch which save-area moves.")
        head.setWordWrap(True); head.setStyleSheet(f"color:{t.muted};font-size:12px;")
        root.addWidget(head)

        modes = QHBoxLayout()
        self._mode_group = QButtonGroup(self); self._mode_group.setExclusive(True)
        self._mode_btns = {}
        for key in ("syscall", "context", "preempt"):
            b = QPushButton(JOURNEY_TITLES[key])
            b.setCheckable(True); b.setChecked(key == "syscall")
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
        self._tf = self._save_card("trapframe", "34 user registers · saved on every TRAP")
        self._ctx = self._save_card("context", "14 callee-saved · saved on every swtch")
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

        self._set_mode("syscall")

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

    def _set_mode(self, key) -> None:
        self._mode = key
        self._i = 0
        for k, b in self._mode_btns.items():
            b.setChecked(k == key)
        # rebuild the stage chips
        while self._stage_row.count():
            w = self._stage_row.takeAt(0).widget()
            if w:
                w.deleteLater()
        self._chips = []
        t = self.theme.theme
        for s in JOURNEYS[key]:
            c = QLabel(s.title)
            c.setAlignment(Qt.AlignCenter)
            c.setStyleSheet(
                f"color:{t.muted};background:{t.panel2};border:1px solid {t.line};"
                "border-radius:6px;padding:4px 6px;font-size:11px;")
            self._chips.append(c)
            self._stage_row.addWidget(c)
        self._render()

    def _step(self, d) -> None:
        stages = JOURNEYS[self._mode]
        self._i = max(0, min(len(stages) - 1, self._i + d))
        self._render()

    def _render(self) -> None:
        t = self.theme.theme
        stages = JOURNEYS[self._mode]
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
        lane = {"A": "process A", "sched": "scheduler", "B": "process B"}.get(s.lane, s.lane)
        self._band.setText(f"CPU is in: {s.band.upper()} mode · {lane}"
                           + (f"   · privilege {'change' if s.band == 'user' else 'stays S'}"
                              if self._mode != 'context' else "   · never leaves S"))
        self._caption.setText(s.caption)
        # seed real registers into the syscall dispatch caption
        if self._mode == "syscall" and s.title == "syscall()" and self.cpu is not None:
            self._caption.setText(
                s.caption + f"\n\nlive: a7={self.cpu.key('a7')}  a0={self.cpu.key('a0')}  "
                f"pc={self.cpu.key('pc')}")
        # highlight the active save-area
        for card, kind in ((self._tf, "trapframe"), (self._ctx, "context")):
            on = s.save == kind
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
