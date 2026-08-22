"""Shared HUD scaffolding — the parts every heads-up display needs.

Three HUDs already exist (routing, flow, multicast) and each hand-rolled the same skeleton: a
glass panel, a `_busy` flag, a poll timer, an off-GUI-thread worker, `show_topright`/`close`, and
a mutually-exclusive toolbar toggle. The OS HUD would be the fourth copy, so the skeleton lives
here instead.

`HudHistory` is the generalised form of the Routing HUD's `RouteHistory`: a ring of snapshots
deduped by signature, so a state that has not changed costs nothing and every retained entry is a
real change. It is what makes a HUD replayable, which matters far more for the OS than for the
network — kernel events happen in microseconds, so "watch it live" is not an option and scrubbing
is the primary way to read them.

The existing three HUDs are deliberately NOT refactored onto this yet: they work, they have Qt
tests, and the refactor should land where those tests can be run.
"""
from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject, QRectF, QTimer, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen

from .glass import paint_glass_panel


class HudHistory:
    """Ring of (t, payload) snapshots, deduped on a caller-supplied signature.

    `push` records only when the signature CHANGES, so a quiet system costs one entry and every
    retained entry marks a real transition — which is exactly what the scrub timeline draws as
    its tick marks.
    """

    RETAIN_S = 600.0
    MAXSNAPS = 400

    def __init__(self, retain_s: float | None = None) -> None:
        self.snaps: list = []            # [(t, payload)]
        self.t_end: float = 0.0          # the live edge: last time anything was observed
        self._last_sig = None
        self.retain_s = retain_s if retain_s is not None else self.RETAIN_S

    def __len__(self) -> int:
        return len(self.snaps)

    @property
    def t_start(self) -> float:
        return self.snaps[0][0] if self.snaps else 0.0

    def change_times(self) -> list:
        return [t for t, _ in self.snaps]

    def set_retain(self, retain_s: float) -> None:
        """Change how far back the timeline reaches, live.

        Shortening it drops the excess on the next push rather than immediately, so the change is
        visible the moment anything happens and costs nothing while the machine is idle.
        """
        self.retain_s = max(1.0, float(retain_s))

    def push(self, payload, signature, tnow: float | None = None) -> bool:
        """Record `payload`. Returns True when it was a change (a new snapshot)."""
        tnow = time.monotonic() if tnow is None else tnow
        self.t_end = max(self.t_end, tnow)
        if signature == self._last_sig and self.snaps:
            return False
        self._last_sig = signature
        self.snaps.append((tnow, payload))
        cutoff = tnow - self.retain_s
        while len(self.snaps) > 1 and self.snaps[0][0] < cutoff:
            self.snaps.pop(0)
        if len(self.snaps) > self.MAXSNAPS:
            del self.snaps[:-self.MAXSNAPS]
        return True

    def at(self, t: float):
        """The payload in force at time `t` (the latest snapshot at or before it)."""
        best = None
        for st, payload in self.snaps:
            if st <= t:
                best = payload
            else:
                break
        return best if best is not None else (self.snaps[0][1] if self.snaps else None)

    def latest(self):
        return self.snaps[-1][1] if self.snaps else None

    def clear(self) -> None:
        self.snaps.clear()
        self._last_sig = None
        self.t_end = 0.0


# -- the scrub timeline, shared by any HUD that records ---------------------------------------- #
TIMELINE_H = 22
LIVE_W = 44


def timeline_rect(w: int, h: int) -> QRectF:
    return QRectF(12, h - TIMELINE_H - 6, w - 24 - LIVE_W, TIMELINE_H)


def live_rect(w: int, h: int) -> QRectF:
    return QRectF(w - LIVE_W - 8, h - TIMELINE_H - 6, LIVE_W, TIMELINE_H)


