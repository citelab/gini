"""Three toolbar pills for GINI: the current MODE, the AI MODEL, and the ACTIVITY.

  * Mode pill     — which interaction mode is active (Chat / Explain / Wizard / Coach),
                    coloured per mode.
  * Model pill    — the connected local model's name + status (green = reachable, amber =
                    set but not responding, grey = none). Click it to open Settings → LLM.
  * Activity pill — a rotating spinner while the assistant is thinking, else a steady Idle.

One widget paints all three so the toolbar stays tidy; the Model pill emits `model_clicked`.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QWidget

_GAP = 8


class ModeIndicator(QWidget):
    model_clicked = Signal()          # the Model pill was clicked (open Settings → LLM)

    def __init__(self, theme, parent=None) -> None:
        super().__init__(parent)
        self.theme = theme
        self._mode = "Chat"
        self._busy = False
        self._angle = 0
        self._model_name = ""         # "" = no model connected
        self._model_ok = False        # reachable?
        self._model_range = (0.0, 0.0)
        self.setFixedHeight(26)
        self.setMouseTracking(True)
        self._timer = QTimer(self)
        self._timer.setInterval(55)
        self._timer.timeout.connect(self._spin)

    # -- state from the assistant / main window ----------------------------- #
    def set_status(self, label: str, busy: bool) -> None:
        self._mode = (label or "Chat").replace(" mode", "") or "Chat"
        self._busy = busy
        if busy and not self._timer.isActive():
            self._timer.start()
        elif not busy and self._timer.isActive():
            self._timer.stop(); self._angle = 0
        self.updateGeometry(); self.update()

    def set_model(self, name: str, connected: bool) -> None:
        """name='' → 'no model'; name + connected → green; name + not connected → amber."""
        self._model_name = name or ""
        self._model_ok = bool(connected)
        self.setToolTip("Local model connected — Wizard, Coach and open-ended Q&A are on."
                        if (self._model_name and self._model_ok) else
                        f"'{self._model_name}' is set but not responding — click to check Settings."
                        if self._model_name else
                        "No local model — click to connect one (Settings → LLM). Explain and "
                        "build commands still work without it.")
        self.updateGeometry(); self.update()

    def _spin(self) -> None:
        self._angle = (self._angle + 24) % 360
        self.update()

    # -- geometry ----------------------------------------------------------- #
    def _font(self) -> QFont:
        f = QFont(); f.setPointSize(9); f.setBold(True)
        return f

    def _mode_color(self) -> QColor:
        t = self.theme.theme
        return {"Explain": QColor(t.accent), "Wizard": QColor(t.accent2),
                "Coach": QColor(t.accent2), "Tutor": QColor(t.success)}.get(
            self._mode, QColor(t.muted))

    def _model_color(self) -> QColor:
        t = self.theme.theme
        if not self._model_name:
            return QColor(t.muted)
        return QColor(t.success) if self._model_ok else QColor(t.warning)

    def _pills(self):
        """(kind, text, colour, width) for each pill, left to right."""
        fm = QFontMetrics(self._font())
        t = self.theme.theme

        def w(text):
            return 30 + fm.horizontalAdvance(text)

        model_txt = self._model_name or "no model"
        act_txt = "Thinking…" if self._busy else "Idle"
        act_col = QColor(t.warning) if self._busy else QColor(t.muted)
        return [
            ("mode", self._mode, self._mode_color(), w(self._mode)),
            ("model", model_txt, self._model_color(), w(model_txt)),
            ("activity", act_txt, act_col, w(act_txt)),
        ]

    def sizeHint(self) -> QSize:
        total = sum(p[3] for p in self._pills()) + _GAP * 2 + 3
        return QSize(int(total), 26)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    # -- paint -------------------------------------------------------------- #
    def _pill_bg(self, p: QPainter, x: float, w: float, col: QColor) -> None:
        bg = QColor(col); bg.setAlpha(38)
        p.setBrush(bg); p.setPen(QPen(col, 1.4))
        p.drawRoundedRect(QRectF(x, 1.5, w, self.height() - 3), 12, 12)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setFont(self._font())
        h = self.height()
        x = 1.5
        for kind, text, col, w in self._pills():
            self._pill_bg(p, x, w, col)
            if kind == "activity" and self._busy:
                p.save(); p.translate(x + 13, h / 2.0); p.rotate(self._angle)
                pen = QPen(col, 2.4); pen.setCapStyle(Qt.RoundCap); p.setPen(pen)
                p.drawArc(QRectF(-6, -6, 12, 12), 0, 290 * 16); p.restore()
            else:
                p.setBrush(col); p.setPen(Qt.NoPen)
                p.drawEllipse(QRectF(x + 9, h / 2.0 - 4, 8, 8))
            p.setPen(col)
            p.drawText(QRectF(x + 22, 0, w - 20, h), Qt.AlignVCenter | Qt.AlignLeft, text)
            if kind == "model":
                self._model_range = (x, x + w)
            x += w + _GAP
        p.end()

    # -- the Model pill is a click target ----------------------------------- #
    def mousePressEvent(self, e) -> None:
        x0, x1 = self._model_range
        if x0 <= e.position().x() <= x1:
            self.model_clicked.emit()
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:
        x0, x1 = self._model_range
        self.setCursor(Qt.PointingHandCursor if x0 <= e.position().x() <= x1
                       else Qt.ArrowCursor)
        super().mouseMoveEvent(e)
