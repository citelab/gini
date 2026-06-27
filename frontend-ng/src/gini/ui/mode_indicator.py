"""Two toolbar indicators for GINI: the current MODE and the activity (Thinking/Idle).

Left pill = which interaction mode is active (Q&A / Explain / Wizard …), coloured per
mode. Right pill = a rotating spinner while the assistant is thinking, or a steady
"Idle" dot otherwise. Kept as one widget that paints two pills so the toolbar stays tidy.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget


class ModeIndicator(QWidget):
    def __init__(self, theme, parent=None) -> None:
        super().__init__(parent)
        self.theme = theme
        self._mode = "Q&A"
        self._busy = False
        self._angle = 0
        self.setFixedHeight(26)
        self._timer = QTimer(self)
        self._timer.setInterval(55)
        self._timer.timeout.connect(self._spin)

    # called from the assistant via the status_changed signal ----------------- #
    def set_status(self, label: str, busy: bool) -> None:
        # label arrives like "Explain mode" / "Wizard mode" / "Q&A mode"
        self._mode = label.replace(" mode", "") or "Q&A"
        self._busy = busy
        if busy and not self._timer.isActive():
            self._timer.start()
        elif not busy and self._timer.isActive():
            self._timer.stop()
            self._angle = 0
        self.updateGeometry()
        self.update()

    def _spin(self) -> None:
        self._angle = (self._angle + 24) % 360
        self.update()

    def _mode_color(self) -> QColor:
        t = self.theme.theme
        return {"Explain": QColor(t.accent), "Wizard": QColor(t.accent2),
                "Tutor": QColor(t.success)}.get(self._mode, QColor(t.muted))

    def _mode_w(self) -> int:
        return 30 + len(self._mode) * 8

    def sizeHint(self):
        return QSize(self._mode_w() + 96, 26)

    def minimumSizeHint(self):
        return self.sizeHint()

    def _pill(self, p: QPainter, x: float, w: float, col: QColor) -> None:
        bg = QColor(col); bg.setAlpha(38)
        p.setBrush(bg)
        p.setPen(QPen(col, 1.4))
        p.drawRoundedRect(QRectF(x, 1.5, w, self.height() - 3), 12, 12)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        h = self.height()

        # --- mode pill (left) ---
        mcol = self._mode_color()
        mw = self._mode_w()
        self._pill(p, 1.5, mw, mcol)
        p.setBrush(mcol); p.setPen(Qt.NoPen)
        p.drawEllipse(QRectF(11, h / 2 - 4, 8, 8))
        p.setPen(mcol)
        f = QFont(); f.setPointSize(9); f.setBold(True); p.setFont(f)
        p.drawText(QRectF(24, 0, mw - 22, h), Qt.AlignVCenter | Qt.AlignLeft, self._mode)

        # --- activity pill (right) ---
        t = self.theme.theme
        acol = QColor(t.warning) if self._busy else QColor(t.muted)
        ax = mw + 8.0
        aw = self.width() - ax - 1.5
        self._pill(p, ax, aw, acol)
        cx, cy = ax + 13, h / 2.0
        if self._busy:
            p.save(); p.translate(cx, cy); p.rotate(self._angle)
            pen = QPen(acol, 2.4); pen.setCapStyle(Qt.RoundCap); p.setPen(pen)
            p.drawArc(QRectF(-6, -6, 12, 12), 0, 290 * 16)
            p.restore()
        else:
            p.setBrush(acol); p.setPen(Qt.NoPen)
            p.drawEllipse(QRectF(cx - 4, cy - 4, 8, 8))
        p.setPen(acol)
        p.drawText(QRectF(ax + 24, 0, aw - 24, h), Qt.AlignVCenter | Qt.AlignLeft,
                   "Thinking…" if self._busy else "Idle")
