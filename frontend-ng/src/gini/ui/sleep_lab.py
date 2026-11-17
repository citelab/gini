"""Sleep Lab — who is asleep in an xv6 Machine, on what, for how long, and how each channel's
sleeps and wakeups add up.

A kernel waits in two ways. A CPU can spin on a lock, and the Lock Lab shows that. Or a process
can sleep: give up the CPU until somebody calls wakeup() on the channel it chose. Sleeping is
everywhere in xv6 (wait(), pipes, the console, the disk, the log, inodes, buffers) and it is
invisible: a sleeping process looks exactly like one with nothing to do, and a sleep/wakeup bug --
a lost wakeup, a wakeup on the wrong channel, an `if` that should be a `while` -- shows up only as
a machine that stops making progress.

The feed is `gini_sleepdump` (the agent's `/sleep`, no gdb halt): the processes with a channel
set, and per-channel counters since the last Reset. Everything drawn is a pure function of a
`SleepView` (domain/xv6.parse_sleep) -- `sleeper_rows`, `channel_rows` and `verdict` -- so the
rules are unit-tested without Qt; the controller polls off the GUI thread, like the Lock Lab.

**What a snapshot cannot see.** The panel reads the kernel about once a second, so the *asleep
now* list only ever shows long sleepers. The *channels* counters see every sleep, however short.
The help text says so, because otherwise an empty list reads as "nothing sleeps".

**The lost-wakeup hint is a hint.** A wakeup that finds nobody is normal (a post when nobody
waits), and so is a sleep soon after one. A LONG sleep that began a TINY moment after a wakeup on
the same channel found nobody is what a lost wakeup produces: the waiter checked its condition
before the wakeup and armed itself after it. The panel offers that as worth a look, in those words.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..domain.xv6 import SleepView, parse_sleep
from .theme import ThemeManager, icons
from .theme.manager import scale_css as _scss
from .windowing import standalone
from .worker_host import run_off_gui

#: The hint fires for a sleep longer than this...
HINT_ASLEEP = 1.0
#: ...that began less than this after a wakeup on its channel found nobody.
HINT_GAP = 0.001
#: Back to sleep per process woken at or above this is drawn as the thing worth a look.
HOT = 0.5

NO_HOOKS = ("This kernel's proc.c is a student copy without GINI's sleep hooks, so nothing is "
            "timed or counted; the processes below come from the process table, as in the "
            "Process Tree.")


def fmt_duration(sec: float | None) -> str:
    """0.000031 -> '31 µs', 0.0042 -> '4.2 ms', 4.2 -> '4.2 s', 125 -> '2 min 5 s'."""
    if sec is None:
        return "—"
    if sec < 0.001:
        return f"{sec * 1e6:.0f} µs"
    if sec < 1.0:
        return f"{sec * 1e3:.1f} ms"
    if sec < 60:
        return f"{sec:.1f} s"
    m, s = divmod(int(sec), 60)
    return f"{m} min {s} s"


def lost_wakeup_hint(s) -> str:
    """The hint for one sleeper, or "". Only a long sleep that started a tiny moment after a
    wakeup on the same channel found nobody."""
    if not s.asleep or s.seconds is None or s.gap is None:
        return ""
    if s.seconds > HINT_ASLEEP and s.gap < HINT_GAP:
        return (f"a wakeup on this channel found nobody {fmt_duration(s.gap)} before it slept "
                "— worth a look")
    return ""


def sleeper_rows(view: SleepView) -> list:
    """[(process, waiting_on, asleep_for, hint, armed)] — asleep longest first, then the armed
    ones ("about to sleep"), which the table draws greyed."""
    rows = []
    for s in view.sleepers:
        rows.append((f"{s.name} (pid {s.pid})", s.channel,
                     fmt_duration(s.seconds) if s.asleep else "about to sleep",
                     lost_wakeup_hint(s), not s.asleep))
    return rows


def channel_rows(channels, limit: int = 8) -> list:
    """[(name, sleeps, wakeups, empty, woken, resleeps, longest, ratio, frac)] for the busiest
    channels, where `frac` scales each bar against the busiest. Idle channels are left out."""
    busy = sorted((c for c in channels if c.activity), key=lambda c: (-c.activity, c.index))
    top = busy[:limit]
    most = max((c.activity for c in top), default=0)
    return [(c.name, c.sleeps, c.wakeups, c.empty, c.woken, c.resleeps, c.longest,
             c.resleep_ratio, (c.activity / most) if most else 0.0) for c in top]


def channel_text(sleeps, wakeups, empty, woken, resleeps, longest) -> str:
    """One channel's counters as a line. A part with nothing to say is left out: "0 of 0 slept
    again" and "longest 0 µs" are noise on a channel nobody sleeps on."""
    def n(k, word):
        return f"{k:,} {word}" + ("" if k == 1 else "s")
    parts = [n(sleeps, "sleep"), n(wakeups, "wakeup"), f"{empty:,} found nobody"]
    if woken:
        parts.append(f"{resleeps:,} of {woken:,} woken slept again")
    if longest:
        parts.append(f"longest {fmt_duration(longest)}")
    return " · ".join(parts)


