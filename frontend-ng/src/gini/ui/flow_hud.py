"""Flow HUD — a toggle-on live view of the network's TCP congestion windows.

A glass panel (upper-right of the canvas) that lists every active TCP flow as a
clickable **chip** (sender -> receiver, with its congestion-control algorithm). Click a
chip and the panel draws that flow's congestion window over time, in the sawtooth shape
of a textbook TCP plot: the cwnd line, the ssthresh level, and red ticks where packets
were dropped (retransmitted). RTT and delivery rate ride along the top.

The widget is pure rendering over a `FlowTracker` (domain/flows.py); the controller
polls `ss -tin` on each running station, parses it, and feeds the tracker. See the
Flow HUD in Chapter "TCP Congestion Control".
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..domain.flows import FlowTracker, parse_ss
from .glass import apply_glass, paint_glass_panel

_CHIP_H = 22
_WINDOW_S = 60          # the cwnd plot shows only the most recent 60 s (a scrolling window)


class FlowHud(QWidget):
    def __init__(self, parent, theme) -> None:
        super().__init__(parent)
        self.theme = theme
        self._tracker: FlowTracker | None = None
        self._selected: str | None = None
        self._chip_rects: list[tuple[QRectF, str]] = []
        self.window_s = _WINDOW_S          # seconds of cwnd history shown (set from Settings)
        self.resize(460, 340)
        self.setMouseTracking(True)
        apply_glass(self)

    def set_tracker(self, tracker: FlowTracker) -> None:
        self._tracker = tracker
        flows = tracker.active() if tracker else []
        keys = {f.key for f in flows}
        # auto-select the busiest flow if nothing valid is selected
        if self._selected not in keys:
            self._selected = None
            if flows:
                self._selected = max(flows, key=lambda f: (f.cwnd[-1] if f.cwnd else 0)).key
        self.update()

    # -- paint ------------------------------------------------------------- #
    def paintEvent(self, _e) -> None:  # noqa: N802
        t = self.theme.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        paint_glass_panel(p, self.rect(), self.theme, "TCP FLOWS")

        flows = self._tracker.active() if self._tracker else []
        if not flows:
            p.setPen(QColor(t.faint))
            p.drawText(self.rect(), Qt.AlignCenter, "— no TCP flows —")
            return

        # chips row (wraps) ------------------------------------------------
        self._chip_rects = []
        x, y = 12, 28
        p.setFont(QFont(self.font().family(), 8, QFont.Bold))
        for f in flows:
            text = f"{f.label}" + (f"  ·  {f.cc}" if f.cc else "")
            w = p.fontMetrics().horizontalAdvance(text) + 18
            if x + w > self.width() - 12:
                x = 12; y += _CHIP_H + 4
            r = QRectF(x, y, w, _CHIP_H)
            sel = (f.key == self._selected)
            p.setBrush(QColor(t.accent) if sel else QColor(t.panel2))
            p.setPen(QColor(t.accent) if sel else QColor(t.line))
            p.drawRoundedRect(r, 8, 8)
            p.setPen(QColor("#ffffff") if sel else QColor(t.text))
            p.drawText(r, Qt.AlignCenter, text)
            self._chip_rects.append((r, f.key))
            x += w + 6
        plot_top = y + _CHIP_H + 10

        sel = next((f for f in flows if f.key == self._selected), None)
        if sel is None:
            p.setPen(QColor(t.faint))
            p.drawText(0, plot_top, self.width(), 24, Qt.AlignCenter, "click a flow to plot its cwnd")
            return
        self._paint_plot(p, sel, plot_top)

    def _paint_plot(self, p, f, top: int) -> None:
        t = self.theme.theme
        m = 34
        area = QRectF(m, top + 18, self.width() - m - 12, self.height() - top - 18 - 14)
        # header line: cc · rtt · rate
        p.setFont(QFont(self.font().family(), 8))
        p.setPen(QColor(t.muted))
        hdr = f"{f.label}   cwnd {f.cwnd[-1] if f.cwnd else 0} MSS   rtt {f.rtt_ms:g} ms   {f.delivery_mbps:.1f} Mbps"
        p.drawText(m, top, self.width() - m - 12, 14, Qt.AlignLeft, hdr)

        # scrolling window: show only the most recent `window_s` seconds
        win = max(5, int(self.window_s))
        t1 = f.t[-1] if f.t else 0.0
        t0 = max(f.t[0], t1 - win) if f.t else 0.0
        pts = [(tt, cv) for tt, cv in zip(f.t, f.cwnd) if tt >= t0]
        drops = [d for d in f.drops if d >= t0]
        if len(pts) < 2:
            p.setPen(QColor(t.faint))
            p.drawText(area, Qt.AlignCenter, "collecting…")
            return

        span = (t1 - t0) or 1.0
        ymax = max([cv for _, cv in pts] + [f.ssthresh, 4]) * 1.15

        def X(tt): return area.left() + (tt - t0) / span * area.width()
        def Y(vv): return area.bottom() - (vv / ymax) * area.height()

        # axes
        p.setPen(QPen(QColor(t.line), 1))
        p.drawLine(int(area.left()), int(area.top()), int(area.left()), int(area.bottom()))
        p.drawLine(int(area.left()), int(area.bottom()), int(area.right()), int(area.bottom()))
        p.setPen(QColor(t.faint)); p.setFont(QFont(self.font().family(), 7))
        p.drawText(2, int(area.top()) - 4, 30, 12, Qt.AlignRight, "cwnd")
        p.drawText(2, int(Y(ymax)) - 6, int(area.left()) - 4, 12, Qt.AlignRight, f"{ymax:.0f}")
        p.drawText(int(area.right()) - 66, int(area.bottom()) + 2, 66, 12,
                   Qt.AlignRight, f"last {int(t1 - t0)} s")

        # ssthresh reference line
        if f.ssthresh > 0:
            p.setPen(QPen(QColor(t.accent_for("teal")), 1, Qt.DashLine))
            yy = int(Y(f.ssthresh))
            p.drawLine(int(area.left()), yy, int(area.right()), yy)

        # drop marks (red ticks along the bottom)
        p.setPen(QPen(QColor(t.accent_for("red")), 1.4))
        for d in drops:
            xx = int(X(d))
            p.drawLine(xx, int(area.bottom()), xx, int(area.bottom()) - 8)

        # the cwnd sawtooth (windowed)
        p.setPen(QPen(QColor(t.accent), 1.8))
        prev = None
        for tt, cv in pts:
            cur = (X(tt), Y(cv))
            if prev is not None:
                p.drawLine(int(prev[0]), int(prev[1]), int(cur[0]), int(cur[1]))
            prev = cur

    # -- interaction ------------------------------------------------------- #
    def mousePressEvent(self, e) -> None:  # noqa: N802
        pos = e.position() if hasattr(e, "position") else e.pos()
        for r, key in self._chip_rects:
            if r.contains(pos):
                self._selected = key
                self.update()
                return


class FlowHudController(QObject):
    """Owns a FlowHud and refreshes it live off the GUI thread. Everything it needs from
    the app is injected as callables (so it never imports main_window).

    Wire-up (one line in MainWindow behind a 'Flow HUD' toolbar toggle):

        self._fhud = FlowHudController(
            self.canvas, self.theme,
            machines=lambda: [d.name for d in self.ctx.topology.devices.values()
                              if d.type_key == "machine"],
            query=self.element_query)              # (name, cmd) -> text
        self._fhud.show_topright()                 # toggle off -> self._fhud.close()
    """

    samples_ready = Signal(object, float)          # (list[FlowSample], tnow)

    def __init__(self, parent, theme, machines, query, window_getter=None,
                 interval_ms: int = 900) -> None:
        super().__init__(parent)
        self.hud = FlowHud(parent, theme)
        self._machines = machines
        self._query = query
        self._window_getter = window_getter or (lambda: 60)
        self._tracker = FlowTracker()
        self._busy = False
        self.samples_ready.connect(self._on_samples)
        self._poll = QTimer(self); self._poll.timeout.connect(self.refresh)
        self._interval = interval_ms

    def _on_samples(self, samples, tnow: float) -> None:
        try:
            self.hud.window_s = int(self._window_getter())
        except Exception:
            pass
        self._tracker.ingest(samples, tnow)
        self.hud.set_tracker(self._tracker)

    def refresh(self) -> None:
        if self._busy:
            return
        import threading
        import time

        # Snapshot the machine list HERE, on the GUI thread. `_machines()` iterates
        # ctx.topology.devices, and doing that from the worker races every mutation of the
        # same dict -- a project load replaces ctx.topology outright, and the iteration then
        # raises "dictionary changed size during iteration" INSIDE the worker, where the only
        # handler is a `finally` that resets the flag. The poll is silently lost and nothing
        # says why. The Network HUD hit this and guards against it; these two never did.
        try:
            names = list(self._machines())
        except Exception:
            return                              # topology in flux — skip this tick, not fatal
        self._busy = True

        def work():
            try:
                tnow = time.monotonic()
                samples = []
                for name in names:
                    try:
                        samples.extend(parse_ss(self._query(name, "ss -tin"), host=name))
                    except Exception:
                        pass
                self.samples_ready.emit(samples, tnow)
            finally:
                self._busy = False
        threading.Thread(target=work, daemon=True).start()

    def show_topright(self) -> None:
        par = self.hud.parentWidget()
        if par is not None:
            self.hud.move(max(0, par.width() - self.hud.width() - 16), 16)
        self.hud.show(); self.hud.raise_()
        self.refresh()
        self._poll.start(self._interval)

    def reset(self) -> None:
        """Forget every tracked flow. Called when the TOPOLOGY is swapped: the flows in the
        tracker belong to the previous network's machines, and keeping them would chart a
        network that is no longer on screen."""
        self._tracker = FlowTracker()

    def close(self) -> None:
        self._poll.stop()
        self.hud.hide()
