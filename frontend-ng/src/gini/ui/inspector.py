"""Inspector — design properties + compiler addressing + live runtime state.

Properties stay editable. Interfaces/Routes show the IP/MAC/subnet/gateway the
compiler assigns (so addressing is visible before anything runs). The Live tab queries
the element's control socket when the topology is running. A "Log in" button opens the
device's terminal/console.
"""
from __future__ import annotations

import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
    QScrollArea, QTabWidget, QVBoxLayout, QWidget,
)

from ..agent.api import GiniAPI
from ..app import AppContext
from .theme import ThemeManager, icons


class Inspector(QWidget):
    live_ready = Signal(str)

    def __init__(self, ctx: AppContext, api: GiniAPI, theme: ThemeManager) -> None:
        super().__init__()
        self.setObjectName("Inspector")
        self.ctx = ctx
        self.api = api
        self.theme = theme
        self._device_id: str | None = None
        self.query_fn = None        # set by MainWindow: (device_name, command) -> str

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
        self.name_lbl.setStyleSheet("font-size:15px; font-weight:600;")
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
        live_host = QWidget(); lv = QVBoxLayout(live_host)
        lv.setContentsMargins(12, 10, 12, 12)
        self.refresh_btn = QPushButton("Refresh live state")
        self.refresh_btn.clicked.connect(self._refresh_live)
        lv.addWidget(self.refresh_btn)
        lv.addWidget(self.live, 1)

        self.tabs.addTab(ps, "Properties")
        self.tabs.addTab(self._ifaces_scroll, "Interfaces")
        self.tabs.addTab(self._wrap(self.routes), "Routes")
        self.tabs.addTab(live_host, "Live")
        root.addWidget(self.tabs, 1)

        ctx.bus.selection_changed.connect(self._on_select)
        ctx.bus.device_changed.connect(self._on_changed)
        ctx.bus.addressing_changed.connect(self._rebuild)
        self.live_ready.connect(self.live.setPlainText)

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

    # selection ------------------------------------------------------------- #
    def _on_select(self, device_id) -> None:
        self._device_id = device_id
        self._rebuild()

    def _on_changed(self, device_id) -> None:
        if device_id == self._device_id:
            self._rebuild()

    def _clear_form(self) -> None:
        while self.props_form.rowCount():
            self.props_form.removeRow(0)

    def _rebuild(self) -> None:
        self._clear_form()
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
        note = self._runtime_note(d)
        self.runs_lbl.setText(note)
        self.runs_lbl.setVisible(bool(note))

        choices = dt.property_choices
        for key, value in d.properties.items():
            if key in choices:                         # render a dropdown for enum props
                combo = QComboBox()
                opts = list(choices[key])
                if str(value) and str(value) not in opts:
                    opts.insert(0, str(value))         # keep any custom value selectable
                combo.addItems(opts)
                combo.setCurrentText(str(value))
                combo.currentTextChanged.connect(lambda t, k=key: self._commit(k, t))
                self.props_form.addRow(key, combo)
            else:
                edit = QLineEdit(str(value))
                edit.editingFinished.connect(
                    lambda e=edit, k=key: self._commit(k, e.text()))
                self.props_form.addRow(key, edit)

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

    def _refresh_live(self) -> None:
        if not self._device_id:
            return
        d = self.ctx.topology.devices[self._device_id]
        from ..services.compiler import _role
        role = _role(d.type_key)
        if self.query_fn is None:
            self.live.setPlainText("Live state is available once the topology is running.")
            return
        self.live.setPlainText("loading…")

        def work():
            if role == "router":
                out = ("interfaces:\n" + self.query_fn(d.name, "interfaces")
                       + "\n\nroutes:\n" + self.query_fn(d.name, "routes")
                       + "\n\narp cache:\n" + self.query_fn(d.name, "arp"))
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
        from ..services.compiler import _norm_image
        from ..services.orchestrator import MACHINE_BASE, MACHINE_TOOLS_HUMAN
        key = d.type_key
        if key == "host":
            return (f"Runs {MACHINE_BASE} with the GINI toolkit preinstalled — "
                    f"{MACHINE_TOOLS_HUMAN}. (apt is available for anything else.)")
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

    def _commit(self, key: str, value: str) -> None:
        if self._device_id:
            self.api.set_property(self._device_id, key, value)
