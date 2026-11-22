"""Terminal — a real shell (or router CLI) in the right-hand pane, following the selection.

Each running element serves its own terminal from inside its container: ttyd, on a loopback-only
host port. The PTY therefore lives in Linux no matter what the student is running gBuilder on, so
macOS, Linux and Windows behave identically with no host-side pseudo-terminal code.

The SCREEN is drawn by Qt — see terminal_view.py, which puts pyte behind a painted cell grid. It
used to be ttyd's own xterm.js page in a QWebEngineView, which meant a Chromium render process
per terminal: ~150MB, a start-up cost heavy enough to stall a slow machine, a page that could not
follow GINI's themes, and a run of failures that cost a day (an app-wide event filter meeting
Chromium's internals, a navigation loop pinning the busy cursor, a start-up flicker). The Zoo and
Desktop screens keep QtWebEngine, where booting an OS is a deliberate, occasional act and nobody
minds the cost. A terminal is on every click.

What the terminal FRONTS is decided per element by compose (`TTYD_CMD`), not here: a gRouter
opens straight into the gRouter CLI, a switch and a host get a shell. The panel only needs to
know which port to point at.

Two elements deliberately have no terminal here:
  * xv6 — its QEMU serial is single-owner (the agent holds it and refuses a second client), so
    its console stays in the Machine Lab where the agent can arbitrate.
  * Container / Instance / Kata Instance — they run arbitrary user-chosen images we cannot bake
    ttyd into, so they keep the external-terminal button.

PANES (asked for 2026-11-22). A networks lab runs `iperf3 -s` on M1 and `iperf3 -c M1` on M2 and
wants to watch both; another wants a server and a client on the SAME machine. So the tab holds up
to four panes, stacked top to bottom with draggable dividers, each one terminal: its own screen,
its own ttyd connection, its own command recording. Which pane the canvas selection points:

  * the ACTIVE pane (outlined; the one last clicked or typed in) follows the selection;
  * an empty active pane always takes it — that is how a student gets M1 twice: Split, click M1;
  * otherwise, selecting an element another pane already shows makes THAT pane active instead
    of re-pointing this one. Clicking M1 on the canvas to read its Inspector must not throw away
    the M2 terminal the student is typing in.
  * a click on empty canvas clears the terminal only when there is one pane. With several, it
    would blank a terminal somebody is watching for no reason they chose.

Two panes on ONE element must be two shells. Every terminal on an element used to attach the one
tmux session (`gini`), which is what keeps a ping running across clicks — and would also have
made a second M1 pane the same shell, mirrored. So each pane on an element takes the lowest
session slot no other pane there holds — `gini`, `gini-2`, … — and asks for it in the WebSocket
URL (see `orchestrator._persist`). Slot 1 is the old session, so the first pane on an element
opens the work left there before panes existed, and a closed pane's session keeps running in the
container exactly as the single terminal's always did.

CONNECTING IS LAZY. Nothing is opened until the tab is actually visible: an unopened Terminal tab
costs a widget and no socket. Switching elements re-points a pane's one widget and one client
rather than creating anything; only Split constructs, and only up to MAX_SESSIONS.

Follows the conventions in source_browser.py: `show_*` methods per mode, a `refresh_theme` that
re-derives colours on a theme switch, and an honest empty state that says WHY rather than sitting
blank.
"""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..services.orchestrator import MAX_SESSIONS, TERMINALS_FILE, TMUX_SESSION, tmux_session


def history_cmd(session: str = TMUX_SESSION) -> str:
    """tmux's history for one session, oldest line first, colour included -- and NOTHING when
    there is none yet. That last part is why the size is read first: with an empty history
    `capture-pane -S -N -E -1` does not return nothing, it returns the screen's first line, which
    would then appear twice. Capped at the pane's own scrollback.

    `=` makes the target an EXACT name: tmux otherwise matches a prefix, so with `gini` gone and
    `gini-2` alive, `-t gini` would read the second pane's history into the first. The trailing
    `:` is not decoration: for a PANE target tmux 3.4 reads `=gini-2` as a pane name, finds none,
    and prints nothing with exit status 0. Found against a real container, not a fake tmux.
    """
    return (f"n=$(tmux display -p -t ={session}: '#{{history_size}}' 2>/dev/null) || exit 0; "
            '[ "${n:-0}" -gt 0 ] 2>/dev/null || exit 0; [ "$n" -gt 5000 ] && n=5000; '
            f"tmux capture-pane -p -e -S -$n -E -1 -t ={session}:")


