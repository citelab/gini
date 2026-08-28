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
    QAbstractItemView, QComboBox, QDialog, QDoubleSpinBox, QFrame, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QPlainTextEdit, QPushButton, QScrollArea, QTableWidget,
    QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from ..domain.router_modules import BASE, CUSTOM, INLINE, MODULE_BY_KEY, RouterProgram
from .theme import ThemeManager, icons


class RouterLab(QDialog):
    worker_done = Signal()      # one live-poll worker finished (queued to the GUI thread)
    flows_ready = Signal(object)  # parsed FlowEntry rows, or None for a FAILED poll
    tablestats_ready = Signal(object)  # OpenFlow table-level stats dict
    routes_ready = Signal(object)  # parsed RouteEntry rows, or None for a FAILED poll
    chain_ready = Signal(str)    # live `gpipe list` output (the deployed service chain)
    delay_ready = Signal(str)    # live `delay show` output (link-delay status)
    qstats_ready = Signal(object)  # (policy, [QueueStat]) from `queue stats`

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
        self.flows_ready.connect(self._on_flows)
        self.tablestats_ready.connect(self._on_table_stats)
        self.routes_ready.connect(self._on_routes)
        self.chain_ready.connect(self._on_chain)
        self.delay_ready.connect(self._set_delay_status)
        self.qstats_ready.connect(self._on_qstats)
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
        self.resize(880, 720 if self.face != "router" else 560)
        self.setStyleSheet(f"QDialog{{background:{t.bg};}}")

        root = QVBoxLayout(self)

        # header -------------------------------------------------------------
        head = QHBoxLayout()
        ic = QLabel(); ic.setPixmap(icons.render_pixmap(icon_key, t.accent_for("blue"), 24))
        title = QLabel(f"  {kind} — {device.name}")
        title.setStyleSheet("font-size:15px; font-weight:600;")
        head.addWidget(ic); head.addWidget(title); head.addStretch(1)
        # The datapath mode is a PROPERTY of the element, not a user toggle: a Router/Firewall is a
        # legacy L3 forwarder; an OVS is an OpenFlow (SDN) switch. Show it as a static label rather
        # than offering an OpenFlow toggle a plain router can't actually be in.
        head.addWidget(QLabel("mode:"))
        mode_text = "OpenFlow · SDN" if self.face == "ovs" else "Legacy L3"
        mode_lbl = QLabel(mode_text)
        mode_lbl.setStyleSheet(
            f"font-weight:600; color:{t.accent_for('teal' if self.face == 'ovs' else 'blue')};")
        head.addWidget(mode_lbl)
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
            adv.addWidget(body_w); adv.addWidget(self._build_sfc_row())
            adv.addWidget(self._build_delay_panel()); adv.addWidget(foot_w)
            self._adv_box.setVisible(False)
            root.addWidget(self._advanced_toggle())
            root.addWidget(self._adv_box, 1)
            root.addWidget(self._build_route_table())
            root.addWidget(self._build_qos_panel())
        else:  # router — the full pipeline is the point
            root.addWidget(body_w, 1)
            root.addWidget(self._build_sfc_row())
            root.addWidget(self._build_delay_panel())
            root.addWidget(self._build_route_table())
            root.addWidget(self._build_qos_panel())
            root.addWidget(foot_w)

        self.worker_done.connect(self._round_worker_done)
        self._rebuild()

        # poll the live table (flows for an OVS, routes + queue stats otherwise) while open
        refresh = self._refresh_flows if self.sdn else self._refresh_router_live
        self._live_timer = QTimer(self)
        self._live_timer.timeout.connect(refresh)
        if query_fn is not None:
            self._live_timer.start(2500)
        refresh()

    # palette ---------------------------------------------------------------
    def _build_palette(self) -> QWidget:
        t = self.theme.theme
        w = QWidget(); w.setObjectName("Sidebar")
        lay = QVBoxLayout(w); lay.setContentsMargins(10, 10, 10, 10); lay.setSpacing(5)

        def header(text: str) -> QLabel:
            label = QLabel(text); label.setObjectName("PanelHead"); return label

        def pal_btn(mt, locked: bool) -> QPushButton:
            # An inline VNF is either a REAL gRouter data-plane function (deploys via the chain) or
            # an illustrative stub shown for learning. Mark previews honestly rather than pretending.
            preview = (not locked) and not mt.real
            suffix = "   · preview" if preview else ""
            b = QPushButton(f"  {mt.label}{suffix}")
            b.setIcon(icons.icon(mt.icon, t.accent_for(mt.accent), 18))
            b.setStyleSheet("text-align:left;" + (f"color:{t.faint};" if preview else ""))
            if locked:
                b.setToolTip(f"{mt.description}\n\nAlways in the pipeline — the router's fixed base.")
                b.setEnabled(False)
            elif preview:
                b.setToolTip(f"{mt.description}\n\nIllustrative — shown in the pipeline to learn the "
                             "shape of a VNF; not yet a deployable data-plane function.")
                b.clicked.connect(lambda _=False, k=mt.key: self._add(k))
            else:
                b.setToolTip(f"{mt.description}\n\nReal native function — deploys into the running "
                             "gRouter via the service chain.")
                b.clicked.connect(lambda _=False, k=mt.key: self._add(k))
            return b

        lay.addWidget(header("Base · required"))
        for mt in BASE:
            lay.addWidget(pal_btn(mt, locked=True))
        # Inline VNFs = the gRouter's in-datapath service functions. Two flavours: built-in NATIVE
        # functions, and ones YOU write (Lua script or a native module).
        lay.addWidget(header("Inline VNFs · native (built-in)"))
        for mt in INLINE:
            lay.addWidget(pal_btn(mt, locked=False))
        lay.addWidget(header("Inline VNFs · you write (Lua / native)"))
        for mt in CUSTOM:
            lay.addWidget(pal_btn(mt, locked=False))
        lay.addStretch(1)
        # keep the palette from stretching the window tall on small screens: scroll it
        sc = QScrollArea(); sc.setWidgetResizable(True); sc.setWidget(w)
        sc.setObjectName("Sidebar"); sc.setFrameShape(QFrame.NoFrame)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sc.setFixedWidth(216)
        return sc

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

    # Friendly label for each editable parameter, keyed by (module type, param key).
    _PARAM_LABEL = {
        ("acl", "deny"):      "deny CIDR",
        ("nat", "ip"):        "source IP",
        ("block", "ip"):      "target IP",
        ("rate", "spec"):     "pps / burst",
        ("classify", "spec"): "match  cidr:dscp",
        ("tap", "path"):      "pcap path",
    }

    def _set_param(self, inst, key: str, text: str) -> None:
        """Live-edit a dropped VNF's parameter; the deploy path and offline trace read it back."""
        inst.params[key] = text.strip()

    def _stage_row(self, st) -> QFrame:
        t = self.theme.theme
        accent = t.accent_for(st.accent)
        f = QFrame(); f.setObjectName("Card"); f.setFixedWidth(380)
        f._accent = accent
        f.setStyleSheet(f"QFrame#Card{{background:{t.panel2};border:1px solid {t.line};"
                        f"border-radius:10px;}}")
        outer = QVBoxLayout(f); outer.setContentsMargins(11, 8, 8, 8); outer.setSpacing(6)

        top = QHBoxLayout(); top.setSpacing(8)
        iname = {"ingress": "chevron_right", "egress": "chevron_right",
                 "mode": "controller"}.get(st.kind)
        if iname is None:
            iname = MODULE_BY_KEY[st.key].icon if st.key in MODULE_BY_KEY else "dot"
        icon = QLabel(); icon.setPixmap(icons.render_pixmap(iname, accent, 16))
        name = QLabel(st.label); name.setStyleSheet("font-weight:500;")
        tag = QLabel(st.kind if not st.locked else f"{st.kind} · locked")
        tag.setObjectName("Faint")
        top.addWidget(icon); top.addWidget(name); top.addStretch(1); top.addWidget(tag)
        if st.kind == "inline" and st.index is not None:
            for sym, fn in (("▲", lambda i=st.index: self._move(i, -1)),
                            ("▼", lambda i=st.index: self._move(i, 1)),
                            ("✕", lambda i=st.index: self._remove(i))):
                btn = QPushButton(sym); btn.setFixedWidth(26)
                btn.clicked.connect(lambda _=False, fn=fn: fn())
                top.addWidget(btn)
        outer.addLayout(top)

        # Editable parameters for an inline VNF: one field per param, written straight into
        # inst.params (deploy_commands() and the offline trace read them back). Editing here
        # is why dropping an ACL and then typing a CIDR actually re-points the filter.
        if st.kind == "inline" and st.index is not None:
            inst = self.program.inline[st.index]
            if inst.params:
                pr = QHBoxLayout(); pr.setSpacing(6); pr.setContentsMargins(24, 0, 0, 0)
                for key in inst.params:
                    lbl = QLabel(self._PARAM_LABEL.get((inst.type_key, key), key) + ":")
                    lbl.setObjectName("Faint")
                    edit = QLineEdit(str(inst.params[key])); edit.setMinimumWidth(150)
                    edit.textChanged.connect(
                        lambda text, i=inst, k=key: self._set_param(i, k, text))
                    pr.addWidget(lbl); pr.addWidget(edit)
                pr.addStretch(1)
                outer.addLayout(pr)
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

    def _inject(self, dst: str = "10.0.2.10") -> None:
        # Walk a test packet through the composed pipeline using the offline model: the
        # current stage lights up and reports its verdict, Step advances one stage, Reset
        # clears. This is the dependable teaching path and works whether or not a topology
        # is running; the live router is programmed separately via Deploy chain.
        self._trace = self.program.trace(dst)
        self._step_idx = 0
        self._show_step()

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

    def _emit(self, signal, *args) -> bool:
        """Emit from a worker thread, tolerating this dialog having been destroyed meanwhile.

        A query can sit in `docker compose exec` for up to 12s. If the window is closed and
        retired in that time, MainWindow._retire_lab deletes the C++ object and the worker's emit
        lands on nothing:

            RuntimeError: Signal source has been deleted

        which killed the thread with an unhandled traceback on the console. There is no reliable
        way to ask from another thread whether a QObject is still alive — checking and then
        emitting is a race — so the emit itself is the check. Returns False if the dialog is gone,
        which lets a worker stop early instead of running the rest of its queries for nobody.
        """
        try:
            signal.emit(*args)
            return True
        except RuntimeError:
            return False

    def _refresh_flows(self, counted: bool = True) -> None:
        """Read the live OpenFlow table. Driven only by the poll timer, so `counted` defaults True.

        `counted` marks this call as part of a timed poll round. Ad-hoc refreshes (after
        applying a policy, say) pass False so they do not decrement the round's worker
        count and let the next tick start while the round's own workers are still out.
        """
        if self.query_fn is None:
            self._set_flow_status("not running — press Run, then reopen to see live flows")
            return
        if not self._round_begin(1):
            return                              # previous round still out; skip this tick
        import threading
        self._set_flow_status("reading flow table…")
        qf = self.query_fn

        def work():
            from ..domain.flowtable import flows, parse_table_stats
            from ..domain.routing_model import query_failed
            # element_query NEVER raises: on a timeout it RETURNS "(query failed: …)". So the
            # old `try/except` caught nothing, `flows("(query failed…)")` parsed to [], the
            # table blanked, and the event log emitted a phantom "expired" for every rule
            # that was in fact still installed. That is the "flows disappearing" students on
            # slower machines reported (a timeout, not a real teardown). Detect it and carry
            # the last table forward instead of blanking.
            entry = qf("openflow entry all")
            if query_failed(entry):
                self._emit(self.flows_ready, None)      # FAILED poll — do not blank
                if counted:
                    self._emit(self.worker_done)
                return
            rows, stats = [], {}
            try:
                rows = flows(entry, qf("openflow stats entry all"))
                stats = parse_table_stats(qf("openflow stats table"))
            except Exception:
                pass
            if not self._emit(self.flows_ready, rows):
                return
            self._emit(self.tablestats_ready, stats)
            if counted:
                self._emit(self.worker_done)
        threading.Thread(target=work, daemon=True).start()

    def _on_flows(self, rows) -> None:
        if rows is None:
            # The switch did not answer this poll (a timed-out query). Keep the last table
            # on screen rather than blanking, and — crucially — do NOT fold an empty
            # snapshot into the event log: that is what turned a slow poll into a screen
            # full of "－ expired" for rules that never actually left the switch.
            self._set_flow_status("switch didn't answer this poll — showing last table")
            return
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
        lay = QVBoxLayout(w); lay.setContentsMargins(12, 8, 12, 10); lay.setSpacing(6)

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

        head = QHBoxLayout()
        title = self._chevron("Routing table", self.route_table, expanded=True)
        self.route_status = QLabel("…"); self.route_status.setObjectName("Muted")
        refresh = QPushButton("  Refresh"); refresh.setIcon(icons.icon("play", t.muted, 13))
        refresh.clicked.connect(self._refresh_routes)
        head.addWidget(title); head.addStretch(1)
        head.addWidget(self.route_status); head.addSpacing(10); head.addWidget(refresh)
        lay.addLayout(head)
        lay.addWidget(self.route_table)
        return w

    def _refresh_routes(self, counted: bool = False) -> None:
        """Read the live route table.

        `counted` marks this call as part of a timed poll round. Ad-hoc refreshes (after
        applying a policy, say) pass False so they do not decrement the round's worker
        count and let the next tick start while the round's own workers are still out.
        """
        if self.query_fn is None:
            self._set_route_status("not running — press Run to see the live route table")
            return
        import threading
        self._set_route_status("reading route table…")
        qf = self.query_fn

        def work():
            from ..domain.routetable import parse_routes
            from ..domain.routing_model import query_failed
            # Same trap as the flow table: a timed-out `route show` comes back as
            # "(query failed: …)", which parse_routes reads as zero routes and the panel
            # renders "no routes" — the "routes disappearing" report. Carry the last table
            # forward on a failed read instead of blanking.
            raw = qf("route show")
            if query_failed(raw):
                self._emit(self.routes_ready, None)     # FAILED poll — do not blank
                if counted:
                    self._emit(self.worker_done)
                return
            rows, chain = [], ""
            try:
                rows = parse_routes(raw)
                chain = qf("gpipe list")     # the live deployed service chain
            except Exception:
                pass
            if not self._emit(self.routes_ready, rows):
                return
            self._emit(self.chain_ready, chain)
            if counted:
                self._emit(self.worker_done)
        threading.Thread(target=work, daemon=True).start()

    def _on_routes(self, rows) -> None:
        if rows is None:
            self._set_route_status("router didn't answer this poll — showing last table")
            return
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

    # Traffic & QoS (classifier + weighted queues + scheduler) --------------
    # -- live polling ------------------------------------------------------- #
    # Every tick of the 2.5s timer fires SEVERAL `docker compose exec` calls — routes, the gpipe
    # chain and queue stats for a router; three openflow queries for an OVS. Each one is a whole
    # docker CLI invocation plus a round trip to the gRouter's control socket.
    #
    # On a fast machine a round finishes well inside the interval. On a slow one it does not, and
    # without a guard the timer starts another round on top of the last: threads pile up
    # unboundedly, every round re-sets the status to "reading…", and the spinner never clears —
    # the Lab looks hung while the router is perfectly healthy. Worse, the pile-up is
    # self-sustaining, because the extra execs are themselves what make each round slow.
    #
    # So: one round at a time, and if a round overruns the interval, back the interval off to fit.
    MIN_POLL_MS = 2500
    MAX_POLL_MS = 20000

    def _round_begin(self, n: int) -> bool:
        """Claim the poll for `n` workers. False if a round is still running."""
        import time
        if getattr(self, "_inflight", 0) > 0:
            return False
        self._inflight = n
        self._round_started = time.monotonic()
        return True

    def _round_worker_done(self) -> None:
        """One worker finished (queued to the GUI thread, so this is not a race)."""
        import time
        self._inflight = max(0, getattr(self, "_inflight", 0) - 1)
        if self._inflight or not getattr(self, "_live_timer", None):
            return
        took_ms = (time.monotonic() - getattr(self, "_round_started", 0)) * 1000
        # Leave the machine half the wall clock to itself rather than polling flat out.
        want = int(min(self.MAX_POLL_MS, max(self.MIN_POLL_MS, took_ms * 2)))
        if want != self._live_timer.interval():
            self._live_timer.setInterval(want)

    # -- the poll must not outlive the window ------------------------------- #
    # This dialog is parented to MainWindow, so Qt keeps it alive after it is closed and after
    # main_window rebinds self._router_lab to a newer one. Nothing stopped the timer, so every
    # Router Lab ever opened left a PERMANENT background poller behind, each firing three
    # `docker compose exec` calls every 2.5s at a router nobody was looking at.
    #
    # That is what the py-spy dump showed: four live query threads for what should be one round,
    # and two still running after the window was closed. The main thread was idle throughout —
    # the app was not stalled, the machine was saturated. On Linux the window manager then paints
    # a busy cursor because the app misses its _NET_WM_PING deadlines, which is the "spinner" that
    # kept appearing while the route table filled in perfectly well. It got worse the longer a
    # session ran, and a slow box crossed the threshold first.
    def hideEvent(self, e):                  # noqa: N802 - Qt naming
        """Stop polling whenever the window stops being visible.

        hideEvent alone, deliberately — closing a dialog hides it, so a closeEvent override that
        also stopped the timer was dead code: removing it changed no test. Hiding is the broader
        condition anyway, and it covers being hidden without a close.
        """
        self._live_timer.stop()
        super().hideEvent(e)

    def showEvent(self, e):                  # noqa: N802 - Qt naming
        if self.query_fn is not None and not self._live_timer.isActive():
            self._live_timer.start(max(self.MIN_POLL_MS, self._live_timer.interval()))
        super().showEvent(e)

    def _refresh_router_live(self) -> None:
        """Live poll for the router/firewall face: routes and per-queue stats."""
        if not self._round_begin(2):
            return                              # previous round still out; skip this tick
        self._refresh_routes(counted=True)
        self._refresh_qstats(counted=True)

    def _build_qos_panel(self) -> QWidget:
        t = self.theme.theme
        w = QFrame(); w.setObjectName("Card")
        w.setStyleSheet(f"QFrame#Card{{background:{t.panel2};border:1px solid {t.line};"
                        f"border-radius:10px;}}")
        lay = QVBoxLayout(w); lay.setContentsMargins(12, 8, 12, 10); lay.setSpacing(6)

        cols = ["Queue", "Qdisc", "Weight", "Backlog", "Fwd pkts", "Drop pkts",
                "Fwd bytes", "Share"]
        self.qos_table = QTableWidget(0, len(cols))
        self.qos_table.setHorizontalHeaderLabels(cols)
        self.qos_table.verticalHeader().setVisible(False)
        self.qos_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.qos_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.qos_table.setMinimumHeight(120)
        hh = self.qos_table.horizontalHeader()
        for i in range(len(cols)):
            hh.setSectionResizeMode(i, QHeaderView.Stretch if i == 0
                                    else QHeaderView.ResizeToContents)

        head = QHBoxLayout()
        title = self._chevron("Traffic & QoS", self.qos_table, expanded=True)
        head.addWidget(title); head.addStretch(1)
        head.addWidget(QLabel("scheduler:"))
        self.qos_policy = QComboBox()
        self.qos_policy.addItem("Round robin", "rr")
        self.qos_policy.addItem("Deficit RR (weighted)", "drr")
        self.qos_policy.currentIndexChanged.connect(self._set_scheduler)
        head.addWidget(self.qos_policy)
        self.qos_status = QLabel("…"); self.qos_status.setObjectName("Muted")
        refresh = QPushButton("  Refresh"); refresh.setIcon(icons.icon("play", t.muted, 13))
        refresh.clicked.connect(self._refresh_qstats)
        head.addSpacing(10); head.addWidget(self.qos_status)
        head.addSpacing(10); head.addWidget(refresh)
        lay.addLayout(head)

        # add a class + its weighted queue in one go
        add = QHBoxLayout()
        self.qos_name = QLineEdit(); self.qos_name.setPlaceholderText("class (e.g. flowA)")
        self.qos_src = QLineEdit(); self.qos_src.setPlaceholderText("source IP (e.g. 10.0.1.10)")
        self.qos_weight = QDoubleSpinBox(); self.qos_weight.setRange(0.1, 100.0)
        self.qos_weight.setSingleStep(1.0); self.qos_weight.setValue(1.0)
        self.qos_weight.setPrefix("w ")
        self.qos_qdisc = QComboBox(); self.qos_qdisc.addItems(["taildrop", "red"])
        addbtn = QPushButton("  Add class + queue")
        addbtn.clicked.connect(self._add_qos_class)
        for wdg in (self.qos_name, self.qos_src, self.qos_weight, self.qos_qdisc, addbtn):
            add.addWidget(wdg)
        lay.addLayout(add)
        lay.addWidget(self.qos_table)
        return w

    def _set_scheduler(self) -> None:
        if self.command_fn is None:
            self._set_qos_status("not running — press Run to change the scheduler")
            return
        pol = self.qos_policy.currentData()
        try:
            self.command_fn(f"spolicy set {pol}")
            self._set_qos_status(f"scheduler set to {pol}")
        except Exception:
            self._set_qos_status("could not set scheduler")
        self._refresh_qstats()

    def _add_qos_class(self) -> None:
        if self.command_fn is None:
            self._set_qos_status("not running — press Run to add a class")
            return
        name = self.qos_name.text().strip()
        src = self.qos_src.text().strip()
        weight = self.qos_weight.value()
        qdisc = self.qos_qdisc.currentText()
        if not name:
            self._set_qos_status("enter a class name")
            return
        try:
            cmd = f"class add {name}"
            if src:
                cmd += f" -src ( -net {src} )"
            self.command_fn(cmd)
            if qdisc == "red":                    # RED must exist before a queue uses it
                self.command_fn("qdisc add red -min 0.3 -max 0.8 -pmax 0.1")
            self.command_fn(f"queue add {name} {qdisc} -weight {weight:g} -size 50")
            self._set_qos_status(f"added class {name} (weight {weight:g}, {qdisc})")
        except Exception:
            self._set_qos_status("could not add class/queue")
        self._refresh_qstats()

    def _refresh_qstats(self, counted: bool = False) -> None:
        """Read live per-queue stats.

        `counted` marks this call as part of a timed poll round. Ad-hoc refreshes (after
        applying a policy, say) pass False so they do not decrement the round's worker
        count and let the next tick start while the round's own workers are still out.
        """
        if self.query_fn is None:
            self._set_qos_status("not running — press Run to see live queue stats")
            return
        import threading
        qf = self.query_fn

        def work():
            from ..domain.qos import parse_queue_stats
            from ..domain.routing_model import query_failed
            raw = qf("queue stats")
            if query_failed(raw):
                self._emit(self.qstats_ready, None)     # FAILED poll — keep the last table
                if counted:
                    self._emit(self.worker_done)
                return
            payload = ("", [])
            try:
                payload = parse_queue_stats(raw)
            except Exception:
                pass
            self._emit(self.qstats_ready, payload)
            if counted:
                self._emit(self.worker_done)
        threading.Thread(target=work, daemon=True).start()

    def _on_qstats(self, payload) -> None:
        if payload is None:                             # a failed poll — carry the last table
            self._set_qos_status("router didn't answer this poll — showing last stats")
            return
        policy, rows = payload
        if policy and hasattr(self, "qos_policy"):
            self.qos_policy.blockSignals(True)
            idx = self.qos_policy.findData(policy)
            if idx >= 0:
                self.qos_policy.setCurrentIndex(idx)
            self.qos_policy.blockSignals(False)
        total = sum(r.fwd_bytes for r in rows)
        self.qos_table.setRowCount(len(rows))
        for r, q in enumerate(rows):
            cells = [q.name, q.qdisc, f"{q.weight:g}", str(q.backlog),
                     str(q.fwd_pkts), str(q.drop_pkts), str(q.fwd_bytes),
                     f"{q.share_pct(total):.0f}%"]
            for c, val in enumerate(cells):
                self.qos_table.setItem(r, c, QTableWidgetItem(val))
        n = len(rows)
        self._set_qos_status(f"{n} queue{'s' if n != 1 else ''}" if n else "no queues")

    def _set_qos_status(self, text: str) -> None:
        if hasattr(self, "qos_status"):
            self.qos_status.setText(text)

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
            self._emit(self.chain_ready, listing)
        threading.Thread(target=work, daemon=True).start()

    def _set_fw_status(self, text: str) -> None:
        if hasattr(self, "fw_status"):
            self.fw_status.setText(text)

    def _chevron(self, title: str, target: QWidget, expanded: bool) -> QPushButton:
        """A flat header button that collapses/expands `target` (to save vertical space)."""
        btn = QPushButton(); btn.setCheckable(True); btn.setChecked(expanded); btn.setFlat(True)
        btn.setStyleSheet("text-align:left; font-size:13px; font-weight:600; border:none; padding:0;")

        def sync(on):
            target.setVisible(on)
            btn.setText(("  ▾  " if on else "  ▸  ") + title)
        btn.toggled.connect(sync); sync(expanded)
        return btn

    # Link delay (ingress / egress holding queues) --------------------------
    def _delay_spin(self, val: float, hi: float, step: float, dec: int, suffix: str) -> QDoubleSpinBox:
        s = QDoubleSpinBox()
        s.setRange(0.0, hi); s.setSingleStep(step); s.setDecimals(dec)
        s.setValue(val); s.setSuffix(suffix); s.setMaximumWidth(96)
        return s

    def _delay_row(self, lay, name: str, saved: str):
        """One side's controls (base / jitter / correlation), seeded from a saved string."""
        base = jit = corr = 0.0
        try:
            parts = (saved or "").split()
            if parts: base = float(parts[0])
            if len(parts) > 1: jit = float(parts[1])
            if len(parts) > 2: corr = float(parts[2])
        except ValueError:
            pass
        row = QHBoxLayout()
        tag = QLabel(name); tag.setMinimumWidth(64); tag.setStyleSheet("font-weight:600;")
        row.addWidget(tag)
        row.addWidget(QLabel("base"));   b = self._delay_spin(base, 5000, 5, 0, " ms")
        row.addWidget(b)
        row.addWidget(QLabel("jitter")); j = self._delay_spin(jit, 1000, 1, 0, " ms")
        row.addWidget(j)
        row.addWidget(QLabel("corr"));   c = self._delay_spin(corr, 0.99, 0.05, 2, "")
        row.addWidget(c)
        row.addStretch(1)
        lay.addLayout(row)
        return b, j, c

    def _build_delay_panel(self) -> QWidget:
        t = self.theme.theme
        w = QFrame(); w.setObjectName("Card")
        w.setStyleSheet(f"QFrame#Card{{background:{t.panel2};border:1px solid {t.line};"
                        f"border-radius:10px;}}")
        lay = QVBoxLayout(w); lay.setContentsMargins(12, 8, 12, 10); lay.setSpacing(6)

        # collapsible body (hint + the two parameter rows) — collapsed by default to save height
        body = QWidget()
        bl = QVBoxLayout(body); bl.setContentsMargins(0, 6, 0, 0); bl.setSpacing(6)
        hint = QLabel("Hold every packet on the way in (ingress) or out (egress) to model link "
                      "latency. Jitter wanders with the correlation, order preserved. base 0 = off.")
        hint.setObjectName("Faint"); hint.setWordWrap(True)
        bl.addWidget(hint)
        props = getattr(self.device, "properties", {}) or {}
        self.di_base, self.di_jit, self.di_corr = self._delay_row(bl, "ingress", props.get("DelayIngress", ""))
        self.de_base, self.de_jit, self.de_corr = self._delay_row(bl, "egress", props.get("DelayEgress", ""))

        head = QHBoxLayout()
        title = self._chevron("Link delay", body, expanded=False)
        self.delay_status = QLabel(""); self.delay_status.setObjectName("Muted")
        apply = QPushButton("  Apply"); apply.setObjectName("Accent")
        apply.setIcon(icons.icon("send", "#ffffff", 13)); apply.clicked.connect(self._apply_delay)
        clear = QPushButton("  Clear"); clear.clicked.connect(self._clear_delay)
        head.addWidget(title); head.addStretch(1)
        head.addWidget(self.delay_status); head.addSpacing(10)
        head.addWidget(clear); head.addWidget(apply)
        lay.addLayout(head)
        lay.addWidget(body)
        return w

    @staticmethod
    def _delay_cmd(side: str, base: float, jit: float, corr: float) -> str:
        if base <= 0 and jit <= 0:
            return f"delay {side} off"
        return f"delay {side} {base:.0f} {jit:.0f} {corr:.2f}"

    def _apply_delay(self) -> None:
        ing = f"{self.di_base.value():.0f} {self.di_jit.value():.0f} {self.di_corr.value():.2f}"
        egr = f"{self.de_base.value():.0f} {self.de_jit.value():.0f} {self.de_corr.value():.2f}"
        props = getattr(self.device, "properties", None)
        if props is not None:                       # persist onto the element
            props["DelayIngress"] = ing if (self.di_base.value() or self.di_jit.value()) else ""
            props["DelayEgress"]  = egr if (self.de_base.value() or self.de_jit.value()) else ""
        qf = self.query_fn
        if qf is None:
            self._set_delay_status("not running — press Run, then Apply")
            return
        import threading
        self._set_delay_status("applying…")
        cmds = [self._delay_cmd("ingress", self.di_base.value(), self.di_jit.value(), self.di_corr.value()),
                self._delay_cmd("egress",  self.de_base.value(),  self.de_jit.value(),  self.de_corr.value())]

        def work():
            out = ""
            try:
                for c in cmds:
                    qf(c)
                out = qf("delay show")
            except Exception as e:
                out = f"(apply failed: {e})"
            self._emit(self.delay_ready, out.strip() or "applied")
        threading.Thread(target=work, daemon=True).start()

    def _clear_delay(self) -> None:
        for s in (self.di_base, self.di_jit, self.di_corr,
                  self.de_base, self.de_jit, self.de_corr):
            s.setValue(0.0)
        props = getattr(self.device, "properties", None)
        if props is not None:
            props["DelayIngress"] = ""; props["DelayEgress"] = ""
        qf = self.query_fn
        if qf is None:
            self._set_delay_status("cleared (not running)")
            return
        import threading
        self._set_delay_status("clearing…")

        def work():
            try:
                qf("delay clear")
            except Exception as e:
                self._emit(self.delay_ready, f"(clear failed: {e})"); return
            self._emit(self.delay_ready, "cleared")
        threading.Thread(target=work, daemon=True).start()

    def _set_delay_status(self, text: str) -> None:
        if hasattr(self, "delay_status"):
            self.delay_status.setText(text.splitlines()[0] if text else "")

    # Service Function Chain controls (router / legacy pipeline) -------------
    def _build_sfc_row(self) -> QWidget:
        t = self.theme.theme
        w = QFrame(); w.setObjectName("Card")
        w.setStyleSheet(f"QFrame#Card{{background:{t.panel2};border:1px solid {t.line};"
                        f"border-radius:10px;}}")
        lay = QVBoxLayout(w); lay.setContentsMargins(12, 8, 12, 8); lay.setSpacing(5)
        top = QHBoxLayout()
        title = QLabel("Inline VNF chain")
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
            self._emit(self.chain_ready, listing)
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
