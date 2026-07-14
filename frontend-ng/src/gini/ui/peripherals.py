"""xv6 Terminal — the console as a single software peripheral you wire on the canvas.

xv6 has no networking, so instead of switches/routers you attach peripherals to the Machine.
The **Terminal** is the console: one shell view (a screen and keyboard in one, like a real tty),
because xv6's console is a single bidirectional UART. Double-clicking a Terminal on the canvas
opens this view, bound to its linked xv6 Machine's live provider.

It talks to the same provider the Machine Lab uses. Output STREAMS in append-only via
`console_since(cursor)` (so kernel output interleaves with your echoed input exactly like a real
terminal); input goes out via `send_input()`. A few conveniences are handled terminal-side — the
kind of thing a real terminal + readline do for you, not the shell: up/down-arrow command history,
and the built-ins `help`, `clear`, `history`, and `ps` (xv6 has no `ps` program — it's Ctrl-P,
which we surface under a friendly name). Everything else is passed straight to xv6's real `sh`.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QVBoxLayout,
)

from .theme import ThemeManager

# terminal-side built-ins (handled here, not sent to xv6 sh) and the common real xv6 programs the
# `help` text advertises — so students know what they can actually run.
_BUILTINS = ("help", "clear", "history", "ps")
_XV6_PROGRAMS = ("ls", "cat", "echo", "grep", "wc", "mkdir", "rm", "ln", "kill",
                 "spin [secs]", "busy [secs]", "alloc", "writer", "forktest", "grind", "usertests")


class TerminalView(QDialog):
    """A bash-like console for an xv6 Machine: streaming output + a prompt line with history."""

    delta_ready = Signal(str, int)           # (new console text, new cursor)

    def __init__(self, parent, theme: ThemeManager, provider, device=None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.provider = provider
        self._cursor = 0                      # console byte offset we've displayed up to
        self._fetching = False               # one console read in flight — never overlap them
        self._history: list[str] = []
        self._hist_idx = 0                    # points one past the last entry (i.e. "new line")
        t = theme.theme
        self.setWindowTitle(f"Terminal — {getattr(device, 'name', 'xv6')}")
        self.resize(760, 480)
        self.setStyleSheet(f"QDialog{{background:{t.bg};}}")
        v = QVBoxLayout(self)
        v.setSpacing(6)
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setStyleSheet(
            f"QPlainTextEdit{{background:{t.panel};color:{t.text};border:1px solid {t.line};"
            "border-radius:6px;font-family:monospace;font-size:12px;}")
        v.addWidget(self.view, 1)
        # prompt row: a "$" marker + the input line, styled as one terminal strip
        row = QHBoxLayout()
        row.setSpacing(6)
        prompt = QLabel("$")
        prompt.setStyleSheet(f"color:{t.accent};font-family:monospace;font-size:13px;")
        row.addWidget(prompt)
        self.input = QLineEdit()
        self.input.setPlaceholderText("type a command — e.g.  spin 10 &   ·   ls   ·   help")
        self.input.setStyleSheet(
            f"QLineEdit{{background:{t.panel};color:{t.text};border:1px solid {t.line};"
            "border-radius:6px;padding:8px;font-family:monospace;font-size:13px;}")
        self.input.returnPressed.connect(self._submit)
        self.input.installEventFilter(self)          # up/down = history
        row.addWidget(self.input, 1)
        # xv6 has no Ctrl-C / SIGINT, so a foreground program (e.g. `spin` with no &) blocks the
        # shell. Break kills the most-recent user process via the kernel's console handler, so the
        # prompt comes back even though sh isn't reading input.
        brk = QPushButton("Break  ⌃C")
        brk.setToolTip("Interrupt a hung foreground command (xv6 has no Ctrl-C — this kills the "
                       "most-recently started program so the shell prompt returns)")
        # CRITICAL: a dialog push button is auto-default, so Enter in the input line would ALSO
        # click it — every submitted command would fire a Break and kill a process. Opt out so
        # Enter only submits.
        brk.setAutoDefault(False)
        brk.setDefault(False)
        brk.setStyleSheet(
            f"QPushButton{{color:{t.text};background:{t.panel2};border:1px solid {t.line};"
            f"border-radius:6px;padding:6px 12px;font-family:monospace;}}"
            f"QPushButton:hover{{border-color:{t.accent};}}")
        brk.clicked.connect(self._break)
        self._break_btn = brk
        row.addWidget(brk)
        v.addLayout(row)
        hint = QLabel("Programs take arguments (spin 10 & runs 10s in the background); ↑ recalls "
                      "history; type help to see what you can run.")
        hint.setStyleSheet(f"color:{t.faint};font-size:11px;")
        v.addWidget(hint)
        self._streaming = hasattr(provider, "console_since")
        self.delta_ready.connect(self._append_stream)
        self._poll = QTimer(self)
        self._poll.timeout.connect(self._refresh)
        self._poll.start(350)
        self.input.setFocus()
        self._append("Connected. Type help to get started.\n")
        self._refresh()

    # -- output --------------------------------------------------------------- #
    def _bg(self, fn):
        import threading
        threading.Thread(target=fn, daemon=True).start()

    def _refresh(self):
        # Never overlap reads: a slow agent + a fixed timer would stack background reads that all
        # fetch from the SAME cursor, duplicating output (and echoed commands). One in flight only.
        if self._fetching:
            return
        self._fetching = True
        cur = self._cursor
        def work():
            try:
                if self._streaming:
                    text, nxt = self.provider.console_since(cur)
                else:                                # provider without streaming -> full tail
                    text, nxt = "\x00" + (self.provider.console() or ""), cur
            except Exception:
                text, nxt = "", cur                  # a failed read: skip, keep what we have
            self.delta_ready.emit(text, nxt)
        self._bg(work)

    def _append_stream(self, text: str, nxt: int):
        self._fetching = False                       # read done — the next tick may fetch again
        self._cursor = nxt                           # advance the cursor on the GUI thread (safe)
        if text.startswith("\x00"):                  # non-streaming fallback: replace the pane
            body = text[1:]
            if body and body != self.view.toPlainText():
                self._set_text(body)
            return
        if text:
            self._append(text)

    def _append(self, text: str):
        sb = self.view.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 4
        cur = self.view.textCursor()
        cur.movePosition(cur.MoveOperation.End)
        cur.insertText(text)
        if at_bottom:
            sb.setValue(sb.maximum())

    def _set_text(self, text: str):
        sb = self.view.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 4
        self.view.setPlainText(text)
        if at_bottom:
            sb.setValue(sb.maximum())

    # -- input ---------------------------------------------------------------- #
    def _submit(self):
        cmd = self.input.text().strip()
        self.input.clear()
        if cmd:
            self._history.append(cmd)
        self._hist_idx = len(self._history)
        if cmd in _BUILTINS:
            self._builtin(cmd)                       # handled terminal-side, echoed locally
            return
        # a real xv6 command: xv6's console echoes it back, so it appears in the stream itself
        self._bg(lambda: self.provider.send_input(cmd + "\n"))

    def _builtin(self, cmd: str):
        self._append(f"$ {cmd}\n")                   # local echo (xv6 never sees these)
        if cmd == "help":
            self._append(self._help_text())
        elif cmd == "clear":
            if hasattr(self.provider, "clear_console"):
                self._bg(self.provider.clear_console)
            self.view.clear()
        elif cmd == "history":
            for i, h in enumerate(self._history, 1):
                self._append(f"{i:>4}  {h}\n")
        elif cmd == "ps":
            # xv6 has no `ps` program — Ctrl-P triggers the kernel's native procdump, which prints
            # to the console and streams back in like any other output.
            self._append("(xv6 process table — Ctrl-P)\n")
            self._bg(lambda: self.provider.send_input("\x10"))

    def _break(self):
        # send the kernel's break control-char (Ctrl-C). The console driver handles it directly —
        # NOT sh — so it works even while a foreground program has the shell blocked in wait().
        self._append("^C\n")
        if hasattr(self.provider, "interrupt"):
            self._bg(self.provider.interrupt)
        else:                                        # older provider: fall back to raw Ctrl-C
            self._bg(lambda: self.provider.send_input("\x03"))

    def _help_text(self) -> str:
        return (
            "terminal built-ins: " + ", ".join(_BUILTINS) + "\n"
            "xv6 programs:       " + ", ".join(_XV6_PROGRAMS) + "\n"
            "tips: add & to run in the background (spin 10 &); ↑/↓ recall history; "
            "programs take arguments.\n")

    def eventFilter(self, obj, ev):  # noqa: N802
        if obj is self.input and ev.type() == ev.Type.KeyPress:
            if ev.key() == Qt.Key_Up:
                self._recall(-1)
                return True
            if ev.key() == Qt.Key_Down:
                self._recall(+1)
                return True
        return super().eventFilter(obj, ev)

    def _recall(self, step: int):
        if not self._history:
            return
        self._hist_idx = max(0, min(len(self._history), self._hist_idx + step))
        self.input.setText(self._history[self._hist_idx] if self._hist_idx < len(self._history)
                           else "")

    def closeEvent(self, e):  # noqa: N802
        self._poll.stop()
        super().closeEvent(e)
