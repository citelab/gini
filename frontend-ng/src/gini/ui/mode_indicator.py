"""Three toolbar pills for GINI: the AI MODEL, the COURSE SERVER, and YOU.

  * Model pill    — the connected local model's name + status (green = reachable, amber =
                    set but not responding, grey = none). Click it to open Settings → LLM.
  * Server pill   — whether the course server (the Teaching Center students hand in to) can be
                    reached from this computer right now: green "GINI server", or red with the
                    reason ("not found" = not on the campus VPN). Hidden when no course server
                    is configured. Click it for the details and a Copy button for a report —
                    see `services/tc_reach.py`. Separate from the User pill on purpose: that one
                    is about signing in, which most students never do, and handing in does not
                    need it.
  * User pill     — your Teaching Center enrolment: who you're signed in as, whether the course
                    server is reachable, and **how many assigned missions are still due** (0 is a
                    perfectly good answer, and worth showing — "nothing due" is information).
                    gBuilder is fully usable signed OUT, so the signed-out state is calm and grey,
                    not an error. Click it to sign in (Settings) or to jump to your assigned work.

One widget paints them all so the toolbar stays tidy; both are click targets.

There were two more — a Mode pill and an Activity pill — and they are gone deliberately. Both said
what the Ask GINI panel says better and more specifically: the mode chips name the mode, and the
progress line names the actual step and how long it has been running, against a bare "Thinking…".
A pill reading "Idle" said nothing at all.

The cost, recorded because it is real and was accepted: the Ask GINI panel is ONE TAB. Switch to
Inspector, GINI Source or Terminal and the chips and the progress line go with it. Nothing now
shows, from those tabs, that Explain mode is on — and Explain changes what a click on the canvas
does. `Assistant.status_changed` still carries (mode, busy) for whatever wants to show it next.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QWidget

_GAP = 8


class ModeIndicator(QWidget):
    model_clicked = Signal()          # the Model pill was clicked (open Settings → LLM)
    user_clicked = Signal()           # the User pill was clicked (sign in, or go to my missions)
    server_clicked = Signal()         # the GINI server pill was clicked (show what was found)

    def __init__(self, theme, parent=None) -> None:
        super().__init__(parent)
        self.theme = theme
        self._model_name = ""         # "" = no model connected
        self._model_ok = False        # reachable?
        self._student = ""            # "" = not enrolled in any course
        self._tc_ok = False           # course server reachable?
        self._is_teacher = False      # signed in with a teacher role?
        self._due = 0                 # assigned missions not yet completed
        self._server_text = ""        # "" = no course server configured: the pill is hidden
        self._server_ok = False
        self._server_tip = ""
        self.setFixedHeight(26)
        self.setMouseTracking(True)

    # -- state from the assistant / main window ----------------------------- #
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

    def set_server(self, text: str, online: bool = False, tip: str = "") -> None:
        """The GINI server pill. `text` "" hides it (no course server configured); otherwise
        green when `online`, red when not. `tip` is the hover text — the pill's own tooltip
        stays the widget's, so it is shown only while the pointer is over this pill."""
        self._server_text = text or ""
        self._server_ok = bool(online)
        self._server_tip = tip or ""
        self.updateGeometry(); self.update()

    def _server_color(self) -> QColor:
        t = self.theme.theme
        return QColor(t.success) if self._server_ok else QColor(t.danger)

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

    # -- geometry ----------------------------------------------------------- #
    def _font(self) -> QFont:
        from .theme.manager import sp
        f = QFont(); f.setPointSize(sp(9)); f.setBold(True)
        return f

    def _model_color(self) -> QColor:
        t = self.theme.theme
        if not self._model_name:
            return QColor(t.muted)
        return QColor(t.success) if self._model_ok else QColor(t.warning)

    def _pills(self):
        """(kind, text, colour, width) for each pill, left to right."""
        fm = QFontMetrics(self._font())

        def w(text):
            return 30 + fm.horizontalAdvance(text)

        model_txt = self._model_name or "no model"
        user_txt = self._user_text()
        pills = [("model", model_txt, self._model_color(), w(model_txt))]
        if self._server_text:
            pills.append(("server", self._server_text, self._server_color(),
                          w(self._server_text)))
        pills.append(("user", user_txt, self._user_color(), w(user_txt)))
        return pills

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
            if kind == "user":
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

    # -- every pill is a click target -------------------------- #
    def _hit(self, x: float) -> str:
        for kind in ("model", "server", "user"):
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
        elif hit == "server":
            self.server_clicked.emit()
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:
        hit = self._hit(e.position().x())
        self.setCursor(Qt.PointingHandCursor if hit else Qt.ArrowCursor)
        # The server pill has its own hover text; the other two keep the widget's tooltip, which
        # their setters write. Shown by hand so it follows the pointer between pills.
        if hit == "server" and self._server_tip:
            from PySide6.QtWidgets import QToolTip
            QToolTip.showText(e.globalPosition().toPoint(), self._server_tip, self)
        super().mouseMoveEvent(e)
