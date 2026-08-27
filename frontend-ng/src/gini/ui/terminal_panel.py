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

CONNECTING IS LAZY. Nothing is opened until the tab is actually visible: an unopened Terminal tab
costs a widget and no socket. Switching elements re-points the one widget and the one client
rather than creating anything.

Follows the conventions in source_browser.py: `show_*` methods per mode, a `refresh_theme` that
re-derives colours on a theme switch, and an honest empty state that says WHY rather than sitting
blank.
"""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

from ..services.orchestrator import TERMINALS_FILE


def _scss(s: str) -> str:
    return s


def _hostport(url: str) -> str:
    """http://127.0.0.1:37600/ -> 127.0.0.1:37600, for the subtitle."""
    return url.split("//", 1)[-1].rstrip("/")


class TerminalPanel(QWidget):
    """Read the element -> port map, then embed that element's ttyd."""

    # How long to keep waiting for a container's ttyd to bind its port. Generous on purpose: a
    # cold `docker compose up --build` on a slow machine can take a while, and giving up early
    # looks to the student exactly like the feature being broken.
    PROBE_MS = 600
    MAX_PROBES = 40                       # ~24s

    def __init__(self, theme, workdir_fn=None, running_fn=None, parent=None) -> None:
        super().__init__(parent)
        self.theme = theme
        # () -> the running project's working directory, or "" when nothing is running. Injected
        # so this panel never imports main_window and never talks to Docker itself.
        self.workdir_fn = workdir_fn
        # () -> bool. The workdir OUTLIVES a run: gini-terminals.json is still sitting there from
        # last time, so without this the panel would embed a terminal for a container that is not
        # running — at startup, before the student has pressed anything.
        self.running_fn = running_fn or (lambda: True)
        self._name = ""
        self._pending = None              # (url, name, cmd) waiting for the tab to become visible
        self._probe = None                # QTcpSocket checking whether ttyd is listening yet
        self._tries = 0
        self._connected_to = ""           # URL the client is attached to; "" when detached

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        self._title = QLabel("Terminal")
        self._sub = QLabel("")
        self._sub.setWordWrap(True)
        root.addWidget(self._title)
        root.addWidget(self._sub)

        # ONE terminal widget and ONE client for the life of the panel. Selecting another element
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

        # Subscribe to theme changes ourselves, the SourceBrowser/Dashboard convention —
        # MainWindow._on_theme_changed deliberately pokes nobody.
        if hasattr(theme, "themeChanged"):
            theme.themeChanged.connect(self.refresh_theme)

        self._apply_theme()
        self.show_none()

    # -- theme -------------------------------------------------------------- #
    def _apply_theme(self) -> None:
        t = self.theme.theme
        self._title.setStyleSheet(_scss(f"color:{t.text};font-size:13px;font-weight:600;"))
        self._sub.setStyleSheet(_scss(f"color:{t.muted};font-size:11px;"))

    def refresh_theme(self, *_a) -> None:
        """Theme switched. Takes *_a because themeChanged carries the new theme's name.

        The terminal itself now follows too — it is painted by Qt from the theme's tokens, which
        the xterm.js page never could. ThemeManager emits this for a TEXT SIZE change as well as a
        palette change, so it is also how Settings › Text size reaches the terminal font.
        """
        self._apply_theme()
        self._view.refresh_theme()

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
            return json.loads((Path(wd) / TERMINALS_FILE).read_text())
        except (OSError, ValueError):
            return {}

    # -- public: one element's terminal ------------------------------------- #
    def show_device(self, name: str, label: str = "") -> None:
        """Point the terminal at `name`, or explain why there isn't one."""
        self._name = name or ""
        terms = self._terminals()
        entry = terms.get(name)
        if entry is None:
            if not terms:
                self.show_none(name, "Nothing is running — press Run, then select an element.")
            else:
                self.show_none(name, f"{label or name} does not serve a terminal.")
            return
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
        self._sub.setText(why or "Select a machine, router, switch or controller to open a "
                                 "terminal on it.")

    # -- internals ---------------------------------------------------------- #
    def _detach(self) -> None:
        """Close the session and clear the screen. The tmux session in the container survives."""
        self._connected_to = ""
        self._client.disconnect_from()
        self._view.reset()

    def _on_failed(self, why: str) -> None:
        self._connected_to = ""
        self._sub.setText(f"could not attach: {why}")

    def _on_closed(self) -> None:
        self._connected_to = ""

    def _embed(self, url: str, name: str, cmd: str) -> None:
        kind = "router CLI" if "grconsole" in cmd else "shell"
        self._title.setText(f"Terminal  ·  {name}")
        # The port stays on screen: it is the one thing that makes a wrong-element or
        # not-yet-listening terminal diagnosable without reading gini-terminals.json by hand.
        self._sub.setText(f"{kind}  ·  {_hostport(url)}")
        self._pending = (url, name, cmd)
        self._drop_probe()                 # a probe for the PREVIOUS element must not fire later
        if self.isVisible():               # tab is on top: connect now
            self._realise()

    def showEvent(self, e) -> None:        # noqa: N802 - Qt naming
        """The tab just became visible. Connect to whatever we deferred, if any."""
        super().showEvent(e)
        self._realise()

    def _realise(self) -> None:
        """Wait for the container's ttyd to be listening, THEN connect.

        `docker compose up` returns as soon as the containers are created; ttyd inside them binds
        its port a moment later, and later still on a cold build or a slow machine. Connecting
        into that gap fails, and a WebSocket that failed once does not retry itself.
        """
        if self._pending is None or self._probe is not None:
            return
        if self._connected_to == self._pending[0]:
            return                         # already attached to this element
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
        """Point the ONE terminal widget at the pending element.

        One widget and one client for the life of the panel — switching elements re-points them.
        There is no per-element process here to spawn or reap: that was the QWebEngineView design,
        and it cost a Chromium start-up on every click.
        """
        if self._pending is None:
            return
        url, _name, cmd = self._pending
        kind = "router CLI" if "grconsole" in cmd else "shell"
        self._sub.setText(f"{kind}  ·  {_hostport(url)}")
        self._connected_to = url
        self._view.reset()                 # the previous element's screen must not linger
        cols, rows = self._view.cols_rows()
        self._client.connect_to(url, cols, rows)
        self._view.setFocus()

    # -- selection ---------------------------------------------------------- #
    def on_selection(self, device_id, topology=None) -> None:
        """Wired to bus.selection_changed. Fills quietly — never raises the tab, so it does not
        steal the pane from the Inspector while a student is reading it."""
        if device_id is None:
            self.show_none()
            return
        dev = (topology.devices.get(device_id) if topology else None)
        if dev is None:
            self.show_none()
            return
        from ..services.compiler import _svc
        label = getattr(getattr(dev, "type", None), "label", "") or getattr(dev, "type_key", "")
        self.show_device(_svc(dev.name), label)
