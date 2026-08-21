"""Lock Lab — live spinlock contention for an xv6 Machine.

Contention is the canonical *invisible* kernel phenomenon: a CPU spinning on a lock looks exactly
like a CPU doing work. Courses teach it by having students read counters printed by a test
program. Here the counters are live, so a student can change something and watch contention move.

Two numbers per lock, straight from the kernel (`gini_lockdump`, no gdb halt):
  acquires : how often the lock was taken
  spins    : FAILED test-and-set attempts — time a core burned waiting for another core

`spins / acquires` is the contention ratio and the thing worth optimising. It is **zero on a
single-core kernel by construction** (no second CPU to spin against), which is why GINI now boots
xv6 with two harts — the panel says so explicitly rather than showing a confusing all-zero board.

Rendering is pure over a `[LockStat]` list (domain/xv6.parse_locks), so the layout maths is
unit-testable without Qt; the controller polls `/locks` off the GUI thread.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from ..domain.xv6 import parse_lock_cpus, parse_locks
from .theme import ThemeManager, icons
from .theme.manager import scale_css as _scss

# Above this many spins per acquire a lock is worth splitting — the threshold is a teaching
# heuristic, not a hard rule, and the panel says which side of it each lock is on.
HOT = 0.5


def bar_rows(locks, limit: int = 8) -> list:
    """[(name, acquires, spins, ratio, frac)] for the most contended locks, where `frac` scales
    each bar against the worst offender. Pure — the panel is just this list drawn."""
    top = [l for l in locks if l.acquires][:limit]
    worst = max((l.contention for l in top), default=0.0)
    return [(l.name, l.acquires, l.spins, l.contention,
             (l.contention / worst) if worst else 0.0) for l in top]


class ContentionBars(QWidget):
    """One row per lock: name, a bar scaled to the worst offender, and the raw counters."""

    ROW_H = 26

    def __init__(self, theme) -> None:
        super().__init__()
        self.theme = theme
        self._rows: list = []
        self.setMinimumHeight(160)

    def set_locks(self, locks) -> None:
        self._rows = bar_rows(locks)
        self.setMinimumHeight(max(120, 12 + self.ROW_H * max(len(self._rows), 1)))
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        t = self.theme.theme
        p.fillRect(self.rect(), QColor(t.panel))
        if not self._rows:
            p.setPen(QColor(t.faint))
            p.drawText(self.rect(), Qt.AlignCenter,
                       "no lock telemetry — rebuild the xv6 image")
            return
        lblw, numw = 96, 150
        for i, (name, acq, spins, ratio, frac) in enumerate(self._rows):
            y = 6 + i * self.ROW_H
            p.setFont(QFont("monospace", 9))
            p.setPen(QColor(t.text))
            p.drawText(6, y, lblw, self.ROW_H - 6, Qt.AlignVCenter | Qt.AlignLeft, name)
            track = QRectF(lblw + 8, y + 5, max(20, self.width() - lblw - numw - 20),
                           self.ROW_H - 14)
            p.setBrush(QColor(t.panel2)); p.setPen(Qt.NoPen)
            p.drawRoundedRect(track, 3, 3)
            hot = ratio >= HOT
            p.setBrush(QColor(t.accent_for("red" if hot else "green")))
            p.drawRoundedRect(QRectF(track.left(), track.top(),
                                     max(2.0, track.width() * frac), track.height()), 3, 3)
            p.setFont(QFont("monospace", 8))
            p.setPen(QColor(t.muted))
            p.drawText(int(track.right()) + 8, y, numw, self.ROW_H - 6,
                       Qt.AlignVCenter | Qt.AlignLeft,
                       f"{ratio:5.2f} spins/acq   {acq:,} acq")


class LockLab(QDialog):
    """The Lock Lab window: contention bars + the counters, refreshed live."""

    locks_ready = Signal(str)

    def __init__(self, parent, theme: ThemeManager, device=None, provider=None,
                 live: bool = False) -> None:
        super().__init__(parent)
        self.theme = theme
        self.device = device
        self.provider = provider
        self.live = live
        self._closed = False
        self._busy = False

        t = theme.theme
        self.setWindowTitle(f"Lock Lab — {getattr(device, 'name', 'xv6')}")
        self.resize(760, 520)
        self.setStyleSheet(f"QDialog{{background:{t.bg};}}")
        root = QVBoxLayout(self)

        head = QHBoxLayout()
        ic = QLabel(); ic.setPixmap(icons.render_pixmap("host", t.accent_for("amber"), 22))
        title = QLabel(f"  Lock Lab — {getattr(device, 'name', 'xv6')}")
        title.setStyleSheet(_scss(f"color:{t.text};font-size:16px;font-weight:600;"))
        head.addWidget(ic); head.addWidget(title); head.addStretch(1)
        self._reset_btn = QPushButton("  Reset counters")
        self._reset_btn.setToolTip("Zero every lock's counters, so you can measure ONE workload")
        self._reset_btn.setStyleSheet(
            f"QPushButton{{color:{t.text};background:{t.panel2};border:1px solid {t.line};"
            f"border-radius:8px;padding:5px 11px;}}QPushButton:hover{{border-color:{t.accent};}}")
        self._reset_btn.clicked.connect(self._reset)
        self._reset_btn.setEnabled(bool(live))
        head.addWidget(self._reset_btn)
        root.addLayout(head)

        self._note = QLabel(); self._note.setWordWrap(True)
        self._note.setStyleSheet(_scss(f"color:{t.muted};font-size:11px;"))
        root.addWidget(self._note)

        box = QFrame(); box.setStyleSheet(
            _scss(f"QFrame{{background:{t.panel};border:1px solid {t.line};border-radius:10px;}}"))
        bl = QVBoxLayout(box); bl.setContentsMargins(10, 10, 10, 10)
        cap = QLabel("Spinlock contention  ·  spins per acquire")
        cap.setStyleSheet(_scss(f"color:{t.muted};font-size:12px;border:none;"))
        bl.addWidget(cap)
        self._bars = ContentionBars(theme)
        bl.addWidget(self._bars)
        root.addWidget(box, 1)

        hint = QLabel(
            "A spin is a FAILED test-and-set: one core waiting on another. Ratios near zero mean "
            "the lock is fine; a hot lock is a design problem — it protects too much, and the fix "
            "is to split it (per-CPU state, or hash buckets) so different work takes different "
            "locks. Run `grind` or several `writer`s to generate traffic.")
        hint.setWordWrap(True)
        hint.setStyleSheet(_scss(f"color:{t.faint};font-size:11px;"))
        root.addWidget(hint)

        self.locks_ready.connect(self._on_locks)
        self._poll = QTimer(self)
        self._poll.timeout.connect(self._fetch)
        if live:
            self._poll.start(1200)
            self._fetch()
        else:
            self._note.setText("offline — open this on a running xv6 Machine to see live contention")
            self._bars.set_locks([])

    # -- data ------------------------------------------------------------- #
    def _read(self) -> str:
        agent = getattr(self.provider, "agent", None)
        if agent is None:
            return ""
        try:
            return agent.get_text("/locks") or ""
        except Exception:
            return ""

    def _fetch(self) -> None:
        if self._busy or self._closed:
            return
        self._busy = True
        import threading

        def work():
            try:
                txt = self._read()
                if not self._closed:
                    self.locks_ready.emit(txt)
            finally:
                self._busy = False
        threading.Thread(target=work, daemon=True).start()

    def _on_locks(self, txt: str) -> None:
        if self._closed:
            return
        locks = parse_locks(txt)
        ncpu = parse_lock_cpus(txt)
        self._bars.set_locks(locks)
        if not locks:
            self._note.setText("no lock telemetry in this kernel — rebuild the xv6 image")
            return
        worst = locks[0]
        if ncpu == 1:
            self._note.setText(
                "This kernel is running on ONE core, so contention is impossible by construction "
                "— every spin count will stay zero, however hard you load it. Set this machine's "
                "Size to L (2 cores) or XL (4) in the Inspector, then Reboot.")
        elif worst.contention >= HOT:
            self._note.setText(
                f"{ncpu} cores  ·  hottest lock: {worst.name} at {worst.contention:.2f} "
                f"spins per acquire — cores are spending real time waiting on it.")
        else:
            self._note.setText(
                f"{ncpu} cores  ·  nothing is badly contended (worst: {worst.name} at "
                f"{worst.contention:.2f} spins/acq). Add parallel work to create pressure.")

    def _reset(self) -> None:
        agent = getattr(self.provider, "agent", None)
        if agent is None:
            return
        try:
            agent.post("/locks/reset")
        except Exception:
            pass
        self._fetch()

    def closeEvent(self, e) -> None:  # noqa: N802
        self._closed = True
        self._poll.stop()
        super().closeEvent(e)