def verdict(view: SleepView) -> str:
    """One line, in the Lock Lab's manner: what a student should take from the panel right now."""
    if not view.ok:
        return "no sleep telemetry in this kernel — rebuild the xv6 image"
    asleep = [s for s in view.sleepers if s.asleep]
    if not view.hooks:
        n = len(asleep)
        return NO_HOOKS + (f" {n} process{'es' if n != 1 else ''} asleep." if n else "")
    for s in asleep:
        if lost_wakeup_hint(s):
            return (f"pid {s.pid} ({s.name}) has slept {fmt_duration(s.seconds)} on a channel "
                    f"that had a wakeup {fmt_duration(s.gap)} before it went to sleep. That is "
                    "what a lost wakeup looks like — a hint, not a verdict.")
    if not asleep:
        return "Nothing is asleep."
    longest = asleep[0]
    n = len(asleep)
    line = (f"{n} process{'es' if n != 1 else ''} asleep; the longest, {longest.name} "
            f"(pid {longest.pid}), has waited {fmt_duration(longest.seconds)} for "
            f"{longest.channel}")
    if longest.channel == "console input" and longest.name == "sh":
        line += " (that is just the shell waiting for you)"
    elif longest.channel == "a child to exit":
        line += " (a parent in wait())"
    return line + "."


class ChannelBars(QWidget):
    """One row per busy channel: name, a bar scaled to the busiest, and the counters."""

    ROW_H = 26

    def __init__(self, theme) -> None:
        super().__init__()
        self.theme = theme
        self._rows: list = []
        self._empty = "no sleep telemetry — rebuild the xv6 image"
        self.setMinimumHeight(140)

    def set_channels(self, channels, empty: str = "") -> None:
        self._rows = channel_rows(channels)
        if empty:
            self._empty = empty
        self.setMinimumHeight(max(110, 12 + self.ROW_H * max(len(self._rows), 1)))
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        t = self.theme.theme
        p.fillRect(self.rect(), QColor(t.panel))
        if not self._rows:
            p.setPen(QColor(t.faint))
            p.drawText(self.rect(), Qt.AlignCenter, self._empty)
            return
        lblw = 168
        trackw = max(60, min(240, int(self.width() * 0.22)))   # the counters get the rest
        textx = lblw + 8 + trackw + 8
        small = QFont("monospace", 8)
        fm = QFontMetrics(small)
        for i, (name, sleeps, wakeups, empty, woken, res, longest, ratio, frac) in \
                enumerate(self._rows):
            y = 6 + i * self.ROW_H
            p.setFont(QFont("monospace", 9))
            p.setPen(QColor(t.text))
            p.drawText(6, y, lblw, self.ROW_H - 6, Qt.AlignVCenter | Qt.AlignLeft, name)
            track = QRectF(lblw + 8, y + 5, trackw, self.ROW_H - 14)
            p.setBrush(QColor(t.panel2)); p.setPen(Qt.NoPen)
            p.drawRoundedRect(track, 3, 3)
            p.setBrush(QColor(t.accent_for("amber" if ratio >= HOT else "purple")))
            p.drawRoundedRect(QRectF(track.left(), track.top(),
                                     max(2.0, track.width() * frac), track.height()), 3, 3)
            p.setFont(small)
            p.setPen(QColor(t.muted))
            room = max(40, self.width() - textx - 6)
            text = channel_text(sleeps, wakeups, empty, woken, res, longest)
            p.drawText(textx, y, room, self.ROW_H - 6, Qt.AlignVCenter | Qt.AlignLeft,
                       fm.elidedText(text, Qt.ElideRight, room))

