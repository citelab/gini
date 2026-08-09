"""Author a fragment — teacher mode.

The composer, built on what already exists: you build the winning arrangement on the real canvas,
this reads it into candidate objectives (by demonstration), you confirm/prune, optionally add a
difficulty **fork**, and finalize — which validates and writes the blessed YAML to the user content
layer (`~/.gini/content/fragments`). "Test" is the same Run/Check students use, so a fragment is
proven gradable by the real engine before it ships.

Deliberately a dialog over the live canvas rather than a new editor: the canvas IS the board, and the
mission engine IS the grader. We only add the authoring controls around them.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from ..domain import authoring as _au


class AuthorDialog(QDialog):
    def __init__(self, parent, ctx, *, author: str = "") -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._author = author
        self._objs: list[dict] = []            # core objectives (dicts)
        self._fork_objs: list[dict] = []       # objectives on the harder fork (optional)
        self.setWindowTitle("Author a fragment")
        self.setMinimumWidth(560)

        root = QVBoxLayout(self)
        root.addWidget(QLabel("<b>Author a fragment from what you've built on the canvas.</b> "
                              "Build the winning arrangement, read off the objectives, then finalize.",
                              wordWrap=True))

        form = QFormLayout()
        self.fid = QLineEdit(); self.fid.setPlaceholderText("a short id, e.g. private-db-vpc")
        form.addRow("Fragment id", self.fid)
        self.teaches = QLineEdit(); self.teaches.setPlaceholderText("concept key, e.g. vpc-networking")
        form.addRow("Teaches", self.teaches)
        self.summary = QLineEdit(); self.summary.setPlaceholderText("one line the student sees")
        form.addRow("Summary", self.summary)
        self.spirit = QLineEdit()
        self.spirit.setPlaceholderText("mechanism-free goal (what 'done' means, not how)")
        form.addRow("Spirit", self.spirit)
        root.addLayout(form)

        # --- core objectives (derived from the canvas) -----------------------
        head = QHBoxLayout()
        head.addWidget(QLabel("<b>Objectives</b> — derived from your canvas:"))
        head.addStretch(1)
        derive = QPushButton("⟲ Read from canvas")
        derive.clicked.connect(self._derive)
        head.addWidget(derive)
        root.addLayout(head)
        self.obj_list = QListWidget()
        self.obj_list.setMaximumHeight(150)
        root.addWidget(self.obj_list)
        rm = QPushButton("Remove selected objective"); rm.clicked.connect(self._remove_obj)
        root.addWidget(rm)

        # --- fork (optional difficulty branch) -------------------------------
        self.fork_on = QCheckBox("Add a harder fork (the difficulty knob — earns gold)")
        self.fork_on.toggled.connect(self._toggle_fork)
        root.addWidget(self.fork_on)
        self._fork_box = QWidget(); fb = QVBoxLayout(self._fork_box); fb.setContentsMargins(16, 0, 0, 0)
        frow = QHBoxLayout()
        self.fork_label = QLineEdit(); self.fork_label.setPlaceholderText("what the harder way is")
        frow.addWidget(self.fork_label, 1)
        self.fork_kind = QComboBox(); self.fork_kind.addItems(["converge", "diverge"])
        self.fork_kind.setToolTip("converge = a harder way to the SAME goal · diverge = a different path")
        frow.addWidget(self.fork_kind)
        fb.addLayout(frow)
        fhead = QHBoxLayout()
        fhead.addWidget(QLabel("Fork objectives — build the harder solution, then:"))
        fhead.addStretch(1)
        fderive = QPushButton("⟲ Read fork from canvas"); fderive.clicked.connect(self._derive_fork)
        fhead.addWidget(fderive)
        fb.addLayout(fhead)
        self.fork_list = QListWidget(); self.fork_list.setMaximumHeight(100)
        fb.addWidget(self.fork_list)
        self._fork_box.setVisible(False)
        root.addWidget(self._fork_box)

        self.note = QLabel(""); self.note.setWordWrap(True)
        root.addWidget(self.note)

        bb = QDialogButtonBox()
        self.test_btn = bb.addButton("Test on canvas", QDialogButtonBox.ActionRole)
        self.save_btn = bb.addButton("Finalize & save", QDialogButtonBox.AcceptRole)
        bb.addButton("Cancel", QDialogButtonBox.RejectRole)
        self.test_btn.clicked.connect(self._test)
        self.save_btn.clicked.connect(self._finalize)
        bb.rejected.connect(self.reject)
        root.addWidget(bb)

    # -- objective derivation ------------------------------------------------ #
    def _derive(self) -> None:
        self._objs = _au.derive_objectives(self.ctx.topology)
        self._render(self.obj_list, self._objs)
        if not self._objs:
            self._warn("The canvas is empty — place the winning arrangement first, then read it.")
        else:
            self._info(f"Read {len(self._objs)} objective(s). Prune any you don't want to grade.")

    def _derive_fork(self) -> None:
        # the fork's objectives are whatever is on the canvas NOW that isn't already a core objective
        core_checks = {o["check"] for o in self._objs}
        cand = [o for o in _au.derive_objectives(self.ctx.topology) if o["check"] not in core_checks]
        for o in cand:                          # keep fork objective ids distinct from core
            o["id"] = "fork-" + o["id"]
        self._fork_objs = cand
        self._render(self.fork_list, self._fork_objs)
        self._info(f"Fork: {len(cand)} objective(s) beyond the core.")

    def _render(self, widget: QListWidget, objs: list[dict]) -> None:
        widget.clear()
        for o in objs:
            it = QListWidgetItem(f"L{o['level']}   {o['say']}   ·   {o['check']}")
            it.setData(Qt.UserRole, o["id"])
            widget.addItem(it)

    def _remove_obj(self) -> None:
        row = self.obj_list.currentRow()
        if 0 <= row < len(self._objs):
            del self._objs[row]
            self._render(self.obj_list, self._objs)

    def _toggle_fork(self, on: bool) -> None:
        self._fork_box.setVisible(on)
        self.adjustSize()

    # -- build the dict ------------------------------------------------------ #
    def _dict(self) -> dict | None:
        fid = self.fid.text().strip()
        if not fid:
            self._warn("Give the fragment an id."); return None
        if not self._objs:
            self._warn("Read at least one objective from the canvas."); return None
        forks = None
        if self.fork_on.isChecked() and self._fork_objs:
            forks = [{"id": "hard", "label": self.fork_label.text().strip(),
                      "difficulty": 2, "kind": self.fork_kind.currentText(),
                      "objectives": self._fork_objs}]
        return _au.build_fragment_dict(
            frag_id=fid, teaches=self.teaches.text().strip(), summary=self.summary.text().strip(),
            spirit=self.spirit.text().strip(), objectives=self._objs, forks=forks,
            author=self._author)

    def _test(self) -> None:
        """Validate structurally, then hand the teacher a real playtest: build the fragment, drop into
        it as a mission, and use Run/Check. Here we just validate + confirm gradability; the caller
        launches the live mission."""
        d = self._dict()
        if d is None:
            return
        problems = _au.validate_dict(d)
        if problems:
            self._warn("Not gradable yet: " + "; ".join(problems))
            return
        self._info("Valid + gradable. Finalize to save, or keep refining. "
                   "(Play it as a mission to prove it winnable against real Docker.)")

    def _finalize(self) -> None:
        d = self._dict()
        if d is None:
            return
        try:
            path = _au.save_fragment(d)
        except ValueError as e:
            self._warn(f"Can't save — not gradable: {e}")
            return
        from ..domain import fragments as _frag
        _frag.reload()                          # pick the new fragment up immediately
        QMessageBox.information(self, "Saved",
                                f"Fragment '{d['id']}' saved and loaded.\n\n{path}\n\n"
                                f"Upload it to the Teaching Center to compose experiments from it.")
        self.accept()

    # -- notes --------------------------------------------------------------- #
    def _warn(self, msg): self.note.setText(msg); self.note.setStyleSheet("color:#d9534f;font-weight:600;")
    def _info(self, msg): self.note.setText(msg); self.note.setStyleSheet("color:#3fb950;")
