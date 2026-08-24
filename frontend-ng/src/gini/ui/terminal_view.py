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

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget

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
        self._font.setPointSize(11)
        self._metrics()

        import pyte
        self._screen = pyte.HistoryScreen(DEFAULT_COLS, DEFAULT_ROWS, history=SCROLLBACK,
                                          ratio=0.2)
        self._stream = pyte.ByteStream(self._screen)   # BYTE stream: it holds partial UTF-8
        self._scroll = 0                               # lines scrolled back from the live screen

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

    def set_font_size(self, pt: int) -> None:
        self._font.setPointSize(max(6, int(pt)))
        self._metrics()
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
        for y, line in enumerate(rows):
            top = y * self._ch
            for x in range(self._screen.columns):
                ch = line[x]
                if ch.bg != "default":
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

    # -- keyboard ------------------------------------------------------------ #
    def keyPressEvent(self, e) -> None:               # noqa: N802 - Qt naming
        data = encode_key(e.key(), e.modifiers(), e.text())
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


def encode_key(key: int, mods, text: str) -> bytes:
    """One key press -> the bytes a PTY expects, or b"" to let Qt handle it.

    Ctrl-C, Ctrl-D and Ctrl-Z matter more here than anywhere else: a student who cannot interrupt
    a `ping` has no way out of it, and Ctrl-D is how they leave the gRouter CLI.
    """
    if mods & Qt.ControlModifier:
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
