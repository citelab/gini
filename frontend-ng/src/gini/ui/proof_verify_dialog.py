"""Teacher mode — read a proof, say whether it holds together, and show what it says.

Strictly read-only. The dialog verifies, reports and renders; it never scores, never edits and
never phones anywhere. The instructor decides — this only puts the evidence in front of them,
which is the same invariant the mission oracle and the Reasoning Twin hold to.

Two things share the top of the window because they answer different questions. *Integrity*
asks whether the file is what gBuilder wrote. *Provenance* asks whether the topology handed in was
actually built in this chain. A proof can pass the first and fail the second — that is exactly the
"imported a friend's topology, generated my own proof" case, and it must not be able to hide
behind a green PASS.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QVBoxLayout,
)

from ..domain import narration as _narr
from ..domain import proof as _proof


class ProofVerifyDialog(QDialog):
    """Verify proof… — load a proof file (or paste one) and read the result."""

    def __init__(self, theme=None, parent=None) -> None:
        super().__init__(parent)
        self.theme = theme
        self._proof: dict | None = None
        self.setWindowTitle("Verify proof")
        self.setMinimumSize(760, 620)

        root = QVBoxLayout(self)

        row = QHBoxLayout()
        self.load_btn = QPushButton("Open proof file…")
        self.load_btn.clicked.connect(self._open)
        row.addWidget(self.load_btn)
        self.paste_btn = QPushButton("Paste proof")
        self.paste_btn.clicked.connect(self._paste)
        row.addWidget(self.paste_btn)
        row.addStretch(1)
        row.addWidget(QLabel("expected code:"))
        self.expect = QLineEdit()
        self.expect.setPlaceholderText("optional — the code you issued")
        self.expect.setFixedWidth(190)
        self.expect.textChanged.connect(self._render)
        row.addWidget(self.expect)
        root.addLayout(row)

        self.verdict = QLabel("Open a proof file to check it.")
        self.verdict.setObjectName("ProofVerdict")
        self.verdict.setTextFormat(Qt.RichText)
        self.verdict.setWordWrap(True)
        root.addWidget(self.verdict)

        self.provenance = QLabel("")
        self.provenance.setTextFormat(Qt.RichText)
        self.provenance.setWordWrap(True)
        root.addWidget(self.provenance)

        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.NoWrap)
        # The transcript is a column of aligned timestamps; a proportional font turns it into a
        # ragged mess that is much harder to skim than the same text in a fixed pitch.
        self.text.setStyleSheet("font-family: 'SF Mono', Menlo, Consolas, monospace;")
        root.addWidget(self.text, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        root.addWidget(buttons)

    # -- loading ------------------------------------------------------------- #
    def _open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open proof", "", "GINI proof (*.json)")
        if not path:
            return
        try:
            self._proof = _proof.load_proof(path)
        except _proof.ChainError as e:
            self._proof = None
            self._fail(str(e))
            return
        self._render()

    def _paste(self) -> None:
        from PySide6.QtGui import QGuiApplication
        clip = QGuiApplication.clipboard()
        try:
            self._proof = _proof.parse_proof(clip.text() if clip else "")
        except _proof.ChainError as e:
            self._proof = None
            self._fail(str(e))
            return
        self._render()

    # -- rendering ----------------------------------------------------------- #
    def set_proof(self, proof: dict) -> None:
        """Show a proof supplied by the caller (used by tests and by any future 'verify the one
        this student just generated' shortcut)."""
        self._proof = dict(proof or {})
        self._render()

    def _colour(self, ok: bool) -> str:
        if self.theme is None:
            return "#1a7f37" if ok else "#cf222e"
        t = self.theme.theme
        return t.success if ok else t.danger

    def _fail(self, message: str) -> None:
        self.verdict.setText(f'<b style="color:{self._colour(False)}">FAIL</b> — {message}')
        self.provenance.setText("")
        self.text.setPlainText("")

    def _render(self) -> None:
        if not self._proof:
            return
        expect = self.expect.text().strip() or None
        verdict = _proof.verify_proof(self._proof, expect_ticket=expect)
        entries = _proof.entries_of(self._proof)

        head = (f'<b style="color:{self._colour(verdict.ok)};font-size:15px">{verdict.label}</b>'
                f' — {verdict.reason or "the chain is intact and the proof is unedited."}')
        if verdict.broken_seq is not None:
            head += f'<br><span>The break is at entry {verdict.broken_seq}.</span>'
        receipt = _proof.receipt_code(self._proof)
        if receipt:
            head += f'<br><span>Receipt {receipt} · code {self._proof.get("ticket", "")}</span>'
        self.verdict.setText(head)

        acc = _proof.account_for_artifact(entries)
        if acc.total:
            ok = acc.ok
            self.provenance.setText(
                f'<b style="color:{self._colour(ok)}">'
                f'{"Built here" if ok else "NOT fully built here"}</b> — '
                f'{len(acc.built)} of {acc.total} submitted elements were placed action by '
                f'action under this code.'
                + (f' Imported: {", ".join(acc.imported)}.' if acc.imported else "")
                + (f' Already on the canvas when recording started: '
                   f'{", ".join(acc.preexisting)}.' if acc.preexisting else "")
                + (f' Unaccounted for: {", ".join(acc.unexplained)}.' if acc.unexplained else ""))
        else:
            self.provenance.setText("<i>No topology was submitted in this proof.</i>")

        self.text.setPlainText(_narr.narrate(entries, verdict))
