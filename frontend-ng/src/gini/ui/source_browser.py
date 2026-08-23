"""GINI Source — the kernel's own code, read-only, beside the board that sent you here.

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

import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QTextCursor, QTextFormat
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPlainTextEdit, QSplitter,
    QTextEdit, QVBoxLayout, QWidget,
)

from ..domain.kernel_source import files_for, parse_source, safe_rel


def _scss(s: str) -> str:
    return s


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

        t = theme.theme
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        head = QHBoxLayout()
        self._title = QLabel("GINI Source")
        self._title.setStyleSheet(_scss(f"color:{t.text};font-size:13px;font-weight:600;"))
        self._files = QComboBox()
        self._files.setStyleSheet(_scss(f"color:{t.text};font-size:11px;"))
        self._files.currentTextChanged.connect(self._on_file_pick)
        head.addWidget(self._title)
        head.addStretch(1)
        head.addWidget(self._files)
        root.addLayout(head)

        self._sub = QLabel("Double-click a block on the kernel board to open its source.")
        self._sub.setWordWrap(True)
        self._sub.setStyleSheet(_scss(f"color:{t.muted};font-size:11px;"))
        root.addWidget(self._sub)

        split = QSplitter(Qt.Vertical)
        self._jump = QListWidget()
        self._jump.setStyleSheet(_scss(
            f"QListWidget{{background:{t.panel2};color:{t.text};border:1px solid {t.line};"
            f"border-radius:8px;font-size:11px;}}"))
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
        self._view.setStyleSheet(_scss(
            f"QPlainTextEdit{{background:{t.panel2};color:{t.text};border:1px solid {t.line};"
            f"border-radius:8px;}}"))

        split.addWidget(self._jump)
        split.addWidget(self._view)
        split.setStretchFactor(1, 1)
        root.addWidget(split, 1)

        self.loaded.connect(self._on_loaded)

    # -- public ------------------------------------------------------------ #
    def show_block(self, block: str, files=None) -> None:
        """Open the source behind a board block. Called from the HUD's open_source signal."""
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
