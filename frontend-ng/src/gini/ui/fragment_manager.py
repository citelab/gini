"""Fragment Manager — teacher mode.

A narrow icon rail (List · Create · Edit · Delete) over the fragment editor. The editor authors a
fragment from the live canvas two ways:

  * **Record (scan mode)** — flip it on and BUILD on the canvas; each action becomes an ordered step
    (place → wire → group). Deletes are not auto-pruned — remove stray steps by hand.
  * **Read from canvas** — the one-shot fallback for a board you already built.

Steps are editable inline: per-row ▲▼ reorder, ✕ delete, and a clickable level chip (place → connect
→ group → live). Plus **Add a live check** (the L4 runtime probe the recorder can't see) and **Add a
fork** (the difficulty knob). Finalize validates + writes the stamped YAML to ~/.gini/content.

Non-modal (floats over gBuilder) so recording keeps the canvas interactive.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QScrollArea, QToolButton, QVBoxLayout, QWidget,
)

from ..domain import authoring as _au
from ..domain import certify as _certify
from ..domain import content as _content
from ..domain import fragments as _frag
from ..domain import objectives as _obj
from ..domain import riders as _riders
from .theme import icons as _icons

_LEVEL_SHORT = {_obj.PLACEMENT: "L1", _obj.CONNECTION: "L2",
                _obj.CONTAINMENT: "L3", _obj.LIVE: "L4"}
_LEVEL_TIP = {_obj.PLACEMENT: "Place", _obj.CONNECTION: "Connect",
              _obj.CONTAINMENT: "Group", _obj.LIVE: "Live"}
# a distinct, high-contrast colour per level so the chips read clearly on any theme
_LEVEL_COLOR = {_obj.PLACEMENT: "#3B82F6", _obj.CONNECTION: "#10B981",
                _obj.CONTAINMENT: "#F59E0B", _obj.LIVE: "#8B5CF6"}


class FragmentManager(QDialog):
    _cert_ready = Signal(object, object)         # (CertReport, frag_dict) — from the grade thread

    def __init__(self, parent, ctx, *, author: str = "") -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._author = author
        self._certified_hash: str | None = None  # hash of the dict last certified runtime-green
        self._cert_ready.connect(self._on_cert_ready)
        self._recorder: _au.Recorder | None = None
        self._editing_id: str | None = None
        self._steps: list[dict] = []
        self._forks: list[dict] = []
        self.setWindowTitle("Fragment Manager")
        self.setMinimumSize(520, 480)            # narrow enough for a small student laptop
        self.resize(560, 560)
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.setWindowFlag(Qt.Tool, True)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self._icon_color = self.palette().color(QPalette.ColorRole.WindowText).name()

        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # -- narrow icon rail ------------------------------------------------
        rail = QVBoxLayout(); rail.setSpacing(4)
        for icon, tip, slot in (("layout", "List", self._show_list),
                                ("plus", "Create", self._create),
                                ("pencil", "Edit", self._edit_selected),
                                ("trash", "Delete", self._delete_selected)):
            b = QToolButton()
            b.setIcon(self._ic(icon, 20)); b.setToolTip(tip)
            b.setToolButtonStyle(Qt.ToolButtonIconOnly)
            b.setAutoRaise(True); b.setFixedSize(38, 34)
            b.clicked.connect(slot)
            rail.addWidget(b)
        rail.addStretch(1)
        rail_w = QWidget(); rail_w.setLayout(rail); rail_w.setFixedWidth(46)
        rail_w.setObjectName("FragRail")
        # a darker panel behind the icon rail so it reads as its own column
        rail_w.setStyleSheet("#FragRail { background: rgba(0,0,0,0.11); border-radius: 10px; }")
        root.addWidget(rail_w)

        self._body = QVBoxLayout(); self._body.setSpacing(8)
        body_w = QWidget(); body_w.setLayout(self._body)
        root.addWidget(body_w, 1)
        self._show_list()

    # -- helpers ------------------------------------------------------------ #
    def _ic(self, name: str, size: int = 18):
        return _icons.icon(name, self._icon_color, size)

    def _card(self, layout) -> QWidget:
        """A flat grouping — no drop shadow (it muddied the look). Just spacing + a hairline top rule
        via a thin separator, which reads clean on every theme."""
        w = QWidget(); w.setLayout(layout)
        return w

    def _clear_body(self) -> None:
        while self._body.count():
            it = self._body.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None); w.deleteLater()

    # ------------------------------------------------------------ certify
    def _library_excluding(self, fid: str) -> list:
        return [f for f in _frag.all_fragments() if f.id != fid]

    def _dict_from_fragment(self, f) -> dict:
        """Rebuild the authoring dict from a saved Fragment (inverse of build_fragment_dict)."""
        def objs(items):
            return [{"id": o.id, "say": o.say, "check": o.check, "kind": o.kind,
                     "probe": o.probe, "level": o.level or _obj.level_of(o)} for o in items]
        forks = [{"id": fk.id, "label": fk.label, "difficulty": fk.difficulty, "kind": fk.kind,
                  "objectives": objs(fk.objectives)} for fk in f.forks]
        return _au.build_fragment_dict(
            frag_id=f.id, teaches=f.teaches, summary=f.summary, spirit=f.spirit,
            objectives=objs(f.objectives), forks=forks or None,
            provides=f.provides or None, requires=f.requires or None, author=self._author)

    def _cert_text(self, rep) -> str:
        out = []
        blocks, warns, infos = rep.of(_certify.BLOCK), rep.of(_certify.WARN), rep.of(_certify.INFO)
        if blocks:
            out.append("✗  Blocking — must fix before upload:")
            out += [f"    • {i.message}" for i in blocks]
        if warns:
            if out:
                out.append("")
            out.append("⚠  Composability (soft — the composer will see these):")
            out += [f"    • {i.message}" for i in warns]
        if infos:
            if out:
                out.append("")
            out += [f"    {i.message}" for i in infos]
        return "\n".join(out) or "Certified — no issues."

    def _dict_hash(self, d: dict) -> str:
        import hashlib
        import json
        body = json.dumps({k: d.get(k) for k in ("id", "spirit", "objectives", "forks",
                                                  "provides", "requires")},
                          sort_keys=True, default=str)
        return hashlib.sha256(body.encode()).hexdigest()

    def _runtime_grade(self, d: dict):
        """Grade the fragment's authoring board on the LIVE stack (Item 2): auto-start its Sources so
        the Sinks see traffic, grade every objective, then stop the Sources. Runs off the GUI thread.
        Returns a certify.RuntimeGrade (available=False when there's no running stack)."""
        import time
        from ..domain import objectives as _obj
        from ..domain import fragment_yaml as _fy
        from ..domain.probes import TypeRunner
        orch = getattr(self.ctx, "orchestrator", None)
        runner = None
        if orch is not None:
            from ..services.probe_runner import RuntimeRunner
            # measure() reads the LIVE streaming rider snapshots (ctx.rider_results), which is why we
            # start the Sinks too and let them accumulate before grading.
            base = RuntimeRunner(orch, lambda: self.ctx.topology, lambda: self.ctx.rider_results)
            if base.available():
                runner = TypeRunner(base, lambda: self.ctx.topology)
        if runner is None:
            return _certify.RuntimeGrade(available=False)

        started = []
        from ..domain.connection_rules import is_rider
        # start BOTH Sources (drive traffic) and Sinks (accumulate a live reading) as streaming
        # sessions — the same path that works when you double-click them by hand.
        for dev in list(self.ctx.topology.devices.values()):
            if is_rider(dev.type_key) and self.ctx.topology.donor_of(dev.id) is not None:
                if self.ctx.start_rider(dev.id).get("ok"):
                    started.append(dev.id)
        time.sleep(5.0)                                       # let traffic flow + sinks accumulate
        try:
            frag = _fy.fragment_from_dict(d)
            world = _obj.TopologyWorld(self.ctx.topology)
            # On a COMPOSED board, grade the composed instance's objectives (their labels match the
            # board); otherwise the fragment's own authoring objectives. instantiate() → Objective
            # objects (evaluate_all needs .is_behavioral(); the stored templates don't have it).
            objs = getattr(self, "_composed_objectives", None) or frag.instantiate()
            results = _obj.evaluate_all(objs, world, runner)
        finally:
            for rid in started:
                self.ctx.stop_rider(rid)
        return _certify.runtime_from_results(results, available=True)

    def _certify_current(self) -> None:
        d = self._current_dict()
        if d is None:
            return
        import threading
        self.ctx.log("Certifying (grading the board on the live stack)…", "info")

        def work():
            try:
                runtime = self._runtime_grade(d)
                rep = _certify.certify(d, library=self._library_excluding(d.get("id", "")),
                                       runtime=runtime)
            except Exception as e:                       # noqa: BLE001 — surface, never die silently
                rep = _certify.CertReport(fragment_id=d.get("id", ""))
                rep.add(_certify.BLOCK, "error", f"Certify hit an error: {e}")
            self._cert_ready.emit(rep, d)
        threading.Thread(target=work, daemon=True).start()

    def _on_cert_ready(self, rep, d) -> None:
        if rep.certified:
            self._certified_hash = self._dict_hash(d)          # Save may now stamp it certified
        title = "Certified ✓" if rep.certified else "Not certified ✗"
        QMessageBox.information(self, title, self._cert_text(rep))

    # ------------------------------------------------------------------ list
    def _authored_ids(self) -> list[str]:
        d = _content.user_content_dir()
        return sorted(p.stem for p in d.glob("*.yaml")) if d.exists() else []

    def _show_list(self) -> None:
        self._clear_body()
        self._body.addWidget(QLabel("<b>Your fragments</b>"))
        from PySide6.QtGui import QColor
        self.listw = QListWidget()
        for fid in self._authored_ids():
            f = _frag.get(fid)
            certified = bool(getattr(f, "certified", False))
            forks = f"   ·  {len(f.forks)} fork(s)" if (f and f.forks) else ""
            mark = "✓ " if certified else "○ "           # ✓ = runtime-certified, ○ = not yet
            it = QListWidgetItem(f"{mark}{fid}{forks}")
            it.setData(Qt.UserRole, fid)
            it.setToolTip("Certified — runtime-playtested (winnable + live)" if certified
                          else "Not certified — Run the topology and press Certify before upload")
            if certified:
                it.setForeground(QColor("#10B981"))       # green marks a certified block
            self.listw.addItem(it)
        self.listw.itemDoubleClicked.connect(lambda *_: self._edit_selected())
        self._body.addWidget(self.listw, 1)
        if not self._authored_ids():
            self._body.addWidget(QLabel("None yet — press ＋ (Create).", objectName="Faint"))
        up = QPushButton("  Upload selected to Teaching Center")
        up.setObjectName("Accent")                       # the primary action on the list — make it pop
        up.setIcon(_icons.icon("send", "#ffffff", 16))   # white glyph on the accent fill
        up.setMinimumHeight(34)
        up.clicked.connect(self._upload_selected)
        self._body.addWidget(up)

    def _selected_id(self) -> str | None:
        it = getattr(self, "listw", None) and self.listw.currentItem()
        return it.data(Qt.UserRole) if it else None

    def _delete_selected(self) -> None:
        fid = self._selected_id()
        if not fid:
            QMessageBox.information(self, "Delete", "Select a fragment in the list first.")
            return
        if QMessageBox.question(self, "Delete fragment", f"Delete '{fid}'?") != QMessageBox.Yes:
            return
        # Remove the authored file. Match by stem first, but also sweep any file whose INTERNAL id
        # equals fid — a fragment saved with a spaced/mixed-case id (e.g. "simple LAN.yaml") won't
        # match a slugified stem, and would otherwise survive the delete.
        import yaml as _yaml
        d = _content.user_content_dir()
        removed = False
        target = d / f"{fid}.yaml"
        if target.exists():
            target.unlink(missing_ok=True); removed = True
        for p in list(d.glob("*.yaml")) if d.exists() else []:
            try:
                spec = _yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            except Exception:                            # noqa: BLE001 — unreadable → skip
                continue
            if str(spec.get("id", "")) == fid or p.stem == fid:
                p.unlink(missing_ok=True); removed = True
        _frag.reload()
        if _frag.get(fid) is not None:                    # survived the unlink → it's a built-in
            QMessageBox.information(self, "Built-in fragment",
                f"'{fid}' is a built-in that ships with GINI, so it reloads from the app each "
                f"launch and can't be removed here. (Any authored copy was deleted.) Built-ins are "
                f"uncertified, so they no longer show up as dependency options.")
        elif removed:
            QMessageBox.information(self, "Deleted",
                f"Removed '{fid}' locally. Note: if you had published it to the Teaching Center, it "
                f"will re-sync on next sign-in — delete it on the Teaching Center too to remove it "
                f"for good.")
        self._show_list()

    def _edit_selected(self) -> None:
        fid = self._selected_id()
        if not fid:
            QMessageBox.information(self, "Edit", "Select a fragment in the list first.")
            return
        self._open_editor(fid)

    def _create(self) -> None:
        self._open_editor(None)

    def _load_board(self, stage: dict) -> None:
        """Restore a fragment's saved authoring board onto the canvas (types, positions, wiring, and
        rider attachments). Names auto-regenerate — grading is type-based, so that's fine."""
        devs = stage.get("devices", []) or []
        links = stage.get("links", []) or []
        if not devs:
            return
        self.ctx.clear_topology()
        idmap: dict[str, str] = {}
        for d in devs:
            inst = self.ctx.add_device(d["type_key"], x=float(d.get("x", 0)), y=float(d.get("y", 0)),
                                       properties=dict(d.get("properties") or {}))
            inst.slot = d.get("slot", "") or ""          # scaffold membership — drives `type@slot`
            idmap[d["id"]] = inst.id
            for attr in ("size", "w", "h"):
                if d.get(attr):
                    setattr(inst, attr, d[attr])
        for d in devs:                                   # containment, once every id is remapped
            pid = d.get("parent_id")
            if pid and pid in idmap:
                self.ctx.topology.devices[idmap[d["id"]]].parent_id = idmap[pid]
        for l in links:
            s, t = idmap.get(l.get("source_id")), idmap.get(l.get("target_id"))
            if not (s and t):
                continue
            try:
                if l.get("kind") == "attach":
                    self.ctx.add_attach(s, t)
                else:
                    self.ctx.add_link(s, t)
            except Exception:                            # noqa: BLE001 — a stale/invalid edge is skipped
                pass
        self.ctx.bus.topology_changed.emit()

    def _upload_selected(self) -> None:
        fid = self._selected_id()
        if not fid:
            QMessageBox.information(self, "Upload", "Select a fragment first."); return
        tc = getattr(self.ctx, "teaching_center", None)
        if tc is None or not getattr(tc, "is_teacher", lambda: False)():
            QMessageBox.information(self, "Upload", "Sign in as a teacher to upload fragments.")
            return
        from ..domain import fragment_yaml as _fy
        f = _frag.get(fid)
        if f is None:
            return
        # HARD gate: upload requires a runtime pass — the fragment must be certified (Run + Certify).
        if not getattr(f, "certified", False):
            QMessageBox.warning(self, "Not certified",
                                "This fragment isn't runtime-certified. Open it, Run the topology, "
                                "and press Certify so its live/output checks are proven — the "
                                "Teaching Center only accepts certified building blocks.")
            return
        # its live checks are proven (the stamp); surface any SOFT composability warnings
        rep = _certify.certify(self._dict_from_fragment(f), library=self._library_excluding(fid),
                               runtime=_certify.RuntimeGrade(available=True))
        if rep.blocked:
            QMessageBox.warning(self, "Not certified",
                                "This fragment can't go to the Teaching Center until it's fixed:\n\n"
                                + self._cert_text(rep))
            return
        if rep.of(_certify.WARN):
            if QMessageBox.question(self, "Certified with warnings",
                                    self._cert_text(rep) + "\n\nUpload anyway?") != QMessageBox.Yes:
                return
        import threading
        yaml_text = _fy.to_yaml(f)

        def work():
            res = tc.upload_fragment(yaml_text)
            self.ctx.log(f"Uploaded '{res.get('id', fid)}' — the Teaching Center can now compose "
                         f"experiments from it." if res.get("ok")
                         else f"Upload refused: {res.get('error', 'unknown error')}",
                         "ok" if res.get("ok") else "error")
        threading.Thread(target=work, daemon=True).start()
        self.ctx.log(f"Uploading '{fid}' to the Teaching Center…", "info")

    # ---------------------------------------------------------------- editor
    def _open_editor(self, fid: str | None) -> None:
        self._editing_id = fid
        self._steps = []
        self._forks = []
        self._scaffold_ids: set = set()      # loaded-dependency device ids (excluded from the delta)
        f = _frag.get(fid) if fid else None
        self._slots: list = [{"name": s.name, "role": s.role, "min": s.min, "max": s.max,
                              "distinct": s.distinct} for s in getattr(f, "slots", ())] \
            if f is not None else []
        self._peerings: list = [{"name": p.name, "role": p.role, "min": p.min, "max": p.max,
                                 "topology": p.topology} for p in getattr(f, "peerings", ())] \
            if f is not None else []
        # when the canvas holds a COMPOSED board (from Compose ×N), Certify must grade THESE objectives
        # (the composed instance), not the fragment's authoring @slot objectives whose labels differ.
        self._composed_objectives = None
        if f is not None:
            for o in f.objectives:
                self._steps.append({"id": o.id, "say": o.say, "check": o.check, "kind": o.kind,
                                    "probe": o.probe, "level": o.level or _obj.level_of(o),
                                    "stars": getattr(o, "stars", 0)})
            self._forks = [{"id": fk.id, "label": fk.label, "difficulty": fk.difficulty,
                            "kind": fk.kind,
                            "objectives": [{"id": o.id, "say": o.say, "check": o.check,
                                            "kind": o.kind, "probe": o.probe,
                                            "level": o.level or _obj.level_of(o)}
                                           for o in fk.objectives]} for fk in f.forks]
            # bring the fragment's own authoring board back, so steps/ports/contract all agree
            if getattr(f, "stage", None) and f.stage.get("devices"):
                if (not self.ctx.topology.devices
                        or QMessageBox.question(self, "Load board",
                                                "Load this fragment's saved board onto the canvas? "
                                                "(replaces what's currently there)")
                        == QMessageBox.Yes):
                    self._load_board(f.stage)
                    # Slotted devices ARE the scaffold (materialized dependencies); the delta carries
                    # no slot. Rebuild the exclude set so re-save/recompute stays delta-only.
                    self._scaffold_ids = {did for did, dev in self.ctx.topology.devices.items()
                                          if getattr(dev, "slot", "")}

        self._clear_body()

        # --- meta card ------------------------------------------------------
        meta = QVBoxLayout(); meta.setContentsMargins(10, 8, 10, 8); meta.setSpacing(4)
        self.fid = QLineEdit(fid or ""); self.fid.setPlaceholderText("fragment id (e.g. simple-lan)")
        if fid:
            self.fid.setReadOnly(True)
        self.teaches = QLineEdit(getattr(f, "teaches", "") if f else "")
        self.teaches.setPlaceholderText("teaches — a concept key")
        self.summary = QLineEdit(getattr(f, "summary", "") if f else "")
        self.summary.setPlaceholderText("one-line summary the student sees")
        self.spirit = QLineEdit(getattr(f, "spirit", "") if f else "")
        self.spirit.setPlaceholderText("spirit — what counts as success (the AI reasons on this)")
        meta.addWidget(self.fid); meta.addWidget(self.teaches)
        meta.addWidget(self.summary); meta.addWidget(self.spirit)
        self._body.addWidget(self._card(meta))

        # --- actions card ---------------------------------------------------
        acts = QHBoxLayout(); acts.setContentsMargins(8, 6, 8, 6); acts.setSpacing(6)
        self.record_btn = QPushButton("  Record")
        self.record_btn.setCheckable(True)
        self.record_btn.setToolTip("Scan mode — build on the canvas and each action becomes a step")
        self.record_btn.toggled.connect(self._toggle_record)
        acts.addWidget(self.record_btn)
        read = QPushButton("  Read canvas"); read.setIcon(self._ic("search", 16))
        read.setToolTip("Read objectives off the board you've already built")
        read.clicked.connect(self._read_once); acts.addWidget(read)
        dep = QPushButton("  Add dependency"); dep.setIcon(self._ic("layers", 16))
        dep.setToolTip("Load a provider fragment as a locked scaffold and build on top of it")
        dep.clicked.connect(self._add_dependency); acts.addWidget(dep)
        acts.addStretch(1)
        live = QPushButton(); live.setIcon(self._ic("metrics", 16)); live.setToolTip("Add a live check")
        live.clicked.connect(self._add_live); acts.addWidget(live)
        # (difficulty is now per-step stars — a step's ★ marks it as a harder progressive pass; the
        # old "Add fork" button is retired.)
        cert = QPushButton("Certify"); cert.setToolTip("Check this fragment (compiler + composability)")
        cert.clicked.connect(self._certify_current); acts.addWidget(cert)
        comp = QPushButton("Compose ×N")
        comp.setToolTip("Scale this fragment: bind its slot to N certified providers, build the whole "
                        "topology on the canvas, and grade it")
        comp.clicked.connect(self._compose_validate); acts.addWidget(comp)
        self._body.addWidget(self._card(acts))
        self._sync_record_btn()

        # --- Ports (In / Out) — attached Sources & Sinks -------------------- #
        self._ports_box = QVBoxLayout(); self._ports_box.setSpacing(2)
        self._ports_box.setContentsMargins(10, 2, 10, 2)
        self._body.addWidget(self._card(self._ports_box))

        self.rec_note = QLabel(""); self.rec_note.setObjectName("Faint")
        self.rec_note.setWordWrap(True)
        self._body.addWidget(self.rec_note)

        # --- steps (custom rows with per-row controls) ----------------------
        self._steps_box = QVBoxLayout(); self._steps_box.setSpacing(4)
        self._steps_box.setContentsMargins(2, 2, 2, 2)
        inner = QWidget(); inner.setLayout(self._steps_box)
        scroll = QScrollArea(); scroll.setWidget(inner); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        self._body.addWidget(scroll, 1)

        # --- footer ---------------------------------------------------------
        foot = QHBoxLayout()
        back = QPushButton("Back to list"); back.clicked.connect(self._show_list); foot.addWidget(back)
        foot.addStretch(1)
        save = QPushButton("  Save fragment"); save.setObjectName("Accent")
        save.setIcon(self._ic("save", 16)); save.clicked.connect(self._finalize)
        foot.addWidget(save)
        fw = QWidget(); fw.setLayout(foot); self._body.addWidget(fw)
        self._render_steps()
        self._render_ports()

    # -- recording -----------------------------------------------------------
    def _sync_record_btn(self) -> None:
        on = self._recorder is not None
        self.record_btn.setText("  Stop" if on else "  Record")
        self.record_btn.setIcon(self._ic("minus" if on else "robot", 16))

    def _toggle_record(self, on: bool) -> None:
        if on:
            self._recorder = _au.Recorder(exclude=self._scaffold_ids)
            self._recorder.steps = list(self._steps)
            self._recorder._by_key = {s.get("key", s["id"]): s for s in self._steps}
            self.ctx.bus.topology_changed.connect(self._on_change)
            self._rec_connected = True
            self.rec_note.setText("Recording — build on the canvas; each action becomes a step.")
        else:
            self._disconnect_recorder()
            if self._recorder is not None:
                self._steps = self._recorder.result()
            self._recorder = None
            self.rec_note.setText("Stopped. Reorder ▲▼, delete ✕, or click a level chip to change it.")
            self._render_steps()
        self._sync_record_btn()

    def _disconnect_recorder(self) -> None:
        if getattr(self, "_rec_connected", False):
            self.ctx.bus.topology_changed.disconnect(self._on_change)
            self._rec_connected = False

    def _on_change(self) -> None:
        if self._recorder is not None:
            self._recorder.capture(self.ctx.topology)
            self._steps = self._recorder.result()
            self._render_steps()
        self._render_ports()                     # attaching a rider updates the Ports panel live

    def closeEvent(self, e) -> None:
        self._disconnect_recorder()
        super().closeEvent(e)

    def _read_once(self) -> None:
        self._composed_objectives = None            # back to authoring — grade the fragment, not a compose
        derived = _au.derive_objectives(self.ctx.topology, exclude=self._scaffold_ids)
        have = {s.get("key") for s in self._steps}
        for d in derived:
            if d["key"] not in have:
                self._steps.append(d)
        self._render_steps()
        self._render_ports()
        self.rec_note.setText(f"Read {len(derived)} candidate(s) from the canvas.")

    def _add_dependency(self) -> None:
        """Load a certified provider fragment onto the canvas as a LOCKED scaffold, so a *dependent*
        fragment (router/gateway/firewall) can be authored + certified on top of it. Its `provides`
        become this fragment's `requires`; only the delta the teacher builds is captured and saved."""
        self._composed_objectives = None            # editing again — Certify grades the fragment
        # a dependency must be a CERTIFIED block — you only build on proven, runtime-playtested
        # fragments (this also hides the uncertified built-in samples).
        providers = [f for f in _frag.all_fragments()
                     if getattr(f, "provides", ()) and getattr(f, "certified", False)]
        if not providers:
            QMessageBox.information(self, "Add dependency",
                "No certified providers yet. A dependency must be a CERTIFIED fragment that "
                "`provides` a capability (e.g. a LAN that provides an l2-fabric). Author one, Run "
                "the topology, and press Certify — then it can be built on.")
            return
        labels = [f"{f.id}   ·   provides {', '.join(f.provides)}" for f in providers]
        pick, ok = QInputDialog.getItem(
            self, "Add dependency",
            "Build on which provider? It loads as a LOCKED scaffold — only your delta is saved:",
            labels, 0, False)
        if not ok:
            return
        prov = providers[labels.index(pick)]
        slot_role = self._slot_role(prov)

        # Cardinal? A repeatable leg (min≥2, unbounded) is what the composer SCALES — the fragment
        # becomes "a router over N of these", not a fixed 2-legged one. A single leg keeps the old
        # A/B behaviour. We drop `min` representatives on the board, all sharing ONE slot name, so the
        # derived objective is the symbolic `link(router, switch@<slot>)` the assembler expands.
        n, ok = QInputDialog.getInt(
            self, "Repeatable leg — set the floor",
            "Minimum members that must bind (the floor).\n\n"
            "2 or more  →  a REPEATABLE leg the composer scales to N (a router over N LANs).\n"
            "1  →  a single fixed leg.\n\n"
            "Note: to author + certify, only TWO representatives are placed — that proves the pattern. "
            "Scaling to more is Compose ×N.",
            2, 1, 16, 1)
        if not ok:
            return
        cardinal = n >= 2

        # Cardinal legs can either be a HUB (each member links back to your delta — a router over N
        # LANs) or a lateral PEER GROUP (members interconnect as a graph — a mesh of routers).
        if cardinal:
            shape, ok = QInputDialog.getItem(
                self, "How do the members connect?",
                "none  →  each links to YOUR delta (a hub, e.g. a router over N LANs)\n"
                "mesh / ring / line / star  →  the members interconnect as PEERS (a graph of routers)",
                ["none (hub from your delta)", "mesh", "ring", "line", "star"], 0, False)
            if not ok:
                return
            shape = shape.split()[0]
            if shape != "none":
                base, name, k = "sites", "sites", 0
                taken = {p["name"] for p in self._peerings} | {s["name"] for s in self._slots}
                while name in taken:
                    k += 1; name = f"{base}{k}"
                self._peerings.append({"name": name, "role": slot_role, "min": n, "max": 0,
                                       "topology": shape})
                self._steps.append({"id": f"reach-{name}",
                                    "say": f"every peer in {name} reaches every other",
                                    "kind": "behavioral",
                                    "probe": f"reach(host@{name} -> host@{name}) == ok", "level": 4})
                self._render_steps()
                self.rec_note.setText(
                    f"Declared a {shape} peer group '{name}' of {n}+ {slot_role} members. Press "
                    f"Compose ×N to build + validate the graph.")
                return
            base = "lans" if "l2-fabric" in prov.provides else "nets"
            slot_name = base
            k = 0
            while any(s["name"] == slot_name for s in self._slots):
                k += 1; slot_name = f"{base}{k}"
        else:
            slot_name = chr(ord("A") + len(self._slots))     # A, B, C, … (fixed leg)

        # AUTHOR the pattern with just TWO representatives — enough to prove the connection and keep
        # the certify board small (fast boot). The floor `n` still rides on the slot for Compose ×N.
        reps = 2 if cardinal else 1
        composite = bool(getattr(prov, "slots", ()) or getattr(prov, "peerings", ()))
        if composite:
            # A composite provider (a routed-network with its own LANs) is materialized FULLY through
            # the composer so its internals show — router + LANs + hosts — not just its top router.
            # Resolve its sub-binding once, reuse it for each representative.
            sub = self._resolve_binding(prov)
            if sub is None:
                return
            from ..domain import compose as _compose
            for r in range(reps):
                try:
                    subtopo, _ = _compose.materialize(sub)
                except _compose.CompositionError as e:
                    QMessageBox.warning(self, "Add dependency", str(e)); return
                self._merge_scaffold(subtopo, slot_name, col=r)
        else:
            objs = [{"check": o.check} for o in prov.instantiate() if o.check]
            for _ in range(reps):                            # drop `reps` representatives of the LAN
                ids = _au.materialize(self.ctx, objs)
                for did in ids:                              # all share the one slot name
                    self.ctx.topology.devices[did].slot = slot_name
                self._scaffold_ids |= set(ids)
        self._slots.append({"name": slot_name, "role": slot_role,
                            "min": n if cardinal else 1, "max": 0 if cardinal else 1,
                            "distinct": True})
        self.ctx.bus.topology_changed.emit()
        kind = f"a repeatable slot (bind {n}+, the composer scales it)" if cardinal else "a single leg"
        hint = (f"router@{slot_name}" if composite else f"switch@{slot_name}")   # what to wire the delta to
        self.rec_note.setText(
            f"Loaded '{prov.id}' as slot {slot_name} — {kind}. Wire your delta to it and refer to it "
            f"as type@{slot_name} — e.g. link(router, {hint}). Only your delta is saved; press "
            f"Compose ×N to scale + validate.")

    def _slot_role(self, prov) -> str:
        """The role a slot should require: the ROOT ancestor of a network-ish provide (loose, so the
        composer can substitute a LAN or a routed sub-network → recursion), else the first provide."""
        from ..domain import capabilities as _caps
        best = None
        for r in prov.provides:
            for anc in _caps.ancestors(r):
                if not _caps.PARENTS.get(anc):               # a root role
                    if anc == "network":
                        return "network"
                    best = best or anc
        return best or (prov.provides[0] if prov.provides else "network")

    def _compose_validate(self) -> None:
        """Scale THIS fragment: bind each slot to N certified providers, materialize the whole
        topology on the canvas, and grade it structurally. This is the generative step — a hand-built
        fragment becomes a real N-node network the oracle checks. (Run + Certify then proves it live.)"""
        from ..domain import compose as _compose
        from ..domain import fragment_yaml as _fy
        d = self._current_dict()
        if not d:
            QMessageBox.information(self, "Compose", "Give the fragment an id and some steps first.")
            return
        frag = _fy.fragment_from_dict(d)
        if not (frag.slots or frag.peerings):
            QMessageBox.information(self, "Compose ×N",
                "This fragment has nothing to scale. Press 'Add dependency', choose a certified "
                "provider, and make it a repeatable leg (2+) or a peer group (mesh/ring/…).")
            return
        _frag.FRAGMENTS[frag.id] = frag                      # so the assembler can resolve it by id
        binding = self._resolve_binding(frag)
        if binding is None:
            return
        mode_pick, ok = QInputDialog.getItem(self, "Grading mode",
            "How are students graded?\n\n"
            "• open — any N ≥ the floor passes (the general pattern; the composer's power move)\n"
            "• fixed — exactly this N (a specific, reproducible lab)",
            ["open (student picks N ≥ floor)", "fixed (exactly this N)"], 0, False)
        if not ok:
            return
        mode = "open" if mode_pick.startswith("open") else "fixed"
        try:
            topo, objs = _compose.materialize(binding, mode=mode)
        except _compose.CompositionError as e:
            QMessageBox.warning(self, "Compose ×N", str(e))
            return
        from ..domain import objectives as _obj
        results = _obj.evaluate_all(objs, _obj.TopologyWorld(topo), None)   # structural preview
        self._composed_objectives = objs            # Certify (after Run) grades THESE, live
        self._load_composed(topo)
        met = sum(1 for r in results if r.status == "met")
        pend = sum(1 for r in results if r.status == "pending")
        unmet = [r.say for r in results if r.status == "unmet"]
        nodes = len(topo.devices)
        gm = ("Open-N — students may build ANY N ≥ the floor; graded by quantifiers."
              if mode == "open" else "Fixed-N — students must match this exact shape.")
        msg = (f"Built a {nodes}-element sample topology on the canvas.\n{gm}\n\n"
               f"{met}/{len(results)} structural objectives met.")
        if pend:
            msg += (f"\n{pend} live check(s) pending — press Run, then Certify to prove reachability "
                    f"on the running stack.")
        if unmet:
            msg += "\n\nNot satisfied:\n• " + "\n• ".join(unmet[:8])
        QMessageBox.information(self, "Composition validated", msg)

    def _load_composed(self, topo) -> None:
        """Replace the canvas with a materialized composition, laid out in a simple grid and keeping
        each device's slot tag so the scaled network is visible and re-gradable."""
        self.ctx.clear_topology()
        idmap: dict[str, str] = {}
        for i, dv in enumerate(topo.devices.values()):
            col, row = i % 8, i // 8
            inst = self.ctx.add_device(dv.type_key, x=90 + col * 120, y=90 + row * 120)
            inst.slot = getattr(dv, "slot", "")
            idmap[dv.id] = inst.id
        for l in topo.links.values():
            s, t = idmap.get(l.source_id), idmap.get(l.target_id)
            if not (s and t):
                continue
            try:
                self.ctx.add_attach(s, t) if l.kind == "attach" else self.ctx.add_link(s, t)
            except Exception:                                # noqa: BLE001 — skip an invalid edge
                pass
        self._scaffold_ids = set()                           # a composed board isn't a scaffold
        self.ctx.bus.topology_changed.emit()

    def _merge_scaffold(self, topo, slot_name: str, col: int = 0) -> None:
        """Merge a fully-materialized composite provider onto the canvas as a scaffold — every device
        tagged with `slot_name` (so the delta refers to it as type@slot), laid out in a column so
        representatives don't overlap. Its links (and rider attachments) come along too."""
        idmap: dict[str, str] = {}
        base_x = 70 + col * 300
        for i, dv in enumerate(topo.devices.values()):
            inst = self.ctx.add_device(dv.type_key, x=base_x + (i % 3) * 85, y=70 + (i // 3) * 90)
            inst.slot = slot_name
            idmap[dv.id] = inst.id
            self._scaffold_ids.add(inst.id)
        for l in topo.links.values():
            s, t = idmap.get(l.source_id), idmap.get(l.target_id)
            if not (s and t):
                continue
            try:
                self.ctx.add_attach(s, t) if l.kind == "attach" else self.ctx.add_link(s, t)
            except Exception:                                # noqa: BLE001 — skip an invalid edge
                pass

    @staticmethod
    def _materializable(f) -> bool:
        """A provider the assembler can build: a leaf with a saved board, or a composite (has its own
        slots/peerings, built by derivation)."""
        return (bool((getattr(f, "stage", None) or {}).get("devices"))
                or bool(getattr(f, "slots", ())) or bool(getattr(f, "peerings", ())))

    def _resolve_binding(self, frag) -> dict | None:
        """Recursively ask which certified provider (and how many) fills each slot / peer group —
        descending into composite providers — and return a composition binding, or None if cancelled."""
        bind: dict = {}
        for S in frag.slots:
            members = self._pick_members(S.name, S.role, S.min, S.max, cardinal=(S.max != 1))
            if members is None:
                return None
            bind.setdefault("bind", {})[S.name] = members
        for PG in frag.peerings:
            members = self._pick_members(PG.name, PG.role, PG.min, PG.max, cardinal=True)
            if members is None:
                return None
            bind.setdefault("peer", {})[PG.name] = members
        return {"fragment": frag.id, **bind}

    def _pick_members(self, name, role, mn, mx, cardinal):
        from ..domain import capabilities as _caps
        provs = [f for f in _frag.all_fragments()
                 if getattr(f, "certified", False) and _caps.any_satisfies(f.provides, role)
                 and self._materializable(f)]
        if not provs:
            QMessageBox.information(self, "Compose ×N",
                f"No certified provider fills '{name}' (role {role}). Certify a {role} fragment first.")
            return None
        names = [f.id for f in provs]
        pick, ok = QInputDialog.getItem(self, f"Fill '{name}'",
            f"Fill '{name}' ({role}) with which certified provider?", names, 0, False)
        if not ok:
            return None
        n = mn
        if cardinal:
            n, ok = QInputDialog.getInt(self, f"Fill '{name}'",
                f"How many '{pick}' in '{name}'?", max(2, mn), mn, mx or 32, 1)
            if not ok:
                return None
        pf = _frag.get(pick)
        member = pick
        if pf is not None and (pf.slots or pf.peerings):     # composite → resolve its sub-binding once
            member = self._resolve_binding(pf)
            if member is None:
                return None
        return [member] * n                                  # a uniform group of the chosen provider

    # -- step rows -----------------------------------------------------------
    def _render_steps(self) -> None:
        while self._steps_box.count():
            it = self._steps_box.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None); w.deleteLater()
        for i, s in enumerate(self._steps):
            self._steps_box.addWidget(self._step_row(i, s))
        self._steps_box.addStretch(1)

    def _step_row(self, i: int, s: dict) -> QWidget:
        row = QWidget()                              # flat row — no per-row box (many frames read busy)
        h = QHBoxLayout(row); h.setContentsMargins(6, 2, 6, 2); h.setSpacing(6)
        lvl = int(s.get("level", 1))
        col = _LEVEL_COLOR.get(lvl, "#3B82F6")
        chip = QPushButton(_LEVEL_SHORT.get(lvl, "L1"))
        chip.setFixedSize(30, 22)
        chip.setToolTip(f"{_LEVEL_TIP.get(lvl, '')} — click to change the level")
        chip.setStyleSheet(
            f"QPushButton {{ background:{col}; color:white; font-weight:700; border:none; "
            f"border-radius:6px; }} QPushButton:hover {{ background:{col}; }}")
        chip.clicked.connect(lambda _=False, idx=i: self._cycle_level(idx))
        h.addWidget(chip)
        stars = int(s.get("stars", 0) or 0)
        star = QToolButton()
        star.setText("☆" if stars == 0 else "★" * stars)
        star.setAutoRaise(True)
        star.setToolTip("Difficulty pass — click to add a star (0 = base pass, ★ = harder pass "
                        "the student unlocks after the base)")
        star.clicked.connect(lambda _=False, idx=i: self._cycle_stars(idx))
        h.addWidget(star)
        body = s.get("check") or s.get("probe") or ""
        lbl = QLabel(s.get("say", "")); lbl.setToolTip(body)
        h.addWidget(lbl, 1)
        for glyph, tip, slot in (("▲", "Move up", lambda _=False, idx=i: self._move(idx, -1)),
                                 ("▼", "Move down", lambda _=False, idx=i: self._move(idx, 1)),
                                 ("✕", "Delete step", lambda _=False, idx=i: self._del(idx))):
            b = QToolButton(); b.setText(glyph); b.setAutoRaise(True); b.setToolTip(tip)
            b.clicked.connect(slot); h.addWidget(b)
        return row

    def _move(self, i: int, delta: int) -> None:
        j = i + delta
        if 0 <= i < len(self._steps) and 0 <= j < len(self._steps):
            self._steps[i], self._steps[j] = self._steps[j], self._steps[i]
            self._render_steps()

    def _del(self, i: int) -> None:
        if 0 <= i < len(self._steps):
            del self._steps[i]
            self._render_steps()

    def _cycle_level(self, i: int) -> None:
        if 0 <= i < len(self._steps):
            self._steps[i]["level"] = (int(self._steps[i].get("level", 1)) % 4) + 1
            self._render_steps()

    def _cycle_stars(self, i: int) -> None:
        if 0 <= i < len(self._steps):
            self._steps[i]["stars"] = (int(self._steps[i].get("stars", 0) or 0) + 1) % 4  # 0…3 passes
            self._render_steps()

    # -- ports (In / Out from attached Sources & Sinks) ----------------------
    def _canvas_riders(self):
        """Attached riders on the canvas, split (sources, sinks) — the fragment's In / Out ports."""
        from ..domain.connection_rules import is_rider
        srcs, snks = [], []
        for d in self.ctx.topology.devices.values():
            if not is_rider(d.type_key) or self.ctx.topology.donor_of(d.id) is None:
                continue
            (srcs if getattr(d.type, "role", "") == "source" else snks).append(d)
        return srcs, snks

    def _has_check(self, rider_type: str) -> bool:
        return any(s.get("key", "").startswith(f"measure:{rider_type}|") for s in self._steps)

    def _render_ports(self) -> None:
        while self._ports_box.count():
            it = self._ports_box.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None); w.deleteLater()
        srcs, snks = self._canvas_riders()
        self._ports_box.addWidget(QLabel("<b>Ports</b>"))
        if not srcs and not snks:
            self._ports_box.addWidget(QLabel(
                "No Sources/Sinks attached. Drop a probe on an element to add an input or output "
                "port — its measurement becomes a gradable output.", objectName="Faint"))
            return
        for title, group in (("In · sources", srcs), ("Out · sinks", snks)):
            if not group:
                continue
            self._ports_box.addWidget(QLabel(title, objectName="Faint"))
            for d in group:
                self._ports_box.addWidget(self._port_row(d))
        # the composition contract, auto-derived from the board (read-only — never typed)
        provides, requires = _au.derive_contract(self.ctx.topology)
        if provides or requires:
            prov = ", ".join(provides) or "—"
            req = ", ".join(requires) or "—"
            lbl = QLabel(f"Contract (auto):  provides {prov}   ·   requires {req}")
            lbl.setObjectName("Faint"); lbl.setWordWrap(True)
            self._ports_box.addWidget(lbl)

    def _port_row(self, d) -> QWidget:
        row = QWidget()
        h = QHBoxLayout(row); h.setContentsMargins(6, 1, 6, 1); h.setSpacing(6)
        donor = self.ctx.topology.donor_of(d.id)
        tick = "✓ " if self._has_check(d.type_key) else ""
        h.addWidget(QLabel(f"{tick}{d.type.label} on {donor.name if donor else '?'}"), 1)
        if _riders.metrics_for(d.type_key):
            b = QToolButton(); b.setText("＋ check"); b.setAutoRaise(True)
            b.setToolTip("Assert on this rider's measurement — its output check")
            b.clicked.connect(lambda _=False, tk=d.type_key: self._add_output_check(tk))
            h.addWidget(b)
        return row

    def _add_output_check(self, rider_type: str) -> None:
        metrics = _riders.metrics_for(rider_type)
        if not metrics:
            return
        labels = [lbl for _, lbl in metrics]
        keys = [k for k, _ in metrics]
        mi, ok = QInputDialog.getItem(self, "Output check", "Measure:", labels, 0, False)
        if not ok:
            return
        metric = keys[labels.index(mi)]
        op, ok = QInputDialog.getItem(self, "Output check", "Condition:",
                                      [">=", "<=", ">", "<", "=="], 0, False)
        if not ok:
            return
        val, ok = QInputDialog.getDouble(self, "Output check", "Threshold:", 1.0, -1e9, 1e9, 2)
        if not ok:
            return
        obj = _au.output_check(rider_type, metric, op, val)
        self._steps = [s for s in self._steps if s.get("key") != obj["key"]]   # replace same metric
        self._steps.append(obj)
        self._render_steps()
        self._render_ports()

    # -- add live / fork -----------------------------------------------------
    def _canvas_types(self) -> list[str]:
        return sorted({d.type_key for d in self.ctx.topology.devices.values()})

    def _canvas_specs(self) -> list[str]:
        """Distinct type specs on the canvas, slot-scoped where tagged: host, switch@A, host@B, …
        This is what lets a live check target a specific slot — reach(host@A → host@B)."""
        specs = set()
        for d in self.ctx.topology.devices.values():
            if getattr(d.type, "rider", False):
                continue
            slot = getattr(d, "slot", "")
            specs.add(f"{d.type_key}@{slot}" if slot else d.type_key)
        return sorted(specs)

    def _add_live(self) -> None:
        from ..domain import devices as _dev
        specs = self._canvas_specs()
        if not specs:
            QMessageBox.information(self, "Add a live check",
                                    "Put the elements on the canvas first — a live check is expressed "
                                    "on the fragment's own elements.")
            return

        def label(spec: str) -> str:
            tk, _, slot = spec.partition("@")
            return f"{_dev.get(tk).label}{(' · slot ' + slot) if slot else ''}  ({spec})"

        disp = {label(s): s for s in specs}
        labels = list(disp)
        src, ok = QInputDialog.getItem(self, "Add a live check", "Source (on the canvas):",
                                       labels, 0, False)
        if not ok:
            return
        dst, ok = QInputDialog.getItem(self, "Add a live check", "Destination (on the canvas):",
                                       labels, 0, False)
        if not ok:
            return
        expect, ok = QInputDialog.getItem(self, "Add a live check", "Expected:",
                                          ["reaches it", "cannot reach it"], 0, False)
        if not ok:
            return
        self._steps.append(_au.live_check(disp[src], disp[dst], expect.startswith("reaches")))
        self._render_steps()

    # -- finalize ------------------------------------------------------------
    def _current_dict(self) -> dict | None:
        """The authored fragment as a dict, from the current editor state (or None if not ready)."""
        raw = self.fid.text().strip()
        if not raw:
            QMessageBox.warning(self, "Fragment", "Give the fragment an id first."); return None
        if not self._steps:
            QMessageBox.warning(self, "Fragment", "Add at least one step first."); return None
        # auto-derive the contract from the DELTA (scaffold excluded); the slots ARE the requires
        # (one per named dependency), so requires = slot roles + any grammar needs of the delta.
        provides, requires = _au.derive_contract(self.ctx.topology, exclude=self._scaffold_ids)
        requires = sorted(set(requires) | {s["role"] for s in self._slots}
                          | {p["role"] for p in self._peerings})
        # a standalone fragment carries its authoring board; a dependent one (built on a scaffold)
        # doesn't — its board only makes sense once the dependency is composed back in.
        # Save the FULL authoring board — including any scaffold dependencies and their slot tags —
        # so reloading a slot-based fragment restores exactly what was certified (the slot-scoped
        # predicates need the slotted devices present to re-grade). The shipped delta is still
        # scaffold-excluded via `_scaffold_ids`; `stage` is author-side metadata the composer ignores.
        stage = self.ctx.topology.to_dict()
        return _au.build_fragment_dict(
            frag_id=_au.slug(raw), teaches=self.teaches.text().strip(),
            summary=self.summary.text().strip(), spirit=self.spirit.text().strip(),
            objectives=self._steps, forks=self._forks or None,
            provides=provides or None, requires=requires or None, slots=self._slots or None,
            peerings=self._peerings or None,
            stage=stage if (stage and stage.get("devices")) else None, author=self._author)

    def _finalize(self) -> None:
        raw = self.fid.text().strip()
        if not raw:
            QMessageBox.warning(self, "Save", "Give the fragment an id."); return
        if not self._steps:
            QMessageBox.warning(self, "Save", "Add at least one step."); return
        # no dangling output: a Sink with no output check can't be graded — offer to fix first
        _, snks = self._canvas_riders()
        dangling = [d for d in snks if not self._has_check(d.type_key)]
        if dangling:
            names = ", ".join(dict.fromkeys(d.type.label for d in dangling))
            if QMessageBox.question(
                    self, "Dangling output",
                    f"These Sinks have no output check, so nothing is graded on their output: "
                    f"{names}.\nSave anyway? (Cancel to add a ＋ check in the Ports panel.)"
                    ) != QMessageBox.Yes:
                return
        fid = _au.slug(raw)
        if fid != raw and not self._editing_id:
            self.fid.setText(fid)
            self.rec_note.setText(f"Id normalized to '{fid}' (no spaces/punctuation in ids).")
        d = self._current_dict()
        if d is None:
            return
        # carry the runtime-certification stamp only if THIS exact content was certified green
        d["certified"] = (self._certified_hash is not None
                          and self._dict_hash(d) == self._certified_hash)
        problems = _au.validate_dict(d)
        if problems:
            QMessageBox.warning(self, "Not gradable", "; ".join(problems)); return
        _au.save_fragment(d)
        _frag.reload()
        QMessageBox.information(self, "Saved", f"Fragment '{fid}' saved to ~/.gini/content.")
        self._show_list()