def paint_timeline(p: QPainter, theme, hist: HudHistory, w: int, h: int,
                   scrub_t: float | None) -> None:
    """Track, one tick per recorded change, a playhead, and the LIVE chip."""
    t = theme.theme
    tl = timeline_rect(w, h)
    cy = tl.center().y()
    span = (hist.t_end - hist.t_start) or 1.0

    def X(tt):
        return tl.left() + min(1.0, max(0.0, (tt - hist.t_start) / span)) * tl.width()

    p.setPen(QPen(QColor(t.line), 2))
    p.drawLine(int(tl.left()), int(cy), int(tl.right()), int(cy))
    # Thin the ticks: on a busy machine a snapshot is recorded almost every poll, and drawing all
    # of them turns the timeline into a solid bar that says nothing and cannot be aimed at. One
    # tick per 4px keeps it readable and still shows where activity clustered.
    p.setPen(QPen(QColor(t.accent), 2))
    last_x = -99
    for ct in hist.change_times():
        x = int(X(ct))
        if x - last_x < 4:
            continue
        last_x = x
        p.drawLine(x, int(cy) - 5, x, int(cy) + 5)

    cur = scrub_t if scrub_t is not None else hist.t_end
    knob = QColor(t.accent_for("amber")) if scrub_t is not None else QColor(t.accent)
    p.setBrush(knob)
    p.setPen(QPen(knob, 1))
    p.drawEllipse(QRectF(X(cur) - 5, cy - 5, 10, 10))

    # How far back the scrub reaches. It grows until the retention cap and then stops — saying so
    # turns "is this thing bounded?" from a guess into a reading.
    reach = hist.t_end - hist.t_start
    p.setFont(QFont(p.font().family(), 7))
    p.setPen(QColor(t.faint))
    p.drawText(int(tl.left()), int(tl.bottom()) - 2, 90, 10, Qt.AlignLeft,
               f"−{reach:.0f}s / {hist.retain_s:.0f}s")

    lr = live_rect(w, h)
    p.setFont(QFont(p.font().family(), 8, QFont.Bold))
    p.setBrush(QColor(0, 0, 0, 0))
    if scrub_t is not None:
        p.setPen(QColor(t.line))
        p.drawRoundedRect(lr, 8, 8)
        p.setPen(QColor(t.muted))
        p.drawText(lr, Qt.AlignCenter, "LIVE")
        p.setPen(QColor(t.accent_for("amber")))
        p.drawText(int(tl.left()), int(tl.top()) - 12, int(tl.width()), 12,
                   Qt.AlignLeft, f"replay  ·  t−{hist.t_end - cur:.0f}s")
    else:
        p.setPen(QColor(t.accent))
        p.drawRoundedRect(lr, 8, 8)
        p.drawText(lr, Qt.AlignCenter, "● LIVE")


def paint_empty(p: QPainter, theme, rect, title: str, message: str) -> None:
    """The honest empty state every HUD needs — say why there is nothing, never draw fake data."""
    paint_glass_panel(p, rect, theme, title)
    p.setPen(QColor(theme.theme.faint))
    p.drawText(rect, Qt.AlignCenter, message)


class HudController(QObject):
    """Poll off the GUI thread, feed the widget, own the history.

    Subclasses implement `read()` (blocking I/O — runs on a worker thread) and `apply(payload)`
    (runs on the GUI thread via the signal the subclass declares). Everything the controller
    needs from the app is injected, so it never imports main_window and is testable with fakes.
    """

    def __init__(self, parent, interval_ms: int = 1000, retain_s: float | None = None) -> None:
        super().__init__(parent)
        self.history = HudHistory(retain_s=retain_s)
        self._busy = False
        self._interval = interval_ms
        self._poll = QTimer(self)
        self._poll.timeout.connect(self.refresh)

    # -- subclass hooks --------------------------------------------------- #
    def read(self):                       # pragma: no cover - subclass responsibility
        raise NotImplementedError

    def deliver(self, payload) -> None:   # pragma: no cover - subclass responsibility
        raise NotImplementedError

    # -- machinery -------------------------------------------------------- #
    def refresh(self) -> None:
        if self._busy:
            return
        self._busy = True

        def work():
            try:
                payload = self.read()
                if payload is not None:
                    self.deliver(payload)
            except Exception:
                pass                       # a failed poll must never kill the timer
            finally:
                self._busy = False
        threading.Thread(target=work, daemon=True).start()

    def start(self) -> None:
        self.refresh()
        self._poll.start(self._interval)

    def stop(self) -> None:
        self._poll.stop()
