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

# Runs INSIDE the faas container (python -c). Reads GINI_FN/METHOD/BODY from the env,
# checks whether this is the function's first call (cold), invokes it on localhost:8000,
# and prints one JSON line {code, ms, cold, body} for the inspector to display.
_FAAS_INVOKE = (
    "import os,time,json,urllib.request,urllib.error\n"
    "fn=os.environ['GINI_FN'];method=os.environ.get('GINI_METHOD','GET')\n"
    "body=os.environ.get('GINI_BODY','');base='http://localhost:8000'\n"
    "try:\n"
    "    m=json.load(urllib.request.urlopen(base+'/_gini/metrics',timeout=5))\n"
    "    prev=m.get('functions',{}).get(fn,{}).get('invocations',0)\n"
    "except Exception:\n"
    "    prev=0\n"
    "data=body.encode() if body else None\n"   # send the body whenever one is typed
    "req=urllib.request.Request(base+'/'+fn,data=data,method=method)\n"
    "t=time.time()\n"
    "try:\n"
    "    r=urllib.request.urlopen(req,timeout=15);code=r.getcode();out=r.read().decode('utf-8','replace')\n"
    "except urllib.error.HTTPError as e:\n"
    "    code=e.code;out=e.read().decode('utf-8','replace')\n"
    "except Exception as e:\n"
    "    code=0;out=str(e)\n"
    "print(json.dumps({'code':code,'ms':round((time.time()-t)*1000),'cold':prev==0,'body':out}))\n"
)


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
        self._remote = None              # RemoteClient when connected to a GINI server, else None
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
        self._fabric_poll = QTimer(self)        # cloud-fabric app metrics
        self._fabric_poll.setInterval(2000)
        self._fabric_poll.timeout.connect(self._poll_fabric)
        self.ctx.bus.fabric_metrics.connect(self._on_fabric_metrics)
        self._k8s_poll = QTimer(self)           # kubernetes metrics (kubectl)
        self._k8s_poll.setInterval(3000)
        self._k8s_poll.timeout.connect(self._poll_k8s)
        self.ctx.bus.k8s_metrics.connect(self._on_k8s_metrics)
        self.theme = ThemeManager(app, self.ctx.settings.theme)
        self.theme.apply()

        self.setWindowTitle("gBuilder 6.0 — networks + cloud")
        from .branding import app_icon
        self.setWindowIcon(app_icon())              # window + taskbar/dock icon (the GINI mascot)
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
        self.ctx.bus.device_changed.connect(self._on_device_changed_live)
        self.ctx.bus.log.connect(self._on_log)
        self.ctx.bus.device_delete_requested.connect(self._delete_device)
        self.ctx.bus.warning_explain_requested.connect(self._on_warning_explain)
        self.ctx.bus.device_logs_requested.connect(self._open_logs)
        self.ctx.bus.device_console_requested.connect(self._open_console)
        self.ctx.bus.function_invoke_requested.connect(self._on_function_invoke)
        self.ctx.bus.function_deploy_requested.connect(self._on_function_deploy)
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
        ctx = f"{digest}\nRuntime: the topology is {state}."
        m = getattr(self.ctx, "mission", None)            # Wizard: keep follow-ups goal-aware
        if m is not None:
            ctx += f"\nThe student's current build objective is: \"{m.goal}\"."
        return ctx

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
            backend = OllamaBackend(s.llm_url, s.llm_model, think=s.llm_think,
                                    num_ctx=getattr(s, "llm_num_ctx", 8192))
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
        self._server_act = act("server", "cloud",
                               "Backend: run on a remote Kata GINI server (or go local)",
                               self._toggle_backend, checkable=True)
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
        self.inspector.stats_fn = self._element_stats
        self.inspector.stats_all_fn = self._element_stats_all
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
        scene.groups.clear()
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

    # -- remote (GINI server) backend -------------------------------------- #
    def _toggle_backend(self) -> None:
        """Toolbar toggle: connect to a remote Kata GINI server, or drop back to local."""
        if self._remote is not None:
            self._remote = None
            self.ctx.settings.backend = "local"
            self.ctx.log("Backend: local Docker.", "info")
            self._server_act.setChecked(False)
            return
        if self._running:
            self.ctx.log("Stop the running lab before switching backend.", "info")
            self._server_act.setChecked(False)
            return
        self._connect_server()
        self._server_act.setChecked(self._remote is not None)

    def _connect_server(self, client=None) -> bool:
        """Log in to the configured GINI server (host/port/user from Settings; password is
        prompted, never stored). `client` is injectable for tests."""
        from PySide6.QtWidgets import QInputDialog, QLineEdit
        s = self.ctx.settings
        if client is None and not s.gini_server_host:
            self.ctx.log("Set the GINI server host in Settings → Backend first.", "info")
            return False
        if client is None:
            pw, ok = QInputDialog.getText(self, "Connect to GINI server",
                                          f"Password for {s.gini_server_user}@{s.gini_server_host}:",
                                          QLineEdit.Password)
            if not ok:
                return False
            from ..services.remote import RemoteClient
            client = RemoteClient(f"http://{s.gini_server_host}:{s.gini_server_port}")
            good, err = client.login(s.gini_server_user, pw)
            if not good:
                self.ctx.log(f"Server login failed: {err}", "error")
                return False
        self._remote = client
        s.backend = "gini-server"
        kata = client.kata_available()
        self.ctx.log(f"Connected to GINI server{' (Kata available)' if kata else ''}. "
                     "Build a topology and Run — it executes on the server.", "ok")
        return True

    def _run_remote(self) -> None:
        if self._running:
            self.ctx.log("Already running — stop first.", "info")
            return
        import threading
        topo = self.ctx.topology
        self.ctx.log("Sending topology to the GINI server…", "info")

        def worker():
            ok, msg = self._remote.run(topo)
            self.ctx.bus.run_state.emit(ok, msg)
        threading.Thread(target=worker, daemon=True).start()

    def _on_remote_run_state(self, ok: bool, msg: str) -> None:
        if ok:
            self._running = True
            self._stopping = False
            self._set_runtime_status("running")
            self.canvas.scene_.running = True
            self.inspector.set_live_running(True)
            self.ctx.log("Topology running on the GINI server.", "ok")
            from PySide6.QtCore import QTimer
            QTimer.singleShot(2500, self._poll_remote_metrics)
        else:
            self._running = False
            self.ctx.log(f"Server run failed: {msg}", "error")
            self._set_runtime_status("idle")

    def _poll_remote_metrics(self) -> None:
        if not self._running or self._remote is None:
            return
        import threading

        def work():
            m = self._remote.metrics() or {}
            startup = m.get("startup") or {}
            if startup:
                line = ", ".join(f"{s} {ms:.0f} ms" for s, ms in sorted(startup.items()))
                self.ctx.log("Startup times — " + line, "info")
        threading.Thread(target=work, daemon=True).start()

    def _run(self) -> None:
        import tempfile
        import threading
        if self._remote is not None:           # remote backend: the server runs it
            self._run_remote()
            return
        if self._running:
            self.ctx.log("Already running — stop first.", "info")
            return
        cfg = self._compile()
        runnable = (cfg.machines or cfg.routers or cfg.ovs_switches
                    or cfg.controllers or cfg.services or cfg.k8s or cfg.faas)
        if not runnable:
            self.ctx.log("Nothing runnable on the canvas yet (add devices + links).", "info")
            return
        # Kata Instances need a 'kata' OCI runtime on the backend — fail with a clear
        # message instead of a raw Docker error if it's not there (e.g. on a Mac).
        if any(getattr(s, "runtime", "") == "kata" for s in cfg.services) \
                and not self._gloader.runtime_available("kata"):
            self.ctx.log("This topology has Kata Instance(s), but the current backend has no "
                         "'kata' runtime. Point GINI at a Kata-enabled Linux backend "
                         "(Settings → Backend) to run VM-isolated workloads.", "info")
            return
        self._last_services = list(cfg.services)
        self._last_k8s = list(cfg.k8s)
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
        if self._remote is not None:           # remote backend has its own (lighter) handling
            self._on_remote_run_state(ok, msg)
            return
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
            self.inspector.set_live_running(True)       # enable the Live metrics plots
            self.canvas.scene_.running = True           # enable console/logs/login actions
            self._fabric_poll.start()                   # poll cloud-fabric app metrics
            from PySide6.QtCore import QTimer
            QTimer.singleShot(6000, self._drive_loadgens)   # let Fortio boot, then load
            QTimer.singleShot(2000, self._log_startup_times)  # VM-vs-container startup signal
            if getattr(self, "_last_k8s", None):
                self._apply_k8s()                           # wait for k3s, kubectl apply
                self._k8s_poll.start()                      # poll deployment metrics
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
            ok, msg = self._remote.stop() if self._remote is not None else self._gloader.down()
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
                self._fabric_poll.stop()
                self._k8s_poll.stop()
                self.dashboard.set_fabric({})
                self.inspector.set_live_running(False)   # stop the Live metrics polling
                self.canvas.scene_.running = False        # grey out console/logs/login actions
                self.inspector.set_fabric_snapshot({})
                self.inspector.set_k8s_snapshot({})
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
            elif role in ("router", "ovs", "controller", "service", "compute", "k8scluster"):
                st = states.get(_svc(node.inst.name))            # each its own container
                node.set_status("running" if st == "running"
                                else "error" if st else "idle")
            elif role in ("k8sworkload", "hpa", "k8snode"):      # live inside the cluster
                node.set_status("running" if fabric is not None or any(
                    v == "running" for v in states.values()) else "idle")
            elif role == "function":                # functions live in the shared faas runtime
                f = states.get("faas")
                node.set_status("running" if f == "running"
                                else "error" if f else "idle")
            elif role == "switch":                  # switches live in the fabric container
                node.set_status("running" if fabric == "running"
                                else "error" if fabric else "idle")

    def _set_runtime_status(self, status: str) -> None:
        from ..services.compiler import _role
        for node in self.canvas.scene_.nodes.values():
            if _role(node.inst.type_key) in ("machine", "switch", "router", "ovs",
                                             "controller", "service", "compute", "function",
                                             "k8scluster", "k8sworkload", "hpa", "k8snode"):
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

    def _on_device_changed_live(self, device_id: str) -> None:
        """A property changed while running — re-drive a Load Generator if its QPS/target
        changed (the live throttle)."""
        if not self._running:
            return
        d = self.ctx.topology.devices.get(device_id)
        if d is None:
            return
        from ..services.compiler import _role, _svc
        if d.type_key == "load_generator":
            self._drive_loadgen(device_id)
        elif _role(d.type_key) == "k8sworkload":          # Replicas slider -> kubectl scale
            self._k8s_live(d, scale=True)
        elif _role(d.type_key) == "hpa":                  # Target CPU slider -> patch HPA
            self._k8s_live(d, scale=False)

    def _k8s_live(self, d, *, scale: bool) -> None:
        import threading
        from ..services.compiler import _role, _svc
        cluster = self._k8s_cluster_svc(d.id)
        if not cluster:
            return
        if scale:
            dep, n = _svc(d.name), d.properties.get("Replicas", "2")

            def work_scale():
                ok, msg = self._gloader.k8s_scale(cluster, dep, n)
                self.ctx.log(f"{d.name}: scaled to {n} replicas." if ok
                             else f"{d.name}: scale failed ({msg})", "ok" if ok else "info")
            threading.Thread(target=work_scale, daemon=True).start()
        else:
            # the HPA name == the connected Pod's deployment name
            hpa = None
            for l in self.ctx.topology.links.values():
                other = (l.target_id if l.source_id == d.id else
                         l.source_id if l.target_id == d.id else None)
                od = self.ctx.topology.devices.get(other) if other else None
                if od and _role(od.type_key) == "k8sworkload":
                    hpa = _svc(od.name); break
            if not hpa:
                return
            tgt = d.properties.get("TargetCPU", "60")

            def work_patch():
                ok, msg = self._gloader.k8s_set_hpa(cluster, hpa, target=tgt,
                                                    mn=d.properties.get("Min"),
                                                    mx=d.properties.get("Max"))
                self.ctx.log(f"{d.name}: HPA target {tgt}% applied." if ok
                             else f"{d.name}: HPA patch failed ({msg})", "ok" if ok else "info")
            threading.Thread(target=work_patch, daemon=True).start()

    def _loadgen_target(self, did: str) -> str | None:
        """The full URL a Load Generator should hit, from what it's wired to:
          * a Function          -> http://faas:8000/<fn>           (direct invoke)
          * an API Gateway      -> http://<gw>/<fn>                (through the front door)
          * proxy/LB/web/service -> http://<name>/                 (the existing HTTP path)
        Returns None if it isn't connected to anything drivable."""
        from ..services.compiler import _role, _svc
        topo = self.ctx.topology
        for l in topo.links.values():
            other = (l.target_id if l.source_id == did else
                     l.source_id if l.target_id == did else None)
            if not other or other not in topo.devices:
                continue
            d = topo.devices[other]
            tk = d.type_key
            if tk == "function":
                return f"http://faas:8000/{_svc(d.name)}"
            if tk == "api_gateway":
                fn = self._gateway_function(other)
                return f"http://{_svc(d.name)}/{fn}" if fn else None
            if tk in ("proxy", "load_balancer", "web_app") or \
                    _role(tk) in ("service", "compute"):
                return f"http://{_svc(d.name)}/"
        return None

    def _gateway_function(self, gw_did: str) -> str | None:
        """A function service name routed by this API Gateway (the first one connected)."""
        from ..services.compiler import _svc
        topo = self.ctx.topology
        for l in topo.links.values():
            other = (l.target_id if l.source_id == gw_did else
                     l.source_id if l.target_id == gw_did else None)
            if other and other in topo.devices and topo.devices[other].type_key == "function":
                return _svc(topo.devices[other].name)
        return None

    def _loadgen_hostport(self, name: str) -> int | None:
        from ..services.compiler import _svc
        for s in getattr(self, "_last_services", []):
            if _svc(s.name) == _svc(name):
                for p in s.ports:
                    if p.get("web"):
                        return p["host"]
        return None

    def _drive_loadgen(self, did: str) -> None:
        """Drive a single Load Generator at its QPS against its connected target."""
        if not self._running:
            return
        import threading
        d = self.ctx.topology.devices.get(did)
        if d is None or d.type_key != "load_generator":
            return
        hp = self._loadgen_hostport(d.name)
        url = self._loadgen_target(did)
        if not hp or not url:
            if self._running and d.type_key == "load_generator":
                self.ctx.log(f"{d.name}: connect it to a Function, API Gateway, Web App "
                             f"or Proxy to send load.", "info")
            return
        qps = d.properties.get("QPS", "100")
        conns = d.properties.get("Connections", "8")
        name = d.name
        try:
            off = float(qps) <= 0          # Fortio qps=0 means UNLIMITED — treat 0 as "off"
        except ValueError:
            off = False

        def work():
            if off:
                self._gloader.stop_load(hp)
                self.ctx.log(f"{name}: load paused (rate 0).", "info")
                return
            ok, msg = self._gloader.drive_load(hp, url, qps, conns)
            self.ctx.log(f"{name}: {msg}" if ok else f"{name}: load failed ({msg})",
                         "ok" if ok else "info")
        threading.Thread(target=work, daemon=True).start()

    def _drive_loadgens(self) -> None:
        for d in list(self.ctx.topology.devices.values()):
            if d.type_key == "load_generator":
                self._drive_loadgen(d.id)

    def _poll_k8s(self) -> None:
        if not self._running or not getattr(self, "_last_k8s", None):
            return
        import threading
        clusters = list(self._last_k8s)

        def work():
            merged = {"deployments": {}, "pods": 0}
            for k in clusters:
                m = self._gloader.k8s_metrics(k.svc)
                merged["deployments"].update(m.get("deployments", {}))
                merged["pods"] += m.get("pods", 0)
            self.ctx.bus.k8s_metrics.emit(merged)
        threading.Thread(target=work, daemon=True).start()

    def _on_k8s_metrics(self, snap) -> None:
        self.inspector.set_k8s_snapshot(snap)

    def _k8s_cluster_svc(self, did: str) -> str | None:
        """The k3s cluster service a K8s element belongs to (its connected cluster, or the
        cluster of its connected Pod)."""
        from ..services.compiler import _role, _svc
        topo = self.ctx.topology
        seen, frontier = set(), [did]
        while frontier:                      # 2-hop walk: element -> [pod] -> cluster
            cur = frontier.pop()
            for l in topo.links.values():
                other = (l.target_id if l.source_id == cur else
                         l.source_id if l.target_id == cur else None)
                od = topo.devices.get(other) if other else None
                if not od or other in seen:
                    continue
                seen.add(other)
                if _role(od.type_key) == "k8scluster":
                    return _svc(od.name)
                if _role(od.type_key) == "k8sworkload":
                    frontier.append(other)
        return self._last_k8s[0].svc if getattr(self, "_last_k8s", None) else None

    def _apply_k8s(self) -> None:
        """Once running, wait for each k3s cluster to be Ready and apply its manifests,
        then report the pods that scheduled."""
        import threading
        clusters = list(getattr(self, "_last_k8s", []))

        def work():
            for k in clusters:
                self.ctx.log(f"{k.name}: starting k3s cluster (this takes ~20-30s)…", "info")
                ok, msg = self._gloader.k8s_apply(k.svc)
                if not ok:
                    self.ctx.log(f"{k.name}: kubectl apply failed — {msg}", "error")
                    continue
                pods = self._gloader.k8s_pods(k.svc)
                self.ctx.log(f"{k.name}: applied {len(k.deployments)} deployment(s); "
                             f"{len(pods)} pod(s) scheduling.", "ok")
        threading.Thread(target=work, daemon=True).start()

    def _poll_fabric(self) -> None:
        if not self._running:
            return
        import threading

        def work():
            snap = self._gloader.fabric_metrics()
            if snap:
                self.ctx.bus.fabric_metrics.emit(snap)
        threading.Thread(target=work, daemon=True).start()

    def _on_fabric_metrics(self, snap) -> None:
        self.dashboard.set_fabric(snap.get("totals", {}))
        self.inspector.set_fabric_snapshot(snap)

    def _element_stats(self, device_name: str):
        """Live CPU%/memory sample for the Inspector's metrics plots (None if not running)."""
        if not self._running:
            return None
        from ..services.compiler import _svc
        return self._gloader.stats(_svc(device_name))

    def _element_stats_all(self):
        """CPU/mem/net for every running container — keeps per-element Live history."""
        return self._gloader.stats_all() if self._running else {}

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

    def _log_startup_times(self) -> None:
        """Log per-element startup times to the Console — the VM-vs-container headline
        (a Kata Instance boots a guest kernel, so it starts much slower than a container)."""
        if not self._running:
            return
        import threading

        def work():
            times = self._gloader.startup_times()
            if times:
                line = ", ".join(f"{svc} {ms:.0f} ms" for svc, ms in sorted(times.items()))
                self.ctx.log("Startup times — " + line, "info")
        threading.Thread(target=work, daemon=True).start()

    def _on_function_deploy(self) -> None:
        """AWS-style 'Deploy': push the current function code to the running runtime by
        recreating only the faas container (the rest of the lab keeps running)."""
        import threading
        if not self._running or not self._workdir:
            self.ctx.log("Run the topology first, then Deploy.", "info")
            return
        cfg = self._compile()
        if not cfg.faas:
            self.ctx.log("No Functions on the canvas to deploy.", "info")
            return
        self.ctx.log("Deploying functions — recreating the faas runtime…", "info")

        def worker(c=cfg, ai=self.ctx.settings.auto_internet):
            ok, msg = self._gloader.redeploy_faas(c, auto_internet=ai)
            self.ctx.log("Functions deployed — the runtime restarted with your latest code."
                         if ok else f"Deploy failed: {msg}", "ok" if ok else "error")

        threading.Thread(target=worker, daemon=True).start()

    def _on_function_invoke(self, device_id: str, method: str, body: str) -> None:
        """Invoke a Function from the inspector's Test panel: call it inside the faas
        runtime, capture status/duration/cold, and send the result back to the inspector."""
        import json
        import subprocess
        import threading
        from ..services.compiler import _svc
        dev = self.ctx.topology.devices.get(device_id)
        if dev is None:
            return
        if not self._running or not self._workdir:
            self.ctx.bus.function_invoke_result.emit(
                device_id, "Start the topology first (Run), then Invoke.")
            return
        fn = _svc(dev.name)

        def work():
            cmd = ["docker", "compose", "exec", "-T",
                   "-e", f"GINI_FN={fn}", "-e", f"GINI_METHOD={method}",
                   "-e", f"GINI_BODY={body}", "faas", "python", "-c", _FAAS_INVOKE]
            try:
                r = subprocess.run(cmd, cwd=self._workdir, capture_output=True,
                                   text=True, timeout=30)
                line = (r.stdout or "").strip().splitlines()[-1] if r.stdout.strip() else ""
                d = json.loads(line) if line else None
                if d:
                    warm = "cold start" if d.get("cold") else "warm"
                    text = f"HTTP {d['code']} · {d['ms']} ms · {warm}\n\n{d['body']}"
                else:
                    text = "(no response)\n" + (r.stderr or "").strip()
            except Exception as e:
                text = f"Invoke failed: {e}"
            self.ctx.bus.function_invoke_result.emit(device_id, text)

        threading.Thread(target=work, daemon=True).start()

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
        if not self._running or not self._workdir:
            self.ctx.log("Start the topology first (Run), then right-click → View logs.",
                         "info")
            return
        role = _role(dev.type_key)
        if role == "switch":                  # plain switches live in the fabric container
            log_cmd = "docker compose logs --tail=200 -f fabric"
        elif role == "k8sworkload":           # a Pod has no container — tail it via kubectl
            cluster = self._k8s_cluster_svc(device_id)
            if not cluster:
                self.ctx.log(f"{dev.name} isn't connected to a K8s Cluster yet.", "info")
                return
            log_cmd = f"docker compose exec {cluster} kubectl logs deploy/{_svc(dev.name)} --tail=200 -f"
        elif role == "hpa":
            self.ctx.log(f"{dev.name} (autoscaler) has no logs — double-click it for status.",
                         "info")
            return
        elif role == "function":              # functions share the one `faas` runtime container
            log_cmd = "docker compose logs --tail=200 -f faas"
        else:
            log_cmd = f"docker compose logs --tail=200 -f {_svc(dev.name)}"
        ok, msg = open_terminal(f"GINI {dev.name} logs", self._workdir, log_cmd)
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
        elif role in ("k8scluster", "k8snode"):   # the real k3s container — kubectl lives here
            cmd = f"docker compose exec {svc} sh"
            kind = "Kubernetes shell (run kubectl here)"
        elif role == "k8sworkload":   # a Pod = a Deployment; exec into one of its pods via the cluster
            cluster = self._k8s_cluster_svc(device_id)
            if not cluster:
                self.ctx.log(f"{dev.name} isn't connected to a K8s Cluster yet.", "info")
                return
            cmd = f"docker compose exec {cluster} kubectl exec -it deploy/{svc} -- sh"
            kind = "pod shell"
        elif role == "hpa":   # the Pod Autoscaler (HPA) — show its live status
            cluster = self._k8s_cluster_svc(device_id)
            hpa = next((_svc(self.ctx.topology.devices[o].name)
                        for l in self.ctx.topology.links.values()
                        for o in (l.target_id if l.source_id == device_id else
                                  l.source_id if l.target_id == device_id else None,)
                        if o and _role(self.ctx.topology.devices[o].type_key) == "k8sworkload"), None)
            if not cluster or not hpa:
                self.ctx.log(f"{dev.name} isn't attached to a Pod yet.", "info")
                return
            cmd = f"docker compose exec {cluster} kubectl describe hpa {hpa}"
            kind = "autoscaler status"
        elif role == "function":   # a handler in the shared `faas` runtime, not its own container
            cmd = "docker compose exec faas sh"
            kind = "faas runtime shell"
            self.ctx.log(
                f"{dev.name} runs in the shared faas runtime. Invoke it from this shell with:\n"
                f"  python -c \"import urllib.request as u; "
                f"print(u.urlopen('http://localhost:8000/{svc}').read().decode())\"", "info")
        else:  # plain switch — attach to that element's console in the fabric container
            cmd = f"docker compose exec fabric python -m dataplane.console {svc}"
            kind = "console"
        ok, msg = open_terminal(f"GINI {dev.name} {kind}", self._workdir, cmd)
        self.ctx.log(f"Opening {kind} for {dev.name}…" if ok
                     else f"Could not open terminal: {msg}", "info" if ok else "error")

    # -- reactions ---------------------------------------------------------- #
    def _on_scene_selection(self) -> None:
        # single source of truth for selection -> inspector (avoids the click race)
        from .canvas import GroupItem, NodeItem
        try:
            selected = self.canvas.scene_.selectedItems()
        except RuntimeError:
            return                              # scene torn down (window closing)
        nodes = [i for i in selected if isinstance(i, (NodeItem, GroupItem))]
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
