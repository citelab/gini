"""One morphing circular power button that replaces the separate Run / Stop
toolbar actions.

States (driven by MainWindow from the real run lifecycle):

  * ready    — green disc, white ▶ glyph. Click launches the topology.
  * booting  — a progress ring fills clockwise to (containers up / total) as the
               fabric comes online; a soft core pulses. Click cancels (stops).
  * running  — accent disc, white ■ stop glyph, a gentle green "alive" breathing
               ring. Click stops.
  * stopping — muted disc with an indeterminate rotating arc while it winds down.
  * error    — red disc, white ▶ (retry), a pulsing red halo.

The widget only *paints* state; MainWindow decides what a click means and calls
`set_state` / `set_progress` at the right lifecycle points.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF, QRadialGradient
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QWidget

_ANIM_STATES = {"booting", "running", "stopping", "error"}


class RunButton(QWidget):
    clicked = Signal()          # user pressed the button (MainWindow decides run vs stop)

    def __init__(self, theme, parent=None) -> None:
        super().__init__(parent)
        self.theme = theme
        self._state = "ready"
        self._frac = 0.0           # eased boot-progress fraction actually drawn
        self._target = 0.0         # up / total
        self._up = self._total = 0
        self._phase = 0.0          # animation phase (pulse / spin)
        self._hover = False
        self.setFixedSize(40, 34)
        self.setCursor(Qt.PointingHandCursor)
        self._refresh_tip()
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)

    # -- state in --------------------------------------------------------------
    def set_state(self, state: str) -> None:
        if state not in ("ready", "booting", "running", "stopping", "error"):
            return
        self._state = state
        if state == "ready":
            self._target = self._frac = 0.0
            self._up = self._total = 0
        elif state == "running":
            self._target = 1.0
        if state in _ANIM_STATES and not self._timer.isActive():
            self._timer.start()
        elif state not in _ANIM_STATES and self._timer.isActive():
            self._timer.stop(); self._phase = 0.0
        self._refresh_tip()
        self.update()

    def set_progress(self, up: int, total: int) -> None:
        self._up, self._total = int(up), int(total)
        self._target = (up / total) if total else 0.0
        self._refresh_tip()
        self.update()

    def state(self) -> str:
        return self._state

    def _refresh_tip(self) -> None:
        tip = {
            "ready": "Run — launch this topology on Docker",
            "booting": (f"Starting… {self._up}/{self._total} containers up"
                        if self._total else "Starting the topology…"),
            "running": "Running — click to stop",
            "stopping": "Stopping…",
            "error": "Run failed — click to try again",
        }[self._state]
        self.setToolTip(tip)

    # -- animation -------------------------------------------------------------
    def _tick(self) -> None:
        self._phase = (self._phase + 0.12) % (math.tau)
        # ease the drawn fraction toward the real target so the ring glides
        self._frac += (self._target - self._frac) * 0.25
        self.update()

    # -- theme hook (called by MainWindow on theme change) ---------------------
    def refresh_theme(self) -> None:
        self.update()

    # -- input -----------------------------------------------------------------
    def enterEvent(self, _e) -> None:
        self._hover = True; self.update()

    def leaveEvent(self, _e) -> None:
        self._hover = False; self.update()

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)

    def sizeHint(self) -> QSize:
        return QSize(40, 34)

    # -- paint -----------------------------------------------------------------
    def _disc_color(self) -> QColor:
        t = self.theme.theme
        return {
            "ready": QColor(t.success),
            "booting": QColor(t.success).darker(155),   # dim so the progress ring pops
            "running": QColor(t.accent),
            "stopping": QColor(t.muted),
            "error": QColor(t.danger),
        }[self._state]

    def paintEvent(self, _e) -> None:
        t = self.theme.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0
        r = min(w, h) / 2.0 - 4.0
        disc = self._disc_color()

        # ambient halo: hover on any state, plus the pulsing error / alive glow
        halo = 0.0
        if self._state == "error":
            halo = 0.55 + 0.45 * math.sin(self._phase * 2)
        elif self._state == "running":
            halo = 0.25 + 0.20 * math.sin(self._phase)
        elif self._hover:
            halo = 0.5
        if halo > 0.01:
            glow_col = QColor(t.success) if self._state == "running" else disc
            grad = QRadialGradient(cx, cy, r + 6)
            gc = QColor(glow_col); gc.setAlpha(int(120 * halo))
            grad.setColorAt(0.55, gc)
            grad.setColorAt(1.0, QColor(gc.red(), gc.green(), gc.blue(), 0))
            p.setPen(Qt.NoPen); p.setBrush(grad)
            p.drawEllipse(QRectF(cx - r - 6, cy - r - 6, 2 * (r + 6), 2 * (r + 6)))

        # the disc
        core = QColor(disc)
        if self._state == "booting":                     # subtle breathing core
            core = core.lighter(int(108 + 8 * math.sin(self._phase * 2)))
        p.setPen(QPen(disc.darker(135), 1.2))
        p.setBrush(core)
        p.drawEllipse(QRectF(cx - r, cy - r, 2 * r, 2 * r))

        # rings
        ring_rect = QRectF(cx - r + 3, cy - r + 3, 2 * (r - 3), 2 * (r - 3))
        if self._state == "booting":
            track = QPen(QColor(255, 255, 255, 85), 3.6); track.setCapStyle(Qt.RoundCap)
            p.setPen(track); p.setBrush(Qt.NoBrush)
            p.drawArc(ring_rect, 0, 360 * 16)
            arc = QPen(QColor(255, 255, 255, 255), 3.6); arc.setCapStyle(Qt.RoundCap)
            p.setPen(arc)
            p.drawArc(ring_rect, 90 * 16, -int(self._frac * 360) * 16)
        elif self._state == "stopping":                  # indeterminate spinner
            arc = QPen(QColor(255, 255, 255, 210), 3); arc.setCapStyle(Qt.RoundCap)
            p.setPen(arc); p.setBrush(Qt.NoBrush)
            start = int(-math.degrees(self._phase * 2.4)) * 16
            p.drawArc(ring_rect, start, 110 * 16)

        # glyph
        p.setPen(Qt.NoPen); p.setBrush(QColor("#ffffff"))
        if self._state in ("running", "stopping"):
            s = r * 0.62
            p.drawRoundedRect(QRectF(cx - s / 2, cy - s / 2, s, s), 2, 2)
        elif self._state in ("ready", "error"):
            tri = QPolygonF([
                QPointF(cx - r * 0.26, cy - r * 0.42),
                QPointF(cx - r * 0.26, cy + r * 0.42),
                QPointF(cx + r * 0.46, cy),
            ])
            p.drawPolygon(tri)
        p.end()
