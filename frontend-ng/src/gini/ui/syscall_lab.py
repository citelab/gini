"""Syscall Lab — live histogram of system-call activity + an strace-style trace.

Reads gini_scdump over the serial (the agent's /sc, no gdb halt): per-syscall counters drive a
histogram of calls in the last 60 seconds (so you see what the machine is doing under the hood),
and a recent-call ring drives a running trace (`pid name(arg) = ret`). User-defined syscalls from
the Builder appear automatically, since counters are keyed by syscall number.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QVBoxLayout, QWidget,
)

from ..domain.xv6 import SyscallRate, parse_sccounts, parse_sctrace, syscall_name
from .theme import ThemeManager


class HistogramBars(QWidget):
    """Horizontal bars for the top syscalls by call count in the window."""

    def __init__(self, theme) -> None:
        super().__init__()
        self.theme = theme
        self._rows: list = []          # [(label, value)]
        self.setMinimumHeight(220)

    def set_data(self, rows) -> None:
        self._rows = list(rows)
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        t = self.theme.theme
        p.fillRect(self.rect(), QColor(t.panel))
        if not self._rows:
            p.setPen(QColor(t.muted))
            p.drawText(self.rect(), Qt.AlignCenter, "no syscalls yet — launch a program")
            return
        top = self._rows[:12]
        peak = max((v for _l, v in top), default=1) or 1
        rowh = min(24, max(16, (self.height() - 8) // max(len(top), 1)))
        label_w, count_w = 92, 52
        bar_x = label_w
        bar_max = max(40, self.width() - label_w - count_w - 12)
        y = 4
        for label, v in top:
            p.setPen(QColor(t.text))
            p.drawText(4, y, label_w - 6, rowh, Qt.AlignVCenter | Qt.AlignLeft, label)
            w = int(bar_max * (v / peak))
            p.fillRect(bar_x, y + 3, max(w, 1), rowh - 8, QColor(t.accent_for("blue")))
            p.setPen(QColor(t.muted))
            p.drawText(bar_x + bar_max + 6, y, count_w, rowh, Qt.AlignVCenter | Qt.AlignLeft,
                       str(v))
            y += rowh


class SyscallLab(QDialog):
    sc_ready = Signal(str)             # raw /sc text pushed from the poll worker

    def __init__(self, parent, theme: ThemeManager, device=None, sc_source=None,
                 name_extra=None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.device = device
        self._sc_source = sc_source or (lambda: "")
        self._name_extra = name_extra or {}
        self._rate = SyscallRate(window=60.0)
        self._busy = False

        t = theme.theme
        self.setWindowTitle(f"System Calls Lab — {getattr(device, 'name', 'xv6')}")
        self.resize(760, 560)
        self.setStyleSheet(f"QDialog{{background:{t.bg};}}")
        root = QVBoxLayout(self)

        head = QLabel("What the kernel is doing under the hood — system calls in the last 60s "
                      "(histogram) and the most recent calls (trace). No gdb; live over serial.")
        head.setWordWrap(True); head.setStyleSheet(f"color:{t.muted};font-size:12px;")
        root.addWidget(head)

        self._bars = HistogramBars(theme)
        root.addWidget(self._panel("Syscalls · calls in last 60s", self._bars), 1)

        self._trace = QPlainTextEdit(); self._trace.setReadOnly(True)
        self._trace.setStyleSheet(
            f"QPlainTextEdit{{background:{t.panel};color:{t.text};border:1px solid {t.line};"
            "border-radius:6px;font-family:monospace;font-size:12px;}")
        root.addWidget(self._panel("Trace · recent calls  (pid  name(arg) = ret)", self._trace), 1)

        self.sc_ready.connect(self._apply)
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

    def _fetch(self) -> None:
        if self._busy:
            return
        self._busy = True
        import threading

        def work():
            try:
                txt = self._sc_source()
            except Exception:
                txt = ""
            self.sc_ready.emit(txt or "")
        threading.Thread(target=work, daemon=True).start()

    def _apply(self, txt) -> None:
        self._busy = False
        import time
        counts = parse_sccounts(txt)
        if counts:
            self._rate.add(time.monotonic(), counts)
        rates = self._rate.rates()
        rows = sorted(((syscall_name(n, self._name_extra), v) for n, v in rates.items()),
                      key=lambda kv: -kv[1])
        self._bars.set_data(rows)
        evs = parse_sctrace(txt)
        if evs:
            lines = [f"{e.pid:>3}  {syscall_name(e.num, self._name_extra)}({e.a0}) = {e.ret}"
                     for e in evs]
            sb = self._trace.verticalScrollBar()
            at_bottom = sb.value() >= sb.maximum() - 4
            self._trace.setPlainText("\n".join(lines))
            if at_bottom:
                sb.setValue(sb.maximum())

    def closeEvent(self, e) -> None:  # noqa: N802
        self._poll.stop()
        super().closeEvent(e)