class SleepLab(QDialog):
    """The Sleep Lab window: who is asleep now, the channels' counters, and a one-line verdict."""

    sleep_ready = Signal(str)

    def __init__(self, parent, theme: ThemeManager, device=None, provider=None,
                 live: bool = False) -> None:
        super().__init__(parent)
        # Its own window, not an owned dialog (see ui/windowing), like every Machine Lab face.
        standalone(self)
        self.theme = theme
        self.device = device
        self.provider = provider
        self.live = live
        self._closed = False
        self._busy = False

        t = theme.theme
        name = getattr(device, "name", "xv6")
        self.setWindowTitle(f"Sleep Lab — {name}")
        self.resize(900, 640)
        self.setStyleSheet(f"QDialog{{background:{t.bg};}}")
        root = QVBoxLayout(self)

        head = QHBoxLayout()
        ic = QLabel(); ic.setPixmap(icons.render_pixmap("sleep", t.accent_for("purple"), 22))
        title = QLabel(f"  Sleep Lab — {name}")
        title.setStyleSheet(_scss(f"color:{t.text};font-size:16px;font-weight:600;"))
        head.addWidget(ic); head.addWidget(title); head.addStretch(1)
        self._reset_btn = QPushButton("  Reset counters")
        self._reset_btn.setToolTip("Zero every channel's counters, so you can measure ONE workload")
        self._reset_btn.setStyleSheet(
            f"QPushButton{{color:{t.text};background:{t.panel2};border:1px solid {t.line};"
            f"border-radius:8px;padding:5px 11px;}}QPushButton:hover{{border-color:{t.accent};}}")
        self._reset_btn.clicked.connect(self._reset)
        self._reset_btn.setEnabled(bool(live))
        head.addWidget(self._reset_btn)
        root.addLayout(head)

        self._note = QLabel(); self._note.setWordWrap(True)
        self._note.setStyleSheet(_scss(f"color:{t.text};font-size:12px;"))
        root.addWidget(self._note)

        frame_css = _scss(f"QFrame{{background:{t.panel};border:1px solid {t.line};"
                          f"border-radius:10px;}}")
        cap_css = _scss(f"color:{t.muted};font-size:12px;border:none;")

        now_box = QFrame(); now_box.setStyleSheet(frame_css)
        nl = QVBoxLayout(now_box); nl.setContentsMargins(10, 10, 10, 10)
        cap = QLabel("Asleep now  ·  longest first"); cap.setStyleSheet(cap_css)
        nl.addWidget(cap)
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["process", "waiting on", "asleep for", "hint"])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionMode(QAbstractItemView.NoSelection)
        self._table.setFocusPolicy(Qt.NoFocus)
        hh = self._table.horizontalHeader()
        for col, mode in ((0, QHeaderView.ResizeToContents), (1, QHeaderView.ResizeToContents),
                          (2, QHeaderView.ResizeToContents), (3, QHeaderView.Stretch)):
            hh.setSectionResizeMode(col, mode)
        self._table.setStyleSheet(_scss(
            f"QTableWidget{{background:{t.panel};color:{t.text};border:none;"
            f"gridline-color:{t.line};font-size:12px;}}"
            f"QHeaderView::section{{background:{t.panel2};color:{t.muted};border:none;"
            f"padding:4px;}}"))
        nl.addWidget(self._table)
        root.addWidget(now_box, 1)

        ch_box = QFrame(); ch_box.setStyleSheet(frame_css)
        cl = QVBoxLayout(ch_box); cl.setContentsMargins(10, 10, 10, 10)
        cap = QLabel("Channels since Reset  ·  sleeps and wakeups, busiest first")
        cap.setStyleSheet(cap_css)
        cl.addWidget(cap)
        self._bars = ChannelBars(theme)
        cl.addWidget(self._bars)
        root.addWidget(ch_box, 1)

        hint = QLabel(
            "A process sleeps on an ADDRESS, its channel, and wakeup() on that address wakes it. "
            "\"Slept again\" counts processes that were woken and went straight back to sleep on the "
            "same channel: many of them means a wakeup rouses everybody for something only one can "
            "have, which is why a wait is a `while` loop and not an `if`. This panel reads the "
            "kernel about once a second, so the list above only shows long sleeps; the counters "
            "below count every sleep, however short.")
        hint.setWordWrap(True)
        hint.setStyleSheet(_scss(f"color:{t.faint};font-size:11px;"))
        root.addWidget(hint)

        self.sleep_ready.connect(self._on_sleep)
        self._poll = QTimer(self)
        self._poll.timeout.connect(self._fetch)
        if live:
            self._poll.start(1200)
            self._fetch()
        else:
            self._note.setText("offline — open this on a running xv6 Machine to see who is asleep")
            self._bars.set_channels([], "offline")

    # -- data ------------------------------------------------------------- #
    def _read(self) -> str:
        agent = getattr(self.provider, "agent", None)
        if agent is None:
            return ""
        try:
            return agent.get_text("/sleep") or ""
        except Exception:  # noqa: BLE001 — a diagnostic panel must never raise into Qt
            return ""

    def _fetch(self) -> None:
        if self._busy or self._closed:
            return
        self._busy = True

        def work():
            try:
                txt = self._read()
                if not self._closed:
                    self.sleep_ready.emit(txt)
            finally:
                self._busy = False
        run_off_gui(self, work)

    def _on_sleep(self, txt: str) -> None:
        if self._closed:
            return
        self.show_view(parse_sleep(txt))

    def show_view(self, view: SleepView) -> None:
        """Draw one view. Public so tests (and a recorded dump) can drive the panel directly."""
        t = self.theme.theme
        self._note.setText(verdict(view))
        rows = sleeper_rows(view)
        self._table.setRowCount(len(rows))
        for r, (proc, chan, dur, hint, armed) in enumerate(rows):
            for c, text in enumerate((proc, chan, dur, hint)):
                item = QTableWidgetItem(text)
                if armed:
                    item.setForeground(QColor(t.faint))
                elif c == 3 and hint:
                    item.setForeground(QColor(t.accent_for("amber")))
                self._table.setItem(r, c, item)
        empty = ("no sleep telemetry — rebuild the xv6 image" if not view.ok else
                 "no counters: this proc.c has no GINI hooks" if not view.hooks else
                 "nothing has slept or woken since Reset")
        self._bars.set_channels(view.channels, empty)

    def _reset(self) -> None:
        agent = getattr(self.provider, "agent", None)
        if agent is None:
            return
        try:
            agent.post("/sleep/reset")
        except Exception:  # noqa: BLE001
            pass
        self._fetch()

    def closeEvent(self, e) -> None:
        self._closed = True
        self._poll.stop()
        super().closeEvent(e)
