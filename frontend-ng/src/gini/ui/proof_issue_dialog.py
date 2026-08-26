"""Teacher mode — mint the assignment codes you hand out.

One code per student (or per group), because a code's whole job is to be *unique*: a proof is
bound to the code that recorded it, so a student who passes their completion code to a friend
gives them something that verifies against the wrong ticket and fails. That property only holds
if no two students share a code, which is why this issues a numbered batch rather than one code
you might be tempted to reuse across a class.

Deliberately NOT tracked. The codes carry no identity and this dialog stores nothing — you hand
row 1 to somebody and row 2 to somebody else, and whether you keep a note of who got which is
your choice, not GINI's. Nothing here phones anywhere.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout,
)

from ..domain import ticket as _ticket


class ProofIssueDialog(QDialog):
    """Issue codes… — mint a batch of assignment codes and save or copy the printout."""

    def __init__(self, theme=None, parent=None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.setWindowTitle("Issue assignment codes")
        self.setMinimumSize(560, 520)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        self._title = QLabel("Issue assignment codes")
        root.addWidget(self._title)
        self._sub = QLabel(
            "One code per student or group. A proof is bound to the code that recorded it, so "
            "codes must not be shared out twice.")
        self._sub.setWordWrap(True)
        root.addWidget(self._sub)

        form = QHBoxLayout()
        form.addWidget(QLabel("Assignment"))
        self._assignment = QLineEdit()
        self._assignment.setPlaceholderText("e.g. chap16-lab")
        form.addWidget(self._assignment, 1)
        form.addWidget(QLabel("How many"))
        self._count = QSpinBox()
        self._count.setRange(1, 500)
        self._count.setValue(30)
        form.addWidget(self._count)
        self._make = QPushButton("Generate")
        self._make.clicked.connect(self._generate)
        form.addWidget(self._make)
        root.addLayout(form)

        self._out = QPlainTextEdit()
        self._out.setReadOnly(True)            # these are minted, not typed: editing one breaks it
        self._out.setLineWrapMode(QPlainTextEdit.NoWrap)
        root.addWidget(self._out, 1)

        btns = QDialogButtonBox()
        self._copy = btns.addButton("Copy", QDialogButtonBox.ActionRole)
        self._save = btns.addButton("Save…", QDialogButtonBox.ActionRole)
        btns.addButton(QDialogButtonBox.Close)
        self._copy.clicked.connect(self._copy_all)
        self._save.clicked.connect(self._save_as)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

        self._apply_theme()
        if theme is not None and hasattr(theme, "themeChanged"):
            theme.themeChanged.connect(self._apply_theme)
        self._generate()

    # -- theming ------------------------------------------------------------ #
    def _apply_theme(self, *_a) -> None:
        t = getattr(self.theme, "theme", None)
        if t is None:
            return
        self._title.setStyleSheet(f"color:{t.text};font-size:14px;font-weight:600;")
        self._sub.setStyleSheet(f"color:{t.muted};font-size:11px;")
        self._out.setStyleSheet(
            f"QPlainTextEdit{{background:{t.panel2};color:{t.text};"
            f"border:1px solid {t.line};border-radius:8px;font-family:monospace;}}")

    # -- actions ------------------------------------------------------------ #
    def _generate(self) -> None:
        name = self._assignment.text().strip() or "assignment"
        # A collision at 55 bits of identity is not a real risk, but "no two students share a
        # code" is the property the whole scheme rests on, so make it true by construction
        # rather than by probability.
        seen: set[str] = set()
        codes: list[str] = []
        while len(codes) < self._count.value():
            t = _ticket.mint()
            if t.code in seen:
                continue
            seen.add(t.code)
            codes.append(t.pretty)               # printed grouped: the form a student types from
        lines = [f"GINI assignment codes — {name}",
                 "Hand ONE row to each student. They enter it in the Dashboard strip before "
                 "starting work; nothing is recorded without it.",
                 ""]
        lines += [f"{i:3}.  {c}" for i, c in enumerate(codes, 1)]
        self._out.setPlainText("\n".join(lines))

    def _copy_all(self) -> None:
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText(self._out.toPlainText())

    def _save_as(self) -> None:
        name = (self._assignment.text().strip() or "assignment").replace(" ", "-")
        path, _ = QFileDialog.getSaveFileName(self, "Save codes", f"{name}-codes.txt",
                                              "Text (*.txt)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self._out.toPlainText() + "\n")
        except OSError as e:                    # noqa: BLE001 - report, never crash the dialog
            self._sub.setText(f"Could not save: {e}")
