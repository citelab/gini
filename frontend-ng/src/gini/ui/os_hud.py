"""OS HUD — X-ray vision into a running kernel.

One HUD, several lanes, ONE shared time axis. That last part is the whole design: panels side by
side are a dashboard (adjacency), but lanes on a common axis are *correlation* — the page fault,
the mode flip and the trap line up vertically, and that alignment is the lesson.

Lanes (each toggleable; X-ray and Mode on by default):

  X-RAY   the causal story of a launch as swimlanes — syscall / proc / memory / fs / trap,
          assembled from the kernel's rings via the global event clock (domain/os_events.py)
  MODE    the user↔kernel band: where the CPU is, and which trap moved it

A launch takes microseconds, so this is a RECORDER first: the timeline at the bottom scrubs back
through what the kernel did. "Type a command, then walk through it" is the core interaction.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..domain.os_events import LANES, episodes, fault_events, merge, syscall_events, trap_events
from .glass import apply_glass, paint_glass_panel
from .hud import HudController, HudHistory, live_rect, paint_timeline, timeline_rect

# lane colours — stable per subsystem so a student learns to read the picture by colour
LANE_ACCENT = {"syscall": "blue", "proc": "green", "memory": "purple",
               "fs": "cyan", "trap": "amber"}
LANE_H = 26
HEAD_W = 62


class OsHud(QWidget):
    """Pure rendering over an event list + a mode split. No I/O here."""

    def __init__(self, parent, theme) -> None:
        super().__init__(parent)
        self.theme = theme
        self._events: list = []
        self._mode: dict = {}
        self._lanes = {"xray": True, "mode": True}
        self._history: HudHistory | None = None
        self._scrub_t: float | None = None
        self._scrub_drag = False
        self._focus_pid: int | None = None      # None = every process
        self.resize(560, 320)
        self.setMouseTracking(True)
        apply_glass(self)

    # -- data -------------------------------------------------------------- #
    def set_frame(self, events, mode) -> None:
        self._events = list(events or [])
        self._mode = dict(mode or {})
        self.update()

    def set_history(self, hist: HudHistory) -> None:
        self._history = hist
        self.update()

    @property
    def scrubbing(self) -> bool:
        return self._scrub_t is not None

    def go_live(self) -> None:
        self._scrub_t = None
        if self._history is not None:
            latest = self._history.latest()
            if latest:
                self.set_frame(*latest)
        self.update()

    # -- paint ------------------------------------------------------------- #
    def paintEvent(self, _e) -> None:  # noqa: N802
        t = self.theme.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        paint_glass_panel(p, self.rect(), self.theme, "OS  ·  X-RAY")

        if not self._events:
            p.setPen(QColor(t.faint))
            p.drawText(self.rect(), Qt.AlignCenter,
                       "no kernel events yet — launch a program, or rebuild the xv6 image\n"
                       "if this kernel predates the event clock")
            self._paint_scrub(p)
            return

        y = 30
        if self._lanes.get("mode"):
            y = self._paint_mode(p, y)
        if self._lanes.get("xray"):
            self._paint_xray(p, y)
        self._paint_scrub(p)

    def _span(self) -> tuple:
        lo = self._events[0].seq
        hi = self._events[-1].seq
        return lo, (hi - lo) or 1

    def _x_of(self, seq: int, left: float, width: float) -> float:
        lo, span = self._span()
        return left + (seq - lo) / span * width

    def _paint_mode(self, p: QPainter, y: int) -> int:
        """The user/kernel band. The privilege boundary is the OS abstraction students most
        often cannot picture; here it is a strip that flickers."""
        t = self.theme.theme
        left, width = HEAD_W, self.width() - HEAD_W - 14
        p.setFont(QFont(self.font().family(), 8))
        p.setPen(QColor(t.muted))
        p.drawText(6, y, HEAD_W - 8, LANE_H, Qt.AlignVCenter | Qt.AlignRight, "mode")
        user = float(self._mode.get("user", 0.0))
        kern = float(self._mode.get("kernel", 0.0))
        tot = (user + kern) or 1.0
        bar = QRectF(left, y + 6, width, LANE_H - 12)
        p.setBrush(QColor(t.accent_for("green")))
        p.setPen(Qt.NoPen)
        p.drawRect(QRectF(bar.left(), bar.top(), bar.width() * (user / tot), bar.height()))
        p.setBrush(QColor(t.accent_for("amber")))
        p.drawRect(QRectF(bar.left() + bar.width() * (user / tot), bar.top(),
                          bar.width() * (kern / tot), bar.height()))
        # every trap is a crossing of that boundary — mark them on the same axis
        p.setPen(QPen(QColor(t.text), 1))
        for e in self._events:
            if e.lane in ("trap", "memory") or e.kind in ("fork", "exec"):
                x = int(self._x_of(e.seq, left, width))
                p.drawLine(x, int(bar.top()) - 3, x, int(bar.bottom()) + 3)
        p.setPen(QColor(t.faint))
        p.setFont(QFont(self.font().family(), 7))
        p.drawText(left, y + LANE_H - 2, width, 10, Qt.AlignLeft,
                   f"user {user / tot * 100:.0f}%   kernel {kern / tot * 100:.0f}%"
                   "   ·   ticks = boundary crossings")
        return y + LANE_H + 12

    def _paint_xray(self, p: QPainter, y: int) -> None:
        """One row per subsystem, events as dots on the shared axis. Reading down a vertical
        slice tells you everything the kernel was doing at that instant."""
        t = self.theme.theme
        left, width = HEAD_W, self.width() - HEAD_W - 14
        for lane in LANES:
            evs = [e for e in self._events if e.lane == lane]
            p.setFont(QFont(self.font().family(), 8))
            p.setPen(QColor(t.text if evs else t.faint))
            p.drawText(6, y, HEAD_W - 8, LANE_H, Qt.AlignVCenter | Qt.AlignRight, lane)
            p.setPen(QPen(QColor(t.line), 1))
            p.drawLine(int(left), int(y + LANE_H / 2), int(left + width), int(y + LANE_H / 2))
            col = QColor(t.accent_for(LANE_ACCENT.get(lane, "slate")))
            for e in evs:
                x = self._x_of(e.seq, left, width)
                p.setBrush(col)
                p.setPen(Qt.NoPen)
                p.drawEllipse(QRectF(x - 4, y + LANE_H / 2 - 4, 8, 8))
            # label the few events that carry the story, if there is room
            if evs and len(evs) <= 6:
                p.setFont(QFont(self.font().family(), 7))
                p.setPen(QColor(t.muted))
                for e in evs:
                    x = self._x_of(e.seq, left, width)
                    p.drawText(int(x) - 26, int(y), 52, 11, Qt.AlignCenter, e.kind[:10])
            y += LANE_H

    def _paint_scrub(self, p: QPainter) -> None:
        if self._history is not None and len(self._history):
            paint_timeline(p, self.theme, self._history, self.width(), self.height(),
                           self._scrub_t)

    # -- interaction --------------------------------------------------------- #
    def mousePressEvent(self, e) -> None:  # noqa: N802
        pos = e.position() if hasattr(e, "position") else e.pos()
        h = self._history
        if h is None or not len(h):
            return
        if live_rect(self.width(), self.height()).contains(pos):
            self.go_live()
            return
        if timeline_rect(self.width(), self.height()).contains(pos):
            self._scrub_drag = True
            self._scrub_to(pos.x())

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self._scrub_drag:
            pos = e.position() if hasattr(e, "position") else e.pos()
            self._scrub_to(pos.x())

    def mouseReleaseEvent(self, _e) -> None:  # noqa: N802
        self._scrub_drag = False

    def _scrub_to(self, x: float) -> None:
        h = self._history
        tl = timeline_rect(self.width(), self.height())
        span = (h.t_end - h.t_start) or 1.0
        frac = min(1.0, max(0.0, (x - tl.left()) / (tl.width() or 1.0)))
        t = h.t_start + frac * span
        if t >= h.t_end - 0.5:
            self.go_live()
            return
        self._scrub_t = t
        frame = h.at(t)
        if frame:
            self.set_frame(*frame)


class OsHudController(HudController):
    """Polls the xv6 agent, merges the rings, records each frame for replay."""

    frame_ready = Signal(object, object)          # (events, mode)

    def __init__(self, parent, theme, agent_of, mode_of=None, interval_ms: int = 900) -> None:
        super().__init__(parent, interval_ms=interval_ms)
        self.hud = OsHud(parent, theme)
        self.hud.set_history(self.history)
        self._agent_of = agent_of                 # () -> AgentClient|None
        self._mode_of = mode_of or (lambda: {})   # () -> {"user":n,"kernel":n,"idle":n}
        self.frame_ready.connect(self._on_frame)

    def read(self):
        """Blocking reads on a worker thread: three rings, merged by the global event clock."""
        agent = self._agent_of()
        if agent is None:
            return None
        sc = agent.get_text("/sc")
        flt = agent.get_text("/faults")
        tr = agent.get_text("/traps")
        events = merge(syscall_events(sc), fault_events(flt), trap_events(tr), limit=400)
        return events, self._mode_of()

    def deliver(self, payload) -> None:
        self.frame_ready.emit(payload[0], payload[1])

    def _on_frame(self, events, mode) -> None:
        # signature = the newest event's clock value: a machine doing nothing records nothing,
        # so every retained frame is a real change and every timeline tick means something
        sig = events[-1].seq if events else 0
        self.history.push((events, mode), sig)
        if not self.hud.scrubbing:
            self.hud.set_frame(events, mode)
        self.hud.set_history(self.history)

    def latest_episodes(self) -> list:
        """Per-process stories from the current frame — what a 'which launch?' picker lists."""
        frame = self.history.latest()
        return episodes(frame[0]) if frame else []

    def show_topright(self) -> None:
        par = self.hud.parentWidget()
        if par is not None:
            self.hud.move(max(0, par.width() - self.hud.width() - 16), 16)
        self.hud.show()
        self.hud.raise_()
        self.start()

    def close(self) -> None:
        self.stop()
        self.hud.hide()

