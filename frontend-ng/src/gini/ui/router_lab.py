"""Router Lab — the visual module-graph editor for one gRouter.

Open it by double-clicking a router. You compose the data plane by adding inline
modules onto the locked base pipeline (parse → route → rewrite), reorder/remove them,
toggle the SDN mode (OpenFlow = flow-table front door), and step a test packet through
to see the verdict at each stage. Today it drives a local trace; later it binds to the
real gRouter's module graph over the control protocol.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from ..domain.router_modules import BASE, CUSTOM, INLINE, MODULE_BY_KEY, RouterProgram
from .theme import ThemeManager, icons


class RouterLab(QDialog):
    live_ready = Signal(str)   # real-router trace output (from a worker thread)

    def __init__(self, parent, theme: ThemeManager, device, program: RouterProgram,
                 on_console=None, command_fn=None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.device = device
        self.program = program
        self.on_console = on_console
        self.command_fn = command_fn   # set when running: sends `gpipe …` to the real router
        self._trace: list[str] = []
        self._step_idx = -1
        self._stage_widgets: list[QFrame] = []
        self.live_ready.connect(self._show_live)

        t = theme.theme
        self.setWindowTitle(f"Router Lab — {device.name}")
        self.resize(880, 620)
        self.setStyleSheet(f"QDialog{{background:{t.bg};}}")

        root = QVBoxLayout(self)

        # header -------------------------------------------------------------
        head = QHBoxLayout()
        ic = QLabel(); ic.setPixmap(icons.render_pixmap("router", t.accent_for("blue"), 24))
        title = QLabel(f"  Router Lab — {device.name}")
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

        # body ---------------------------------------------------------------
        body = QHBoxLayout()
        body.addWidget(self._build_palette(), 0)
        body.addWidget(self._build_pipeline(), 1)
        root.addLayout(body, 1)

        # footer (step debugger) --------------------------------------------
        foot = QHBoxLayout()
        inject = QPushButton("  Inject packet"); inject.setObjectName("Accent")
        inject.setIcon(icons.icon("play", "#ffffff", 14)); inject.clicked.connect(self._inject)
        step = QPushButton("  Step"); step.clicked.connect(self._step)
        reset = QPushButton("  Reset"); reset.clicked.connect(self._reset)
        self.trace_lbl = QLabel("Inject a test packet, then Step through the pipeline.")
        self.trace_lbl.setObjectName("Muted"); self.trace_lbl.setWordWrap(True)
        foot.addWidget(inject); foot.addWidget(step); foot.addWidget(reset)
        foot.addSpacing(12); foot.addWidget(self.trace_lbl, 1)
        root.addLayout(foot)

        self._rebuild()

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
        lay.addWidget(header("Add-ons · click to add"))
        for mt in INLINE:
            lay.addWidget(pal_btn(mt, locked=False))
        lay.addWidget(header("Custom · you write"))
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
