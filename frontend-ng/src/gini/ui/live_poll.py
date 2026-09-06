"""Shared live-poll behaviour for the Machine Lab's data faces.

The Virtual Memory and File System faces had no timer at all: they painted once in the
constructor and once per button press, so `alloc`, `toucher`, `writer` and `sgrind` — programs
written to be *watched* — were watched on a still photograph.

Every rule encoded here was learned the expensive way, and each one has a test:

  * **one read in flight at a time** — a slow round must not have another started on top of it
  * **stop on close, pause on hide** — a lab nobody is looking at must cost nothing
  * **read off the GUI thread, render on it** — Qt objects are touched only in the slot
  * **back off when the wire is busy** — the serial line is the scarce resource, not the CPU
  * **a bad read never blanks a good picture** — it marks it stale and keeps it

**The join in `stop_polling()` is the load-bearing part.** The first draft of this mixin fired
daemon threads and forgot them, guarding only with an `if not self._closed` before the emit. That
loses two ways. The test suite's autouse `_no_leaked_threads` fixture fails any test that leaves a
thread running, so every test with a deliberately slow provider would fail on the guard rather
than on its own assertion. Worse, in production `_retire` calls `close()` then `deleteLater()`
while a worker may already be *past* the flag check and about to emit into a QObject being
destroyed — which is exactly the shape that was crashing pytest-qt on half of all runs before the
runtime `stop()` work. So `_closed` is set BEFORE the timer stops, and the worker is joined: once
the flag is set no emit can happen, and the join only waits for the read itself to return.

The host class must declare `snap_ready = Signal(object)` and implement `_read()` (off the GUI
thread; return None to mean "this round failed") and `_render_live(payload, fresh)`.
"""
from __future__ import annotations

import threading

from PySide6.QtCore import QTimer

#: How long to wait for an in-flight read when a face closes. Generous enough to cover a round
#: that is timing out (three dumps at a 0.35 s ceiling), short enough that closing a window never
#: feels stuck. In the healthy case the read is milliseconds and this returns at once.
JOIN_TIMEOUT = 2.0

#: Widest the backoff may grow: 1x, 2x, 4x, 8x, 16x the base interval. A wedged or saturated
#: machine must not be hammered, but a face must not go so quiet it looks dead either.
MAX_BACKOFF = 4


class LivePollMixin:
    """Mix into a QDialog that declares `snap_ready = Signal(object)`."""

    def _init_poll(self, interval_ms: int, live: bool) -> None:
        self._closed = False
        self._busy = False
        self._paused = not live
        self._interval = int(interval_ms)
        self._backoff = 0                    # consecutive failed rounds
        self._last_ok = None                 # last good payload: a bad read never blanks the face
        self._poll_thread: threading.Thread | None = None
        self._poll = QTimer(self)
        self._poll.timeout.connect(self._tick)
        self.snap_ready.connect(self._on_ready)
        if live:
            self._poll.start(self._interval)

    # -- the round ------------------------------------------------------------ #
    def _tick(self) -> None:
        if self._busy or self._closed or self._paused:
            return
        self._busy = True
        self._poll_thread = threading.Thread(
            target=self._work, daemon=True, name=f"{type(self).__name__}-poll")
        self._poll_thread.start()

    def _work(self) -> None:
        """OFF the GUI thread. Touches no widget — the payload goes back through the signal."""
        payload, ok = None, False
        try:
            payload = self._read()
            ok = payload is not None
        except Exception:                    # noqa: BLE001 — a bad round is data, not a crash
            ok = False
        if not self._closed:
            self.snap_ready.emit((payload, ok))

    def _on_ready(self, res) -> None:
        self._busy = False
        if self._closed:
            return
        payload, ok = res
        if ok:
            self._last_ok, self._backoff = payload, 0
        else:
            self._backoff = min(self._backoff + 1, MAX_BACKOFF)
        self._apply_interval()
        self._render_live(self._last_ok, ok)

    def _apply_interval(self) -> None:
        want = self._interval * (2 ** self._backoff)
        if self._poll.isActive() and self._poll.interval() != want:
            self._poll.setInterval(want)

    def poll_caption(self) -> str:
        """What the header chip says: the rate, and whether it has been widened by failures."""
        secs = (self._poll.interval() or self._interval) / 1000.0
        if self._paused:
            return "paused"
        return f"live · {secs:g}s" + (" · backing off" if self._backoff else "")

    # -- lifetime -------------------------------------------------------------- #
    def set_paused(self, on: bool) -> None:
        """Pause matters pedagogically: a student reading a page table wants it to hold still."""
        self._paused = bool(on)
        if self._paused:
            self._poll.stop()
        elif not self._closed:
            self._poll.start(self._interval)   # resuming also clears any accumulated backoff
            self._backoff = 0

    def stop_polling(self, timeout: float = JOIN_TIMEOUT) -> None:
        """Stop, and WAIT for the worker. See the module docstring — the wait is the point."""
        self._closed = True                  # set FIRST: _work checks it before it emits
        try:
            self._poll.stop()
        except RuntimeError:                 # the C++ timer is already gone
            pass
        t, self._poll_thread = self._poll_thread, None
        if t is not None and t.is_alive():
            t.join(timeout)
        self._busy = False                   # nothing is running now, so the next tick may go

    def hideEvent(self, e):  # noqa: N802
        self._poll.stop()                    # a face nobody can see must cost nothing
        super().hideEvent(e)

    def showEvent(self, e):  # noqa: N802
        # Reopening must not show a frozen picture, so stopping is never one-way.
        if not self._paused:
            self._closed = False
            self._poll.start(self._poll.interval() or self._interval)
        super().showEvent(e)

    def closeEvent(self, e):  # noqa: N802
        self.stop_polling()
        super().closeEvent(e)
