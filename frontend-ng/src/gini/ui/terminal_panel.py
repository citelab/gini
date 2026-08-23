"""Terminal — a real shell (or router CLI) in the right-hand pane, following the selection.

Each running element serves its own terminal from inside its container: ttyd, on a loopback-only
host port, rendering with xterm.js. That choice is what makes this file short — cursor keys,
history, Ctrl-C, colours and even `vim` all work because a real PTY and a real terminal emulator
are doing the work. There is no escape-sequence interpreter here to write, or to keep correct.

What the terminal FRONTS is decided per element by compose (`TTYD_CMD`), not here: a gRouter
opens straight into the gRouter CLI, a switch and a host get a shell. The panel only needs to
know which port to point at.

Two elements deliberately have no terminal here:
  * xv6 — its QEMU serial is single-owner (the agent holds it and refuses a second client), so
    its console stays in the Machine Lab where the agent can arbitrate.
  * Container / Instance / Kata Instance — they run arbitrary user-chosen images we cannot bake
    ttyd into, so they keep the external-terminal button.

WHY THE VIEW IS BUILT LAZILY. A QWebEngineView is a Chromium render process, not a widget. The
first version created one on every selection change, so clicking through ten elements with the
Inspector tab showing spawned ten render processes for a pane nobody was looking at. Now the
panel records what it should show and builds the view when the tab actually becomes visible
(`showEvent`), so an unopened Terminal tab costs nothing.

This panel is also where a segfault was wrongly blamed. Hovering ANY QWebEngineView crashed
gBuilder, because MainWindow had installed an application-wide event filter and PySide could not
wrap the QtQuick objects inside the view. The Terminal was simply the first thing to put a web
view under the mouse often enough to notice; the OS Zoo and Desktop screens had the same latent
bug. Fixed in ui/app.py — do not reintroduce an app-wide event filter.

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


class TerminalPanel(QWidget):
    """Read the element -> port map, then embed that element's ttyd."""

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
        self._view = None                 # QWebEngineView, created lazily (it is a whole process)
        self._name = ""
        self._pending = None              # (url, name, cmd) waiting for the tab to become visible

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        self._title = QLabel("Terminal")
        self._sub = QLabel("")
        self._sub.setWordWrap(True)
        root.addWidget(self._title)
        root.addWidget(self._sub)

        self._holder = QWidget()
        self._holder.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._holder_lay = QVBoxLayout(self._holder)
        self._holder_lay.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._holder, 1)

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

        The embedded terminal is a web page rendering with its own colours, so it does NOT
        follow — same as the OS Zoo screen. Only the chrome around it restyles.
        """
        self._apply_theme()

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
        self._drop_view()
        self._title.setText(f"Terminal  ·  {what}" if what else "Terminal")
        self._sub.setText(why or "Select a machine, router, switch or controller to open a "
                                 "terminal on it.")

    # -- internals ---------------------------------------------------------- #
    def _drop_view(self) -> None:
        if self._view is not None:
            self._view.setParent(None)
            self._view.deleteLater()
            self._view = None

    def _embed(self, url: str, name: str, cmd: str) -> None:
        # WHAT it is stays in the subtitle on both paths. The fallback used to replace the whole
        # line, so a student without QtWebEngine lost the one thing that distinguishes a router's
        # CLI from a switch's shell — the two share an image and differ only by TTYD_CMD.
        kind = "router CLI" if "grconsole" in cmd else "shell"
        self._title.setText(f"Terminal  ·  {name}")
        self._sub.setText(f"{kind}  ·  {url}")
        self._pending = (url, name, cmd)
        self._drop_view()                  # the previous element's shell must not linger
        if self.isVisible():               # tab is on top: build it now
            self._realise()

    def showEvent(self, e) -> None:        # noqa: N802 - Qt naming
        """The tab just became visible. Build the view we deferred, if any."""
        super().showEvent(e)
        self._realise()

    def _realise(self) -> None:
        """Actually construct the QWebEngineView for whatever is pending."""
        if self._pending is None or self._view is not None:
            return
        url, _name, cmd = self._pending
        kind = "router CLI" if "grconsole" in cmd else "shell"
        try:
            from PySide6.QtWebEngineWidgets import QWebEngineView
        except Exception as e:                    # noqa: BLE001 - QtWebEngine is optional
            self._sub.setText(
                f"{kind}  ·  {url}\n"
                f"Open that in a browser — the embedded view needs PySide6-Addons "
                f"(QtWebEngine): {e}")
            return
        from PySide6.QtCore import QUrl
        # Rebuilt per element rather than reused: a web view keeps its page's state, and carrying
        # one element's live shell over to another element's tab would be worse than a reload.
        self._view = QWebEngineView(self._holder)
        self._holder_lay.addWidget(self._view)
        self._view.setUrl(QUrl(url))
        self._view.show()

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
