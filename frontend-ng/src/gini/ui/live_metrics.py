"""LiveMetrics — real-time charts for the Inspector's Live tab.

Two stacked chart boxes, each holding two coloured, independently-scaled series:
  • CPU (%) + Memory (MiB)        — from `docker stats`, every container
  • Throughput (KB/s) + Latency (ms) — network from docker stats; latency from the
                                       cloud fabric (proxies/LBs; blank otherwise)
Each series autoscales to its own range so trends are readable even at different
magnitudes; the current value is labelled in the series colour. Pure QPainter.
"""
from __future__ import annotations

from collections import deque

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

# a layout = list of chart boxes; each box = up to two (key, label, unit, color-token)
# series. Colors resolve against the theme (accent / accent2 / success / warning).
CLOUD_LAYOUT = [
    [("cpu", "CPU", "%", "accent"), ("mem", "Memory", " MiB", "accent2")],
    [("thru", "Throughput", " KB/s", "success"), ("lat", "Latency", " ms", "warning")],
]
K8S_LAYOUT = [
    [("cpu_pct", "CPU", "%", "accent"), ("target_pct", "Target", "%", "warning")],
    [("replicas", "Replicas", "", "success")],
]


class LiveMetrics(QWidget):
    WINDOW = 90          # samples kept (~135s at a 1.5s poll)

    def __init__(self, theme) -> None:
        super().__init__()
        self.theme = theme                       # ThemeManager
        self._layout = CLOUD_LAYOUT
        self._d = {k: deque(maxlen=self.WINDOW) for k in ("cpu", "mem", "thru", "lat")}
        self.setMinimumHeight(260)

    def set_layout(self, layout) -> None:
        """Choose which series go in which chart box (cloud vs kubernetes view)."""
        self._layout = layout
        self.update()

    def set_series(self, series: dict) -> None:
        """Point the chart at an external set of deques (the inspector keeps one per
        element, so each element's history survives selection changes)."""
        self._d = series
        self.update()

    def reset(self) -> None:
        for q in self._d.values():
            q.clear()
        self.update()

    def push(self, cpu, mem, thru=0.0, lat=None) -> None:
        self._d["cpu"].append(_f(cpu))
        self._d["mem"].append(_f(mem))
        self._d["thru"].append(_f(thru))
        self._d["lat"].append(None if lat is None else _f(lat))
        self.update()

    # -- painting ----------------------------------------------------------- #
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        t = self.theme.theme
        w = self.width()
        n = max(1, len(self._layout))
        gap = 14
        ch = (self.height() - gap * (n - 1)) / n
        for i, box in enumerate(self._layout):
            rect = QRectF(4, 4 + i * (ch + gap), w - 8, ch - 6)
            series = [(label, unit, self._d.get(key, ()), QColor(getattr(t, token)))
                      for key, label, unit, token in box]
            self._chart(p, rect, t, series)

    def _chart(self, p: QPainter, rect: QRectF, t, series) -> None:
        p.setBrush(QColor(t.panel2)); p.setPen(QPen(QColor(t.line), 1))
        p.drawRoundedRect(rect, 8, 8)

        # two header labels: series[0] left, series[1] right
        for i, (label, unit, data, col) in enumerate(series):
            vals = [v for v in data if v is not None]
            cur = vals[-1] if vals else None
            align = Qt.AlignLeft if i == 0 else Qt.AlignRight
            p.setPen(QColor(t.faint))
            f = QFont(); f.setPointSize(9); f.setBold(True); p.setFont(f)
            p.drawText(rect.adjusted(11, 7, -11, 0), Qt.AlignTop | align, label.upper())
            p.setPen(col)
            fb = QFont(); fb.setPointSize(13); fb.setBold(True); p.setFont(fb)
            txt = f"{cur:,.1f}{unit}" if cur is not None else "—"
            p.drawText(rect.adjusted(11, 22, -11, 0), Qt.AlignTop | align, txt)

        plot = rect.adjusted(11, 44, -11, -10)
        if all(len([v for v in data if v is not None]) < 2 for _, _, data, _ in series):
            p.setPen(QColor(t.faint))
            fs = QFont(); fs.setPointSize(9); p.setFont(fs)
            p.drawText(plot, Qt.AlignCenter, "sampling…")
            return
        for _label, _unit, data, col in series:
            self._line(p, plot, list(data), col)

    def _line(self, p: QPainter, plot: QRectF, data, col: QColor) -> None:
        vals = [v for v in data if v is not None]
        if len(vals) < 2:
            return
        ymax = max(max(vals) * 1.2, 1e-6)
        n = len(data)
        step = plot.width() / (self.WINDOW - 1)
        path = QPainterPath()
        started = False
        for i, v in enumerate(data):
            if v is None:
                continue
            x = plot.right() - (n - 1 - i) * step
            y = plot.bottom() - (v / ymax) * plot.height()
            if not started:
                path.moveTo(x, y); started = True
            else:
                path.lineTo(x, y)
        p.setBrush(Qt.NoBrush); p.setPen(QPen(col, 2)); p.drawPath(path)


def _f(v) -> float:
    try:
        return max(0.0, float(v))
    except (TypeError, ValueError):
        return 0.0
