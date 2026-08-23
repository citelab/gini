"""GINI Source — read-only code, beside the thing that sent you here.

The pane serves two element families, and picks its mode from what you selected.

Select a **gRouter** and it becomes a browser for ``~/.gini/scripts``, the directory GINI
mounts into every router at ``/scripts``. That is where router modules live, both the ones
GINI ships and the ones a student writes, and because the directory is shared, every router
sees the same files: one program, many routers. The point is to be able to READ a module
before loading it, rather than typing ``gpipe cp add lua /scripts/x.lua`` and hoping.

Select an **xv6** block on the kernel board and it becomes the kernel-source browser below.

Nobody reads ten thousand lines of kernel cold. But a student will read the thirty lines behind a
block they just watched go dark, so double-clicking `bcache` on the board raises this tab with
`bio.c` open and a jump list of the functions the board actually counts.

The source comes from INSIDE the container, so it is the PATCHED tree this kernel was compiled
from — the `GINI_SUB(GSUB_BCACHE)` probe is right there at the top of `bread`, and the board's
numbers stop being magic. It also reflects the student's own shadow edits, which a copy shipped
with the app never could.

READ-ONLY, deliberately. Editing kernel source belongs to the shadow files, which have Load and
Revert and a workflow around them.
"""
from __future__ import annotations

import re
import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import (
    QColor, QFont, QSyntaxHighlighter, QTextCharFormat, QTextCursor, QTextFormat,
)
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPlainTextEdit, QSplitter,
    QTextEdit, QVBoxLayout, QWidget,
)

from ..app.paths import scripts_dir
from ..domain.kernel_source import files_for, parse_source, safe_rel
from ..domain.router_scripts import line_count, list_modules, read_module


def _scss(s: str) -> str:
    return s


# Lua keywords, and the callbacks/services the gRouter's control-plane API defines. Colouring
# the API separately is the useful part for a student: it shows at a glance which names are
# the language and which are GINI's, in a file where both appear on the same line.
_LUA_KEYWORDS = (r"\b(and|break|do|else|elseif|end|false|for|function|goto|if|in|local|nil|"
                 r"not|or|repeat|return|then|true|until|while)\b")
_GINI_API = (r"\b(init|tick|on_message|process|send|emit|route_add|route_del|route_lookup|"
             r"interfaces|listen|log|publish)\b")


class LuaHighlighter(QSyntaxHighlighter):
    """Small read-only Lua highlighter for the module view.

    Deliberately minimal: keywords, the GINI API, strings, numbers, and comments (including
    the ``--[[ ]]`` block form, which needs the block-state machinery). It is a reading aid,
    not an editor feature, so the rules err toward under-colouring rather than mis-colouring.
    """

    def __init__(self, document, theme) -> None:
        super().__init__(document)
        t = theme.theme

        def fmt(color, bold=False, italic=False):
            f = QTextCharFormat()
            f.setForeground(QColor(color))
            if bold:
                f.setFontWeight(QFont.Bold)
            if italic:
                f.setFontItalic(True)
            return f

        self._f_comment = fmt(t.muted, italic=True)
        # ordered: later rules paint over earlier ones, and comments are applied last of all
        self._rules = [
            (re.compile(_LUA_KEYWORDS), fmt(t.accent_for("blue"), bold=True)),
            (re.compile(_GINI_API), fmt(t.accent, bold=True)),
            (re.compile(r"\b\d+(?:\.\d+)?\b"), fmt(t.accent_for("amber"))),
            (re.compile(r'"[^"\n]*"' + r"|'[^'\n]*'"), fmt(t.accent_for("green"))),
        ]
        self._line_comment = re.compile(r"--(?!\[\[).*")

    def highlightBlock(self, text: str) -> None:  # noqa: N802 - Qt override
        if self.previousBlockState() == 1:                 # continuing a --[[ block
            end = text.find("]]")
            if end < 0:
                self.setFormat(0, len(text), self._f_comment)
                self.setCurrentBlockState(1)
                return
            self.setFormat(0, end + 2, self._f_comment)

        for pat, f in self._rules:
            for m in pat.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), f)

        open_at = text.find("--[[")                        # a block comment starting here
        if open_at >= 0:
            end = text.find("]]", open_at + 4)
            if end < 0:
                self.setFormat(open_at, len(text) - open_at, self._f_comment)
                self.setCurrentBlockState(1)
                return
            self.setFormat(open_at, end + 2 - open_at, self._f_comment)

        m = self._line_comment.search(text)                # -- to end of line, painted last
        if m:
            self.setFormat(m.start(), len(text) - m.start(), self._f_comment)


