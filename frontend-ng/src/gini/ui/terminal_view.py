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

from PySide6.QtCore import QSize, Qt, Signal
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
DEFAULT_COLS, DEFAULT_ROWS = 80, 24
SCROLLBACK = 5000


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

        import pyte
        self._screen = pyte.HistoryScreen(DEFAULT_COLS, DEFAULT_ROWS, history=SCROLLBACK,
                                          ratio=0.2)
        self._stream = pyte.ByteStream(self._screen)   # BYTE stream: it holds partial UTF-8
        self._scroll = 0                               # lines scrolled back from the live screen
        self._sel_anchor = None                        # (doc_row, col) where the drag started
        self._sel_head = None                          # (doc_row, col) where it is now
        self._selecting = False
        self.setMouseTracking(False)                   # only track while a button is held

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
        self._refit()

    def _refit(self) -> None:
        """Recompute the grid and tell the PTY if it changed.

        Separate from resizeEvent so a font change can reuse it: calling resizeEvent(None) would
        pass None to QWidget.resizeEvent and raise.
        """
        cols, rows = self.cols_rows()
        if (cols, rows) != (self._screen.columns, self._screen.lines):
            self._screen.resize(rows, cols)           # pyte takes (lines, columns)
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
        self._stream.feed(data)
        self._scroll = 0                              # new output jumps back to the live screen
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
    def wheelEvent(self, e) -> None:                  # noqa: N802 - Qt naming
        step = 3 if e.angleDelta().y() > 0 else -3
        self._scroll = max(0, min(len(self._screen.history.top), self._scroll + step))
        self.update()

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
