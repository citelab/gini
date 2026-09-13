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

import threading
import weakref

from PySide6.QtCore import Qt, QTimer, Signal

from .live_poll import JOIN_TIMEOUT, LivePollMixin
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout,
    QWidget,
)

from ..domain.xv6 import (
    TRAP_KINDS, TrapRate, parse_alarms, parse_trapcounts, parse_traptrace, trap_kind_name,
)
from .theme import ThemeManager, icons
from .windowing import standalone

# trap kinds the "Step a trap" catcher can target (kernel-side one-shot capture, armed over the
# console mux — no gdb); "any" = next trap. NB known issue #13: "any"/"device" usually catch the
# probe's own UART interrupt, which IS a genuine device trap, just GINI's rather than the workload's.
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


class TrapLab(LivePollMixin, QDialog):
    #: (payload, ok) from the poll worker to the GUI thread — see live_poll. The mixin owns the
    #: timer, the worker and, the load-bearing part, the JOIN in stop_polling(): this dialog used
    #: to fire-and-forget its poll and catch threads, guarding only with `if not self._closed`
    #: before the emit, and a worker past that check emitting into a dialog being destroyed was a
    #: nondeterministic SIGSEGV (mid-test in ~1 of 20 two-module runs; in production, _retire()'s
    #: deleteLater() while a catch was in flight). _retire() calls stop_polling() — which this
    #: class never had, so it was silently skipped.
    snap_ready = Signal(object)
    caught = Signal(object)            # a TrapFrame from a live catch (or None), off the worker

    def __init__(self, parent, theme: ThemeManager, device=None, traps_source=None,
                 on_step=None, catch_source=None, alarm_source=None, on_play=None) -> None:
        super().__init__(parent)
        # Its own window, not an owned dialog: on Windows an owned dialog gets no
        # taskbar button and Alt+Tab skips it. See ui/windowing.
        standalone(self)
        self.theme = theme
        self.device = device
        self._src = traps_source or (lambda: "")
        self._on_step = on_step
        self._on_play = on_play             # callable() opening the decode-the-trap game; may be None
        self._catch = catch_source          # callable(kind) -> TrapFrame (kernel-side capture, no gdb); may be None
        self._alarm_src = alarm_source      # callable() -> gini_dump text with ALARM lines; may be None
        self._rate = TrapRate(window=60.0)
        self._busy = False
        self._closed = False

        t = theme.theme
        self.setWindowTitle(f"Traps & Interrupts Lab — {getattr(device, 'name', 'xv6')}")
        self.resize(760, 580)
        self.setStyleSheet(f"QDialog{{background:{t.bg};}}")
        root = QVBoxLayout(self)

        hdr = QHBoxLayout()
        ic = QLabel(); ic.setPixmap(icons.render_pixmap("dashboard", t.accent_for("amber"), 22))
        title = QLabel(f"  Traps & Interrupts Lab — {getattr(device, 'name', 'xv6')}")
        title.setStyleSheet(f"color:{t.text};font-size:16px;font-weight:600;")
        hdr.addWidget(ic); hdr.addWidget(title); hdr.addStretch(1)
        root.addLayout(hdr)

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
        # Why a catch found nothing, when it does. Shown here rather than opening a journey full of
        # authored placeholders as if a trap had been caught.
        self._catch_msg = QLabel("")
        self._catch_msg.setWordWrap(True)
        self._catch_msg.setStyleSheet(f"color:{t.muted};font-size:12px;")
        row.addWidget(self._catch_msg, 1)
        root.addLayout(row)

        self.caught.connect(self._on_caught)
        self._catch_thread: threading.Thread | None = None   # the one-shot catch worker; joined on close
        # The poll is the mixin's: timer, worker, backoff, and the join on close. A round reads
        # the trap dump (and the alarm lines when wired) OFF the GUI thread in _read() and renders
        # ON it in _render_live(). `_tick()` here is the eager first read the old bare `_fetch()`
        # was — a Lab must not open on an empty picture.
        self._init_poll(1500, live=True)
        self._tick()

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
        """Freeze a live trap (off the GUI thread — the catch polls the kernel for up to ~10 s,
        and holds the single-threaded agent while it does, see known issue #12), then open the
        journey seeded with it. With no live catch source, open the authored journey immediately."""
        if not callable(self._on_step):
            return
        if callable(self._catch):
            kind = self._kind.currentText()
            catch = self._catch                 # the bridge's callable — the I/O needs no `self`
            self._step_btn.setEnabled(False)
            self._step_btn.setText("  freezing a trap…")
            # The worker must never hold the last strong reference to this dialog: a closure over
            # `self` did, so when a test (or _retire) dropped its reference while the catch was in
            # flight, the dialog was destroyed either from under the worker's emit or ON the worker
            # thread when the closure died — both undefined in Qt, both a SIGSEGV. A weak ref,
            # resolved only after the I/O, means a straggler finds the dialog gone (destroyed on the
            # GUI thread, where it belongs) or closed, and does nothing. stop_polling() joins it.
            wself = weakref.ref(self)

            def work():
                try:
                    fr = catch(kind)
                except Exception:
                    fr = None
                me = wself()
                if me is None or me._closed:    # gone, or closed while we were catching
                    return
                me.caught.emit(fr)
                del me                          # release on this thread while the GUI still owns it
            self._catch_thread = threading.Thread(target=work, daemon=True, name="TrapLab-catch")
            self._catch_thread.start()
        else:
            self._on_step(None)

    def _on_caught(self, frame) -> None:
        self._step_btn.setEnabled(True)
        self._step_btn.setText("  Step a trap ▸")
        if self._closed:
            return
        # A catch that found nothing: say WHY (the agent's real reason), do not open a journey that
        # would present authored placeholders as a captured trap.
        if frame is not None and not getattr(frame, "ok", False):
            self._catch_msg.setText(getattr(frame, "error", "") or "No trap was caught.")
            return
        self._catch_msg.setText("")
        if callable(self._on_step):
            self._on_step(frame)

    def _read(self):
        """OFF the GUI thread — one coalesced round: the trap dump, plus the alarm lines when an
        alarm source is wired. Touches no widget. Returning None would mean "this round failed"
        and keep the last good picture; a source that raises is exactly that case, and the mixin's
        _work() turns the exception into a failed round for us."""
        txt = self._src() or ""
        atxt = (self._alarm_src() or "") if callable(self._alarm_src) else None
        return (txt, atxt)

    def _render_live(self, payload, fresh: bool) -> None:
        """ON the GUI thread. `payload` is the last GOOD round (the mixin never hands us a failed
        one in place of it), so a bad read never blanks a good picture — it just isn't fresh."""
        if payload is None:                     # nothing good has ever arrived
            return
        txt, atxt = payload
        self._apply(txt)
        if atxt is not None:
            self._apply_alarms(atxt)

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

    def stop_polling(self, timeout: float = JOIN_TIMEOUT) -> None:
        """Stop the poll AND join the one-shot catch worker. The mixin's closeEvent calls this, and
        so does MachineLab._retire() before its deleteLater() — the call this class never answered
        before, which is why retiring a Traps face mid-catch could take gBuilder down.

        A catch can block for the agent's whole wait (~10 s), so the join is bounded: closing a
        window must never feel stuck. A straggler that outlives the bound holds only a WEAK
        reference to this dialog (see _step) and finds `_closed` set, so it can neither emit into
        a dialog being destroyed nor become its last owner. `_closed` is set FIRST, the mixin's
        discipline: once it is set no emit can happen, and the join only waits for the I/O."""
        self._closed = True
        t, self._catch_thread = self._catch_thread, None
        if t is not None and t.is_alive():
            t.join(timeout)
        super().stop_polling(timeout)