class SourceBrowser(QWidget):
    """Read-only kernel source with a jump list. Fetches off the GUI thread."""

    loaded = Signal(str, str)              # (rel path, text) — delivered on the GUI thread

    def __init__(self, theme, fetch_fn=None, parent=None) -> None:
        super().__init__(parent)
        self.theme = theme
        # () -> AgentClient|None, injected. The browser never imports main_window and never talks
        # to Docker; it asks whoever owns the machine for a reader.
        self.fetch_fn = fetch_fn
        self._block = ""
        self._sf = None
        self._mode = "kernel"          # "kernel" (xv6 source) | "scripts" (router Lua) | "none"
        self._lua_hl = None            # LuaHighlighter, attached only in "scripts" mode

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        head = QHBoxLayout()
        self._title = QLabel("GINI Source")
        self._files = QComboBox()
        self._files.currentTextChanged.connect(self._on_file_pick)
        head.addWidget(self._title)
        head.addStretch(1)
        head.addWidget(self._files)
        root.addLayout(head)

        self._sub = QLabel("Select a router to read its Lua modules, or double-click a block "
                           "on the kernel board to open its source.")
        self._sub.setWordWrap(True)
        root.addWidget(self._sub)

        split = QSplitter(Qt.Vertical)
        self._jump = QListWidget()
        self._jump.itemActivated.connect(self._on_jump)
        self._jump.currentItemChanged.connect(lambda cur, _p: self._on_jump(cur))
        self._jump.setMaximumHeight(150)

        self._view = QPlainTextEdit()
        self._view.setReadOnly(True)                 # see the module docstring
        self._view.setLineWrapMode(QPlainTextEdit.NoWrap)
        mono = QFont("Menlo")
        mono.setStyleHint(QFont.Monospace)
        mono.setPointSize(10)
        self._view.setFont(mono)

        split.addWidget(self._jump)
        split.addWidget(self._view)
        split.setStretchFactor(1, 1)
        root.addWidget(split, 1)

        # Restyle on every theme switch. Connecting to the ThemeManager directly (the same way
        # the dashboard does) keeps this self-contained: a widget that styles itself from theme
        # tokens is responsible for noticing when they change, rather than relying on the main
        # window to remember it exists.
        if hasattr(theme, "themeChanged"):
            theme.themeChanged.connect(self.refresh_theme)
        self._apply_theme()                          # colours live in ONE place, see below
        self.loaded.connect(self._on_loaded)

    # -- theming ------------------------------------------------------------ #
    def _apply_theme(self) -> None:
        """Paint every child from the CURRENT theme. Called at build time and again on each
        theme switch, so the pane never keeps the palette it was born with."""
        t = self.theme.theme
        self._title.setStyleSheet(_scss(f"color:{t.text};font-size:13px;font-weight:600;"))
        self._files.setStyleSheet(_scss(f"color:{t.text};font-size:11px;"))
        self._sub.setStyleSheet(_scss(f"color:{t.muted};font-size:11px;"))
        self._jump.setStyleSheet(_scss(
            f"QListWidget{{background:{t.panel2};color:{t.text};border:1px solid {t.line};"
            f"border-radius:8px;font-size:11px;}}"))
        self._view.setStyleSheet(_scss(
            f"QPlainTextEdit{{background:{t.panel2};color:{t.text};border:1px solid {t.line};"
            f"border-radius:8px;}}"))

    def refresh_theme(self, *_a) -> None:
        """Theme switched. Restyle, and rebuild the highlighter, whose QTextCharFormats hold
        colours of their own and would otherwise keep painting the old palette's ink.
        Takes *_a because themeChanged carries the new theme's name."""
        self._apply_theme()
        if self._lua_hl is not None:
            self._lua_hl.setDocument(None)
            self._lua_hl = None
        if self._mode == "scripts":
            self._set_lua_highlight(True)

    # -- Lua highlighting: on for .lua, off for the C kernel tree ------------ #
    def _set_lua_highlight(self, on: bool) -> None:
        try:
            if on:
                if self._lua_hl is None:
                    self._lua_hl = LuaHighlighter(self._view.document(), self.theme)
                else:
                    self._lua_hl.setDocument(self._view.document())
            elif self._lua_hl is not None:
                self._lua_hl.setDocument(None)      # C source must not be read as Lua
        except Exception:                           # noqa: BLE001 - colour is never worth a crash
            pass

    # -- public: router Lua modules ----------------------------------------- #
    def show_scripts(self, router: str = "") -> None:
        """Browse ~/.gini/scripts, the module directory every gRouter shares.

        Read straight off the host filesystem, so it works whether or not the topology is
        running: a student can read a module before deciding to load it.
        """
        self._mode = "scripts"
        self._block = ""
        self._set_lua_highlight(True)
        self._files.blockSignals(True)
        self._files.clear()
        self._files.setVisible(False)          # in this mode the top pane IS the file list
        self._files.blockSignals(False)
        self._title.setText(f"GINI Source  ·  {router}" if router else "GINI Source")

        d = scripts_dir()
        mods = list_modules(d)

        self._jump.blockSignals(True)
        self._jump.clear()
        for m in mods:
            it = QListWidgetItem(m.label)
            it.setData(Qt.UserRole, str(m.path))
            self._jump.addItem(it)
        self._jump.blockSignals(False)

        if not mods:
            self._sub.setText(
                f"No .lua modules in {d}. GINI seeds its reference modules there the first "
                f"time you run a topology; your own modules go in the same place.")
            self._view.setPlainText("")
            return
        self._sub.setText(
            f"{len(mods)} module(s) in {d} — every router sees these at /scripts. "
            f"Read-only here; load one from a router console with "
            f"gpipe cp add lua /scripts/<name>.lua")
        self._jump.setCurrentRow(0)
        self._open_script(str(mods[0].path))

    def _open_script(self, path: str) -> None:
        from pathlib import Path
        text, err = read_module(path)
        if err:
            self._sub.setText(err)
            self._view.setPlainText("")
            return
        name = Path(path).name
        self._view.setPlainText(text)
        self._view.moveCursor(QTextCursor.Start)
        self._sub.setText(f"{name}  ·  {line_count(text)} lines  ·  loads as /scripts/{name}")

    def show_none(self, what: str = "") -> None:
        """Selected something with no source of its own. Clear, and say why: a stale pane
        still showing the last router's module is worse than an empty one, because it looks
        like it belongs to what you just clicked."""
        self._mode = "none"
        self._block = ""
        self._set_lua_highlight(False)
        self._files.setVisible(False)
        self._jump.clear()
        self._view.setPlainText("")
        self._title.setText(f"GINI Source  ·  {what}" if what else "GINI Source")
        self._sub.setText(
            f"No source is exposed for {what or 'this element'}. Select a router to read the "
            f"Lua modules in ~/.gini/scripts, or double-click a block on the kernel board to "
            f"read xv6 source.")

    # -- public: xv6 kernel source ------------------------------------------ #
    def show_block(self, block: str, files=None) -> None:
        """Open the source behind a board block. Called from the HUD's open_source signal."""
        self._mode = "kernel"
        self._set_lua_highlight(False)
        self._files.setVisible(True)
        self._block = block or ""
        paths = [p for p in (list(files or []) or files_for(block)) if safe_rel(p)]
        self._title.setText(f"GINI Source  ·  {block}" if block else "GINI Source")
        self._files.blockSignals(True)
        self._files.clear()
        self._files.addItems(paths)
        self._files.blockSignals(False)
        if paths:
            self.open_path(paths[0])
        else:
            self._sub.setText(f"No source is mapped for “{block}”.")

    def open_path(self, rel: str) -> None:
        rel = safe_rel(rel)
        if not rel:
            self._sub.setText("That path is not inside the kernel tree.")
            return
        self._sub.setText(f"loading {rel} …")
        agent = self.fetch_fn() if self.fetch_fn else None
        if agent is None:
            self._sub.setText("No running xv6 machine — start one to read its kernel source.")
            self._view.setPlainText("")
            self._jump.clear()
            return

        def work():
            # Blocking HTTP: off the GUI thread, like every other reader in the app.
            try:
                text = agent.get_text(f"/source?file={rel}")
            except Exception as e:                    # noqa: BLE001 - never take the app down
                text = f"// unreadable: {e}"
            self.loaded.emit(rel, text)

        threading.Thread(target=work, daemon=True).start()

    # -- internals ---------------------------------------------------------- #
    def _on_file_pick(self, rel: str) -> None:
        if rel:
            self.open_path(rel)

    def _on_loaded(self, rel: str, text: str) -> None:
        sf = parse_source(rel, text)
        self._sf = sf
        self._jump.clear()
        if not sf.ok:
            self._sub.setText(sf.error or "could not read that file")
            self._view.setPlainText("")
            return
        self._view.setPlainText(sf.text)
        probed = sum(1 for e in sf.entries if e.block)
        self._sub.setText(
            f"{rel}  ·  {sf.lines} lines  ·  "
            + (f"{probed} entry points the board counts" if probed
               else f"{len(sf.entries)} functions (no board probes in this file)"))
        for e in sf.entries:
            it = QListWidgetItem(e.label)
            it.setData(Qt.UserRole, e.line)
            self._jump.addItem(it)

    def _on_jump(self, item) -> None:
        if item is None:
            return
        if self._mode == "scripts":          # the list holds file paths, not line numbers
            path = item.data(Qt.UserRole)
            if path:
                self._open_script(str(path))
            return
        line = int(item.data(Qt.UserRole) or 0)
        if line <= 0:
            return
        doc = self._view.document()
        cur = QTextCursor(doc.findBlockByLineNumber(max(0, line - 1)))
        self._view.setTextCursor(cur)
        self._view.centerCursor()

        # Highlight the landing line — a jump you cannot see has not really landed.
        #
        # ExtraSelection lives on QTextEdit, NOT on QPlainTextEdit, even though it is
        # QPlainTextEdit.setExtraSelections() that consumes it. And the theme's *_soft tokens are
        # "rgba(...)" strings, which QColor does not parse, so the tint is built from the accent
        # with an explicit alpha instead of handed an unparseable string.
        #
        # Guarded because these three enum spellings appear nowhere else in this codebase and so
        # are unverified against the PySide6 build we actually ship. The SCROLL above is the part
        # that matters; if the highlight is wrong on some binding version it should cost a tint,
        # not the feature.
        try:
            tint = QColor(self.theme.theme.accent)
            tint.setAlpha(48)
            sel = QTextEdit.ExtraSelection()
            sel.format.setBackground(tint)
            sel.format.setProperty(QTextFormat.FullWidthSelection, True)
            sel.cursor = cur
            sel.cursor.clearSelection()
            self._view.setExtraSelections([sel])
        except Exception:                             # noqa: BLE001 - cosmetic only
            pass
