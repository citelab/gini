"""Four toolbar pills for GINI: the current MODE, the AI MODEL, the ACTIVITY, and YOU.

  * Mode pill     — which interaction mode is active (Chat / Explain / Wizard / Coach),
                    coloured per mode.
  * Model pill    — the connected local model's name + status (green = reachable, amber =
                    set but not responding, grey = none). Click it to open Settings → LLM.
  * Activity pill — a rotating spinner while the assistant is thinking, else a steady Idle.
  * User pill     — your Teaching Center enrolment: who you're signed in as, whether the course
                    server is reachable, and **how many assigned missions are still due** (0 is a
                    perfectly good answer, and worth showing — "nothing due" is information).
                    gBuilder is fully usable signed OUT, so the signed-out state is calm and grey,
                    not an error. Click it to sign in (Settings) or to jump to your assigned work.

One widget paints them all so the toolbar stays tidy; the Model and User pills are click targets.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QWidget

_GAP = 8


class ModeIndicator(QWidget):
    model_clicked = Signal()          # the Model pill was clicked (open Settings → LLM)
    user_clicked = Signal()           # the User pill was clicked (sign in, or go to my missions)

    def __init__(self, theme, parent=None) -> None:
        super().__init__(parent)
        self.theme = theme
        self._mode = "Chat"
        self._busy = False
        self._angle = 0
        self._model_name = ""         # "" = no model connected
        self._model_ok = False        # reachable?
        self._student = ""            # "" = not enrolled in any course
        self._tc_ok = False           # course server reachable?
        self._is_teacher = False      # signed in with a teacher role?
        self._due = 0                 # assigned missions not yet completed
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

    def set_enrolment(self, student: str, online: bool, due: int = 0) -> None:
        """student='' → signed out (grey, and that is FINE); signed in + reachable → green with the
        count of missions still due; signed in + unreachable → amber. `due < 0` = a TEACHER: no
        assignments-due notion applies, so the pill shows the role instead of a count."""
        self._student = student or ""
        self._tc_ok = bool(online)
        self._is_teacher = int(due) < 0
        self._due = max(0, int(due))
        if not self._student:
            tip = ("Not signed in to a course. gBuilder works fully without one — click to enrol "
                   "in a Teaching Center (Settings → Teaching Center).")
        elif self._is_teacher:
            tip = f"Signed in as {self._student} (teacher). Click for teacher tools."
        elif not self._tc_ok:
            tip = (f"Signed in as {self._student}, but the course server isn't reachable. "
                   f"Showing your cached assignments; results will sync when it's back.")
        elif self._due:
            tip = (f"Signed in as {self._student} — {self._due} assigned mission"
                   f"{'s' if self._due != 1 else ''} still to do. Click to open them.")
        else:
            tip = f"Signed in as {self._student} — nothing due. Click to browse missions."
        self.setToolTip(tip)
        self.updateGeometry(); self.update()

    def _user_text(self) -> str:
        if not self._student:
            return "sign in"
        if getattr(self, "_is_teacher", False):
            return f"{self._student} · teacher"
        if not self._tc_ok:
            return f"{self._student} · offline"
        return f"{self._student} · {self._due} due" if self._due else f"{self._student} · clear"

    def _user_color(self) -> QColor:
        t = self.theme.theme
        if not self._student:
            return QColor(t.muted)                       # signed out is not an error state
        if not self._tc_ok:
            return QColor(t.warning)
        return QColor(t.accent) if self._due else QColor(t.success)

    def _spin(self) -> None:
        self._angle = (self._angle + 24) % 360
        self.update()

    # -- geometry ----------------------------------------------------------- #
    def _font(self) -> QFont:
        from .theme.manager import sp
        f = QFont(); f.setPointSize(sp(9)); f.setBold(True)
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
        user_txt = self._user_text()
        return [
            ("mode", self._mode, self._mode_color(), w(self._mode)),
            ("model", model_txt, self._model_color(), w(model_txt)),
            ("activity", act_txt, act_col, w(act_txt)),
            ("user", user_txt, self._user_color(), w(user_txt)),
        ]

    def _ranges(self) -> dict[str, tuple[float, float]]:
        """Each pill's x-span. Derived from the layout, NOT recorded during paint — otherwise the
        click targets don't exist until the widget has been painted once."""
        out: dict[str, tuple[float, float]] = {}
        x = 1.5
        for kind, _t, _c, w in self._pills():
            out[kind] = (x, x + w)
            x += w + _GAP
        return out

    def sizeHint(self) -> QSize:
        total = sum(p[3] for p in self._pills()) + _GAP * (len(self._pills()) - 1) + 3
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
            elif kind == "user":
                self._person(p, x + 13, h / 2.0, col)     # a person, not a status dot
            else:
                p.setBrush(col); p.setPen(Qt.NoPen)
                p.drawEllipse(QRectF(x + 9, h / 2.0 - 4, 8, 8))
            p.setPen(col)
            p.drawText(QRectF(x + 22, 0, w - 20, h), Qt.AlignVCenter | Qt.AlignLeft, text)
            x += w + _GAP
        p.end()

    def _person(self, p: QPainter, cx: float, cy: float, col: QColor) -> None:
        """A little head-and-shoulders — the pill reads as YOU at a glance, not as another status
        light. Hollow when signed out; filled once you're enrolled."""
        p.save()
        signed_in = bool(self._student)
        p.setPen(QPen(col, 1.4))
        p.setBrush(col if signed_in else Qt.NoBrush)
        p.drawEllipse(QRectF(cx - 3.2, cy - 6.0, 6.4, 6.4))              # head
        p.drawChord(QRectF(cx - 5.4, cy - 1.4, 10.8, 9.6), 0, 180 * 16)  # shoulders
        p.restore()

    # -- the Model and User pills are click targets -------------------------- #
    def _hit(self, x: float) -> str:
        for kind in ("model", "user"):
            x0, x1 = self._ranges().get(kind, (0.0, 0.0))
            if x0 <= x <= x1:
                return kind
        return ""

    def mousePressEvent(self, e) -> None:
        hit = self._hit(e.position().x())
        if hit == "model":
            self.model_clicked.emit()
        elif hit == "user":
            self.user_clicked.emit()
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:
        self.setCursor(Qt.PointingHandCursor if self._hit(e.position().x())
                       else Qt.ArrowCursor)
        super().mouseMoveEvent(e)
