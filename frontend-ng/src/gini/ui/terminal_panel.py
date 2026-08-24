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
        self._view = None                 # QWebEngineView, created lazily (it is a whole process)
        self._name = ""
        self._pending = None              # (url, name, cmd) waiting for the tab to become visible
        self._probe = None                # QTcpSocket checking whether ttyd is listening yet
        self._tries = 0
        self._warmed = False              # QtWebEngine start-up cost paid? see warm_up()

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
        self._drop_probe()                 # nothing to wait for any more
        # HIDE the view, do not destroy it. Clicking empty canvas clears the selection, and
        # tearing down a Chromium render process every time the student deselects — then building
        # a new one the moment they select again — is exactly the churn that made the Router Lab
        # crawl. The page keeps its socket to a terminal that is still there.
        self._park_view()
        self._title.setText(f"Terminal  ·  {what}" if what else "Terminal")
        self._sub.setText(why or "Select a machine, router, switch or controller to open a "
                                 "terminal on it.")

    # -- internals ---------------------------------------------------------- #
    def _park_view(self) -> None:
        """Hide the view without destroying it — see show_none()."""
        if self._view is not None:
            self._view.hide()

    def _drop_view(self) -> None:
        """Destroy the render process. Only for teardown; NOT for switching elements."""
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
        # The view is REUSED, not rebuilt. A QWebEngineView is a Chromium render process, and
        # spawning one is expensive enough to be felt: double-clicking a router changes the
        # selection AND opens the Router Lab, so on a slow machine the process spawn landed on top
        # of the Lab's `docker compose exec` calls and the two starved each other — the Lab sat
        # spinning while a terminal nobody had asked for was being built.
        #
        # Rebuilding was originally there so one element's live shell could not linger under
        # another element's name. tmux made that moot: the session lives in the container, so
        # navigating this view to the new URL both shows the right element AND leaves the previous
        # element's work running, ready to re-attach.
        self._drop_probe()                 # a probe for the PREVIOUS element must not fire later
        if self.isVisible():               # tab is on top: build it now
            self._realise()

    def showEvent(self, e) -> None:        # noqa: N802 - Qt naming
        """The tab just became visible. Build the view we deferred, if any."""
        super().showEvent(e)
        self._realise()

    def warm_up(self) -> None:
        """Pay QtWebEngine's one-time start-up cost NOW, while nothing is waiting on it.

        Constructing the first QWebEngineView initialises Chromium, and that happens on the GUI
        THREAD. On a slow machine with software GL it stalls the whole app for seconds. It landed
        on the worst possible moment: double-clicking a router both opens the Router Lab and
        changes the selection, so the stall arrived while the Lab was trying to draw — spinning
        cursor, and an ordinary click misread as a long-press because the queued long-press timer
        was delivered ahead of the queued mouse release.

        Called once from MainWindow after the window is up. Subsequent views are cheap, so from
        then on switching elements is a navigation and costs nothing noticeable.

        Best-effort by design: if QtWebEngine is not installed there is nothing to warm and the
        panel's browser fallback handles it.
        """
        if self._warmed or self._view is not None:
            return
        self._warmed = True
        try:
            from PySide6.QtCore import QUrl
            from PySide6.QtWebEngineWidgets import QWebEngineView
        except Exception:                          # noqa: BLE001 - optional dependency
            return
        self._view = QWebEngineView(self._holder)
        self._holder_lay.addWidget(self._view)
        self._view.setUrl(QUrl("about:blank"))
        self._view.hide()                          # parked until an element is selected

    def _showing(self) -> str:
        """URL the live view is already on, or ""."""
        if self._view is None:
            return ""
        try:
            return self._view.url().toString()
        except Exception:                      # noqa: BLE001 - a torn-down view has no url
            return ""

    def _realise(self) -> None:
        """Wait for the container's ttyd to be listening, THEN point the view at it.

        `docker compose up` returns as soon as the containers are created; ttyd inside them binds
        its port a moment later, and later still on a cold build or a slow machine. Loading the
        page into that gap gives the student a broken terminal that never recovers:

            js: [ttyd] fetch http://127.0.0.1:37601/token: TypeError: Failed to fetch

        The page has already loaded at that point, so there is nothing for QWebEngineView's
        loadFinished to report — the failure is a fetch INSIDE the page. Hence a port probe up
        front rather than a reload-on-error.
        """
        if self._pending is None or self._probe is not None:
            return
        if self._showing() == self._pending[0]:
            self._view.show()                  # may have been parked by a deselect
            return                             # already on this element: no navigation needed
        url, _name, cmd = self._pending
        kind = "router CLI" if "grconsole" in cmd else "shell"
        try:
            from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401 - availability check
        except Exception as e:                    # noqa: BLE001 - QtWebEngine is optional
            self._sub.setText(
                f"{kind}  ·  {url}\n"
                f"Open that in a browser — the embedded view needs PySide6-Addons "
                f"(QtWebEngine): {e}")
            return
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

    def _drop_probe(self):
        if self._probe is not None:
            self._probe.abort()
            self._probe.deleteLater()
            self._probe = None

    def _probe_ok(self) -> None:
        self._drop_probe()
        self._build_view()

    def _probe_failed(self, *_a) -> None:
        from PySide6.QtCore import QTimer
        self._drop_probe()
        self._tries += 1
        if self._pending is None:
            return                                # student moved on; abandon quietly
        if self._tries > self.MAX_PROBES:
            url = self._pending[0]
            self._sub.setText(f"{url}\nNo terminal answered there. Is the element still starting? "
                              f"Re-select it to try again.")
            return
        if self._tries == 2:                      # only say it once it is actually slow
            self._sub.setText(f"{self._sub.text().splitlines()[0]}\nwaiting for the container…")
        QTimer.singleShot(self.PROBE_MS, self._probe_port)

    def _build_view(self) -> None:
        """Point the view at the pending element, creating it only the first time.

        ONE render process for the whole panel. Spawning a QWebEngineView is not cheap — it is a
        Chromium process — and doing it per selection was heavy enough to be felt: double-clicking
        a router changes the selection AND opens the Router Lab, so the spawn landed on top of the
        Lab's docker exec calls and left it spinning on a slow machine.

        Navigating instead of rebuilding is safe now that sessions live in tmux inside the
        container: the page is replaced, so no element's shell appears under another's name, and
        the element we navigate AWAY from keeps running, ready to re-attach.
        """
        if self._pending is None:
            return
        from PySide6.QtCore import QUrl
        from PySide6.QtWebEngineWidgets import QWebEngineView
        url, _name, cmd = self._pending
        kind = "router CLI" if "grconsole" in cmd else "shell"
        self._sub.setText(f"{kind}  ·  {url}")    # clear any "waiting…" line
        if self._view is not None:                # reuse: just navigate
            self._view.setUrl(QUrl(url))
            self._view.show()                     # may have been parked by a deselect
            return
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
