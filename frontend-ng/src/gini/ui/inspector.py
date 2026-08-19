"""Inspector — design properties + compiler addressing + live runtime state.

Properties stay editable. Interfaces/Routes show the IP/MAC/subnet/gateway the
compiler assigns (so addressing is visible before anything runs). The Live tab queries
the element's control socket when the topology is running. A "Log in" button opens the
device's terminal/console.
"""
from __future__ import annotations

import threading

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
    QScrollArea, QSlider, QTabWidget, QVBoxLayout, QWidget,
)

from ..agent.api import GiniAPI
from ..app import AppContext
from .theme import ThemeManager, icons
from .theme.manager import scale_css as _scss


_FUNCTION_STARTER = (
    "def handle(event, context):\n"
    "    # event:   method, path, query, headers, body, source\n"
    "    # context: function_name, invocation_id, remaining_ms\n"
    "    name = event.get(\"body\") or \"world\"\n"
    "    return {\"statusCode\": 200, \"body\": f\"Hello, {name}!\"}\n"
)


def _optnum(v):
    """A metric value -> float, or None (so the chart line skips missing samples)."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class Inspector(QWidget):
    live_ready = Signal(str)
    metrics_ready = Signal(object)               # (cpu%, mem MiB, KB/s, latency) tuple

    def __init__(self, ctx: AppContext, api: GiniAPI, theme: ThemeManager) -> None:
        super().__init__()
        self.setObjectName("Inspector")
        self.ctx = ctx
        self.api = api
        self.theme = theme
        self._device_id: str | None = None
        self.query_fn = None        # set by MainWindow: (device_name, command) -> str
        # set by MainWindow: () -> the relay's view of every real GINI32 board.
        # A board has no container to exec into, so its Live tab is served from here.
        self.board_status_fn = None
        # set by MainWindow: (action, board_name) -> bool. claim/release/blink.
        self.board_action_fn = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        # header
        header = QWidget()
        hl = QVBoxLayout(header)
        hl.setContentsMargins(14, 12, 14, 8)
        top = QHBoxLayout()
        self.icon_lbl = QLabel()
        top.addWidget(self.icon_lbl)
        top.addStretch(1)
        self.login_btn = QPushButton(" Log in")
        self.login_btn.setObjectName("Accent")
        self.login_btn.setIcon(icons.icon("link", "#ffffff", 14))
        self.login_btn.clicked.connect(self._login)
        self.login_btn.hide()
        top.addWidget(self.login_btn)
        hl.addLayout(top)
        self.name_lbl = QLabel("No selection")
        self.name_lbl.setStyleSheet(_scss("font-size:15px; font-weight:600;"))
        self.type_lbl = QLabel("Select a device to edit it")
        self.type_lbl.setObjectName("Faint")
        hl.addWidget(self.name_lbl)
        hl.addWidget(self.type_lbl)
        # "what does this actually run?" — the backing image + tools, so users don't guess
        self.runs_lbl = QLabel("")
        self.runs_lbl.setObjectName("Muted")
        self.runs_lbl.setWordWrap(True)
        self.runs_lbl.setVisible(False)
        hl.addWidget(self.runs_lbl)
        root.addWidget(header)

        self.tabs = QTabWidget()
        # properties
        self.props_host = QWidget()
        self.props_form = QFormLayout(self.props_host)
        self.props_form.setContentsMargins(14, 10, 14, 14)
        self.props_form.setSpacing(8)
        ps = QScrollArea(); ps.setWidgetResizable(True); ps.setWidget(self.props_host)
        # interfaces (a rebuilt widget so IPs can be edited in manual mode) / routes / live
        self.ifaces_host = QWidget()
        self.ifaces_lay = QVBoxLayout(self.ifaces_host)
        self.ifaces_lay.setContentsMargins(14, 12, 14, 12)
        self.ifaces_lay.setSpacing(8)
        self.ifaces_lay.setAlignment(Qt.AlignTop)
        self._ifaces_scroll = QScrollArea()
        self._ifaces_scroll.setWidgetResizable(True)
        self._ifaces_scroll.setWidget(self.ifaces_host)
        self.routes = self._scroll_label()
        self.live = QPlainTextEdit(); self.live.setReadOnly(True)
        self.live.setObjectName("Console")
        from .live_metrics import LiveMetrics
        self.metrics = LiveMetrics(self.theme)   # real-time CPU/mem plots for containers
        self.metrics.setVisible(False)
        self._live_host = QWidget(); lv = QVBoxLayout(self._live_host)
        lv.setContentsMargins(12, 10, 12, 12)
        self.refresh_btn = QPushButton("Refresh live state")
        self.refresh_btn.clicked.connect(self._refresh_live)
        lv.addWidget(self.refresh_btn)
        lv.addWidget(self.metrics, 1)
        self.kpis_lbl = QLabel("")               # cloud-fabric app KPIs (req/s, queue, …)
        self.kpis_lbl.setObjectName("DashChips")
        self.kpis_lbl.setTextFormat(Qt.RichText)
        self.kpis_lbl.setWordWrap(True)
        self.kpis_lbl.setVisible(False)
        lv.addWidget(self.kpis_lbl)
        lv.addWidget(self.live, 1)
        live_host = self._live_host

        self.tabs.addTab(ps, "Properties")
        self.tabs.addTab(self._ifaces_scroll, "Interfaces")
        self.tabs.addTab(self._wrap(self.routes), "Routes")
        self.tabs.addTab(live_host, "Live")
        root.addWidget(self.tabs, 1)

        ctx.bus.selection_changed.connect(self._on_select)
        ctx.bus.device_changed.connect(self._on_changed)
        ctx.bus.addressing_changed.connect(self._rebuild)
        # The board panel reads hardware state ONCE, when it is built. Without this the
        # Inspector kept describing a board that had been unplugged — claim, MAC and all
        # — which is the most misleading thing on the screen, because every other pane
        # had already noticed it was gone.
        ctx.bus.boards_changed.connect(self._on_boards_changed)
        ctx.bus.function_invoke_result.connect(self._on_invoke_result)
        ctx.bus.rider_ran.connect(self._on_rider_ran)
        self._invoke_result = None               # the Function Invoke panel's result widget
        self._invoke_btn = None                  # the Invoke button (enabled only while running)
        self._deploy_btn = None                  # the Deploy button (enabled only while running)
        self.live_ready.connect(self.live.setPlainText)

        # real-time metrics: stats_fn(name) -> {"cpu","mem_used"} | None (set by MainWindow)
        from PySide6.QtCore import QTimer
        self.stats_fn = None
        self.stats_all_fn = None                 # () -> {svc: {cpu,mem_used,net_bytes}}
        self._live_running = False
        self._polling = False
        self._fabric: dict = {}                  # latest cloud-fabric snapshot
        self._k8s: dict = {}                     # latest kubernetes metrics snapshot
        self._live_metrics_on = False
        self._hist: dict = {}                    # svc -> {cpu,mem,thru,lat: deque} per element
        self._prev_net: dict = {}                # svc -> (net_bytes, monotonic) for KB/s
        self._live_timer = QTimer(self)
        self._live_timer.setInterval(1500)
        self._live_timer.timeout.connect(self._poll_metrics)
        self.metrics_ready.connect(self._on_metrics)
        self.tabs.currentChanged.connect(lambda _i: self._update_live_mode())

    # helpers --------------------------------------------------------------- #
    @staticmethod
    def _scroll_label() -> QLabel:
        lbl = QLabel("—")
        lbl.setWordWrap(True)
        lbl.setAlignment(Qt.AlignTop)
        lbl.setTextFormat(Qt.RichText)
        lbl.setContentsMargins(14, 12, 14, 12)
        return lbl

    @staticmethod
    def _wrap(label: QLabel) -> QScrollArea:
        s = QScrollArea(); s.setWidgetResizable(True); s.setWidget(label)
        return s

    def _login(self) -> None:
        if self._device_id:
            self.ctx.bus.device_activated.emit(self._device_id)

    def _login_allowed(self) -> bool:
        """Log in needs the lab running — except a Router, whose Router Lab opens offline."""
        from ..services.compiler import _role
        d = self.ctx.topology.devices.get(self._device_id)
        # A GINI32 is a board on a desk, not a container: there is no shell to open.
        # Offering one would promise something that cannot exist.
        if d is not None and d.type_key == "gini32":
            return False
        if self._live_running:
            return True
        return d is not None and _role(d.type_key) == "router"

    def _login_hint(self) -> str:
        if self._login_allowed():
            return ""
        d = self.ctx.topology.devices.get(self._device_id)
        if d is not None and d.type_key == "gini32":
            return ("This is a real board, not a container — there is no shell to open.\n"
                    "Use its serial console (`gini32 monitor`), or the Live tab here.")
        return "Run the topology first"

    def _hide_container_tabs(self, d) -> None:
        """Interfaces and Routes describe an emulated node's stack. A GINI32 has
        neither — its addressing lives in the Live tab, reported by the hardware."""
        board = d is not None and d.type_key == "gini32"
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i) in ("Interfaces", "Routes"):
                self.tabs.setTabVisible(i, not board)

    # selection ------------------------------------------------------------- #
    def _on_select(self, device_id) -> None:
        self._device_id = device_id
        self._rebuild()
        self._update_live_mode()

    def _on_changed(self, device_id) -> None:
        # Defer the rebuild: this fires from inside an editor's own signal (slider drag,
        # combo change, line-edit commit). Rebuilding now would delete that widget while
        # Qt is still using it -> segfault. singleShot(0) runs it after the signal unwinds.
        if device_id == self._device_id:
            QTimer.singleShot(0, self._rebuild)

    def _on_boards_changed(self) -> None:
        """A real board came online or went quiet — refresh, if we are showing one.

        Deferred for the same reason as _on_changed: this arrives on a poll tick that may
        be nested inside a widget's own signal, and rebuilding the form under Qt's feet
        deletes a widget it is still using. Restricted to gini32 so a board appearing
        does not disturb someone editing an unrelated element's properties.
        """
        d = self.ctx.topology.devices.get(self._device_id) if self._device_id else None
        if d is not None and d.type_key == "gini32":
            QTimer.singleShot(0, self._rebuild)

    def _clear_form(self) -> None:
        while self.props_form.rowCount():
            self.props_form.removeRow(0)

    def _rebuild(self) -> None:
        self._clear_form()
        self._invoke_result = None               # cleared widgets must not be touched
        self._invoke_btn = None
        self._deploy_btn = None
        self._rider_btn = None
        t = self.theme.theme
        if not self._device_id or self._device_id not in self.ctx.topology.devices:
            self.name_lbl.setText("No selection")
            self.type_lbl.setText("Select a device to edit it")
            self.icon_lbl.clear()
            self.login_btn.hide()
            self._clear_layout(self.ifaces_lay)
            self.routes.setText("—")
            return
        d = self.ctx.topology.devices[self._device_id]
        dt = d.type
        accent = t.accent_for(dt.accent.value)
        self.icon_lbl.setPixmap(icons.render_pixmap(dt.icon, accent, size=30))
        self.name_lbl.setText(d.name)
        self.type_lbl.setText(f"{dt.label} · {dt.category.value}")
        self.login_btn.setVisible(dt.key not in
                                  ("vpc", "cloud_subnet", "region",
                                   "k8s_cluster", "instance_group", "pod"))
        self.login_btn.setEnabled(self._login_allowed())   # needs the lab up (Router excepted)
        self.login_btn.setToolTip(self._login_hint())
        self._hide_container_tabs(d)      # a board has no stack of ours to show
        note = self._runtime_note(d)
        self.runs_lbl.setText(note)
        self.runs_lbl.setVisible(bool(note))

        # size tier (resizable elements) — mirrors the on-node + / - stepper
        from ..domain import pricing
        if pricing.resizable(dt.key):
            sizebox = QComboBox()
            for lvl in range(pricing.SIZE_MIN, pricing.SIZE_MAX + 1):
                lab, vcpu, _mem, mult = pricing.size_tier(lvl)
                sizebox.addItem(f"{lab} · {vcpu:g} vCPU · ×{mult} cost", lvl)
            sizebox.blockSignals(True)
            sizebox.setCurrentIndex(pricing.size_level(getattr(d, "size", 1))
                                    - pricing.SIZE_MIN)
            sizebox.blockSignals(False)
            sizebox.currentIndexChanged.connect(
                lambda _i, sb=sizebox: self._commit_size(sb.currentData()))
            self.props_form.addRow("Size", sizebox)

        # Kubernetes live knobs: Pod replicas (kubectl scale) + HPA target CPU (patch)
        if dt.key == "pod":
            self.props_form.addRow("Replicas", self._int_slider("Replicas", 0, 10, 2))
        if dt.key == "instance_group":
            self.props_form.addRow("Target CPU %", self._int_slider("TargetCPU", 1, 100, 60))

        # Load Generator gets a live rate throttle (drives Fortio while running)
        if dt.key == "load_generator":
            row = QWidget(); rl = QHBoxLayout(row); rl.setContentsMargins(0, 0, 0, 0)
            sl = QSlider(Qt.Horizontal); sl.setRange(0, 1000)
            try:
                sl.setValue(int(float(d.properties.get("QPS", "100"))))
            except ValueError:
                sl.setValue(100)
            vlbl = QLabel(str(sl.value())); vlbl.setMinimumWidth(34)
            sl.valueChanged.connect(lambda v, lb=vlbl: lb.setText(str(v)))
            sl.sliderReleased.connect(lambda s=sl: self._commit("QPS", str(s.value())))
            rl.addWidget(sl, 1); rl.addWidget(vlbl)
            self.props_form.addRow("Rate (req/s)", row)

        choices = dt.property_choices
        for key, value in d.properties.items():
            if (dt.key, key) in (("load_generator", "QPS"), ("pod", "Replicas"),
                                 ("instance_group", "TargetCPU"), ("function", "Code")):
                continue                               # shown as a slider / code editor below
            if key in choices:                         # render a dropdown for enum props
                combo = QComboBox()
                opts = list(choices[key])
                if str(value) and str(value) not in opts:
                    opts.insert(0, str(value))         # keep any custom value selectable
                combo.addItems(opts)
                combo.setCurrentText(str(value))
                combo.currentTextChanged.connect(lambda t, k=key: self._commit(k, t))
                self.props_form.addRow(key, combo)
            elif key in getattr(dt, "readonly_properties", ()):
                # Observed, not requested: the element reports this from the real
                # world, so showing an editable box would promise control we do not
                # have (a GINI32's channel is forced by the uplink it joins).
                lbl = QLabel(str(value) if str(value) else "—")
                lbl.setStyleSheet("color: %s;" % self.theme.theme.muted)
                lbl.setToolTip("Reported by the device — not settable here.")
                self.props_form.addRow(key, lbl)
            else:
                edit = QLineEdit(str(value))
                if not str(value) and key in ("BoardID", "ApSSID", "PhysicalSubnet"):
                    edit.setPlaceholderText(
                        {"BoardID": "the id on the board's label, e.g. gini-5",
                         "ApSSID": "blank = named after this element",
                         "PhysicalSubnet": "blank = allocated automatically"}[key])
                edit.editingFinished.connect(
                    lambda e=edit, k=key: self._commit(k, e.text()))
                self.props_form.addRow(key, edit)

        if dt.key == "gini32":                 # real hardware: claim a board to this element
            self._build_board_panel(d, accent)

        if dt.key == "function":               # serverless: code editor + Invoke (Test) panel
            self._build_function_panel(d, accent)

        if getattr(dt, "rider", False):        # Source/Sink: a Run button; output shows in Live tab
            self._build_rider_panel(d, accent)

        self._build_interfaces(d, accent)
        self.routes.setText(self._render_routes(d.name))

    @staticmethod
    def _clear_layout(lay) -> None:
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _build_interfaces(self, d, accent: str) -> None:
        """Render the Interfaces tab. In manual addressing mode each interface's IP is
        an editable field; otherwise it's read-only text."""
        lay = self.ifaces_lay
        self._clear_layout(lay)
        name = d.name
        addr = self.ctx.addressing.get(name)
        manual = getattr(self.ctx.topology, "manual_addressing", False)

        def add(text: str, obj: str = "", mono: bool = False, rich: bool = False) -> QLabel:
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            if rich:
                lbl.setTextFormat(Qt.RichText)
            if obj:
                lbl.setObjectName(obj)
            if mono:
                lbl.setStyleSheet("font-family:monospace")
            lay.addWidget(lbl)
            return lbl

        if not addr:
            add("Addressing appears after you Compile.\n\nConnected to: "
                + self._neighbors(name), obj="Faint")
            return
        if addr.get("role") == "switch":
            peers = ", ".join(addr.get("peers", [])) or "nothing yet"
            add(f"<b>Layer-2 switch</b> · {addr.get('ports', 0)} ports<br>"
                f"Ports to: {peers}", rich=True)
            return

        if manual:
            add("Manual addressing is <b>on</b> — type an IP for each interface. "
                "Leave one blank to auto-fill it.", obj="Muted", rich=True)

        for itf in addr["interfaces"]:
            add(f"<b>{itf['name']}</b> &rarr; {itf['peer']}", rich=True)
            if manual:
                row = QWidget()
                rl = QHBoxLayout(row)
                rl.setContentsMargins(0, 0, 0, 0)
                rl.addWidget(QLabel("IP"))
                edit = QLineEdit(str(itf["ip"]).split("/")[0])
                edit.setPlaceholderText("auto")
                lid = itf.get("link_id", "")
                edit.editingFinished.connect(
                    lambda e=edit, lid=lid: self._commit_iface_ip(lid, e.text()))
                rl.addWidget(edit, 1)
                lay.addWidget(row)
            else:
                add(itf["ip"], mono=True)
            gw = f" · <span style='color:{accent}'>gateway</span> {itf['gateway']}" \
                 if itf.get("gateway") else ""
            add(f"subnet {itf['subnet']}{gw}", obj="Faint", rich=True)
            add(itf["mac"], obj="Faint", mono=True)

    def _commit_iface_ip(self, link_id: str, text: str) -> None:
        if self._device_id and link_id:
            self.api.set_interface_ip(self._device_id, link_id, text)

    def _render_routes(self, name: str) -> str:
        addr = self.ctx.addressing.get(name)
        if not addr:
            return "<i>Routes appear after you Compile.</i>"
        if addr["role"] == "switch":
            return "Switches forward at Layer 2 — no IP routes."
        lines = [f"{itf['subnet']} &rarr; dev {itf['name']} (connected)"
                 for itf in addr["interfaces"]]
        # static inter-router routes the compiler installed (so a router shows how it
        # reaches non-adjacent subnets, not just its connected ones)
        import ipaddress
        for rt in addr.get("routes", []):
            try:
                plen = ipaddress.IPv4Network(f"0.0.0.0/{rt['mask']}").prefixlen
            except (ipaddress.AddressValueError, ipaddress.NetmaskValueError, KeyError, ValueError):
                plen = None
            if rt.get("net") == "0.0.0.0":
                lines.append(f"default &rarr; via {rt['gw']} dev {rt['dev']}")
            else:
                dest = f"{rt['net']}/{plen}" if plen is not None else rt.get("net", "?")
                lines.append(f"{dest} &rarr; via {rt['gw']} dev {rt['dev']}")
        gw = next((i["gateway"] for i in addr["interfaces"] if i.get("gateway")), None)
        if gw:
            lines.append(f"default &rarr; via {gw}")
        return "<br>".join(lines)

    def _neighbors(self, name: str) -> str:
        d = self.ctx.topology.find_by_name(name)
        if not d:
            return "—"
        nb = [n.name for n in self.ctx.topology.neighbors(d.id)]
        return ", ".join(nb) if nb else "nothing yet"

    # -- real-time CPU/memory metrics (Live tab, container elements) --------- #
    def set_live_running(self, on: bool) -> None:
        """Called by MainWindow on run/stop. Drives whether the metrics plots poll and
        enables run-only panel actions (e.g. a Function's Invoke button)."""
        self._live_running = bool(on)
        self._hist.clear(); self._prev_net.clear()   # fresh history each session
        if not on:
            self.metrics.set_series({})
        for attr in ("_invoke_btn", "_deploy_btn"):        # toggle run-only buttons in place
            btn = getattr(self, attr)
            if btn is not None:
                try:
                    btn.setEnabled(on)
                    btn.setToolTip("" if on else "Run the topology first.")
                except RuntimeError:
                    setattr(self, attr, None)              # widget was rebuilt away
        self.login_btn.setEnabled(self._login_allowed())   # Log in follows the same gate
        self._update_live_mode()

    _HIST_KEYS = ("cpu", "mem", "thru", "lat", "cpu_pct", "target_pct", "replicas")

    def _hist_for(self, key: str) -> dict:
        from collections import deque
        from .live_metrics import LiveMetrics
        h = self._hist.get(key)
        if h is None:
            h = {k: deque(maxlen=LiveMetrics.WINDOW) for k in self._HIST_KEYS}
            self._hist[key] = h
        return h

    def _is_container(self, d) -> bool:
        from ..services.compiler import _role
        return _role(d.type_key) in ("compute", "service", "machine", "k8scluster")

    def _is_k8s_workload(self, d) -> bool:
        from ..services.compiler import _role
        return _role(d.type_key) in ("k8sworkload", "hpa")

    def _k8s_dep(self, d) -> str:
        """The Deployment name a K8s element reports on (a Pod = itself; an Autoscaling
        Group = its connected Pod)."""
        from ..services.compiler import _role, _svc
        if _role(d.type_key) == "k8sworkload":
            return _svc(d.name)
        for l in self.ctx.topology.links.values():
            other = (l.target_id if l.source_id == d.id else
                     l.source_id if l.target_id == d.id else None)
            od = self.ctx.topology.devices.get(other) if other else None
            if od and _role(od.type_key) == "k8sworkload":
                return _svc(od.name)
        return _svc(d.name)

    # -- Sources / Sinks: a Run button + output rendered in the Live tab ----- #
    def _build_rider_panel(self, d, accent: str) -> None:
        donor = self.ctx.topology.donor_of(d.id)
        row = QWidget(); rl = QHBoxLayout(row); rl.setContentsMargins(0, 0, 0, 0)
        self._rider_btn = QPushButton("Stop" if self.ctx.is_rider_running(d.id) else "Start")
        self._rider_btn.setToolTip("Start/stop this on its donor — output streams to the Live tab")
        self._rider_btn.clicked.connect(lambda: self._toggle_rider(d.id))
        rl.addWidget(self._rider_btn)
        rl.addWidget(QLabel(f"on {donor.name}" if donor else "not attached to a donor"))
        rl.addStretch(1)
        self.props_form.addRow("Runs", row)
        self._show_rider_live(d)                 # reflect its last run (or a hint) in the Live tab

    def _toggle_rider(self, device_id: str) -> None:
        import threading
        threading.Thread(target=lambda: self.ctx.toggle_rider(device_id), daemon=True).start()

    def _on_rider_ran(self, device_id: str, result) -> None:
        if device_id != self._device_id:
            return
        d = self.ctx.topology.devices.get(device_id)
        if d is None:
            return
        if getattr(self, "_rider_btn", None) is not None:
            self._rider_btn.setText("Stop" if (result or {}).get("running") else "Start")
        self._show_rider_live(d)
        self._refresh_kpis()
        if self.tabs.currentWidget() is not self._live_host:   # pop the Live tab once, then leave it
            self.tabs.setCurrentWidget(self._live_host)

    def _show_rider_live(self, d) -> None:
        res = self.ctx.rider_results.get(d.id)
        if res and (res.get("raw") or res.get("error")):
            self.live.setPlainText(res.get("raw") or res.get("error"))
        elif res:
            self.live.setPlainText("(ran, no output)")
        else:
            self.live.setPlainText("Double-click this element, or press Run, to execute it on its "
                                   "donor — its output appears here.")

    def _rider_kpis(self, type_key: str, m: dict) -> list:
        if type_key == "ping_probe":
            out = [{"label": "loss", "value": m.get("loss_pct", "—"), "unit": "%"}]
            if "rtt_avg_ms" in m:
                out.append({"label": "avg rtt", "value": m["rtt_avg_ms"], "unit": "ms"})
            return out
        if type_key == "http_probe":
            out = [{"label": "2xx", "value": m.get("ok_pct", "—"), "unit": "%"},
                   {"label": "reqs", "value": m.get("requests", 0), "unit": ""}]
            if "avg_ms" in m:
                out.append({"label": "avg", "value": m["avg_ms"], "unit": "ms"})
            return out
        if type_key == "packet_view":
            return [{"label": "packets", "value": m.get("packets", 0), "unit": ""}]
        return []

    def _update_live_mode(self) -> None:
        from ..services.compiler import _svc
        from .live_metrics import CLOUD_LAYOUT, K8S_LAYOUT
        d = self.ctx.topology.devices.get(self._device_id) if self._device_id else None
        container = bool(d and self._live_running and self.stats_all_fn and self._is_container(d))
        k8s = bool(d and self._live_running and self._is_k8s_workload(d))
        mode_on = container or k8s
        self._live_metrics_on = mode_on
        on_live_tab = self.tabs.currentWidget() is self._live_host
        self.metrics.setVisible(mode_on and on_live_tab)
        self.live.setVisible(not mode_on)
        self.refresh_btn.setVisible(not mode_on)
        self._refresh_kpis()
        if k8s:                                     # fed by MainWindow's k8s poll (kubectl)
            self.metrics.set_layout(K8S_LAYOUT)
            self.metrics.set_series(self._hist_for(self._k8s_dep(d)))
            self._live_timer.stop()
        elif container:                             # fed by whole-lab docker stats
            self.metrics.set_layout(CLOUD_LAYOUT)
            self.metrics.set_series(self._hist_for(_svc(d.name)))
            if not self._live_timer.isActive():
                self._live_timer.start()
                self._poll_metrics()
        else:
            self._live_timer.stop()

    def set_k8s_snapshot(self, snap) -> None:
        """Per-deployment K8s metrics (from MainWindow's kubectl poll). Appends to every
        deployment's history so each Pod/Autoscaler keeps a continuous graph."""
        self._k8s = snap or {}
        for dep, m in (self._k8s.get("deployments") or {}).items():
            h = self._hist_for(dep)
            h["cpu_pct"].append(_optnum(m.get("cpu_pct")))
            h["target_pct"].append(_optnum(m.get("target_pct")))
            h["replicas"].append(_optnum(m.get("replicas")))
        self.metrics.update()
        self._refresh_kpis()

    def set_fabric_snapshot(self, snap) -> None:
        """Latest cloud-fabric app metrics (from MainWindow's 2s poll)."""
        self._fabric = snap or {}
        self._refresh_kpis()

    def _refresh_kpis(self) -> None:
        from ..services.compiler import _role, _svc
        t = self.theme.theme
        kpis = None
        d = self.ctx.topology.devices.get(self._device_id) if self._device_id else None
        if d and self._live_metrics_on:
            role = _role(d.type_key)
            if role in ("k8sworkload", "hpa"):     # kubernetes element -> deployment KPIs
                m = (self._k8s.get("deployments") or {}).get(self._k8s_dep(d))
                if m:
                    cpu, tgt = m.get("cpu_pct"), m.get("target_pct")
                    kpis = [{"label": "replicas", "value": m.get("replicas", 0), "unit": ""},
                            {"label": "CPU", "value": "—" if cpu is None else cpu, "unit": "%"},
                            {"label": "target", "value": tgt, "unit": "%"}]
                    if m.get("min") is not None:
                        kpis.append({"label": "range",
                                     "value": f"{m.get('min')}–{m.get('max')}", "unit": ""})
            elif role == "k8scluster":
                kpis = [{"label": "pods", "value": self._k8s.get("pods", 0), "unit": ""}]
            else:                                   # cloud service -> fabric KPIs
                s = (self._fabric.get("services") or {}).get(_svc(d.name))
                kpis = s.get("kpis") if s else None
        if not kpis and d and getattr(d.type, "rider", False):   # Source/Sink -> its measurement
            res = self.ctx.rider_results.get(d.id)
            m = res.get("measurement") if res else None
            if m and m.get("ok"):
                kpis = self._rider_kpis(d.type_key, m)
        if not kpis:
            self.kpis_lbl.setVisible(False)
            return
        chips = []
        for k in kpis:
            chips.append(
                f'<span style="color:{t.accent};font-weight:600">{k["value"]}'
                f'<span style="color:{t.faint};font-weight:400">{k.get("unit", "")}</span>'
                f'</span> <span style="color:{t.muted}">{k["label"]}</span>')
        self.kpis_lbl.setText(' &nbsp;·&nbsp; '.join(chips))
        self.kpis_lbl.setVisible(True)

    def _poll_metrics(self) -> None:
        if self._polling or not self.stats_all_fn:
            return
        import time
        self._polling = True

        def work():
            try:
                allstats = self.stats_all_fn()       # whole lab in one docker call
            finally:
                self._polling = False
            if allstats:
                self.metrics_ready.emit((allstats, time.monotonic()))
        threading.Thread(target=work, daemon=True).start()

    def _on_metrics(self, payload) -> None:
        """Append one whole-lab sample to EVERY element's history (so each keeps a
        continuous graph even while another is selected), then repaint the current one."""
        allstats, now = payload
        fabric_svcs = self._fabric.get("services") or {}
        for svc, s in allstats.items():
            h = self._hist_for(svc)
            net = float(s.get("net_bytes", 0.0))
            thru = 0.0
            pn = self._prev_net.get(svc)
            if pn and now - pn[1] > 0:
                thru = max(0.0, (net - pn[0]) / (now - pn[1]) / 1024.0)
            self._prev_net[svc] = (net, now)
            lat = None
            for k in (fabric_svcs.get(svc, {}) or {}).get("kpis", []):
                if k.get("label") == "latency":
                    lat = k.get("value")
            h["cpu"].append(float(s.get("cpu", 0.0)))
            h["mem"].append(float(s.get("mem_used", 0.0)))
            h["thru"].append(thru)
            h["lat"].append(lat)
        self.metrics.update()                        # repaint the selected element's chart

    @staticmethod
    def _signal_word(rssi: int) -> str:
        """A bare dBm number means nothing to a student; say what it implies."""
        if not rssi:
            return ""
        if rssi >= -55:
            return "strong"
        if rssi >= -67:
            return "good"
        if rssi >= -75:
            return "fair"
        return "weak — move the board closer"

    def _board_live_text(self, d) -> str:
        """Everything we know about a real board. No shell, no container — this IS the
        view for a GINI32, because there is nothing to log in to."""
        st = self.board_status_fn() if self.board_status_fn else None
        bid = str((d.properties or {}).get("BoardID", "")).strip()
        if not bid:
            return ("No BoardID set.\n\n"
                    "This element is not bound to any physical board, so nothing will\n"
                    "ever attach to it. Put the id from the board's label here — the one\n"
                    "written by `gini32 provision --id`.")
        if not st:
            return ("Live state is available once the topology is running.\n\n"
                    f"Waiting for board {bid!r} to check in.")
        b = next((x for x in st.get("boards", []) if x.get("board_id") == bid), None)
        if b is None:
            return f"The running lab has no board called {bid!r}.\nPress Run again after changing the BoardID."
        if not b.get("online"):
            return (f"{bid} — OFFLINE (never checked in, or gone quiet)\n\n"
                    "Most likely, in order:\n"
                    "  1. the board is on a different network from this Mac\n"
                    "  2. the id flashed on the board differs from BoardID here\n"
                    "  3. this Wi-Fi blocks multicast, so discovery finds nothing\n"
                    "     (fix: `gini32 provision --server <this Mac's IP>`)\n\n"
                    "The board's own serial console says which of these it is.")

        import time as _t
        age = max(0, int(_t.time() - (b.get("last_seen") or 0)))
        sig = self._signal_word(b.get("rssi") or 0)
        rows = [
            f"{bid} — connected · last heard {age}s ago",
            "",
            f"  server      {b.get('addr') or '?'}",
            f"  fabric      {b.get('ip') or '?'}   mode {b.get('mode') or '?'}",
            f"  hotspot     {b.get('ap_ssid') or '?'} on {b.get('physical_subnet') or '?'}",
            f"  uplink      {b.get('uplink') or '?'}   ch {b.get('channel') or '?'}"
            + (f"   RSSI {b.get('rssi')} dBm ({sig})" if b.get("rssi") else ""),
            f"  counters    rx {b.get('rx', 0)}   tx {b.get('tx', 0)}   "
            f"dropped {b.get('dropped', 0)}",
            "",
        ]
        clients = b.get("clients") or []
        if clients:
            rows.append(f"devices on the radio ({len(clients)})")
            for c in clients:
                rows.append(f"  {c.get('mac','?'):<20} {c.get('ip','?')}")
            rows.append("")
            rows.append("These are real devices. They appear on the canvas while they are")
            rows.append("associated and are never saved with the topology.")
        else:
            rows.append("No devices on the radio yet — join the hotspot "
                        f"{b.get('ap_ssid') or ''} from a phone.")
        return "\n".join(rows)

    def _build_board_offline_picker(self, d) -> None:
        """Offer the boards set up from this computer, while no lab is running."""
        known = list(getattr(self.ctx.settings, "known_boards", None) or [])
        mine = str((d.properties or {}).get("BoardID", "")).strip()
        if not known and not mine:
            lbl = QLabel("No boards set up yet — use Hardware → Set Up a Board with "
                         "one plugged in over USB. Run the topology to see which "
                         "boards are on the network.")
            lbl.setStyleSheet("color: %s;" % self.theme.theme.muted)
            lbl.setWordWrap(True)
            self.props_form.addRow("Board", lbl)
            return

        combo = QComboBox()
        combo.setEditable(True)              # an id can still be typed by hand
        combo.addItem("")
        for name in known:
            combo.addItem(f"{name}  ·  set up here", name)
        if mine and mine not in known:
            combo.addItem(f"{mine}  ·  typed", mine)
        idx = next((i for i in range(combo.count()) if combo.itemData(i) == mine), 0)
        combo.setCurrentIndex(idx)

        def pick(_i: int) -> None:
            name = combo.currentData() or combo.currentText().split("  ·  ")[0].strip()
            self._commit("BoardID", name)
        combo.activated.connect(pick)
        self.props_form.addRow("Board", combo)

        hint = QLabel("Boards set up from this computer. Run the topology to see "
                      "which are actually on the network.")
        hint.setStyleSheet("color: %s;" % self.theme.theme.muted)
        hint.setWordWrap(True)
        self.props_form.addRow("", hint)

    def _build_board_panel(self, d, accent: str) -> None:
        """Pick which physical board plays this element, and claim it.

        Claiming is what stops a room of students taking each other's hardware: a
        claimed board answers only its owner and is invisible to every other gBuilder.
        So this list only ever shows boards that are unclaimed, or already ours.
        """
        st = self.board_status_fn() if self.board_status_fn else None
        if st is None:
            # Nothing is running, so we cannot say who is on the air. We CAN still
            # offer the boards this laptop has set up: retyping an id from memory is
            # exactly where a typo creeps in, and a mistyped BoardID gives a board
            # that is online and healthy but invisible to the topology.
            self._build_board_offline_picker(d)
            return

        avail = st.get("available") or []
        mine = str((d.properties or {}).get("BoardID", "")).strip()

        row = QHBoxLayout()
        combo = QComboBox()
        combo.setEditable(True)          # a known id can still be typed before it appears
        combo.addItem("")
        for b in avail:
            tag = "yours" if b.get("claimed") else "free"
            state = "online" if b.get("online") else "quiet"
            combo.addItem(f"{b['name']}  ·  {tag}, {state}  ·  {b.get('mac', '')}",
                          b["name"])
        # keep whatever is already set, even if that board is not on the air right now
        if mine and mine not in [b["name"] for b in avail]:
            combo.addItem(f"{mine}  ·  not seen", mine)
        idx = next((i for i in range(combo.count()) if combo.itemData(i) == mine), 0)
        combo.setCurrentIndex(idx)

        def pick(_i: int) -> None:
            name = combo.currentData() or combo.currentText().split("  ·  ")[0].strip()
            self._commit("BoardID", name)
        combo.activated.connect(pick)
        row.addWidget(combo, 1)
        self.props_form.addRow("Board", self._wrap_row(row))

        if not avail:
            # A board claimed by another laptop is filtered out before it ever reaches
            # this list, so "nothing is announcing" would be flatly untrue when one is
            # sitting on the bench announcing steadily. Say which case this is: they
            # look identical from here but have completely different fixes.
            foreign = st.get("foreign") or []
            if foreign:
                names = ", ".join(f["board_id"] for f in foreign[:3])
                hint = QLabel(
                    f"<b>{names}</b> is on the network but claimed by another laptop, "
                    f"so it will not answer this one.<br><br>If that is your board, its "
                    f"claim outlived the install that made it. Free it over USB:<br>"
                    f"<tt>./gini32 unpair -p &lt;port&gt;</tt> then type <tt>unpair</tt>.")
                hint.setStyleSheet("color: %s;" % self.theme.theme.warning)
            else:
                hint = QLabel("No boards are announcing themselves. A board appears "
                              "here once it joins the lab Wi-Fi — and only if it is "
                              "unclaimed, or already claimed by this laptop.")
                hint.setStyleSheet("color: %s;" % self.theme.theme.muted)
            hint.setWordWrap(True)
            self.props_form.addRow("", hint)
            return

        sel = next((b for b in avail if b["name"] == mine), None)

        # The mismatch case, said plainly. Hardware is on the air, but not under the id
        # this element is asking for — so the relay turns it away and the board simply
        # keeps searching. Neither end can detect this alone: the board does not know
        # what the canvas wants, and the canvas does not know the board exists. A quiet
        # "not seen" suffix in the dropdown is not enough; name the boards that ARE here.
        if mine and sel is None:
            others = ", ".join(b["name"] for b in avail[:4])
            warn = QLabel(f"No board called <b>{mine}</b> is on the network, but these "
                          f"are: <b>{others}</b>.<br>Pick one above — the id must match "
                          f"the board's label exactly.")
            warn.setStyleSheet("color: %s;" % self.theme.theme.warning)
            warn.setWordWrap(True)
            self.props_form.addRow("", warn)

        btns = QHBoxLayout()
        blink = QPushButton("Blink")
        blink.setToolTip("Flash this board's LED, so you can tell which one it is "
                         "on the bench.")
        blink.setEnabled(sel is not None)
        blink.clicked.connect(lambda: self.board_action_fn and
                              self.board_action_fn("blink", mine))
        btns.addWidget(blink)

        if sel is not None and not sel.get("claimed"):
            claim = QPushButton("Claim this board")
            claim.setToolTip("Bind it to this laptop. It will then ignore every other "
                             "gBuilder, so nobody else can take it.")
            claim.clicked.connect(lambda: self.board_action_fn and
                                  self.board_action_fn("claim", mine))
            btns.addWidget(claim, 1)
        elif sel is not None:
            rel = QPushButton("Release")
            rel.setToolTip("Give the board up so another laptop can claim it. You can "
                           "also do this on the board itself with `unpair`.")
            rel.clicked.connect(lambda: self.board_action_fn and
                                self.board_action_fn("release", mine))
            btns.addWidget(rel, 1)
        self.props_form.addRow("", self._wrap_row(btns))

    @staticmethod
    def _wrap_row(layout) -> QWidget:
        w = QWidget()
        layout.setContentsMargins(0, 0, 0, 0)
        w.setLayout(layout)
        return w

    def _refresh_live(self) -> None:
        if not self._device_id:
            return
        d = self.ctx.topology.devices[self._device_id]
        from ..services.compiler import _role
        role = _role(d.type_key)
        if role == "gini32":
            # Real hardware: no container to query, so this is served from the relay's
            # view rather than by exec'ing into anything.
            self.live.setPlainText(self._board_live_text(d))
            return
        if self.query_fn is None:
            self.live.setPlainText("Live state is available once the topology is running.")
            return
        self.live.setPlainText("loading…")

        def work():
            if role == "router":
                out = ("interfaces:\n" + self.query_fn(d.name, "ifconfig show")
                       + "\n\nroutes:\n" + self.query_fn(d.name, "route show")
                       + "\n\narp cache:\n" + self.query_fn(d.name, "arp show"))
            elif role == "switch":
                out = ("ports: " + self.query_fn(d.name, "ports")
                       + "\n\nmac table:\n" + self.query_fn(d.name, "mactable"))
            else:
                out = "This is a host container — use “Log in” for a shell."
            self.live_ready.emit(out)
        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _runtime_note(d) -> str:
        """A plain 'here's what this actually runs' line so users (and new ones!) don't
        have to guess the backing image or which tools are available."""
        from ..services.cloud_catalog import service_for
        from ..services.compiler import _norm_image, _toolkit_for
        from ..services.orchestrator import (MACHINE_BASE, MACHINE_TOOLS_HUMAN,
                                             MACHINE_TOOLS_LEAN_HUMAN,
                                             MACHINE_TOOLS_SECURITY_HUMAN)
        key = d.type_key
        if key == "host":
            # tell the truth about THIS host's image — the toolkits ship different tools, and a
            # student reaching for `ettercap` on a lean host should learn that here, not at a shell
            tk = _toolkit_for(d)
            if tk == "security":
                return (f"Runs the SECURITY toolkit — {MACHINE_BASE}, the full toolkit plus the "
                        f"Part VI security engines: {MACHINE_TOOLS_SECURITY_HUMAN}. Biggest image; "
                        f"use it for the crypto, VPN, and intrusion-detection labs.")
            if tk == "full":
                return (f"Runs the FULL toolkit — {MACHINE_BASE} with everything preinstalled: "
                        f"{MACHINE_TOOLS_HUMAN}. Big image; use it only when an experiment needs "
                        f"these servers. (apt is available for anything else.)")
            return (f"Runs the LEAN toolkit — Alpine + {MACHINE_TOOLS_LEAN_HUMAN}. Small and quick "
                    f"to boot. Need bind9/postfix/ettercap/haproxy? Set Toolkit to 'full', or "
                    f"'security' for the security engines. (apk is available for anything else.)")
        if key == "instance":
            img = _norm_image(d.properties.get("Image") or "ubuntu:22.04")
            return f"Runs {img} (a cloud VM). Change the Image property to use another."
        if key == "container":
            img = _norm_image(d.properties.get("Image") or "alpine:latest")
            return f"Runs {img}. Set Image/Command to run your own app."
        svc = service_for(key)
        if svc is not None:
            return f"Runs {svc.image} — {svc.summary}"
        return ""

    def _int_slider(self, prop: str, lo: int, hi: int, default: int):
        """A labelled int slider that commits `prop` on release (live knobs: replicas, CPU%)."""
        row = QWidget(); rl = QHBoxLayout(row); rl.setContentsMargins(0, 0, 0, 0)
        sl = QSlider(Qt.Horizontal); sl.setRange(lo, hi)
        d = self.ctx.topology.devices.get(self._device_id)
        try:
            sl.setValue(int(float((d.properties.get(prop) if d else None) or default)))
        except (ValueError, TypeError):
            sl.setValue(default)
        vlbl = QLabel(str(sl.value())); vlbl.setMinimumWidth(34)
        sl.valueChanged.connect(lambda v, lb=vlbl: lb.setText(str(v)))
        sl.sliderReleased.connect(lambda s=sl, p=prop: self._commit(p, str(s.value())))
        rl.addWidget(sl, 1); rl.addWidget(vlbl)
        return row

    def _faint(self, text: str) -> QLabel:
        lbl = QLabel(text); lbl.setObjectName("Faint")
        return lbl

    def _build_function_panel(self, d, accent: str) -> None:
        """Serverless authoring, in the inspector: a Python code editor for custom handlers
        (the AWS-Lambda 'Code' tab) and an Invoke/Test panel (the 'Test' tab)."""
        box = QWidget(); lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 6, 0, 0); lay.setSpacing(6)

        editor = None
        if (d.properties.get("Handler") or "echo") == "custom":
            lay.addWidget(self._faint("Handler code — Python 3.12 · standard library"))
            editor = QPlainTextEdit(d.properties.get("Code") or _FUNCTION_STARTER)
            editor.setStyleSheet(_scss("font-family:monospace; font-size:12px"))
            editor.setMinimumHeight(150)
            lay.addWidget(editor)
            save = QPushButton("Save code")
            save.clicked.connect(
                lambda _=False, e=editor: self._commit("Code", e.toPlainText()))
            lay.addWidget(save)

        # Deploy: push the current code to the running runtime (recreate only the faas
        # container — the rest of the lab keeps running). AWS-style 'Deploy'.
        deploy = QPushButton("Deploy")
        deploy.setObjectName("Accent")
        deploy.setEnabled(self._live_running)
        deploy.setToolTip("Recreate the faas runtime with your latest function code "
                          "(the rest of the lab keeps running)." if self._live_running
                          else "Run the topology first, then Deploy.")

        def _do_deploy(_=False, e=editor):
            if e is not None:                     # save the latest edits before deploying
                self._commit("Code", e.toPlainText())
            self.ctx.bus.function_deploy_requested.emit()
        deploy.clicked.connect(_do_deploy)
        lay.addWidget(deploy)
        self._deploy_btn = deploy

        lay.addWidget(self._faint("Invoke (Test) — runs the live function"))
        row = QWidget(); rl = QHBoxLayout(row); rl.setContentsMargins(0, 0, 0, 0)
        method = QComboBox(); method.addItems(["GET", "POST"])
        body = QLineEdit(); body.setPlaceholderText("request body (sent on POST)")
        invoke = QPushButton("Invoke")
        # the Invoke button only works against the live container, so it's enabled only once
        # the lab is running (docker compose up succeeded -> set_live_running(True)).
        invoke.setEnabled(self._live_running)
        if not self._live_running:
            invoke.setToolTip("Run the topology first (the function must be running).")
        rl.addWidget(method); rl.addWidget(body, 1); rl.addWidget(invoke)
        lay.addWidget(row)

        result = QPlainTextEdit(); result.setReadOnly(True); result.setMinimumHeight(96)
        result.setStyleSheet(_scss("font-family:monospace; font-size:12px"))
        result.setPlainText("Invoke to see the response." if self._live_running
                            else "Run the topology, then Invoke to see the response.")
        lay.addWidget(result)
        self._invoke_result = result
        self._invoke_btn = invoke

        did = self._device_id
        invoke.clicked.connect(
            lambda _=False, m=method, b=body: self.ctx.bus.function_invoke_requested.emit(
                did, m.currentText(), b.text()))
        self.props_form.addRow(box)

    def _on_invoke_result(self, device_id: str, text: str) -> None:
        if device_id == self._device_id and self._invoke_result is not None:
            try:
                self._invoke_result.setPlainText(text)
            except RuntimeError:
                pass                              # the panel was rebuilt away

    def _commit(self, key: str, value: str) -> None:
        if self._device_id:
            self.api.set_property(self._device_id, key, value)

    def _commit_size(self, level) -> None:
        from ..domain import pricing
        if not self._device_id:
            return
        d = self.ctx.topology.devices.get(self._device_id)
        if d is None:
            return
        new = pricing.size_level(level)
        if new == getattr(d, "size", 1):            # no-op guard (breaks rebuild loop)
            return
        d.size = new
        self.ctx.bus.device_changed.emit(self._device_id)   # resize the node + reroute edges
        self.ctx.bus.device_resized.emit(self._device_id)   # live CPU update if running
        self.ctx.bus.topology_changed.emit()                # rebill the dashboard