#: The first session's history command — what every terminal ran before there were panes.
HISTORY_CMD = history_cmd()


def _scss(s: str) -> str:
    return s


def _hostport(url: str) -> str:
    """http://127.0.0.1:37600/ -> 127.0.0.1:37600, for the subtitle."""
    return url.split("//", 1)[-1].rstrip("/")


class TerminalPane(QFrame):
    """One terminal: a header, the painted screen, and the ttyd client behind it."""

    # How long to keep waiting for a container's ttyd to bind its port. Generous on purpose: a
    # cold `docker compose up --build` on a slow machine can take a while, and giving up early
    # looks to the student exactly like the feature being broken.
    PROBE_MS = 600
    MAX_PROBES = 40                       # ~24s

    def __init__(self, panel: TerminalPanel, theme) -> None:
        super().__init__(panel)
        self.setObjectName("TermPane")
        self._panel = panel
        self.theme = theme
        self._tap = None
        self._name = ""
        self._slot = 1                    # which of the element's tmux sessions: 1 is `gini`
        self._pending = None              # (url, name, cmd) waiting for the tab to become visible
        self._probe = None                # QTcpSocket checking whether ttyd is listening yet
        self._tries = 0
        self._connected_to = ""           # "<url>#<slot>" the client is attached to; "" detached

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(4)
        self._title = QLabel("Terminal")
        head.addWidget(self._title, 1)
        self._split_btn = QToolButton(self)
        self._split_btn.setText("Split")
        self._split_btn.setToolTip("Open another terminal below this one — then click an element "
                                   f"on the canvas to fill it (up to {MAX_SESSIONS}).")
        self._split_btn.setFocusPolicy(Qt.NoFocus)
        self._split_btn.clicked.connect(lambda: self._panel.split(self))
        self._close_btn = QToolButton(self)
        self._close_btn.setText("✕")
        self._close_btn.setToolTip("Close this terminal. Whatever it was running keeps running "
                                   "in the element; open it again to get back to it.")
        self._close_btn.setFocusPolicy(Qt.NoFocus)
        self._close_btn.clicked.connect(lambda: self._panel.close_pane(self))
        head.addWidget(self._split_btn)
        head.addWidget(self._close_btn)
        root.addLayout(head)

        self._sub = QLabel("")
        self._sub.setWordWrap(True)
        root.addWidget(self._sub)

        # ONE terminal widget and ONE client for the life of the pane. Selecting another element
        # re-points them; nothing is constructed per element, which is the whole reason this is
        # not a web view any more.
        from ..services.ttyd_client import TtydClient
        from .terminal_view import TerminalView
        self._view = TerminalView(theme, self)
        root.addWidget(self._view, 1)
        self._client = TtydClient(self)
        self._client.output.connect(self._view.feed)
        self._client.failed.connect(self._on_failed)
        self._client.closed.connect(self._on_closed)
        self._view.key_bytes.connect(self._client.send_input)
        self._view.size_changed.connect(self._client.send_resize)
        self._view.focused.connect(lambda: self._panel.activate(self))
        # Tapped AFTER the real connections, so a fault in recording can never be the thing that
        # stops a keystroke reaching the shell.
        self._view.key_bytes.connect(self._tap_key)
        self._client.output.connect(self._tap_output)

        self._apply_theme()
        self.show_none()

    # -- theme -------------------------------------------------------------- #
    def _apply_theme(self) -> None:
        t = self.theme.theme
        self._title.setStyleSheet(_scss(f"color:{t.text};font-size:13px;font-weight:600;"))
        self._sub.setStyleSheet(_scss(f"color:{t.muted};font-size:11px;"))
        # Outlined only when there is a choice to make: a lone pane looks as it always did.
        edge = t.accent if (self._panel.is_active(self) and len(self._panel.panes) > 1) \
            else "transparent"
        self.setStyleSheet(_scss(f"QFrame#TermPane {{ border:1px solid {edge}; "
                                 "border-radius:6px; }"))

    def refresh_theme(self, *_a) -> None:
        self._apply_theme()
        self._view.refresh_theme()

    def _sync_buttons(self) -> None:
        n = len(self._panel.panes)
        self._close_btn.setVisible(n > 1)
        self._split_btn.setEnabled(n < MAX_SESSIONS)

    def mousePressEvent(self, e) -> None:  # noqa: N802 - Qt naming
        """A click on the header or the frame picks this pane, as typing into it does."""
        self._panel.activate(self)
        super().mousePressEvent(e)

    # -- public: one element's terminal ------------------------------------- #
    def show_device(self, name: str, label: str = "") -> None:
        """Point this pane at `name`, or explain why there isn't one."""
        terms = self._panel._terminals()
        entry = terms.get(name)
        if entry is None:
            if not terms:
                self.show_none(name, "Nothing is running — press Run, then select an element.")
            else:
                self.show_none(name, f"{label or name} does not serve a terminal.")
            return
        self._slot = self._panel._slot_for(self, name)
        self._name = name or ""
        self._start_tap(name)
        self._embed(f"http://127.0.0.1:{entry['port']}/", name, entry.get("cmd", ""))

    def show_none(self, what: str = "", why: str = "") -> None:
        """No terminal for this selection. Say why — a blank pane looks broken."""
        self._name = ""
        self._pending = None
        self._drop_probe()                 # nothing to wait for any more
        # The widget stays; only the session is dropped. The work itself is safe either way — it
        # is a tmux session in the container, waiting to be re-attached.
        self._detach()
        self._title.setText(f"Terminal  ·  {what}" if what else "Terminal")
        if why:
            self._sub.setText(why)
        elif len(self._panel.panes) > 1:
            self._sub.setText("Click an element on the canvas to open its terminal here.")
        else:
            self._sub.setText("Select a machine, router, switch or controller to open a "
                              "terminal on it.")

    # -- recording what was run --------------------------------------------- #
    def _tap_key(self, data) -> None:
        self._pump(lambda t: t.key(bytes(data)))

    def _tap_output(self, data) -> None:
        self._pump(lambda t: t.output(bytes(data)))

    def _pump(self, feed) -> None:
        """Feed the tap and hand off whatever it finished.

        Wrapped whole: a terminal that stopped working because the proof chain hiccuped would be a
        far worse bug than a missing entry.
        """
        record = self._panel.record_fn
        if record is None or self._tap is None:
            return
        try:
            feed(self._tap)
            for rec in self._tap.take():
                record(rec["device"], rec["cmd"], rec["out"])
        except Exception:                                        # noqa: BLE001
            pass

    def _start_tap(self, name: str) -> None:
        """A new element: close whatever the last one left open, and start fresh."""
        if self._panel.record_fn is None:
            return
        from ..services.console_tap import ConsoleTap
        self._end_tap()
        self._tap = ConsoleTap(name)

    def _end_tap(self) -> None:
        if self._tap is None:
            return
        try:
            self._tap.flush()
            for rec in self._tap.take():
                if self._panel.record_fn is not None:
                    self._panel.record_fn(rec["device"], rec["cmd"], rec["out"])
        except Exception:                                        # noqa: BLE001
            pass
        self._tap = None

    # -- internals ---------------------------------------------------------- #
    def _detach(self) -> None:
        """Close the session and clear the screen. The tmux session in the container survives."""
        self._end_tap()                    # a command still awaiting its output is closed out
        self._connected_to = ""
        self._client.disconnect_from()
        self._view.set_history_source(None)
        self._view.reset()

    def _on_failed(self, why: str) -> None:
        self._connected_to = ""
        self._sub.setText(f"could not attach: {why}")

    def _on_closed(self) -> None:
        self._connected_to = ""

    def _label(self, url: str, cmd: str) -> str:
        kind = "router CLI" if "grconsole" in cmd else "shell"
        # The port stays on screen: it is the one thing that makes a wrong-element or
        # not-yet-listening terminal diagnosable without reading gini-terminals.json by hand.
        # The session number appears only when it is not the first, i.e. when it is news.
        more = f"  ·  session {self._slot}" if self._slot > 1 else ""
        return f"{kind}{more}  ·  {_hostport(url)}"

    def _embed(self, url: str, name: str, cmd: str) -> None:
        hf = self._panel.history_fn
        hcmd = history_cmd(tmux_session(self._slot))
        self._view.set_history_source((lambda: hf(name, hcmd)) if hf else None)
        self._title.setText(f"Terminal  ·  {name}")
        self._sub.setText(self._label(url, cmd))
        self._pending = (url, name, cmd)
        self._drop_probe()                 # a probe for the PREVIOUS element must not fire later
        if self.isVisible():               # tab is on top: connect now
            self._realise()

    def showEvent(self, e) -> None:        # noqa: N802 - Qt naming
        """The tab just became visible. Connect to whatever we deferred, if any."""
        super().showEvent(e)
        self._realise()

    def _key(self) -> str:
        return f"{self._pending[0]}#{self._slot}" if self._pending else ""

    def _realise(self) -> None:
        """Wait for the container's ttyd to be listening, THEN connect.

        `docker compose up` returns as soon as the containers are created; ttyd inside them binds
        its port a moment later, and later still on a cold build or a slow machine. Connecting
        into that gap fails, and a WebSocket that failed once does not retry itself.
        """
        if self._pending is None or self._probe is not None:
            return
        if self._connected_to == self._key():
            return                         # already attached to this element's session
        self._tries = 0
        self._probe_port()

    def _probe_port(self) -> None:
        """Async connect to the ttyd port. QTcpSocket, not a blocking connect: this runs on the
        GUI thread and a container that is still starting would freeze the whole app."""
        if self._pending is None:
            return
        from PySide6.QtNetwork import QTcpSocket
        url = self._pending[0]
        port = int(url.rsplit(":", 1)[1].rstrip("/"))
        self._probe = QTcpSocket(self)
        self._probe.connected.connect(self._probe_ok)
        self._probe.errorOccurred.connect(self._probe_failed)
        self._probe.connectToHost("127.0.0.1", port)

    def _drop_probe(self) -> None:
        if self._probe is not None:
            self._probe.abort()
            self._probe.deleteLater()
            self._probe = None

    def _probe_ok(self) -> None:
        self._drop_probe()
        self._attach()

    def _probe_failed(self, *_a) -> None:
        from PySide6.QtCore import QTimer
        self._drop_probe()
        self._tries += 1
        if self._pending is None:
            return                                # student moved on; abandon quietly
        if self._tries > self.MAX_PROBES:
            self._sub.setText("No terminal answered there. Is the element still starting? "
                              "Re-select it to try again.")
            return
        if self._tries == 2:                      # only say it once it is actually slow
            self._sub.setText(f"{self._sub.text().splitlines()[0]}\nwaiting for the container…")
        QTimer.singleShot(self.PROBE_MS, self._probe_port)

    def _attach(self) -> None:
        """Point this pane's ONE terminal widget at the pending element.

        One widget and one client for the life of the pane — switching elements re-points them.
        There is no per-element process here to spawn or reap: that was the QWebEngineView design,
        and it cost a Chromium start-up on every click.
        """
        if self._pending is None:
            return
        url, _name, cmd = self._pending
        self._sub.setText(self._label(url, cmd))
        self._connected_to = self._key()
        self._view.reset()                 # the previous element's screen must not linger
        cols, rows = self._view.cols_rows()
        self._client.connect_to(url, cols, rows, self._slot)
        self._view.setFocus()

    def shut(self) -> None:
        """Detach without touching the container: its session keeps running."""
        self._drop_probe()
        self._pending = None
        self._detach()


