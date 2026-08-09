"""The Missions panel — the student-facing arena HUD.

Shows the brief, a live **objective tracker** (met ✓ / unmet ✗ / awaiting-run ⏳), the game
clock, and lives (attempts). It renders a Mission + the current world; the controller calls
`refresh(world)` whenever the canvas changes (structural objectives update instantly) and on
Run/Check. When the attempt is witnessed, a band badge appears.

Kept decoupled from the app bus for testability — a thin controller wires `bus.topology_changed`
to `refresh`. Theme is optional (sensible fallback colors) so the widget renders standalone.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from ..domain import objectives as _obj
from .theme.manager import scale_css as _scss


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
    run_requested = Signal()          # the student pressed Run/Check → run behavioral probes

    def __init__(self, theme=None, parent=None) -> None:
        super().__init__(parent)
        self.theme = theme
        self._mission = None
        self._shown: int | None = None    # the level the student is actually ON
        self._peek: int | None = None     # a level they clicked to look ahead at (read-only)
        # hard-cap the width so the HUD can never inflate the Ask GINI dock / the window
        self.setMaximumWidth(_PANEL_MAX_W)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        header = QHBoxLayout()
        self._title = _fluid(QLabel("Mission"))
        self._title.setStyleSheet(_scss("font-size:16px; font-weight:700;"))
        header.addWidget(self._title, 1)
        self._clock = QLabel("—:—")
        self._clock.setStyleSheet(_scss("font-size:15px; font-weight:600;"))
        self._lives = QLabel("")
        header.addWidget(self._lives)
        header.addSpacing(10)
        header.addWidget(self._clock)
        lay.addLayout(header)

        self._brief = _fluid(QLabel(""))
        self._brief.setStyleSheet(_scss("font-size:12px;"))
        lay.addWidget(self._brief)

        # the LEVEL RIBBON — the shape of the whole journey, always visible
        self._ribbon = QHBoxLayout()
        self._ribbon.setSpacing(4)
        lay.addLayout(self._ribbon)

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

        # Run/Check — resolves behavioral objectives against the live runtime. Shown only when the
        # lesson HAS behavioral objectives (nothing to run otherwise).
        self._run_btn = QPushButton("▶  Run / Check")
        self._run_btn.setObjectName("Accent")
        self._run_btn.setCursor(Qt.PointingHandCursor)
        self._run_btn.setVisible(False)
        self._run_btn.clicked.connect(self.run_requested.emit)
        lay.addWidget(self._run_btn)

        self._band = QLabel("")
        self._band.setStyleSheet(_scss("font-size:14px; font-weight:700;"))
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
        self._shown = self._peek = None        # a new mission starts back at Level 1
        les = mission.lesson
        self._title.setText(les.title or les.id)
        self._brief.setText(les.brief)
        self._brief.setStyleSheet(f"color:{self._c('muted')};")
        self._render_objectives([_obj.ObjectiveResult(o.id, o.say, o.kind, _obj.UNMET)
                                 for o in les.objectives])
        self._run_btn.setVisible(any(o.is_behavioral() for o in les.objectives))
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
        # Show the LIVE band, computed from the objectives on screen — never a frozen snapshot, or the
        # label can contradict the count ("PARTIAL — 9/9"). Appears once there's progress to report.
        sc = m.score()
        if sc.met > 0 or m.state in ("witnessed", "done"):
            col = _BAND_COLOR.get(sc.band, self._c("text"))
            label = {"gold": "★ GOLD", "pass": "✓ PASS", "partial": "◐ PARTIAL",
                     "incomplete": "✗ INCOMPLETE"}.get(sc.band, sc.band.upper())
            self._band.setText(f"{label} — {sc.summary}")
            self._band.setStyleSheet(_scss(f"font-size:14px; font-weight:700; color:{col};"))
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

    def _peek_level(self, level: int) -> None:
        """Click a ribbon tile to look at another level. Read-only: it changes nothing, and the
        moment the level you're actually on advances, the view snaps back to it."""
        self._peek = None if level == self._shown else level
        self.render_current()

    def _clear_ribbon(self) -> None:
        while self._ribbon.count():
            item = self._ribbon.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def _tile(self, lv: int, state: str, done: int, total: int, selected: bool) -> QPushButton:
        """One ribbon tile. `state` ∈ done | active | locked."""
        face = {"done": self._c("met"), "active": self._c("accent"), "locked": self._c("muted")}[state]
        mark = "✓" if state == "done" else str(lv)
        short = _obj.LEVEL_NAME.get(lv, "").split("(")[0].strip()     # tiles stay narrow
        b = QPushButton(f"{mark}  {short}")
        b.setCursor(Qt.PointingHandCursor)
        b.setToolTip({"done": f"Level {lv} — finished ({done}/{total})",
                      "active": f"Level {lv} — you are here ({done}/{total})",
                      "locked": f"Level {lv} — {total} task(s). Click to look ahead."}[state])
        # the active tile is ILLUMINATED (filled); done tiles are solid-but-quiet; locked are outlines
        if state == "active":
            css = f"background:{face}; color:#0d0d10; border:1px solid {face};"
        elif state == "done":
            css = f"background:transparent; color:{face}; border:1px solid {face};"
        else:
            css = f"background:transparent; color:{face}; border:1px dashed {face};"
        ring = f"outline:2px solid {self._c('text')};" if selected else ""
        b.setStyleSheet(
            f"QPushButton{{{css}{ring} border-radius:4px; padding:4px 8px; font-size:11px; "
            f"font-weight:700;}}")
        b.clicked.connect(lambda _=False, l=lv: self._peek_level(l))
        return b

    def _render_objectives(self, results) -> None:
        """A RIBBON of levels + ONLY the level you're on.

        Ten-plus rows of tasks would bury the chat and bury the *next move* among things you've
        already done. So the panel shows one level at a time, in a readable size, and the ribbon
        carries the shape of the whole journey: finished levels tick over, the level you're on is
        lit, later ones sit dashed and unlit. Finish a level and the next simply comes to life.
        Locked tiles can be clicked to look ahead (read-only) — knowing what's coming is motivating;
        the view snaps back the moment you actually advance."""
        self._clear_objectives()
        self._clear_ribbon()
        if not results:
            self._shown = self._peek = None
            return
        # group into rungs, preserving the ladder order
        rungs: list[tuple[int, list]] = []
        for r in results:
            lv = getattr(r, "level", 1)
            if not rungs or rungs[-1][0] != lv:
                rungs.append((lv, []))
            rungs[-1][1].append(r)

        # the ACTIVE rung is the first with anything still open — that's where the student is
        active = next((lv for lv, rs in rungs if not all(x.met for x in rs)), None)
        if active != self._shown:
            self._peek = None                 # you advanced (or the mission just started) → follow
        self._shown = active
        view = self._peek if self._peek is not None else active
        if view is None:                       # everything done
            view = rungs[-1][0]

        for lv, rs in rungs:
            done = sum(1 for x in rs if x.met)
            state = ("done" if done == len(rs) else
                     "active" if lv == active else "locked")
            self._ribbon.addWidget(self._tile(lv, state, done, len(rs), selected=(lv == view)))
        self._ribbon.addStretch(1)

        rows = next((rs for lv, rs in rungs if lv == view), [])
        if self._peek is not None and self._peek != active:
            way = "back at" if active is not None and view < active else "ahead at"
            look = QLabel(f"Looking {way} Level {view} — you're on Level {active}.")
            look.setStyleSheet(_scss(f"color:{self._c('pending')}; font-size:11px; font-weight:600;"))
            self._obj_box.addWidget(look)

        head = QLabel(f"LEVEL {view} · {_obj.LEVEL_NAME.get(view, '').upper()}")
        head.setStyleSheet(f"color:{self._c('muted')}; font-size:11px; font-weight:700; "
                           f"letter-spacing:1px;")
        self._obj_box.addWidget(head)
        for r in rows:
            row = _fluid(QLabel(f"{_GLYPH.get(r.status, '•')}   {r.say}"))
            role = {"met": "met", "unmet": "unmet", "pending": "pending"}.get(r.status, "text")
            weight = "600" if r.status == _obj.MET else "400"
            # only ONE level is on screen, so the tasks can finally be a readable size
            row.setStyleSheet(_scss(f"color:{self._c(role)}; font-weight:{weight}; font-size:13px;"))
            self._obj_box.addWidget(row)
