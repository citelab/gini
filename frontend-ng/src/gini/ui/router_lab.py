"""Router Lab — the visual module-graph editor for one gRouter.

Open it by double-clicking a router. You compose the data plane by adding inline
modules onto the locked base pipeline (parse → route → rewrite), reorder/remove them,
toggle the SDN mode (OpenFlow = flow-table front door), and step a test packet through
to see the verdict at each stage. Today it drives a local trace; later it binds to the
real gRouter's module graph over the control protocol.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QDialog, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QPlainTextEdit, QPushButton, QScrollArea, QTableWidget, QTableWidgetItem,
    QTabWidget, QVBoxLayout, QWidget,
)

from ..domain.router_modules import BASE, CUSTOM, INLINE, MODULE_BY_KEY, RouterProgram
from .theme import ThemeManager, icons


class RouterLab(QDialog):
    live_ready = Signal(str)   # real-router trace output (from a worker thread)
    flows_ready = Signal(list)  # parsed FlowEntry rows (from a worker thread)
    tablestats_ready = Signal(object)  # OpenFlow table-level stats dict
    routes_ready = Signal(list)  # parsed RouteEntry rows (from a worker thread)
    chain_ready = Signal(str)    # live `gpipe list` output (the deployed service chain)

    def __init__(self, parent, theme: ThemeManager, device, program: RouterProgram,
                 on_console=None, command_fn=None, sdn=False, query_fn=None,
                 face=None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.device = device
        self.program = program
        self.on_console = on_console
        self.command_fn = command_fn   # set when running: sends `gpipe …` to the real router
        self.query_fn = query_fn       # set when running: runs a raw CLI cmd (openflow…/route/arp)
        # role-specialized FACE of the one gRouter engine: 'router' (full pipeline), 'firewall'
        # (rules-first, pipeline under Advanced), 'ovs' (SDN flow-table dashboard).
        self.face = face or ("ovs" if sdn else "router")
        self.sdn = (self.face == "ovs")
        self._trace: list[str] = []
        self._step_idx = -1
        self._stage_widgets: list[QFrame] = []
        self.live_ready.connect(self._show_live)
        self.flows_ready.connect(self._on_flows)
        self.tablestats_ready.connect(self._on_table_stats)
        self.routes_ready.connect(self._on_routes)
        self.chain_ready.connect(self._on_chain)
        if self.sdn:
            program.set_mode("openflow")   # an OVS is an OpenFlow switch by definition
            from ..domain.flowlog import FlowLog
            self._flow_log = FlowLog()     # accumulates rule install/expire events over time

        t = theme.theme
        _faces = {"ovs": ("OVS Switch (SDN)", "controller"),
                  "firewall": ("Firewall", "firewall"),
                  "router": ("Router Lab", "router")}
        kind, icon_key = _faces.get(self.face, _faces["router"])
        self.setWindowTitle(f"{kind} — {device.name}")
        self.resize(880, 720 if self.face != "router" else 620)
        self.setStyleSheet(f"QDialog{{background:{t.bg};}}")

        root = QVBoxLayout(self)

        # header -------------------------------------------------------------
        head = QHBoxLayout()
        ic = QLabel(); ic.setPixmap(icons.render_pixmap(icon_key, t.accent_for("blue"), 24))
        title = QLabel(f"  {kind} — {device.name}")
        title.setStyleSheet("font-size:15px; font-weight:600;")
        head.addWidget(ic); head.addWidget(title); head.addStretch(1)
        head.addWidget(QLabel("mode:"))
        self.mode_legacy = QPushButton("Legacy"); self.mode_legacy.setCheckable(True)
        self.mode_of = QPushButton("OpenFlow"); self.mode_of.setCheckable(True)
        grp = QButtonGroup(self); grp.addButton(self.mode_legacy); grp.addButton(self.mode_of)
        self.mode_legacy.clicked.connect(lambda: self._set_mode("legacy"))
        self.mode_of.clicked.connect(lambda: self._set_mode("openflow"))
        head.addWidget(self.mode_legacy); head.addWidget(self.mode_of)
        if on_console:
            con = QPushButton("  Console")
            con.setIcon(icons.icon("link", t.muted, 14))
            con.clicked.connect(lambda: on_console())
            head.addSpacing(12); head.addWidget(con)
        root.addLayout(head)

        # the module-pipeline editor (palette + pipeline) as ONE widget, so a face can
        # hide/collapse it.
        body_w = QWidget()
        body = QHBoxLayout(body_w); body.setContentsMargins(0, 0, 0, 0)
        body.addWidget(self._build_palette(), 0)
        body.addWidget(self._build_pipeline(), 1)

        # footer (packet step debugger) as a widget too
        foot_w = QWidget()
        foot = QHBoxLayout(foot_w); foot.setContentsMargins(0, 0, 0, 0)
        inject = QPushButton("  Inject packet"); inject.setObjectName("Accent")
        inject.setIcon(icons.icon("play", "#ffffff", 14)); inject.clicked.connect(self._inject)
        step = QPushButton("  Step"); step.clicked.connect(self._step)
        reset = QPushButton("  Reset"); reset.clicked.connect(self._reset)
        self.trace_lbl = QLabel("Inject a test packet, then Step through the pipeline.")
        self.trace_lbl.setObjectName("Muted"); self.trace_lbl.setWordWrap(True)
        foot.addWidget(inject); foot.addWidget(step); foot.addWidget(reset)
        foot.addSpacing(12); foot.addWidget(self.trace_lbl, 1)

        # assemble per face --------------------------------------------------
        if self.face == "ovs":
            root.addWidget(self._build_flow_table())
            root.addWidget(foot_w)
        elif self.face == "firewall":
            # firewall leads with its RULES; the full gRouter pipeline is collapsed under
            # "Advanced" — a firewall IS a gRouter, so it's one click away, not removed.
            root.addWidget(self._build_firewall_panel())
            self._adv_box = QWidget()
            adv = QVBoxLayout(self._adv_box); adv.setContentsMargins(0, 0, 0, 0)
            adv.addWidget(body_w); adv.addWidget(self._build_sfc_row()); adv.addWidget(foot_w)
            self._adv_box.setVisible(False)
            root.addWidget(self._advanced_toggle())
            root.addWidget(self._adv_box, 1)
            root.addWidget(self._build_route_table())
        else:  # router — the full pipeline is the point
            root.addWidget(body_w, 1)
            root.addWidget(self._build_sfc_row())
            root.addWidget(self._build_route_table())
            root.addWidget(foot_w)

        self._rebuild()

        # poll the live table (flows for an OVS, routes otherwise) while open
        refresh = self._refresh_flows if self.sdn else self._refresh_routes
        self._live_timer = QTimer(self)
        self._live_timer.timeout.connect(refresh)
        if query_fn is not None:
            self._live_timer.start(2500)
        refresh()

    # palette ---------------------------------------------------------------
    def _build_palette(self) -> QWidget:
        t = self.theme.theme
        w = QWidget(); w.setObjectName("Sidebar"); w.setFixedWidth(196)
        lay = QVBoxLayout(w); lay.setContentsMargins(10, 10, 10, 10); lay.setSpacing(5)

        def header(text: str) -> QLabel:
            label = QLabel(text); label.setObjectName("PanelHead"); return label

        def pal_btn(mt, locked: bool) -> QPushButton:
            b = QPushButton(f"  {mt.label}")
            b.setIcon(icons.icon(mt.icon, t.accent_for(mt.accent), 18))
            b.setStyleSheet("text-align:left;")
            b.setToolTip(mt.description)
            if locked:
                b.setEnabled(False)
            else:
                b.clicked.connect(lambda _=False, k=mt.key: self._add(k))
            return b

        lay.addWidget(header("Base · required"))
        for mt in BASE:
            lay.addWidget(pal_btn(mt, locked=True))
        lay.addWidget(header("Service functions (VNFs) · click to add"))
        for mt in INLINE:
            lay.addWidget(pal_btn(mt, locked=False))
        lay.addWidget(header("Custom VNF · you write"))
        for mt in CUSTOM:
            lay.addWidget(pal_btn(mt, locked=False))
        lay.addStretch(1)
        return w

    def _build_pipeline(self) -> QScrollArea:
        self.pipe_host = QWidget()
        self.pipe_layout = QVBoxLayout(self.pipe_host)
        self.pipe_layout.setContentsMargins(16, 12, 16, 12)
        self.pipe_layout.setSpacing(5)
        self.pipe_layout.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        sc = QScrollArea(); sc.setWidgetResizable(True); sc.setWidget(self.pipe_host)
        return sc

    # pipeline render -------------------------------------------------------
    def _rebuild(self) -> None:
        while self.pipe_layout.count():
            item = self.pipe_layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)        # remove from view immediately (not just deleteLater)
                w.deleteLater()
        self._stage_widgets = []
        stages = self.program.stages()
        for i, st in enumerate(stages):
            row = self._stage_row(st)
            self.pipe_layout.addWidget(row, 0, Qt.AlignHCenter)
            self._stage_widgets.append(row)
            if i < len(stages) - 1:
                arrow = QLabel("▼"); arrow.setObjectName("Faint")
                self.pipe_layout.addWidget(arrow, 0, Qt.AlignHCenter)
        self.mode_legacy.setChecked(self.program.mode == "legacy")
        self.mode_of.setChecked(self.program.mode == "openflow")

    def _stage_row(self, st) -> QFrame:
        t = self.theme.theme
        accent = t.accent_for(st.accent)
        f = QFrame(); f.setObjectName("Card"); f.setFixedWidth(380)
        f._accent = accent
        f.setStyleSheet(f"QFrame#Card{{background:{t.panel2};border:1px solid {t.line};"
                        f"border-radius:10px;}}")
        hl = QHBoxLayout(f); hl.setContentsMargins(11, 8, 8, 8); hl.setSpacing(8)
        iname = {"ingress": "chevron_right", "egress": "chevron_right",
                 "mode": "controller"}.get(st.kind)
        if iname is None:
            iname = MODULE_BY_KEY[st.key].icon if st.key in MODULE_BY_KEY else "dot"
        icon = QLabel(); icon.setPixmap(icons.render_pixmap(iname, accent, 16))
        name = QLabel(st.label); name.setStyleSheet("font-weight:500;")
        tag = QLabel(st.kind if not st.locked else f"{st.kind} · locked")
        tag.setObjectName("Faint")
        hl.addWidget(icon); hl.addWidget(name); hl.addStretch(1); hl.addWidget(tag)
        if st.kind == "inline" and st.index is not None:
            for sym, fn in (("▲", lambda i=st.index: self._move(i, -1)),
                            ("▼", lambda i=st.index: self._move(i, 1)),
                            ("✕", lambda i=st.index: self._remove(i))):
                btn = QPushButton(sym); btn.setFixedWidth(26)
                btn.clicked.connect(lambda _=False, f=fn: f())
                hl.addWidget(btn)
        return f

    def _highlight(self, idx: int) -> None:
        t = self.theme.theme
        for i, w in enumerate(self._stage_widgets):
            if i == idx:
                w.setStyleSheet(f"QFrame#Card{{background:{t.panel2};"
                                f"border:2px solid {w._accent};border-radius:10px;}}")
            else:
                w.setStyleSheet(f"QFrame#Card{{background:{t.panel2};"
                                f"border:1px solid {t.line};border-radius:10px;}}")

    # actions ---------------------------------------------------------------
    def _add(self, key: str) -> None:
        self.program.add(key); self._reset(); self._rebuild()

    def _remove(self, i: int) -> None:
        self.program.remove(i); self._reset(); self._rebuild()

    def _move(self, i: int, d: int) -> None:
        self.program.move(i, d); self._reset(); self._rebuild()

    def _set_mode(self, mode: str) -> None:
        self.program.set_mode(mode); self._reset(); self._rebuild()

    def _inject(self) -> None:
        if self.command_fn is not None:
            self._inject_live()          # drive the REAL running router via gpipe
            return
        self._trace = self.program.trace()
        self._step_idx = 0
        self._show_step()

    def _inject_live(self, dst: str = "10.0.2.10") -> None:
        import threading
        self._highlight(-1)
        self.trace_lbl.setText("running on the live router…")
        prog = self.program

        def work():
            try:
                self.command_fn("clear")
                for inst in prog.inline:
                    k = inst.type_key
                    if k == "acl":
                        self.command_fn(f"add acl {inst.params.get('deny', '10.0.3.0/24')}")
                    elif k == "nat":
                        self.command_fn("add nat 203.0.113.1")
                    elif k in ("rate", "classify", "tap"):
                        self.command_fn("add counter")   # stand-in on the router
                resp = self.command_fn(f"trace {dst}")
            except Exception as e:
                resp = f"(router query failed: {e})"
            self.live_ready.emit(resp)
        threading.Thread(target=work, daemon=True).start()

    def _show_live(self, text: str) -> None:
        self.trace_lbl.setText("live router:  " + text.replace("\n", "   "))

    # SDN flow table (OVS) --------------------------------------------------
    def _table(self, cols, stretch_col, min_h=150) -> QTableWidget:
        tbl = QTableWidget(0, len(cols))
        tbl.setHorizontalHeaderLabels(cols)
        tbl.verticalHeader().setVisible(False)
        tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tbl.setSelectionMode(QAbstractItemView.NoSelection)
        tbl.setMinimumHeight(min_h)
        hh = tbl.horizontalHeader()
        for i in range(len(cols)):
            hh.setSectionResizeMode(i, QHeaderView.Stretch if i == stretch_col
                                    else QHeaderView.ResizeToContents)
        return tbl

    def _build_flow_table(self) -> QWidget:
        t = self.theme.theme
        w = QFrame(); w.setObjectName("Card")
        w.setStyleSheet(f"QFrame#Card{{background:{t.panel2};border:1px solid {t.line};"
                        f"border-radius:10px;}}")
        lay = QVBoxLayout(w); lay.setContentsMargins(12, 10, 12, 12); lay.setSpacing(6)
        head = QHBoxLayout()
        title = QLabel("SDN · OpenFlow rules installed by the controller")
        title.setStyleSheet("font-size:13px; font-weight:600;")
        self.flow_status = QLabel("…"); self.flow_status.setObjectName("Muted")
        refresh = QPushButton("  Refresh"); refresh.setIcon(icons.icon("play", t.muted, 13))
        refresh.clicked.connect(self._refresh_flows)
        head.addWidget(title); head.addStretch(1)
        head.addWidget(self.flow_status); head.addSpacing(10); head.addWidget(refresh)
        lay.addLayout(head)

        # table-level counters — proof the controller is programming the datapath
        self.flow_stats = QLabel(""); self.flow_stats.setObjectName("Faint")
        self.flow_stats.setToolTip("'looked up' = packets the switch checked against the "
                                   "table; 'matched' = packets a rule handled on the datapath "
                                   "without asking the controller.")
        lay.addWidget(self.flow_stats)

        # Tab 1: the live snapshot of currently-installed rules.
        self.flow_table = self._table(["#", "Match", "Actions", "Pkts", "Bytes", "Prio", "Age"],
                                      stretch_col=1)
        # Tab 2: the event log — every rule as it is installed / expires over time. This is
        # the FULL picture (the live table only ever shows what's active this instant).
        self.flow_events = self._table(["Time", "Event", "Match", "Action", "Pkts"],
                                       stretch_col=2)
        tabs = QTabWidget()
        tabs.addTab(self.flow_table, "Installed rules (live)")
        tabs.addTab(self.flow_events, "Rule events (log)")
        lay.addWidget(tabs)
        return w

    def _refresh_flows(self) -> None:
        if self.query_fn is None:
            self._set_flow_status("not running — press Run, then reopen to see live flows")
            return
        import threading
        self._set_flow_status("reading flow table…")
        qf = self.query_fn

        def work():
            from ..domain.flowtable import flows, parse_table_stats
            rows, stats = [], {}
            try:
                entry = qf("openflow entry all")
                rows = flows(entry, qf("openflow stats entry all"))
                stats = parse_table_stats(qf("openflow stats table"))
            except Exception:
                pass
            self.flows_ready.emit(rows)
            self.tablestats_ready.emit(stats)
        threading.Thread(target=work, daemon=True).start()

    def _on_flows(self, rows: list) -> None:
        self.flow_table.setRowCount(len(rows))
        for r, f in enumerate(rows):
            age = "" if f.duration is None else f"{f.duration}s"
            prio = "" if f.priority is None else str(f.priority)
            cells = [str(f.index), f.match_summary(), f.action_summary(),
                     "" if f.packets is None else str(f.packets),
                     "" if f.bytes is None else str(f.bytes), prio, age]
            for c, val in enumerate(cells):
                self.flow_table.setItem(r, c, QTableWidgetItem(val))
        n = len(rows)
        self._set_flow_status(f"{n} installed rule{'s' if n != 1 else ''}" if n
                              else "no installed rules yet")
        # fold this snapshot into the running event log and repaint the log tab
        self._flow_log.update(rows)
        self._render_flow_events()

    def _render_flow_events(self) -> None:
        events = self._flow_log.recent()
        self.flow_events.setRowCount(len(events))
        for r, e in enumerate(events):
            mark = "＋ installed" if e.kind == "installed" else "－ expired"
            cells = [e.when, mark, e.match, e.action,
                     "" if e.packets is None else str(e.packets)]
            for c, val in enumerate(cells):
                self.flow_events.setItem(r, c, QTableWidgetItem(val))

    def _on_table_stats(self, stats) -> None:
        if not hasattr(self, "flow_stats"):
            return
        parts = []
        if stats:
            if "active" in stats:
                parts.append(f"{stats['active']} active")
            if "lookups" in stats:
                parts.append(f"{stats['lookups']} looked up")
            if "matched" in stats:
                parts.append(f"{stats['matched']} matched by a rule")
        self.flow_stats.setText(("Datapath:  " + "  ·  ".join(parts)) if parts else "")

    def _set_flow_status(self, text: str) -> None:
        if hasattr(self, "flow_status"):
            self.flow_status.setText(text)

    # Routing table (regular router) ----------------------------------------
    def _build_route_table(self) -> QWidget:
        t = self.theme.theme
        w = QFrame(); w.setObjectName("Card")
        w.setStyleSheet(f"QFrame#Card{{background:{t.panel2};border:1px solid {t.line};"
                        f"border-radius:10px;}}")
        lay = QVBoxLayout(w); lay.setContentsMargins(12, 10, 12, 12); lay.setSpacing(6)
        head = QHBoxLayout()
        title = QLabel("Routing Table")
        title.setStyleSheet("font-size:13px; font-weight:600;")
        self.route_status = QLabel("…"); self.route_status.setObjectName("Muted")
        refresh = QPushButton("  Refresh"); refresh.setIcon(icons.icon("play", t.muted, 13))
        refresh.clicked.connect(self._refresh_routes)
        head.addWidget(title); head.addStretch(1)
        head.addWidget(self.route_status); head.addSpacing(10); head.addWidget(refresh)
        lay.addLayout(head)

        cols = ["Network", "Netmask", "Next hop", "Interface"]
        self.route_table = QTableWidget(0, len(cols))
        self.route_table.setHorizontalHeaderLabels(cols)
        self.route_table.verticalHeader().setVisible(False)
        self.route_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.route_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.route_table.setMinimumHeight(130)
        hh = self.route_table.horizontalHeader()
        for i in range(len(cols)):
            hh.setSectionResizeMode(i, QHeaderView.Stretch if i in (0, 2)
                                    else QHeaderView.ResizeToContents)
        lay.addWidget(self.route_table)
        return w

    def _refresh_routes(self) -> None:
        if self.query_fn is None:
            self._set_route_status("not running — press Run to see the live route table")
            return
        import threading
        self._set_route_status("reading route table…")
        qf = self.query_fn

        def work():
            from ..domain.routetable import parse_routes
            rows, chain = [], ""
            try:
                rows = parse_routes(qf("route"))
                chain = qf("gpipe list")     # the live deployed service chain
            except Exception:
                pass
            self.routes_ready.emit(rows)
            self.chain_ready.emit(chain)
        threading.Thread(target=work, daemon=True).start()

    def _on_routes(self, rows: list) -> None:
        self.route_table.setRowCount(len(rows))
        for r, e in enumerate(rows):
            cells = [e.network, e.netmask, e.nexthop_str(), e.iface]
            for c, val in enumerate(cells):
                self.route_table.setItem(r, c, QTableWidgetItem(val))
        n = len(rows)
        self._set_route_status(f"{n} route{'s' if n != 1 else ''}" if n else "no routes")

    def _set_route_status(self, text: str) -> None:
        if hasattr(self, "route_status"):
            self.route_status.setText(text)

    # Firewall face ---------------------------------------------------------
    def _build_firewall_panel(self) -> QWidget:
        t = self.theme.theme
        w = QFrame(); w.setObjectName("Card")
        w.setStyleSheet(f"QFrame#Card{{background:{t.panel2};border:1px solid {t.line};"
                        f"border-radius:10px;}}")
        lay = QVBoxLayout(w); lay.setContentsMargins(12, 10, 12, 12); lay.setSpacing(6)
        head = QHBoxLayout()
        title = QLabel("Firewall rules")
        title.setStyleSheet("font-size:13px; font-weight:600;")
        self.fw_status = QLabel(""); self.fw_status.setObjectName("Muted")
        deploy = QPushButton("  Deploy rules"); deploy.setObjectName("Accent")
        deploy.setIcon(icons.icon("send", "#ffffff", 13))
        deploy.clicked.connect(self._deploy_firewall)
        head.addWidget(title); head.addStretch(1)
        head.addWidget(self.fw_status); head.addSpacing(10); head.addWidget(deploy)
        lay.addLayout(head)
        hint = QLabel("One rule per line — e.g.  deny 10.0.3.0/24   (drops traffic to that "
                      "network). These become the router's ACL.")
        hint.setObjectName("Faint"); hint.setWordWrap(True)
        lay.addWidget(hint)
        self.fw_rules = QPlainTextEdit()
        self.fw_rules.setPlaceholderText("deny 10.0.3.0/24")
        self.fw_rules.setPlainText((getattr(self.device, "properties", {}) or {}).get("Rules", ""))
        self.fw_rules.setMaximumHeight(110)
        lay.addWidget(self.fw_rules)
        self.fw_deployed = QLabel("Deployed: (press Run, then Deploy rules)")
        self.fw_deployed.setObjectName("Faint")
        lay.addWidget(self.fw_deployed)
        return w

    def _advanced_toggle(self) -> QWidget:
        btn = QPushButton("  ▸ Advanced — full gRouter pipeline")
        btn.setCheckable(True); btn.setObjectName("Faint")
        btn.setStyleSheet("text-align:left;")

        def toggle(on):
            self._adv_box.setVisible(on)
            btn.setText(("  ▾ Advanced — full gRouter pipeline") if on
                        else "  ▸ Advanced — full gRouter pipeline")
        btn.toggled.connect(toggle)
        return btn

    def _deploy_firewall(self) -> None:
        from ..domain.firewall import deploy_commands
        props = getattr(self.device, "properties", None)
        if props is not None:                       # persist the typed rules onto the element
            props["Rules"] = self.fw_rules.toPlainText()
        if self.command_fn is None:
            self._set_fw_status("not running — press Run to deploy the rules")
            return
        import threading
        self._set_fw_status("deploying…")
        cf, qf = self.command_fn, self.query_fn
        cmds = deploy_commands(self.fw_rules.toPlainText())

        def work():
            listing = ""
            try:
                for c in cmds:
                    cf(c)                            # cf sends `gpipe <c>`
                listing = qf("gpipe list") if qf is not None else cf("list")
            except Exception as e:
                listing = f"(deploy failed: {e})"
            self.chain_ready.emit(listing)
        threading.Thread(target=work, daemon=True).start()

    def _set_fw_status(self, text: str) -> None:
        if hasattr(self, "fw_status"):
            self.fw_status.setText(text)

    # Service Function Chain controls (router / legacy pipeline) -------------
    def _build_sfc_row(self) -> QWidget:
        t = self.theme.theme
        w = QFrame(); w.setObjectName("Card")
        w.setStyleSheet(f"QFrame#Card{{background:{t.panel2};border:1px solid {t.line};"
                        f"border-radius:10px;}}")
        lay = QVBoxLayout(w); lay.setContentsMargins(12, 8, 12, 8); lay.setSpacing(5)
        top = QHBoxLayout()
        title = QLabel("Service Function Chain")
        title.setStyleSheet("font-size:13px; font-weight:600;")
        top.addWidget(title)
        top.addSpacing(12)
        top.addWidget(QLabel("classifier:"))
        self.classifier_edit = QLineEdit(self.program.classifier)
        self.classifier_edit.setPlaceholderText("which traffic enters the chain (blank = all)")
        self.classifier_edit.setMaximumWidth(240)
        self.classifier_edit.textChanged.connect(self.program.set_classifier)
        top.addWidget(self.classifier_edit)
        top.addStretch(1)
        self.deploy_btn = QPushButton("  Deploy chain")
        self.deploy_btn.setObjectName("Accent")
        self.deploy_btn.setIcon(icons.icon("send", "#ffffff", 13))
        self.deploy_btn.clicked.connect(self._deploy_chain)
        top.addWidget(self.deploy_btn)
        lay.addLayout(top)
        self.deployed_lbl = QLabel("Deployed: (press Run, then Deploy chain)")
        self.deployed_lbl.setObjectName("Faint")
        self.deploy_status = QLabel(""); self.deploy_status.setObjectName("Muted")
        row2 = QHBoxLayout()
        row2.addWidget(self.deployed_lbl, 1); row2.addWidget(self.deploy_status)
        lay.addLayout(row2)
        return w

    def _deploy_chain(self) -> None:
        """Program the edited chain into the running gRouter over `gpipe` (clear + add each
        real service function in order), then read back the live chain."""
        if self.command_fn is None:
            self._set_deploy_status("not running — press Run to deploy the chain")
            return
        import threading
        self._set_deploy_status("deploying…")
        prog = self.program
        cf, qf = self.command_fn, self.query_fn

        def work():
            listing = ""
            try:
                for c in prog.deploy_commands():
                    cf(c)                       # cf sends `gpipe <c>`
                listing = qf("gpipe list") if qf is not None else cf("list")
            except Exception as e:
                listing = f"(deploy failed: {e})"
            self.chain_ready.emit(listing)
        threading.Thread(target=work, daemon=True).start()

    def _on_chain(self, text: str) -> None:
        from ..domain.modulechain import chain_summary
        summary = "Deployed: " + chain_summary(text)
        if hasattr(self, "fw_deployed"):        # firewall face
            self.fw_deployed.setText(summary)
        if not hasattr(self, "deployed_lbl"):
            return
        self.deployed_lbl.setText(summary)
        ill = self.program.illustrative()
        if ill:
            self._set_deploy_status(f"{len(ill)} illustrative function(s) shown but not deployed")
        else:
            self._set_deploy_status("in sync" if text else "")

    def _set_deploy_status(self, text: str) -> None:
        if hasattr(self, "deploy_status"):
            self.deploy_status.setText(text)

    def _step(self) -> None:
        if not self._trace:
            self._inject(); return
        cur = self._trace[self._step_idx]
        if self._step_idx < len(self._trace) - 1 and "DROP" not in cur:
            self._step_idx += 1
            self._show_step()

    def _reset(self) -> None:
        self._trace = []; self._step_idx = -1
        self._highlight(-1)
        self.trace_lbl.setText("Inject a test packet, then Step through the pipeline.")

    def _show_step(self) -> None:
        self._highlight(self._step_idx)
        st = self.program.stages()[self._step_idx]
        self.trace_lbl.setText(f"{st.label}:  {self._trace[self._step_idx]}")