class TerminalPanel(QWidget):
    """Read the element -> port map, and keep one to four terminal panes pointed at elements."""

    def __init__(self, theme, workdir_fn=None, running_fn=None, record_fn=None,
                 history_fn=None, parent=None) -> None:
        super().__init__(parent)
        self.theme = theme
        # (device, shell command) -> its output, run inside that element's container. Injected
        # like the others: it is how the terminal reads tmux's history (history_cmd), which holds
        # the lines tmux never passed to the screen. None: scrollback is what passed through.
        self.history_fn = history_fn
        # (device, cmd, [output lines]) -> None. Injected, like workdir_fn: this panel does not
        # know what a proof chain is, and must not start importing one to find out.
        self.record_fn = record_fn
        # () -> the running project's working directory, or "" when nothing is running. Injected
        # so this panel never imports main_window and never talks to Docker itself.
        self.workdir_fn = workdir_fn
        # () -> bool. The workdir OUTLIVES a run: gini-terminals.json is still sitting there from
        # last time, so without this the panel would embed a terminal for a container that is not
        # running — at startup, before the student has pressed anything.
        self.running_fn = running_fn or (lambda: True)
        self.panes: list[TerminalPane] = []
        self._active: TerminalPane | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(2, 2, 2, 2)
        root.setSpacing(0)
        # Top to bottom, so every pane keeps the dock's full width: a terminal narrower than a
        # line of `iperf3` output wraps every row it prints.
        self._split = QSplitter(Qt.Vertical, self)
        self._split.setChildrenCollapsible(False)
        root.addWidget(self._split, 1)

        # Subscribe to theme changes ourselves, the SourceBrowser/Dashboard convention —
        # MainWindow._on_theme_changed deliberately pokes nobody.
        if hasattr(theme, "themeChanged"):
            theme.themeChanged.connect(self.refresh_theme)

        self._add_pane()

    # -- panes -------------------------------------------------------------- #
    @property
    def active(self) -> TerminalPane:
        return self._active

    def is_active(self, pane) -> bool:
        return pane is self._active

    def _add_pane(self, after: TerminalPane | None = None) -> TerminalPane:
        pane = TerminalPane(self, self.theme)
        at = self.panes.index(after) + 1 if after in self.panes else len(self.panes)
        self.panes.insert(at, pane)
        self._split.insertWidget(at, pane)
        self._active = pane
        self._restyle()
        return pane

    def split(self, after: TerminalPane | None = None) -> TerminalPane | None:
        """A new, empty pane below `after` (the active pane by default); it becomes the active
        one, so the next element clicked on the canvas opens there. None at MAX_SESSIONS."""
        if len(self.panes) >= MAX_SESSIONS:
            return None
        pane = self._add_pane(after or self._active)
        pane.show_none()
        n = len(self.panes)
        self._split.setSizes([max(1, self._split.height() // n)] * n)
        pane._view.setFocus()
        return pane

    def close_pane(self, pane: TerminalPane) -> None:
        """Close one pane. Never the last: an empty tab is what a single pane's empty state is
        for. The element's session is left running, as switching away always has."""
        if pane not in self.panes or len(self.panes) == 1:
            return
        i = self.panes.index(pane)
        self.panes.remove(pane)
        pane.shut()
        pane.hide()
        pane.setParent(None)
        pane.deleteLater()
        if self._active is pane:
            self._active = self.panes[min(i, len(self.panes) - 1)]
        self._restyle()

    def activate(self, pane: TerminalPane) -> None:
        if pane in self.panes and pane is not self._active:
            self._active = pane
            self._restyle()

    def _restyle(self) -> None:
        for p in self.panes:
            p._apply_theme()
            p._sync_buttons()

    def _slot_for(self, pane: TerminalPane, name: str) -> int:
        """The session `pane` should open on `name`: the lowest no OTHER pane there holds.

        A pane re-shown the element it already shows keeps its own, so its screen does not swap
        sessions under it because another pane closed meanwhile.
        """
        taken = {p._slot for p in self.panes if p is not pane and p._name == name}
        if pane._name == name and pane._slot not in taken:
            return pane._slot
        return next(n for n in range(1, MAX_SESSIONS + 1) if n not in taken)

    # -- theme -------------------------------------------------------------- #
    def refresh_theme(self, *_a) -> None:
        """Theme switched. Takes *_a because themeChanged carries the new theme's name.

        The terminal itself now follows too — it is painted by Qt from the theme's tokens, which
        the xterm.js page never could. ThemeManager emits this for a TEXT SIZE change as well as a
        palette change, so it is also how Settings › Text size reaches the terminal font.
        """
        for p in self.panes:
            p.refresh_theme()

    # -- the port map ------------------------------------------------------- #
    def _terminals(self) -> dict:
        """element name -> {"port", "cmd"}, written beside the compose file at Run.

        Empty when nothing is running, which is a perfectly ordinary state, not an error.
        """
        if not self.running_fn():
            return {}                      # a leftover map from a previous run is not a terminal
        wd = self.workdir_fn() if self.workdir_fn else ""
        if not wd:
            return {}
        try:
            return json.loads((Path(wd) / TERMINALS_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    # -- public: the selection ---------------------------------------------- #
    def show_device(self, name: str, label: str = "") -> None:
        """Point a pane at `name` — which one is the rule in the module docstring."""
        pane = self._active
        if len(self.panes) > 1 and pane._name:
            shown = next((p for p in self.panes if p._name == name), None)
            if shown is not None:
                self.activate(shown)
                shown._view.setFocus()
                return
        pane.show_device(name, label)

    def show_none(self, what: str = "", why: str = "") -> None:
        self._active.show_none(what, why)

    def on_selection(self, device_id, topology=None) -> None:
        """Wired to bus.selection_changed. Fills quietly — never raises the tab, so it does not
        steal the pane from the Inspector while a student is reading it."""
        dev = (topology.devices.get(device_id) if topology and device_id is not None else None)
        if dev is None:
            if len(self.panes) == 1:
                self.show_none()
            return
        from ..services.compiler import _svc
        label = getattr(getattr(dev, "type", None), "label", "") or getattr(dev, "type_key", "")
        self.show_device(_svc(dev.name), label)
