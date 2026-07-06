"""The Missions panel — the student-facing arena HUD.

Shows the brief, a live **objective tracker** (met ✓ / unmet ✗ / awaiting-run ⏳), the game
clock, and lives (attempts). It renders a Mission + the current world; the controller calls
`refresh(world)` whenever the canvas changes (structural objectives update instantly) and on
Run/Check. When the attempt is witnessed, a band badge appears.

Kept decoupled from the app bus for testability — a thin controller wires `bus.topology_changed`
to `refresh`. Theme is optional (sensible fallback colors) so the widget renders standalone.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
)

from ..domain import objectives as _obj


# Hard width cap for the HUD. A QLabel with wordWrap reports its full one-line text as its
# *minimum* width on real Qt (a long-standing quirk), which would force the whole Ask GINI dock —
# and the window — wider than the screen. `setMaximumWidth` is the one constraint Qt always
# honors (a widget can never exceed it, and its minimum is clamped to it), so we cap the panel.
_PANEL_MAX_W = 500


def _fluid(label: QLabel) -> QLabel:
    """A word-wrapped label that is allowed to shrink (small minimum width) so it wraps into the
    panel instead of demanding its full one-line width."""
    label.setWordWrap(True)
    label.setMinimumWidth(40)
    return label

# status → glyph (kept to widely-available symbols so they render everywhere; ⏳ is not)
_GLYPH = {_obj.MET: "✓", _obj.UNMET: "✗", _obj.PENDING: "○"}
_FALLBACK = {"text": "#e8e8ea", "muted": "#8a8a90", "met": "#3fb950", "unmet": "#8a8a90",
             "pending": "#d0932a", "accent": "#4c8dff", "warn": "#d9534f"}
_BAND_COLOR = {"gold": "#e0b53f", "pass": "#3fb950", "partial": "#d0932a", "incomplete": "#8a8a90"}


def _fmt_clock(remaining) -> str:
    if remaining is None:
        return "—:—"
    remaining = max(0, int(remaining))
    return f"{remaining // 60:d}:{remaining % 60:02d}"


class MissionPanel(QWidget):
    def __init__(self, theme=None, parent=None) -> None:
        super().__init__(parent)
        self.theme = theme
        self._mission = None
        # hard-cap the width so the HUD can never inflate the Ask GINI dock / the window
        self.setMaximumWidth(_PANEL_MAX_W)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        header = QHBoxLayout()
        self._title = _fluid(QLabel("Mission"))
        self._title.setStyleSheet("font-size:15px; font-weight:600;")
        header.addWidget(self._title, 1)
        self._clock = QLabel("—:—")
        self._clock.setStyleSheet("font-size:15px; font-weight:600;")
        self._lives = QLabel("")
        header.addWidget(self._lives)
        header.addSpacing(10)
        header.addWidget(self._clock)
        lay.addLayout(header)

        self._brief = _fluid(QLabel(""))
        lay.addWidget(self._brief)

        # current beat (guided missions): "Step 2 of 5" + the instruction, shown prominently
        self._step_num = QLabel("")
        self._step_num.setObjectName("Muted")
        self._step_num.setVisible(False)
        lay.addWidget(self._step_num)
        self._step = _fluid(QLabel(""))
        self._step.setVisible(False)
        lay.addWidget(self._step)

        line = QFrame(); line.setFrameShape(QFrame.HLine)
        lay.addWidget(line)

        self._obj_box = QVBoxLayout()
        self._obj_box.setSpacing(4)
        lay.addLayout(self._obj_box)

        self._band = QLabel("")
        self._band.setStyleSheet("font-size:14px; font-weight:700;")
        self._band.setVisible(False)
        lay.addWidget(self._band)
        lay.addStretch(1)

    # -- colors ------------------------------------------------------------- #
    def _c(self, role: str) -> str:
        t = getattr(self.theme, "theme", None)
        if t is not None:
            return {"text": getattr(t, "text", _FALLBACK["text"]),
                    "muted": getattr(t, "text_muted", _FALLBACK["muted"]),
                    "accent": getattr(t, "accent", _FALLBACK["accent"]),
                    "met": getattr(t, "accent_for", lambda k: _FALLBACK["met"])("green"),
                    "pending": getattr(t, "accent_for", lambda k: _FALLBACK["pending"])("amber"),
                    "unmet": getattr(t, "text_muted", _FALLBACK["unmet"]),
                    "warn": _FALLBACK["warn"]}.get(role, _FALLBACK.get(role, "#888"))
        return _FALLBACK.get(role, "#888")

    # -- API ---------------------------------------------------------------- #
    def set_mission(self, mission) -> None:
        self._mission = mission
        les = mission.lesson
        self._title.setText(les.title or les.id)
        self._brief.setText(les.brief)
        self._brief.setStyleSheet(f"color:{self._c('muted')};")
        self._render_objectives([_obj.ObjectiveResult(o.id, o.say, o.kind, _obj.UNMET)
                                 for o in les.objectives])
        self._sync_header()

    def set_step(self, text: str, index: int, total: int) -> None:
        """Show the current guided beat prominently (index/total = 'Step 2 of 5')."""
        self._step_num.setText(f"Step {index} of {total}")
        self._step.setText("→  " + text)
        self._step.setStyleSheet(f"color:{self._c('accent')}; font-weight:600;")
        self._step_num.setVisible(True)
        self._step.setVisible(True)

    def clear_step(self) -> None:
        self._step_num.setVisible(False)
        self._step.setVisible(False)

    def refresh(self, world, runner=None) -> None:
        """Re-evaluate against the world and repaint. Structural objectives resolve live (no
        runner); pass a `runner` (a Run/Check) to also resolve behavioral objectives."""
        if self._mission is None:
            return
        self._mission.evaluate(world, runner)
        self.render_current()

    def render_current(self) -> None:
        """Repaint from the mission's LAST evaluated results (e.g. after a Run/Check) without
        re-evaluating — so behavioral verdicts from the run aren't lost to a runner-less refresh."""
        if self._mission is None:
            return
        self._render_objectives(self._mission.last_results)
        self._sync_header()

    def tick(self) -> None:
        """Repaint just the clock (call on a 1s timer while playing)."""
        self._sync_header()

    # -- rendering ---------------------------------------------------------- #
    def _sync_header(self) -> None:
        m = self._mission
        if m is None:
            return
        rem = m.remaining()
        self._clock.setText(_fmt_clock(rem))
        warn = rem is not None and rem <= 60
        self._clock.setStyleSheet("font-size:15px; font-weight:600; color:"
                                  + (self._c("warn") if warn else self._c("text")) + ";")
        self._lives.setText("♥ " * m.lives_left() or "—")
        self._lives.setStyleSheet(f"color:{self._c('accent')};")
        if m.state in ("witnessed", "done") and m.last_band:
            col = _BAND_COLOR.get(m.last_band, self._c("text"))
            label = {"gold": "★ GOLD", "pass": "✓ PASS", "partial": "◐ PARTIAL",
                     "incomplete": "✗ INCOMPLETE"}.get(m.last_band, m.last_band.upper())
            self._band.setText(f"{label} — {m.score().summary}")
            self._band.setStyleSheet(f"font-size:14px; font-weight:700; color:{col};")
            self._band.setVisible(True)
        else:
            self._band.setVisible(False)

    def _clear_objectives(self) -> None:
        while self._obj_box.count():
            item = self._obj_box.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)      # detach immediately (deleteLater is async → stale paint)
                w.deleteLater()

    def _render_objectives(self, results) -> None:
        self._clear_objectives()
        for r in results:
            row = _fluid(QLabel(f"{_GLYPH.get(r.status, '•')}  {r.say}"))
            role = {"met": "met", "unmet": "unmet", "pending": "pending"}.get(r.status, "text")
            weight = "600" if r.status == _obj.MET else "400"
            row.setStyleSheet(f"color:{self._c(role)}; font-weight:{weight};")
            self._obj_box.addWidget(row)
