"""A terminal, drawn by Qt.

This is the replacement for the embedded xterm.js page. pyte does the hard part — parsing the
byte stream into a grid of cells with attributes — and this widget paints that grid and turns key
presses back into bytes. The PTY still lives in the container, reached over ttyd's WebSocket, so
nothing here has to care about pseudo-terminals or platform differences.

WHY NOT A WEB VIEW. The previous version embedded ttyd's own xterm.js page in a QWebEngineView.
That is a Chromium process per terminal: ~150MB, a start-up cost heavy enough to stall a slow
machine, a page that cannot follow GINI's themes, and a whole category of failure that cost a day
— an app-wide event filter meeting Chromium's internals (a segfault), a navigation loop keeping
the busy cursor up, and a start-up flicker. The Zoo and Desktop screens keep QtWebEngine because
booting an OS is a deliberate, occasional act where nobody minds the cost. A terminal is on every
click, so it is drawn natively.

WHAT IT DOES AND DOES NOT DO. Cursor keys, control characters, colour, bold and scrollback, which
is the bar these labs need: reading `tcpdump` output, driving the gRouter CLI, editing with a
line editor. Full-screen curses applications like vim are NOT a goal — that was always a bonus of
the xterm.js route rather than a requirement, and buying it back cost more than it was worth.
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget

from .theme.manager import sp as _sp   # point size scaled by Settings > Text size

# pyte names the 8 ANSI colours; everything else arrives as a hex string or "default".
_ANSI = {
    "black": "#3b4048", "red": "#e06c75", "green": "#98c379", "brown": "#d19a66",
    "yellow": "#d19a66", "blue": "#61afef", "magenta": "#c678dd", "cyan": "#56b6c2",
    "white": "#dcdfe4",
}
_BRIGHT = {
    "black": "#5c6370", "red": "#ff7b86", "green": "#b5e890", "brown": "#e5c07b",
    "yellow": "#e5c07b", "blue": "#7cc7ff", "magenta": "#e2a5f0", "cyan": "#69d9db",
    "white": "#ffffff",
}

def _in_selection(sel, doc: int, col: int) -> bool:
    """Is this cell inside the (start, end) selection? Free function: it is pure index arithmetic
    and gets called for every cell of every repaint."""
    (r0, c0), (r1, c1) = sel
    if doc < r0 or doc > r1:
        return False
    if r0 == r1:
        return c0 <= col < c1
    if doc == r0:
        return col >= c0
    if doc == r1:
        return col < c1
    return True


# What a double-click treats as one word, beyond letters and digits. Tuned for what is actually
# worth copying out of these panes: addresses and masks (10.0.1.10, /24), interface names (tun0,
# eth0-1), paths (/run/r1.ctl). Deliberately NOT ':' or '=' — ping prints "from 10.0.1.10:" and
# "icmp_seq=1", and dragging the punctuation along means editing it out of every paste.
_WORD_EXTRA = "._-/+@~"

# The terminal tracks the rest of the UI rather than having a size of its own: change Settings >
# Text size and this changes with it. UI_BASE_PT is what ThemeManager sets the application font
# to at scale 1.0, so sp(UI_BASE_PT) is "whatever the UI is using right now".
#
# The 0.9 is deliberate. A monospace face at the same nominal point size reads noticeably larger
# and wider than the UI's proportional font, so matching the number exactly makes the terminal
# look oversized next to the Inspector beside it — and costs columns, which a terminal feels more
# than any other pane.
UI_BASE_PT = 10.0
FONT_RATIO = 0.9
MIN_PT = 7.0

MIN_COLS, MIN_ROWS = 20, 4
#: How long to let the geometry settle before resizing the emulator and the PTY. A layout pass
#: hands this widget several sizes in a row — a dock being shown, a tab switch, a splitter drag —
#: and one of them can be a collapsed one. Acting on each would resize the PTY repeatedly and
#: shunt the live screen into the scrollback for a size that existed for one frame.
REFIT_MS = 120
DEFAULT_COLS, DEFAULT_ROWS = 80, 24
SCROLLBACK = 5000


def _emulator(columns: int, lines: int):
    """pyte, plus the two scroll sequences it does not implement.

    `CSI Ps S` (SU, scroll up) and `CSI Ps T` (SD, scroll down) are absent from pyte's CSI
    dispatch table outright — not mis-handled, not stubbed, simply not there — so pyte ignores
    them and the screen does not move.

    That is fatal here, because **tmux scrolls with SU**. Every Terminal session runs through
    `tmux new -A` (see `orchestrator._persist`), and a captured session of a few pings and
    traceroutes contained `\x1b[2S` **130 times**. Each one asked for a two-line scroll that
    never happened; tmux then drew the next lines at the bottom assuming it had, so the new
    text landed on top of output that was still there. Lines a student had already read
    disappeared, and nothing reached the scrollback either, because a scroll that does not
    happen appends nothing to history.

    Routing SU through `index()` rather than moving the buffer directly is the whole point:
    `HistoryScreen.index()` is what pushes the departing line into the scrollback, so the
    output scrolls AND stays reachable. SD mirrors it through `reverse_index()`.
    """
    import pyte                          # local, as it was: pyte is only needed here

    class _Screen(pyte.HistoryScreen):
        def _region(self):
            m = self.margins
            return (m.top, m.bottom) if m else (0, self.lines - 1)

        def scroll_up(self, count=None) -> None:
            # Driven from the BOTTOM margin, because `index()` only scrolls when the cursor is
            # already there and otherwise just steps the cursor down a row. SU must move the
            # region wherever the cursor happens to be — and it must leave the cursor where it
            # found it, which is the part of the spec that makes `index()` alone wrong.
            top, bottom = self._region()
            was = self.cursor.y
            self.cursor.y = bottom
            for _ in range(max(1, int(count or 1))):
                self.index()
            self.cursor.y = was

        def scroll_down(self, count=None) -> None:
            top, bottom = self._region()
            was = self.cursor.y
            self.cursor.y = top
            for _ in range(max(1, int(count or 1))):
                self.reverse_index()
            self.cursor.y = was

    class _Stream(pyte.ByteStream):
        csi = {**pyte.ByteStream.csi, "S": "scroll_up", "T": "scroll_down"}

    screen = _Screen(columns, lines, history=SCROLLBACK, ratio=0.2)
    return screen, _Stream(screen)


class TerminalView(QWidget):
    """Renders a pyte screen and emits the bytes a key press should send."""

    key_bytes = Signal(bytes)          # user typed something; hand it to the transport
    size_changed = Signal(int, int)    # (columns, rows) — the PTY needs to be told

    def __init__(self, theme=None, parent=None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.setFocusPolicy(Qt.StrongFocus)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setCursor(Qt.IBeamCursor)
        # Own background: without it the widget inherits the dock's palette and the cell
        # backgrounds we paint do not line up with the gaps between them.
        self.setAutoFillBackground(True)

        self._font = QFont("Menlo")
        self._font.setStyleHint(QFont.Monospace)     # falls back to DejaVu Sans Mono / Consolas
        self._font.setFixedPitch(True)
        self._override_pt = None                     # set only by an explicit set_font_size()
        self._apply_font()

        # BYTE stream: it holds partial UTF-8 across frames. See `_emulator` for the two CSI
        # sequences pyte is missing and why tmux makes them mandatory.
        self._screen, self._stream = _emulator(DEFAULT_COLS, DEFAULT_ROWS)
        self._scroll = 0                               # lines scrolled back from the live screen
        # Wheel remainders. A trackpad sends a stream of SMALL deltas, so a fixed step per
        # event ignores the magnitude entirely and scrolls in lurches; these carry the
        # leftover between events so a slow drag moves a line at a time.
        self._wheel_px = 0.0
        self._wheel_deg = 0
        self._sel_anchor = None                        # (doc_row, col) where the drag started
        self._sel_head = None                          # (doc_row, col) where it is now
        self._selecting = False
        self.setMouseTracking(False)                   # only track while a button is held
        self._refit_timer = QTimer(self)               # see resizeEvent
        self._refit_timer.setSingleShot(True)
        self._refit_timer.timeout.connect(self._refit)

    # -- geometry ----------------------------------------------------------- #
    def _metrics(self) -> None:
        fm = QFontMetricsF(self._font)
        # horizontalAdvance of a wide-ish glyph, not averageCharWidth: the latter rounds down on
        # some fonts and the grid then drifts a fraction of a pixel per column across the row.
        self._cw = max(1.0, fm.horizontalAdvance("W"))
        self._ch = max(1.0, fm.height())
        self._ascent = fm.ascent()

    def cols_rows(self) -> tuple[int, int]:
        return (max(MIN_COLS, int(self.width() / self._cw)),
                max(MIN_ROWS, int(self.height() / self._ch)))

    def sizeHint(self) -> QSize:                      # noqa: N802 - Qt naming
        return QSize(int(self._cw * DEFAULT_COLS), int(self._ch * DEFAULT_ROWS))

    def resizeEvent(self, e) -> None:                 # noqa: N802 - Qt naming
        super().resizeEvent(e)
        # DEBOUNCED. See REFIT_MS: a transient size during layout is not a user resizing their
        # terminal, and acting on it moves everything on screen into the scrollback.
        self._refit_timer.start(REFIT_MS)

    def _row_text(self, line) -> str:
        return "".join(line[x].data for x in range(self._screen.columns))

    def _scroll_off(self, rows: int) -> None:
        """Move the lines a SHRINK is about to destroy into the scrollback.

        pyte implements a shrink as `delete_lines()` at the top of the screen — an EDITING
        operation, not a scroll — and `HistoryScreen` only appends to history from `index()`. So
        every line a resize removes is destroyed outright and reachable nowhere. Shrinking a
        24-row screen to 12 loses twelve lines for good; a transient collapse to MIN_ROWS empties
        the terminal. That is what made a traceroute in this pane look like a broken network: the
        hops were printed, and the pane deleted them.

        DENSIFY FIRST, and this is the subtle half. `delete_lines` only moves a row when the
        SOURCE row exists in pyte's sparse buffer:

            if y + count <= bottom:
                if y + count in self.buffer:
                    self.buffer[y] = self.buffer.pop(y + count)

        On a screen never written to the bottom those source rows are absent, so the destination
        is left ALONE — an old line survives in place instead of scrolling away, and turns up
        later out of order among newer output. Touching every row makes the buffer dense so the
        moves actually happen, which is what makes the shrink deterministic rather than a
        function of how far down the screen had been written.
        """
        screen = self._screen
        if rows >= screen.lines:
            return                                    # growing destroys nothing
        for y in range(screen.lines):
            screen.buffer[y]                          # defaultdict: materialise the sparse rows
        doomed = [screen.buffer[y] for y in range(screen.lines - rows)]
        while doomed and not self._row_text(doomed[-1]).strip():
            doomed.pop()                              # unwritten screen, not output
        for line in doomed:
            screen.history.top.append(line)

    def _scroll_in(self, rows: int) -> None:
        """Pull lines back out of the scrollback when the screen GROWS — the other half.

        `_scroll_off` stopped a shrink DESTROYING lines by moving them to the scrollback first.
        That left the second half of the same bug: nothing ever brought them back. A dock that
        briefly collapses and returns — a tab switch, a splitter nudge, a text-size change —
        therefore empties the live screen for good. Measured before this existed: six hops of a
        traceroute on screen, collapse to MIN_ROWS and back to 24, and the pane is blank with all
        six in the scrollback. Nothing was lost, and a student reading the pane has no way to
        tell that from output that never arrived.

        Every real terminal reflows this way: shrink pushes the top into history, grow pulls it
        back. pyte's `resize` only ever adds blank rows at the bottom, so this is the half it
        does not implement.
        """
        screen = self._screen
        top = screen.history.top
        take = min(int(rows), len(top))
        if take <= 0:
            return
        # Move what is on screen DOWN by `take`, high row first so a row is read before it is
        # overwritten. A row absent from pyte's sparse buffer must clear its destination rather
        # than leave the old contents there — the same trap `_scroll_off` densifies around.
        for y in range(screen.lines - 1, take - 1, -1):
            src = y - take
            if src in screen.buffer:
                screen.buffer[y] = screen.buffer[src]
            else:
                screen.buffer.pop(y, None)
        # Newest history line lands at the BOTTOM of the restored block, so order is preserved.
        for i in range(take):
            screen.buffer[take - 1 - i] = top.pop()
        screen.cursor.y = min(screen.lines - 1, screen.cursor.y + take)
        screen.dirty.update(range(screen.lines))
        if self._scroll > 0:
            # The document just got shorter by `take`; hold the reader on the same text.
            self._scroll = max(0, self._scroll - take)

    def _refit(self) -> None:
        """Recompute the grid and tell the PTY if it changed.

        Separate from resizeEvent so a font change can reuse it: calling resizeEvent(None) would
        pass None to QWidget.resizeEvent and raise.
        """
        cols, rows = self.cols_rows()
        if (cols, rows) != (self._screen.columns, self._screen.lines):
            was = self._screen.lines
            self._scroll_off(rows)                    # a shrink must scroll, never truncate
            self._screen.resize(rows, cols)           # pyte takes (lines, columns)
            if rows > was:
                self._scroll_in(rows - was)           # ...and a grow must scroll BACK
            self.size_changed.emit(cols, rows)
        self.update()

    def _apply_font(self) -> None:
        """Size the terminal from the UI text-size setting (or an explicit override)."""
        pt = self._override_pt if self._override_pt else max(MIN_PT, _sp(UI_BASE_PT) * FONT_RATIO)
        self._font.setPointSizeF(float(pt))
        self._metrics()

    def refresh_theme(self, *_a) -> None:
        """Theme or text size changed. ThemeManager emits themeChanged for BOTH, so this is where
        a Settings > Text size change reaches the terminal."""
        self._apply_font()
        self._refit()                                # the grid changes with the glyph size
        self.update()

    def set_font_size(self, pt: int) -> None:
        """Pin an explicit size, overriding the UI setting until cleared."""
        self._override_pt = max(MIN_PT, float(pt))
        self._apply_font()
        self._refit()

    def clear_font_override(self) -> None:
        self._override_pt = None
        self._apply_font()
        self._refit()

    # -- input from the container ------------------------------------------- #
    def feed(self, data: bytes) -> None:
        """Terminal output. Bytes, not str — a UTF-8 sequence can be split across frames and
        pyte's ByteStream is what carries that partial state."""
        if not data:
            return
        before = len(self._screen.history.top)
        self._stream.feed(data)
        if self._scroll > 0:
            # READING BACK. Two things have to NOT happen here, and both of them did.
            #
            # 1. The view must not snap to the live screen. It used to: every chunk reset
            #    `_scroll` to 0, so a once-a-second ping gave a student one second of
            #    scrollback before yanking them back to the bottom. That is the terminal
            #    fighting the person using it.
            #
            # 2. The view must not DRIFT either, which is subtler and survives fixing (1).
            #    `_doc_top` is `len(history.top) - _scroll`, so every line that scrolls off
            #    the live screen into history moves the viewport one line further down the
            #    document. Holding `_scroll` still therefore does NOT hold the CONTENT still —
            #    the page slides upward out from under the reader at exactly the rate output
            #    arrives. Growing `_scroll` by what history grew pins the text instead.
            grew = len(self._screen.history.top) - before
            if grew > 0:
                self._scroll = min(len(self._screen.history.top), self._scroll + grew)
        self.update()

    def reset(self) -> None:
        self._screen.reset()
        self._scroll = 0
        self.update()

    # -- painting ------------------------------------------------------------ #
    def _palette(self) -> tuple[str, str]:
        t = getattr(self.theme, "theme", None)
        bg = getattr(t, "panel2", None) or getattr(t, "bg", None) or "#1e222a"
        fg = getattr(t, "text", None) or "#dcdfe4"
        return bg, fg

    def _sel_colour(self) -> str:
        """Highlight colour. From the theme so selection reads as part of GINI rather than as the
        operating system's idea of blue."""
        t = getattr(self.theme, "theme", None)
        return getattr(t, "line", None) or getattr(t, "muted", None) or "#3a4150"

    def _colour(self, name: str, default: str, bold: bool = False) -> QColor:
        if not name or name == "default":
            return QColor(default)
        table = _BRIGHT if bold else _ANSI
        if name in table:
            return QColor(table[name])
        if len(name) == 6:                            # pyte hands 24-bit colour back as raw hex
            return QColor("#" + name)
        return QColor(default)

    def _visible_lines(self):
        """The rows to draw: the live screen, or a window into the scrollback."""
        if self._scroll <= 0:
            return [self._screen.buffer[y] for y in range(self._screen.lines)]
        top = list(self._screen.history.top)
        take = min(self._scroll, len(top))
        rows = [top[len(top) - take + i] for i in range(take)]
        rows += [self._screen.buffer[y] for y in range(self._screen.lines - take)]
        return rows

    def paintEvent(self, _e) -> None:                 # noqa: N802 - Qt naming
        bg, fg = self._palette()
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(bg))
        p.setFont(self._font)
        rows = self._visible_lines()
        sel = self._ordered_selection()
        doc0 = self._doc_top()
        sel_bg = QColor(self._sel_colour())
        for y, line in enumerate(rows):
            top = y * self._ch
            doc = doc0 + y
            for x in range(self._screen.columns):
                ch = line[x]
                if sel is not None and _in_selection(sel, doc, x):
                    p.fillRect(int(x * self._cw), int(top), int(self._cw) + 1, int(self._ch) + 1,
                               sel_bg)
                elif ch.bg != "default":
                    p.fillRect(int(x * self._cw), int(top), int(self._cw) + 1, int(self._ch) + 1,
                               self._colour(ch.bg, bg))
                if ch.data and ch.data != " ":
                    f = self._font
                    if f.bold() != bool(ch.bold):
                        f.setBold(bool(ch.bold)); p.setFont(f)
                    p.setPen(self._colour(ch.fg, fg, bold=bool(ch.bold)))
                    p.drawText(int(x * self._cw), int(top + self._ascent), ch.data)
        self._paint_cursor(p, fg)
        p.end()

    def _paint_cursor(self, p: QPainter, fg: str) -> None:
        """Only on the live screen: a cursor drawn over scrollback points at nothing."""
        if self._scroll > 0 or getattr(self._screen.cursor, "hidden", False):
            return
        c = self._screen.cursor
        p.fillRect(int(c.x * self._cw), int(c.y * self._ch),
                   max(2, int(self._cw)) if self.hasFocus() else 2, int(self._ch),
                   QColor(fg))

    # -- scrollback ---------------------------------------------------------- #
    def _set_scroll(self, lines: int) -> None:
        """Clamp to what there is: 0 is the live screen, `len(history.top)` is the oldest line."""
        self._scroll = max(0, min(len(self._screen.history.top), int(lines)))
        self.update()

    def to_bottom(self) -> None:
        """Back to the live screen — what Shift+End and any keypress do."""
        self._set_scroll(0)

    def scrolled_back(self) -> int:
        """How far back the view is. 0 when following the output."""
        return self._scroll

    def wheelEvent(self, e) -> None:                  # noqa: N802 - Qt naming
        """One wheel notch is three lines; a trackpad moves by pixels.

        The old version took a fixed ±3 lines per EVENT and ignored the delta. A mouse sends one
        event per notch so that looked right, but a trackpad sends a stream of small ones — so a
        gentle two-finger drag jumped three lines per event and the scrollback felt unusable.
        Both kinds are accumulated and the remainder carried, so a slow drag moves one line.
        """
        px = e.pixelDelta().y()
        lines = 0
        if px and self._ch:
            self._wheel_px += px
            lines = int(self._wheel_px // self._ch)
            self._wheel_px -= lines * self._ch
        else:
            self._wheel_deg += e.angleDelta().y()
            lines = int(self._wheel_deg // 40)        # 120 units = one notch = three lines
            self._wheel_deg -= lines * 40
        if lines:
            self._set_scroll(self._scroll + lines)
        e.accept()

    # -- selection ----------------------------------------------------------- #
    # Positions are DOCUMENT rows, not screen rows: index 0 is the oldest line still in
    # scrollback. A selection anchored to the screen would slide up the moment new output
    # arrived, so highlighting a captured packet and then reading on would leave the highlight
    # pointing at something else.
    def _doc_top(self) -> int:
        """Document index of the top visible row."""
        return len(self._screen.history.top) - self._scroll

    def _line_at(self, doc: int):
        """One document row — from scrollback if it has scrolled off, else from the screen."""
        top = self._screen.history.top
        if doc < len(top):
            return top[doc]
        y = doc - len(top)
        return self._screen.buffer[y] if 0 <= y < self._screen.lines else None

    def _cell_at(self, pos) -> tuple[int, int]:
        col = max(0, min(self._screen.columns - 1, int(pos.x() / self._cw)))
        row = max(0, min(self._screen.lines - 1, int(pos.y() / self._ch)))
        return self._doc_top() + row, col

    def _ordered_selection(self):
        """(start, end) with start <= end, or None. Both are (doc_row, col)."""
        if self._sel_anchor is None or self._sel_head is None:
            return None
        a, b = self._sel_anchor, self._sel_head
        return (a, b) if a <= b else (b, a)

    def has_selection(self) -> bool:
        sel = self._ordered_selection()
        return sel is not None and sel[0] != sel[1]

    def clear_selection(self) -> None:
        self._sel_anchor = self._sel_head = None
        self.update()

    def selected_text(self) -> str:
        """The selection as text, trailing blanks trimmed per line.

        Trimming matters: a terminal row is padded to the full width, so without it every copied
        line drags 40 spaces along and pasting into a document looks wrong.
        """
        sel = self._ordered_selection()
        if sel is None:
            return ""
        (r0, c0), (r1, c1) = sel
        out = []
        for doc in range(r0, r1 + 1):
            line = self._line_at(doc)
            if line is None:
                continue
            start = c0 if doc == r0 else 0
            end = c1 if doc == r1 else self._screen.columns
            out.append("".join(line[x].data for x in range(start, min(end, self._screen.columns)))
                       .rstrip())
        return "\n".join(out)

    def select_all(self) -> None:
        last = len(self._screen.history.top) + self._screen.lines - 1
        self._sel_anchor, self._sel_head = (0, 0), (last, self._screen.columns)
        self.update()

    def copy(self) -> bool:
        """Copy the selection. False when there is nothing selected, so a caller can fall through
        (Ctrl-C with no selection must still interrupt)."""
        text = self.selected_text()
        if not text:
            return False
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(text)
        return True

    def paste(self) -> None:
        """Send the clipboard to the PTY.

        Newlines become carriage returns: a shell expects CR for "run this", and pasting LF gives
        a line that looks entered but never executes. Multi-line pastes therefore run every line,
        which is what a student pasting a block of commands means.
        """
        from PySide6.QtWidgets import QApplication
        text = QApplication.clipboard().text()
        if not text:
            return
        self._scroll = 0
        self.key_bytes.emit(text.replace("\r\n", "\r").replace("\n", "\r").encode("utf-8"))

    # -- mouse ---------------------------------------------------------------- #
    def mousePressEvent(self, e) -> None:             # noqa: N802 - Qt naming
        if e.button() == Qt.LeftButton:
            self._sel_anchor = self._sel_head = self._cell_at(e.position())
            self._selecting = True
            self.update()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:              # noqa: N802 - Qt naming
        if self._selecting:
            self._sel_head = self._cell_at(e.position())
            self.update()
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:           # noqa: N802 - Qt naming
        self._selecting = False
        if not self.has_selection():
            self.clear_selection()                    # a plain click just dismisses the highlight
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e) -> None:       # noqa: N802 - Qt naming
        """Double-click selects a word — usually an IP address or an interface name here, which
        is the thing most worth copying out of this pane."""
        doc, col = self._cell_at(e.position())
        line = self._line_at(doc)
        if line is None:
            return
        def is_word(x):
            ch = line[x].data
            return bool(ch) and (ch.isalnum() or ch in _WORD_EXTRA)
        if not is_word(col):
            return
        start = col
        while start > 0 and is_word(start - 1):
            start -= 1
        end = col
        while end < self._screen.columns - 1 and is_word(end + 1):
            end += 1
        self._sel_anchor, self._sel_head = (doc, start), (doc, end + 1)
        self.update()

    def contextMenuEvent(self, e) -> None:            # noqa: N802 - Qt naming
        """Right-click menu. The keyboard chords differ per platform and are not discoverable;
        this is."""
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        act_copy = menu.addAction("Copy")
        act_copy.setEnabled(self.has_selection())
        act_paste = menu.addAction("Paste")
        menu.addSeparator()
        act_all = menu.addAction("Select All")
        chosen = menu.exec(e.globalPos())
        if chosen is act_copy:
            self.copy()
        elif chosen is act_paste:
            self.paste()
        elif chosen is act_all:
            self.select_all()

    # -- keyboard ------------------------------------------------------------ #
    def keyPressEvent(self, e) -> None:               # noqa: N802 - Qt naming
        mods, key = e.modifiers(), e.key()
        if clipboard_chord(mods):
            # Ctrl+Shift+C (Cmd-C on macOS). With nothing selected this does nothing at all,
            # which is what every terminal does — and crucially it is NOT the interrupt: plain
            # Ctrl-C carries no Shift, so it reaches encode_key and becomes \x03.
            if key == Qt.Key_C:
                self.copy()
                return
            if key == Qt.Key_V:
                self.paste(); return
            if key == Qt.Key_A:
                self.select_all(); return
        # Shift+PageUp/PageDown/Home/End move the SCROLLBACK, as in xterm and every terminal
        # since. Unshifted they still go to the program, which is what a pager expects.
        if mods & Qt.ShiftModifier:
            page = max(1, self._screen.lines - 1)
            if key == Qt.Key_PageUp:
                self._set_scroll(self._scroll + page); return
            if key == Qt.Key_PageDown:
                self._set_scroll(self._scroll - page); return
            if key == Qt.Key_Home:
                self._set_scroll(len(self._screen.history.top)); return
            if key == Qt.Key_End:
                self.to_bottom(); return
        data = encode_key(key, mods, e.text())
        if data:
            self._scroll = 0                          # typing returns to the live screen
            self.key_bytes.emit(data)
            self.update()
        else:
            super().keyPressEvent(e)


# Key encoding is a free function so it can be tested without a window, an event loop, or a
# display — which is most of what can actually go wrong with it.
_SPECIAL = {
    Qt.Key_Return: b"\r", Qt.Key_Enter: b"\r",
    Qt.Key_Backspace: b"\x7f",                 # DEL, not BS: what stty erase expects
    Qt.Key_Tab: b"\t",
    Qt.Key_Escape: b"\x1b",
    Qt.Key_Up: b"\x1b[A", Qt.Key_Down: b"\x1b[B",
    Qt.Key_Right: b"\x1b[C", Qt.Key_Left: b"\x1b[D",
    Qt.Key_Home: b"\x1b[H", Qt.Key_End: b"\x1b[F",
    Qt.Key_PageUp: b"\x1b[5~", Qt.Key_PageDown: b"\x1b[6~",
    Qt.Key_Insert: b"\x1b[2~", Qt.Key_Delete: b"\x1b[3~",
}


_MAC = sys.platform == "darwin"


def terminal_ctrl(mods) -> bool:
    """Is the TERMINAL's Control key down?

    On macOS Qt swaps them: Qt.ControlModifier is Command and Qt.MetaModifier is Control. Reading
    ControlModifier as "Ctrl" therefore sends \\x03 when the student presses Cmd-C to COPY, and
    sends nothing at all when they press the real Ctrl-C to interrupt — so on a Mac, copy killed
    your command and interrupt did nothing.
    """
    return bool(mods & (Qt.MetaModifier if _MAC else Qt.ControlModifier))


def clipboard_chord(mods) -> bool:
    """The platform's copy/paste modifier: Cmd on macOS, Ctrl+Shift elsewhere.

    Ctrl+Shift, not Ctrl, because Ctrl-C must stay SIGINT — that is the whole reason terminals
    everywhere use the longer chord.
    """
    if _MAC:
        return bool(mods & Qt.ControlModifier)          # Command
    return bool(mods & Qt.ControlModifier and mods & Qt.ShiftModifier)


def encode_key(key: int, mods, text: str) -> bytes:
    """One key press -> the bytes a PTY expects, or b"" to let Qt handle it.

    Ctrl-C, Ctrl-D and Ctrl-Z matter more here than anywhere else: a student who cannot interrupt
    a `ping` has no way out of it, and Ctrl-D is how they leave the gRouter CLI.
    """
    if clipboard_chord(mods) and key in (Qt.Key_C, Qt.Key_V, Qt.Key_A):
        return b""                                      # the widget handles copy/paste/select-all
    if terminal_ctrl(mods):
        if Qt.Key_A <= key <= Qt.Key_Z:
            return bytes([key - Qt.Key_A + 1])        # Ctrl-A..Ctrl-Z -> 0x01..0x1a
        if key == Qt.Key_BracketLeft:
            return b"\x1b"
        if key == Qt.Key_Backslash:
            return b"\x1c"
        if key == Qt.Key_Space:
            return b"\x00"
    special = _SPECIAL.get(key)
    if special is not None:
        return special
    if mods & Qt.AltModifier and text:
        return b"\x1b" + text.encode("utf-8")         # Alt-x is ESC then x
    if text:
        return text.encode("utf-8")
    return b""
