"""The gBuilder main window — shell that assembles every panel.

Layout: device palette (left dock) · canvas (center) · inspector + assistant
(right docks, tabbed) · console (bottom dock) · toolbar + status bar. All visuals
flow from the ThemeManager so Dark / Light / GINI Brand swap instantly.
"""
from __future__ import annotations

import math

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import (
    QDockWidget, QFrame, QHBoxLayout, QLabel, QMainWindow, QPlainTextEdit, QToolBar,
    QToolButton, QWidget,
)

from ..agent.api import GiniAPI
from ..app import AppContext
from .assistant import Assistant
from .canvas import NODE_H, NODE_W, CanvasView
from .inspector import Inspector
from .palette import Palette
from .theme import ThemeManager, icons


class MainWindow(QMainWindow):
    def __init__(self, app) -> None:
        super().__init__()
        self.ctx = AppContext()
        self.api = GiniAPI(self.ctx)
        self._load_config()          # ~/.gini/config.json defaults
        self.ctx.topology.prefix_overrides = dict(self.ctx.settings.name_prefixes)
        self._apply_env_settings()   # env vars override saved config
        from ..agent.tools import build_registry
        self.registry = build_registry(self.api)   # shared: in-app loop + present tools
        # gLoader: compiles the drawn topology into a runtime plan and launches it
        from pathlib import Path
        from .. import runtime as _rt
        from ..services import GLoader
        self._gloader = GLoader(Path(_rt.__file__).parent)
        self._running = False
        self._stopping = False
        self._workdir: str | None = None
        self._project_path: str | None = None
        self._router_programs: dict = {}        # device id -> RouterProgram (Router Lab)
        self.ctx.bus.run_state.connect(self._on_run_state)
        self.ctx.bus.runtime_status.connect(self._on_runtime_status)
        self.ctx.bus.device_activated.connect(self._on_device_activated)
        from PySide6.QtCore import QTimer
        self._poll = QTimer(self)
        self._poll.setInterval(3000)
        self._poll.timeout.connect(self._poll_status)
        self.theme = ThemeManager(app, self.ctx.settings.theme)
        self.theme.apply()

        self.setWindowTitle("gBuilder 6.0 — networks + cloud")
        self.resize(1280, 820)

        self.canvas = CanvasView(self.ctx, self.theme.theme)
        self.setCentralWidget(self.canvas)
        self.canvas.scene_.selectionChanged.connect(self._on_scene_selection)

        self._make_toolbar()
        self._make_delete_shortcut()
        self._make_menubar()
        self._make_docks()
        self._make_statusbar()
        self._wire_llm()

        self.theme.themeChanged.connect(self._on_theme_changed)
        self.ctx.bus.topology_changed.connect(self._update_status)
        self.ctx.bus.topology_changed.connect(self._recompute_addressing)
        self.ctx.bus.topology_changed.connect(self._revalidate)
        self.ctx.bus.topology_changed.connect(self._rebill)
        self.ctx.bus.device_resized.connect(self._on_device_resized)
        self.ctx.bus.log.connect(self._on_log)
        self.ctx.bus.device_delete_requested.connect(self._delete_device)
        self.ctx.bus.warning_explain_requested.connect(self._on_warning_explain)
        self.ctx.bus.device_logs_requested.connect(self._open_logs)
        self.ctx.bus.device_console_requested.connect(self._open_console)
        self.ctx.bus.selection_changed.connect(self._on_selection_explain)
        self.palette.element_selected.connect(self._on_palette_explain)
        self.assistant.status_changed.connect(self.mode_indicator.set_status)
        self.mode_indicator.set_status("Q&A mode", False)   # initial
        # Ask GINI messages live in the right-hand pane only; the Console is for
        # build/run logs, so we deliberately do NOT mirror chat into it.
        self._update_status()

    def _load_config(self) -> None:
        """Load persisted defaults from ~/.gini/config.json into Settings (applied as the
        ThemeManager and LLM read Settings during construction)."""
        from ..app.paths import PERSISTED_KEYS, load_config
        cfg = load_config()
        s = self.ctx.settings
        for k in PERSISTED_KEYS:
            if k in cfg:
                setattr(s, k, cfg[k])

    def _open_settings(self) -> None:
        from ..app.paths import PERSISTED_KEYS, save_config
        from .settings_dialog import SettingsDialog
        dlg = SettingsDialog(self, self.ctx.settings)
        if not dlg.exec():
            return
        v = dlg.values()
        s = self.ctx.settings
        for k in ("reduced_motion", "auto_internet",
                  "llm_enabled", "llm_url", "llm_model", "llm_think"):
            setattr(s, k, v[k])
        s.theme = v["theme"]
        s.name_prefixes = v["name_prefixes"]
        s.prices = v["prices"]
        self.ctx.topology.prefix_overrides = dict(s.name_prefixes)   # apply to current topo
        self.theme.set_theme(v["theme"])               # live theme switch
        self._wire_llm()                               # re-create / clear the LLM loop
        self._rebill()                                 # prices may have changed
        save_config({k: getattr(s, k) for k in PERSISTED_KEYS})
        self.ctx.log("Settings saved to ~/.gini/config.json.", "ok")

    def _apply_env_settings(self) -> None:
        import os
        s = self.ctx.settings
        if os.environ.get("GINI_LLM_URL"):
            s.llm_url = os.environ["GINI_LLM_URL"]
            s.llm_enabled = True
        if os.environ.get("GINI_LLM_MODEL"):
            s.llm_model = os.environ["GINI_LLM_MODEL"]
        if os.environ.get("GINI_LLM_THINK"):
            s.llm_think = os.environ["GINI_LLM_THINK"] not in ("0", "false", "")
        if os.environ.get("GINI_REDUCED_MOTION"):
            s.reduced_motion = True

    def _ai_context(self) -> str:
        """Live canvas snapshot fed to the assistant each turn (topology + run-state)."""
        digest = self.api.context_digest()
        state = "running on Docker" if self._running else "not running (idle, editable)"
        return f"{digest}\nRuntime: the topology is {state}."

    def _wire_llm(self) -> None:
        s = self.ctx.settings
        if not s.llm_enabled:
            self.assistant.set_loop(None)              # clear any existing loop
            self.ctx.log("GINI AI: offline mode (deterministic). Enable a local LLM in "
                         "Settings (or set GINI_LLM_URL).", "info")
            return
        try:
            from ..agent.llm import OllamaBackend
            from ..agent.loop import AgentLoop
            backend = OllamaBackend(s.llm_url, s.llm_model, think=s.llm_think)
            self.assistant.set_loop(AgentLoop(backend, self.registry,
                                              context_provider=self._ai_context))
            # actually check the server is reachable so the user gets real feedback
            if backend.available():
                self.ctx.log(f"GINI AI: connected to {s.llm_model} at {s.llm_url}.", "ok")
            else:
                self.ctx.log(f"GINI AI: set to {s.llm_model} at {s.llm_url}, but the server "
                             f"isn't responding. Is Ollama running? (run: ollama serve)",
                             "error")
        except Exception as e:  # never let LLM wiring break startup
            self.ctx.log(f"GINI AI: LLM unavailable ({e}); offline mode.", "info")

    # -- toolbar ------------------------------------------------------------ #
    def _make_toolbar(self) -> None:
        tb = QToolBar("Main")
        tb.setMovable(False)
        tb.setFloatable(False)
        self.addToolBar(tb)
        self._tb = tb
        self._actions: dict[str, QAction] = {}
        self._tb_buttons: dict[str, QToolButton] = {}

        def act(key: str, icon: str, text: str, slot, checkable=False) -> QAction:
            a = QAction(text, self)
            a.setCheckable(checkable)
            a.triggered.connect(slot)
            self._actions[key] = (a, icon)
            return a

        # build the actions (same wiring as before — checkable, enable/disable, slots)
        act("new", "new", "New", self._new)
        act("open", "open", "Open", self._open)
        act("save", "save", "Save", self._save)
        act("compile", "compile", "Compile", self._compile)
        act("layout", "layout", "Arrange", self._auto_layout)
        self._connect_act = act("connect", "link", "Connect", self._toggle_connect, checkable=True)
        self._edges_act = act("edges", "elbow",
                              "Connector style: bent ↔ straight",
                              self._toggle_edge_style, checkable=True)
        self._edges_act.setChecked(self.ctx.settings.connector_style == "orthogonal")
        self._manual_addr_act = act("manualaddr", "pencil",
                                    "Manual addressing — assign IP addresses by hand",
                                    self._toggle_manual_addr, checkable=True)
        self._manual_addr_act.setChecked(self.ctx.topology.manual_addressing)
        self._delete_act = act("delete", "trash", "Delete selected device", self._delete_selected)
        self._delete_act.setEnabled(False)
        self._run_act = act("run", "play", "Run", self._run)
        self._stop_act = act("stop", "stop", "Stop", self._stop)
        act("zoom_in", "plus", "Zoom in", lambda: self.canvas.zoom_by(1.15))
        act("zoom_out", "minus", "Zoom out", lambda: self.canvas.zoom_by(1 / 1.15))

        def button(key: str, *, labelled=False, oname="") -> QToolButton:
            a, _ = self._actions[key]
            b = QToolButton(tb)
            b.setDefaultAction(a)                # mirrors icon/checkable/enabled/triggered
            b.setAutoRaise(True)
            if labelled:
                b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            if oname:
                b.setObjectName(oname)
            self._tb_buttons[key] = b
            return b

        def tray(keys, *, name="TbGroup") -> QWidget:
            """A rounded segmented cluster of icon buttons."""
            f = QFrame(tb); f.setObjectName(name)
            lay = QHBoxLayout(f); lay.setContentsMargins(3, 3, 3, 3); lay.setSpacing(1)
            for k in keys:
                lay.addWidget(button(k))
            return f

        # grouped trays: File · Tools · (Run/Stop, free-standing pills) · Zoom
        tb.addWidget(tray(("new", "open", "save")))
        tb.addWidget(self._tb_spacer(6))
        tb.addWidget(tray(("compile", "layout", "connect", "edges", "manualaddr", "delete")))
        tb.addWidget(self._tb_spacer(8))
        run_grp = QWidget(tb); rg = QHBoxLayout(run_grp)
        rg.setContentsMargins(0, 0, 0, 0); rg.setSpacing(6)
        rg.addWidget(button("run", labelled=True, oname="RunBtn"))
        rg.addWidget(button("stop", labelled=True, oname="StopBtn"))
        tb.addWidget(run_grp)
        tb.addWidget(self._tb_spacer(8))
        tb.addWidget(tray(("zoom_in", "zoom_out")))

        spacer = QWidget()
        spacer.setSizePolicy(spacer.sizePolicy().horizontalPolicy().Expanding,
                             spacer.sizePolicy().verticalPolicy().Preferred)
        tb.addWidget(spacer)

        # prominent mode / activity indicator (Explain · Q&A · Thinking spinner)
        from .mode_indicator import ModeIndicator
        self.mode_indicator = ModeIndicator(self.theme)
        tb.addWidget(self.mode_indicator)
        tb.addWidget(self._tb_spacer(8))

        # theme menu
        self._theme_act = QAction("Theme", self)
        self._theme_btn = QToolButton(tb)
        self._theme_btn.setDefaultAction(self._theme_act)
        self._theme_btn.setAutoRaise(True)
        self._theme_btn.setPopupMode(QToolButton.InstantPopup)
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        grp = QActionGroup(self)
        for name in ("Dark", "Light", "GINI Brand", "High Contrast"):
            a = QAction(name, self, checkable=True)
            a.setChecked(name.lower().startswith(self.theme.theme.name.lower()[:4]))
            a.triggered.connect(lambda _=False, n=name: self.theme.set_theme(n))
            grp.addAction(a); menu.addAction(a)
        self._theme_act.setMenu(menu)
        self._theme_btn.setMenu(menu)
        tb.addWidget(self._theme_btn)
        self._refresh_icons()

    @staticmethod
    def _tb_spacer(width: int) -> QWidget:
        w = QWidget(); w.setFixedWidth(width)
        return w

    def _refresh_icons(self) -> None:
        t = self.theme.theme
        col = t.muted
        for a, icon_name in self._actions.values():
            a.setIcon(icons.icon(icon_name, col, 19))
        self._theme_act.setIcon(icons.icon("palette", col, 19))
        # Run is a filled green primary button (white glyph); Stop reads in danger red
        run_a, _ = self._actions["run"]
        run_a.setIcon(icons.icon("play", "#ffffff", 19))
        stop_a, _ = self._actions["stop"]
        stop_a.setIcon(icons.icon("stop", t.danger, 19))

    # -- docks -------------------------------------------------------------- #
    def _make_docks(self) -> None:
        self.palette = Palette(self.theme)
        left = QDockWidget("Devices", self)
        left.setObjectName("dock_palette")
        left.setWidget(self.palette)
        left.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self.addDockWidget(Qt.LeftDockWidgetArea, left)

        self.inspector = Inspector(self.ctx, self.api, self.theme)
        self.inspector.query_fn = self.element_query
        insp = QDockWidget("Inspector", self)
        insp.setObjectName("dock_inspector")
        insp.setWidget(self.inspector)
        self.addDockWidget(Qt.RightDockWidgetArea, insp)

        self.assistant = Assistant(self.ctx, self.api, self.theme)
        asst = QDockWidget("Ask GINI", self)
        asst.setObjectName("dock_assistant")
        asst.setWidget(self.assistant)
        self.addDockWidget(Qt.RightDockWidgetArea, asst)
        self.tabifyDockWidget(insp, asst)
        insp.raise_()

        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setObjectName("Console")
        self.console.setMaximumBlockCount(2000)
        cons = QDockWidget("Console", self)
        cons.setObjectName("dock_console")
        cons.setWidget(self.console)
        self.addDockWidget(Qt.BottomDockWidgetArea, cons)

        # analytics strip — a short "cloud bill" dashboard stacked under the Console
        from .dashboard import Dashboard
        self.dashboard = Dashboard(self.theme)
        self.dashboard.open_grafana_requested.connect(self._open_grafana)
        dash = QDockWidget("Dashboard", self)
        dash.setObjectName("dock_dashboard")
        dash.setWidget(self.dashboard)
        self.addDockWidget(Qt.BottomDockWidgetArea, dash)
        self.splitDockWidget(cons, dash, Qt.Vertical)   # dashboard below the console
        self.resizeDocks([left], [240], Qt.Horizontal)
        self.resizeDocks([cons, dash], [180, 96], Qt.Vertical)
        self._rebill()

    def _make_statusbar(self) -> None:
        self.status_conn = QLabel()
        self.status_counts = QLabel()
        self.status_theme = QLabel()
        sb = self.statusBar()
        sb.addWidget(self.status_conn)
        sb.addPermanentWidget(self.status_counts)
        sb.addPermanentWidget(self.status_theme)

    # -- actions ------------------------------------------------------------ #
    # -- project persistence ------------------------------------------------ #
    def _make_menubar(self) -> None:
        from PySide6.QtGui import QKeySequence
        mb = self.menuBar()
        filem = mb.addMenu("&File")

        def add(menu, text, slot, shortcut=None):
            a = QAction(text, self)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            a.triggered.connect(slot)
            menu.addAction(a)
            return a

        add(filem, "&New", self._new, "Ctrl+N")
        add(filem, "&Open…", self._open, "Ctrl+O")
        add(filem, "&Save", self._save, "Ctrl+S")
        add(filem, "Save &As…", self._save_as, "Ctrl+Shift+S")
        filem.addSeparator()
        add(filem, "&Export PNG…", self._export_png)
        filem.addSeparator()
        # NoRole keeps these in the File menu on macOS (Qt otherwise hoists "Settings"
        # and "Quit" into the application menu, where the book's readers don't expect them)
        settings_act = add(filem, "&Settings…", self._open_settings, "Ctrl+,")
        settings_act.setMenuRole(QAction.MenuRole.NoRole)
        filem.addSeparator()
        quit_act = add(filem, "&Quit", self.close, "Ctrl+Q")
        quit_act.setMenuRole(QAction.MenuRole.NoRole)

    def _new(self) -> None:
        from ..domain import Topology
        self._project_path = None
        self._router_programs.clear()
        self._set_topology(Topology("untitled"))
        self.setWindowTitle("gBuilder 6.0 — networks + cloud")
        self.ctx.log("New topology.", "info")

    def _open(self) -> None:
        from ..app.paths import projects_dir
        from ..services import PROJECT_EXT
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "Open project", str(projects_dir()),
            f"GINI project (*{PROJECT_EXT});;All files (*)")
        if path:
            self._load_from_path(path)

    def _save(self) -> None:
        if self._project_path:
            self._save_to_path(self._project_path)
        else:
            self._save_as()

    def _save_as(self) -> None:
        from ..app.paths import ensure_dirs, projects_dir
        from ..services import PROJECT_EXT
        from PySide6.QtWidgets import QFileDialog
        ensure_dirs()
        default = str(projects_dir() / f"untitled{PROJECT_EXT}")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save project", default, f"GINI project (*{PROJECT_EXT})")
        if path:
            if not path.endswith(PROJECT_EXT):
                path += PROJECT_EXT
            self._save_to_path(path)

    def _save_to_path(self, path: str) -> None:
        from pathlib import Path
        from ..services import save_project
        self.ctx.topology.name = Path(path).stem
        save_project(self.ctx.topology, path)
        self._project_path = path
        self.setWindowTitle(f"gBuilder 6.0 — {Path(path).name}")
        self.ctx.log(f"Saved {path}", "ok")

    def _load_from_path(self, path: str) -> None:
        from pathlib import Path
        from ..services import load_project
        try:
            topo = load_project(path)
        except Exception as e:
            self.ctx.log(f"Open failed: {e}", "error")
            return
        self._project_path = path
        self._router_programs.clear()
        self._set_topology(topo)
        self.setWindowTitle(f"gBuilder 6.0 — {Path(path).name}")
        self.ctx.log(f"Opened {path}", "ok")

    def _set_topology(self, topo) -> None:
        scene = self.canvas.scene_
        scene.clear()
        scene.nodes.clear()
        scene.edges.clear()
        scene._callouts = []
        scene._spotlit = []
        scene._highlit = []
        self.ctx.topology = topo
        topo.prefix_overrides = dict(self.ctx.settings.name_prefixes)   # apply naming prefs
        self.ctx.selected_id = None
        if hasattr(self, "_manual_addr_act"):
            self._manual_addr_act.setChecked(topo.manual_addressing)
        for d in topo.devices.values():
            self.ctx.bus.device_added.emit(d.id)
        for link in topo.links.values():
            self.ctx.bus.link_added.emit(link.id)
        self.ctx.bus.topology_changed.emit()
        self.ctx.bus.selection_changed.emit(None)

    def _export_png(self) -> None:
        from PySide6.QtCore import QRectF, QSize, Qt
        from PySide6.QtGui import QImage, QPainter
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(self, "Export PNG", "topology.png", "PNG (*.png)")
        if not path:
            return
        scene = self.canvas.scene_
        rect = scene.itemsBoundingRect().adjusted(-40, -40, 40, 40)
        img = QImage(QSize(max(1, int(rect.width())), max(1, int(rect.height()))),
                     QImage.Format_ARGB32)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        scene.render(p, QRectF(img.rect()), rect)
        p.end()
        img.save(path)
        self.ctx.log(f"Exported {path}", "ok")

    def _compile(self):
        cfg = self._gloader.compile(self.ctx.topology)
        self.ctx.log(
            f"Compiled “{self.ctx.topology.name}” → {len(cfg.machines)} machines, "
            f"{len(cfg.switches)} switches, {len(cfg.routers)} routers, "
            f"{len(cfg.subnets)} subnets.", "ok")
        from ..services.compiler import validate
        for it in validate(self.ctx.topology):
            tag = "·" if it["level"] == "info" else "⚠"
            self.ctx.log(f"  {tag} {it['message']}",
                         "info" if it["level"] == "info" else "error")
        return cfg

    def _auto_layout(self) -> None:
        nodes = list(self.canvas.scene_.nodes.values())
        n = len(nodes)
        if n == 0:
            return
        cols = max(1, math.ceil(math.sqrt(n)))
        gx, gy = NODE_W + 60, NODE_H + 60
        for i, node in enumerate(nodes):
            r, c = divmod(i, cols)
            node.setPos(c * gx - (cols - 1) * gx / 2, r * gy - 80)
        self.ctx.log("Arranged topology.", "info")

    def _toggle_connect(self, on: bool) -> None:
        self.canvas.set_connect_mode(on)
        self.ctx.log("Connect mode: click two devices to link them." if on
                     else "Connect mode off.", "info")

    def _toggle_edge_style(self, bent: bool) -> None:
        self.ctx.settings.connector_style = "orthogonal" if bent else "straight"
        self.ctx.bus.edges_restyled.emit()
        self.ctx.log(f"Connectors: {'bent (rounded)' if bent else 'straight'}.", "info")

    def _toggle_manual_addr(self, on: bool) -> None:
        self.ctx.topology.manual_addressing = on
        self._recompute_addressing()      # re-derive with/without the manual overrides
        self._revalidate()
        self.ctx.log(
            "Manual addressing: on — set IPs in Inspector › Interfaces; blanks auto-fill."
            if on else "Manual addressing: off — IPs are auto-assigned.", "info")

    def _run(self) -> None:
        import tempfile
        import threading
        if self._running:
            self.ctx.log("Already running — stop first.", "info")
            return
        cfg = self._compile()
        runnable = (cfg.machines or cfg.routers or cfg.ovs_switches
                    or cfg.controllers or cfg.services)
        if not runnable:
            self.ctx.log("Nothing runnable on the canvas yet (add devices + links).", "info")
            return
        self._last_services = list(cfg.services)
        self._workdir = tempfile.mkdtemp(prefix="gini-lab-")
        self.ctx.log(f"Launching {len(cfg.machines)} machines + {len(cfg.routers)} "
                     f"gRouters + {len(cfg.services)} cloud services via Docker…", "info")
        self.ctx.log(f"Project: {self._workdir}  (double-click a device to log in)", "info")

        auto_internet = self.ctx.settings.auto_internet
        if not auto_internet:
            self.ctx.log("Faithful mode: hosts have NO default route to the internet. "
                         "Draw + wire an Internet element for egress. (Web consoles "
                         "still open — only outbound internet is cut.)", "info")

        def worker(workdir=self._workdir, ai=auto_internet):
            ok, msg = self._gloader.up(cfg, workdir, auto_internet=ai)
            self.ctx.bus.run_state.emit(ok, msg)
        threading.Thread(target=worker, daemon=True).start()

    def _on_run_state(self, ok: bool, msg: str) -> None:
        if ok:
            self._running = True
            self._stopping = False
            self._set_runtime_status("running")
            self._poll.start()                  # reconcile with real container state
            self.ctx.log("Topology running on Docker.", "ok")
            grafana = None
            for s in getattr(self, "_last_services", []):   # surface web consoles
                for p in s.ports:
                    if p.get("web"):
                        url = f"http://localhost:{p['host']}{p.get('path', '')}"
                        self.ctx.log(f"{s.name} ({p['label']}): {url}", "ok")
                        if getattr(s, "type_key", None) == "dashboard":
                            grafana = url
            # start the GINI $ meter billing the launched topology
            from ..domain.pricing import bill
            rate = bill(self.ctx.topology, self.ctx.settings.prices)["rate_per_hr"]
            self.dashboard.set_grafana_url(grafana)
            self.dashboard.start(rate)
        else:
            self.ctx.log(f"Run failed: {msg}", "error")
        self._update_status()

    def _stop(self) -> None:
        import threading
        if not self._running:
            self.ctx.log("Not running.", "info")
            return
        self._stopping = True
        self._set_runtime_status("stopping")    # yellow while containers wind down
        self.ctx.log("Stopping…", "info")
        self._update_status()

        def worker():
            ok, msg = self._gloader.down()
            if not ok:
                self.ctx.log(f"Stop issue: {msg}", "error")
            self.ctx.bus.runtime_status.emit({})   # force a final reconcile -> idle
        threading.Thread(target=worker, daemon=True).start()

    # -- real status reconciliation ----------------------------------------- #
    def _poll_status(self) -> None:
        import threading
        if not self._workdir:
            return
        wd = self._workdir

        def worker():
            self.ctx.bus.runtime_status.emit(self._gloader.status(wd))
        threading.Thread(target=worker, daemon=True).start()

    def _on_runtime_status(self, states) -> None:
        from ..services.compiler import _role, _svc
        any_up = any(v == "running" for v in (states or {}).values())

        # finished stopping, or everything died externally
        if not any_up:
            if self._running or self._stopping:
                self._running = False
                self._stopping = False
                self._poll.stop()
                self._set_runtime_status("idle")
                self.dashboard.stop()           # freeze the session's GINI $ bill
                self.dashboard.set_grafana_url(None)
                self.ctx.log("All containers stopped.", "info")
                self._update_status()
            return
        if self._stopping:
            return   # keep the yellow 'stopping' chips until containers are actually gone

        fabric = states.get("fabric")
        for node in self.canvas.scene_.nodes.values():
            role = _role(node.inst.type_key)
            if role == "machine":
                st = states.get(_svc(node.inst.name))
                node.set_status("running" if st == "running"
                                else "error" if st else "idle")
            elif role in ("router", "ovs", "controller", "service", "compute"):
                st = states.get(_svc(node.inst.name))            # each its own container
                node.set_status("running" if st == "running"
                                else "error" if st else "idle")
            elif role == "switch":                  # switches live in the fabric container
                node.set_status("running" if fabric == "running"
                                else "error" if fabric else "idle")

    def _set_runtime_status(self, status: str) -> None:
        from ..services.compiler import _role
        for node in self.canvas.scene_.nodes.values():
            if _role(node.inst.type_key) in ("machine", "switch", "router", "ovs",
                                             "controller", "service", "compute"):
                node.set_status(status)

    def _recompute_addressing(self) -> None:
        from ..services.compiler import address_map
        try:
            self.ctx.addressing = address_map(self.ctx.topology)
        except Exception:
            self.ctx.addressing = {}
        self.ctx.bus.addressing_changed.emit()

    def _rebill(self) -> None:
        """Refresh the dashboard's projected GINI $/hr from the current canvas. While a
        lab runs the meter holds the launched rate, so we only re-estimate when idle."""
        if not hasattr(self, "dashboard") or self._running:
            return
        from ..domain.pricing import bill
        self.dashboard.set_estimate(bill(self.ctx.topology, self.ctx.settings.prices))

    def _on_device_resized(self, device_id: str) -> None:
        """An element's size tier changed. If the lab is running, apply the new CPU cap
        to its container live (vertical scaling) via `docker update` — no restart."""
        if not self._running:
            return
        import threading
        from ..domain import pricing
        from ..services.compiler import _svc
        d = self.ctx.topology.devices.get(device_id)
        if d is None or not pricing.resizable(d.type_key):
            return
        lvl = pricing.size_level(getattr(d, "size", 1))
        lab, cpus, _mem, _mult = pricing.size_tier(lvl)
        svc, dname = _svc(d.name), d.name

        def worker():
            ok, msg = self._gloader.update_cpus(svc, cpus)
            if ok:
                self.ctx.log(f"Scaled {dname} to {lab} ({cpus:g} vCPU) live — no restart.",
                             "ok")
            else:
                self.ctx.log(f"{dname}: couldn't apply size live ({msg}). "
                             f"It takes effect on the next Run.", "info")
        threading.Thread(target=worker, daemon=True).start()

    def _open_grafana(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        url = self.dashboard.grafana_url()
        if not url:
            self.ctx.log("No Grafana running — add a Dashboards element and Run.", "info")
            return
        QDesktopServices.openUrl(QUrl(url))
        self.ctx.log(f"Opening Grafana: {url}", "ok")

    def _on_palette_explain(self, type_key: str) -> None:
        """In explain mode, clicking a palette element explains that element TYPE."""
        a = getattr(self, "assistant", None)
        if a is not None and getattr(a, "explain_mode", False):
            a.explain_element_type(type_key)

    def _on_selection_explain(self, device_id) -> None:
        """In explain mode, selecting a device explains it on the canvas; clicking
        empty space exits. Lets the student move the explanation around freely."""
        a = getattr(self, "assistant", None)
        if a is None or not getattr(a, "explain_mode", False):
            return
        if device_id is not None:          # selecting a device explains it; empty space
            dev = self.ctx.topology.devices.get(device_id)   # is ignored (mode stays on,
            if dev:                                           # exit via the Explain toggle)
                a.explain_selected(dev.name)

    def _on_warning_explain(self, device_id: str) -> None:
        dev = self.ctx.topology.devices.get(device_id)
        if dev:
            self.ctx.select(device_id)            # spotlight + select the flagged node
            self.assistant.explain_warning(dev.name)

    def _revalidate(self) -> None:
        """Run the advisory lint and surface per-device warnings on the canvas."""
        from ..services.compiler import validate
        try:
            issues = validate(self.ctx.topology)
        except Exception:
            issues = []
        warnings: dict[str, list] = {}
        for it in issues:
            if it["level"] == "warn" and it["device"]:
                warnings.setdefault(it["device"], []).append(it["message"])
        self.ctx.warnings = warnings
        self.ctx.bus.warnings_changed.emit()

    def element_query(self, device_name: str, command: str) -> str:
        """Run a one-shot console command against a network element (needs Docker up)."""
        if not self._workdir:
            return "(not running)"
        import subprocess
        from ..services.compiler import _role, _svc
        svc = _svc(device_name)
        dev = next((d for d in self.ctx.topology.devices.values()
                    if d.name == device_name), None)
        # routers AND the OVS (gRouter in OpenFlow mode) speak via the gRouter control
        # socket — so the OVS console can run `openflow ...` to dump its flow table.
        is_router = dev is not None and _role(dev.type_key) in ("router", "ovs")
        try:
            if is_router:
                # the real C gRouter: run one CLI command over its control socket
                cmd = ["docker", "compose", "exec", "-T", svc, "python3",
                       "/build/grouter-zig/grconsole.py", f"/run/{svc}.ctl",
                       "--once", command]
            else:
                cmd = ["docker", "compose", "exec", "-T", "fabric",
                       "python", "-m", "dataplane.console", svc, command]
            r = subprocess.run(cmd, cwd=self._workdir, capture_output=True,
                               text=True, timeout=15)
            return (r.stdout or r.stderr or "").strip() or "(no output)"
        except Exception as e:
            return f"(query failed: {e})"

    # -- double-click: routers open the Router Lab, others open a terminal -- #
    def _on_device_activated(self, device_id: str) -> None:
        from ..services.compiler import _role, _svc
        dev = self.ctx.topology.devices.get(device_id)
        if dev is None:
            return
        if _role(dev.type_key) == "router":
            self._open_router_lab(device_id)
            return
        # a running service with a web dashboard -> open it (Grafana, MinIO, …)
        if self._running and _role(dev.type_key) == "service":
            svc = _svc(dev.name)
            has_web = any(_svc(s.name) == svc and any(p.get("web") for p in s.ports)
                          for s in getattr(self, "_last_services", []))
            if has_web:
                self._open_console(device_id)
                return
        self._open_terminal(device_id)

    def _open_router_lab(self, device_id: str) -> None:
        from ..domain.router_modules import RouterProgram
        from .router_lab import RouterLab
        dev = self.ctx.topology.devices[device_id]
        program = self._router_programs.setdefault(device_id, RouterProgram())
        # When running, the Router Lab drives the REAL C gRouter's pipeline via `gpipe`
        # over its control socket (gr_rctl); offline it uses the local trace.
        cf = ((lambda c, n=dev.name: self.element_query(n, "gpipe " + c))
              if self._running else None)
        self._router_lab = RouterLab(
            self, self.theme, dev, program,
            on_console=lambda: self._open_terminal(device_id),
            command_fn=cf)
        self._router_lab.show()
        self._router_lab.raise_()

    def _open_console(self, device_id: str) -> None:
        """Open a service's web dashboard (Grafana, MinIO, RabbitMQ, …) in the browser.
        These ship full web UIs — the browser is their native interface — so we just
        launch the published console URL rather than rebuilding it natively."""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        dev = self.ctx.topology.devices.get(device_id)
        if dev is None:
            return
        if not self._running:
            self.ctx.log("Start the topology first (Run), then open the console.", "info")
            return
        from ..services.compiler import _svc
        svc = _svc(dev.name)
        web = None
        for s in getattr(self, "_last_services", []):
            if _svc(s.name) == svc:
                web = next((p for p in s.ports if p.get("web")), None)
                break
        if web is None:
            self.ctx.log(f"{dev.name} has no web console — try “Log in” or “View logs”.",
                         "info")
            return
        url = f"http://localhost:{web['host']}{web.get('path', '')}"
        QDesktopServices.openUrl(QUrl(url))
        self.ctx.log(f"Opening {dev.name} console: {url}", "ok")

    def _open_logs(self, device_id: str) -> None:
        """Open a terminal tailing this element's container logs — handy for watching
        the OpenFlow handshake (POX '[…] connected') and gRouter connect attempts."""
        from ..services import open_terminal
        from ..services.compiler import _role, _svc
        dev = self.ctx.topology.devices.get(device_id)
        if dev is None:
            return
        if _role(dev.type_key) == "switch":   # plain switches live in the fabric container
            svc = "fabric"
        else:
            svc = _svc(dev.name)
        if not self._running or not self._workdir:
            self.ctx.log("Start the topology first (Run), then right-click → View logs.",
                         "info")
            return
        ok, msg = open_terminal(f"GINI {dev.name} logs", self._workdir,
                                f"docker compose logs --tail=200 -f {svc}")
        self.ctx.log(f"Opening logs for {dev.name}…" if ok
                     else f"Could not open logs: {msg}", "info" if ok else "error")

    # -- log into a device -------------------------------------------------- #
    def _open_terminal(self, device_id: str) -> None:
        from ..services import open_terminal
        from ..services.compiler import _role, _svc
        dev = self.ctx.topology.devices.get(device_id)
        if dev is None:
            return
        role = _role(dev.type_key)
        if role == "group":
            self.ctx.log(f"{dev.name} is a grouping — nothing to log into.", "info")
            return
        if not self._running or not self._workdir:
            self.ctx.log("Start the topology first (Run), then double-click to log in.",
                         "info")
            return
        svc = _svc(dev.name)
        if role == "machine":
            cmd = f"docker compose exec {svc} sh"
            kind = "shell"
        elif role in ("router", "ovs"):   # real C gRouter CLI over its control socket
            cmd = (f"docker compose exec {svc} python3 "
                   f"/build/grouter-zig/grconsole.py /run/{svc}.ctl")
            kind = "OpenFlow switch console" if role == "ovs" else "router console"
        elif role == "controller":   # POX container — a shell to inspect it / tail logs
            cmd = f"docker compose exec {svc} sh"
            kind = "controller shell"
        elif role in ("service", "compute"):   # cloud service / compute container — shell
            cmd = f"docker compose exec {svc} sh"
            kind = "service shell" if role == "service" else "instance shell"
        else:  # plain switch — attach to that element's console in the fabric container
            cmd = f"docker compose exec fabric python -m dataplane.console {svc}"
            kind = "console"
        ok, msg = open_terminal(f"GINI {dev.name} {kind}", self._workdir, cmd)
        self.ctx.log(f"Opening {kind} for {dev.name}…" if ok
                     else f"Could not open terminal: {msg}", "info" if ok else "error")

    # -- reactions ---------------------------------------------------------- #
    def _on_scene_selection(self) -> None:
        # single source of truth for selection -> inspector (avoids the click race)
        from .canvas import NodeItem
        try:
            selected = self.canvas.scene_.selectedItems()
        except RuntimeError:
            return                              # scene torn down (window closing)
        nodes = [i for i in selected if isinstance(i, NodeItem)]
        if nodes:
            self.ctx.select(nodes[0].inst.id)
        else:
            self.ctx.select(None)
            self.ctx.bus.present_clear.emit()   # clicking empty space dismisses the tutor
        self._update_delete_enabled()

    # -- delete devices ----------------------------------------------------- #
    def _make_delete_shortcut(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QKeySequence
        a = QAction("Delete device", self)
        a.setShortcuts([QKeySequence(QKeySequence.Delete), QKeySequence(Qt.Key_Backspace)])
        a.setShortcutContext(Qt.WidgetWithChildrenShortcut)   # only when the canvas has focus
        a.triggered.connect(self._delete_selected)
        self.canvas.addAction(a)

    def _update_delete_enabled(self) -> None:
        from .canvas import NodeItem
        has_sel = any(isinstance(i, NodeItem)
                      for i in self.canvas.scene_.selectedItems())
        busy = self._running or getattr(self, "_stopping", False)
        self._delete_act.setEnabled(has_sel and not busy)

    def _delete_selected(self) -> None:
        from .canvas import NodeItem
        ids = [i.inst.id for i in self.canvas.scene_.selectedItems()
               if isinstance(i, NodeItem)]
        self._remove_devices(ids)

    def _delete_device(self, device_id: str) -> None:
        self._remove_devices([device_id])

    def _remove_devices(self, ids: list[str]) -> None:
        if not ids:
            return
        if self._running or getattr(self, "_stopping", False):
            self.ctx.log("Stop the topology before removing devices "
                         "(running elements can't be deleted).", "info")
            return
        names = []
        for did in ids:
            dev = self.ctx.topology.devices.get(did)
            if dev:
                names.append(dev.name)
                self.ctx.remove_device(did)     # model + canvas (device_removed) + links
        if names:
            self.ctx.select(None)
            self._recompute_addressing()        # IPs shift when a device leaves
            self._update_status()
            self._update_delete_enabled()
            self.ctx.log(f"Removed {', '.join(names)}.", "info")

    def _on_theme_changed(self, name: str) -> None:
        self.canvas.scene_.set_theme(self.theme.theme)
        self._refresh_icons()
        self._update_status()

    def _on_log(self, level: str, message: str) -> None:
        tag = {"ok": "✓", "error": "✕", "chat": "›"}.get(level, "•")
        self.console.appendPlainText(f"{tag} {message}")

    def _update_status(self) -> None:
        s = self.api.summary()
        running = getattr(self, "_running", False)
        self.status_conn.setText("  ● running" if running else "  ● idle")
        self.status_counts.setText(f"{s['devices']} devices · {s['links']} links   ")
        self.status_theme.setText(f"{self.theme.theme.name}   ")
        if hasattr(self, "_delete_act"):
            self._update_delete_enabled()        # disabled while running
