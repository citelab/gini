"""Trap Lab — live trap-taxonomy histogram + a running feed of individual traps.

Reads gini_trapdump over the serial (the agent's /traps, no gdb halt): per-kind counters drive a
histogram of traps in the last 60s (syscall / page-fault / timer / device / illegal / other), and
a ring drives a live feed (pid · kind · epc · faulting-address). The lesson students take away is
that a system call, a timer preemption, and a demand-paged page are the SAME mechanism with
different causes. "Step a trap ▸" opens the CPU journey to dissect one frame by frame.

Same shape as the Syscall Lab: an injected `traps_source` (the bridge's /traps live, or
DemoScheduler.traps() offline) polled off the GUI thread, so the whole face is testable against a
fake feed.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout,
    QWidget,
)

from ..domain.xv6 import (
    TRAP_KINDS, TrapRate, parse_alarms, parse_trapcounts, parse_traptrace, trap_kind_name,
)
from .theme import ThemeManager

# trap kinds the "Step a trap" catcher can target (conditioned gdb breakpoint); "any" = next trap
_CATCH_KINDS = ["any", "pagefault", "syscall", "timer", "illegal", "device"]

# one colour per trap kind, so the histogram reads at a glance
_KIND_ACCENT = {0: "blue", 1: "purple", 2: "amber", 3: "cyan", 4: "red", 5: "slate"}


class TrapBars(QWidget):
    """Horizontal bars for the six trap kinds, one colour per kind."""

    def __init__(self, theme) -> None:
        super().__init__()
        self.theme = theme
        self._rows: list = []          # [(kind_index, label, value)]
        self.setMinimumHeight(180)

    def set_data(self, rows) -> None:
        self._rows = list(rows)
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        t = self.theme.theme
        p.fillRect(self.rect(), QColor(t.panel))
        if not self._rows or not any(v for _k, _l, v in self._rows):
            p.setPen(QColor(t.muted))
            p.drawText(self.rect(), Qt.AlignCenter, "no traps yet — run the machine (or use Demo)")
            return
        peak = max((v for _k, _l, v in self._rows), default=1) or 1
        rowh = min(26, max(18, (self.height() - 8) // max(len(self._rows), 1)))
        label_w, count_w = 96, 56
        bar_max = max(40, self.width() - label_w - count_w - 12)
        y = 4
        for kind, label, v in self._rows:
            p.setPen(QColor(t.text))
            p.drawText(4, y, label_w - 6, rowh, Qt.AlignVCenter | Qt.AlignLeft, label)
            w = int(bar_max * (v / peak))
            p.fillRect(label_w, y + 3, max(w, 1), rowh - 8,
                       QColor(t.accent_for(_KIND_ACCENT.get(kind, "blue"))))
            p.setPen(QColor(t.muted))
            p.drawText(label_w + bar_max + 6, y, count_w, rowh,
                       Qt.AlignVCenter | Qt.AlignLeft, str(v))
            y += rowh


class TrapLab(QDialog):
    traps_ready = Signal(str)          # raw /traps text pushed from the poll worker
    alarms_ready = Signal(str)         # raw gini_dump text (ALARM lines) from the poll worker
    caught = Signal(object)            # a TrapFrame from a live catch (or None), off the worker

    def __init__(self, parent, theme: ThemeManager, device=None, traps_source=None,
                 on_step=None, catch_source=None, alarm_source=None, on_play=None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.device = device
        self._src = traps_source or (lambda: "")
        self._on_step = on_step
        self._on_play = on_play             # callable() opening the decode-the-trap game; may be None
        self._catch = catch_source          # callable(kind) -> TrapFrame (live gdb freeze); may be None
        self._alarm_src = alarm_source      # callable() -> gini_dump text with ALARM lines; may be None
        self._rate = TrapRate(window=60.0)
        self._busy = False
        self._closed = False

        t = theme.theme
        self.setWindowTitle(f"Trap Lab — {getattr(device, 'name', 'xv6')}")
        self.resize(760, 580)
        self.setStyleSheet(f"QDialog{{background:{t.bg};}}")
        root = QVBoxLayout(self)

        head = QLabel("Every trap the CPU takes, classified by cause. A system call, a timer "
                      "preemption, and a demand-paged page are the same mechanism — watch the mix "
                      "as programs run. No gdb; live over serial.")
        head.setWordWrap(True); head.setStyleSheet(f"color:{t.muted};font-size:12px;")
        root.addWidget(head)

        self._bars = TrapBars(theme)
        root.addWidget(self._panel("Traps · by cause, last 60s", self._bars), 1)

        self._feed = QPlainTextEdit(); self._feed.setReadOnly(True)
        self._feed.setStyleSheet(
            f"QPlainTextEdit{{background:{t.panel};color:{t.text};border:1px solid {t.line};"
            "border-radius:6px;font-family:monospace;font-size:12px;}")
        root.addWidget(self._panel("Feed · recent traps  (pid  kind  epc  addr)", self._feed), 1)

        # the sigalarm-lab strip — only shown when an alarm source is wired (Phase 3). It's the
        # live proof a student's periodic handler works: the countdown ticks, `on` flips on fire.
        self._alarms = QLabel()
        self._alarms.setWordWrap(True)
        self._alarms.setStyleSheet(
            f"color:{t.text};background:{t.panel2};border:1px solid {t.line};border-radius:8px;"
            "padding:6px 10px;font-family:monospace;font-size:12px;")
        self._alarms.setVisible(self._alarm_src is not None)
        root.addWidget(self._alarms)

        row = QHBoxLayout()
        if self._on_play is not None:                 # in-lab game: decode the trap from its scause
            play = QPushButton("  Play: decode the trap")
            play.setStyleSheet(
                f"QPushButton{{color:{t.accent_for('purple')};background:{t.panel2};"
                f"border:1px solid {t.line};border-radius:8px;padding:6px 12px;}}"
                f"QPushButton:hover{{border-color:{t.accent};}}")
            play.clicked.connect(lambda: self._on_play())
            row.addWidget(play)
        row.addStretch(1)
        self._kind = QComboBox(); self._kind.addItems(_CATCH_KINDS)
        self._kind.setToolTip("Which kind of trap to freeze")
        self._kind.setStyleSheet(
            f"QComboBox{{color:{t.text};background:{t.panel2};border:1px solid {t.line};"
            "border-radius:6px;padding:4px 8px;}")
        catch_lbl = QLabel("catch:"); catch_lbl.setStyleSheet(f"color:{t.muted};font-size:12px;")
        row.addWidget(catch_lbl)
        row.addWidget(self._kind)
        self._step_btn = QPushButton("  Step a trap ▸")
        self._step_btn.setToolTip("Freeze a real trap of this kind and dissect it in the CPU journey")
        self._step_btn.setStyleSheet(
            f"QPushButton{{color:{t.text};background:{t.panel2};border:1px solid {t.line};"
            f"border-radius:8px;padding:6px 12px;}}QPushButton:hover{{border-color:{t.accent};}}")
        self._step_btn.clicked.connect(self._step)
        row.addWidget(self._step_btn)
        root.addLayout(row)

        self.traps_ready.connect(self._apply)
        self.alarms_ready.connect(self._apply_alarms)
        self.caught.connect(self._on_caught)
        self._poll = QTimer(self); self._poll.timeout.connect(self._fetch)
        self._poll.start(1500)
        self._fetch()

    def _panel(self, title, inner) -> QFrame:
        t = self.theme.theme
        f = QFrame(); f.setStyleSheet(
            f"QFrame{{background:{t.panel2};border:1px solid {t.line};border-radius:10px;}}")
        v = QVBoxLayout(f); v.setContentsMargins(10, 8, 10, 10)
        h = QLabel(title); h.setStyleSheet(
            f"color:{t.muted};font-size:11px;font-weight:600;border:none;")
        v.addWidget(h)
        inner.setStyleSheet((inner.styleSheet() or "") + "border:none;")
        v.addWidget(inner, 1)
        return f

    def _step(self) -> None:
        """Freeze a live trap (off the GUI thread — the gdb catch can take up to a few seconds),
        then open the journey seeded with it. With no live catch source, open the authored
        journey immediately."""
        if not callable(self._on_step):
            return
        if callable(self._catch):
            kind = self._kind.currentText()
            self._step_btn.setEnabled(False)
            self._step_btn.setText("  freezing a trap…")
            import threading

            def work():
                try:
                    fr = self._catch(kind)
                except Exception:
                    fr = None
                if not self._closed:
                    self.caught.emit(fr)
            threading.Thread(target=work, daemon=True).start()
        else:
            self._on_step(None)

    def _on_caught(self, frame) -> None:
        self._step_btn.setEnabled(True)
        self._step_btn.setText("  Step a trap ▸")
        if not self._closed and callable(self._on_step):
            self._on_step(frame)

    def _fetch(self) -> None:
        if self._busy or self._closed:
            return
        self._busy = True
        import threading

        def work():
            try:
                txt = self._src()
            except Exception:
                txt = ""
            atxt = ""
            if callable(self._alarm_src):
                try:
                    atxt = self._alarm_src() or ""
                except Exception:
                    atxt = ""
            if not self._closed:                # don't signal a dialog that's being torn down
                self.traps_ready.emit(txt or "")
                if callable(self._alarm_src):
                    self.alarms_ready.emit(atxt)
        threading.Thread(target=work, daemon=True).start()

    def _apply_alarms(self, txt) -> None:
        if self._closed:
            return
        alarms = parse_alarms(txt)
        if not alarms:
            self._alarms.setText("⏰ no alarm set — a process calls sigalarm(interval, handler) to "
                                 "run a periodic user-level handler (the sigalarm lab).")
            return
        lines = []
        for pid in sorted(alarms):
            a = alarms[pid]
            state = " · handler RUNNING" if a.on else ""
            lines.append(f"⏰ pid {pid} · every {a.interval} ticks · fires in {a.remaining} · "
                         f"handler {a.handler}{state}")
        self._alarms.setText("\n".join(lines))

    def _apply(self, txt) -> None:
        self._busy = False
        if self._closed:
            return
        import time
        counts = parse_trapcounts(txt)
        if counts:
            self._rate.add(time.monotonic(), counts)
        rates = self._rate.rates()
        # all six kinds, biggest first, so the mix is always fully visible (zeros included)
        rows = sorted(((k, trap_kind_name(k), rates.get(k, 0)) for k in TRAP_KINDS),
                      key=lambda r: -r[2])
        self._bars.set_data(rows)
        evs = parse_traptrace(txt)
        if evs:
            lines = [f"{e.pid:>3}  {trap_kind_name(e.kind):<9} {e.epc}"
                     + (f"  {e.tval}" if e.tval not in ("0x0", "0x0000000000000000") else "")
                     for e in evs]
            sb = self._feed.verticalScrollBar()
            at_bottom = sb.value() >= sb.maximum() - 4
            self._feed.setPlainText("\n".join(lines))
            if at_bottom:
                sb.setValue(sb.maximum())

    def closeEvent(self, e) -> None:  # noqa: N802
        self._closed = True
        self._poll.stop()
        super().closeEvent(e)
