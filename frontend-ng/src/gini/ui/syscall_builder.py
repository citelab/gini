"""Syscall Builder — a visual front-end for adding a real system call to xv6.

The student names the call, declares its arguments and return type, and drops in a C body;
GINI generates the five real xv6 edits (see domain.syscall_builder) and previews them. Applying
writes the patches and recompiles xv6 — that step is the desktop (Mac) build; offline the dialog
still generates and previews the exact code, so the whole authoring experience is usable and
testable without a compiler.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QTableWidget, QVBoxLayout, QWidget,
)

from ..domain import syscall_builder as sb
from .theme import ThemeManager, icons

_ARG_LABELS = [("int", "int"), ("pointer (addr)", "addr"), ("string", "str")]


class SyscallBuilder(QDialog):
    def __init__(self, parent, theme: ThemeManager, device=None, on_apply=None,
                 existing=None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.device = device
        self.on_apply = on_apply                 # callable(Codegen) — Mac writes+recompiles
        self._existing = tuple(existing or sb.STOCK_SYSCALLS)
        self._added = 0                          # syscalls authored this session (for numbering)
        self._codegen = None

        t = theme.theme
        self.setWindowTitle("Syscall Builder — xv6")
        self.resize(940, 680)
        self.setStyleSheet(f"QDialog{{background:{t.bg};}}")
        root = QVBoxLayout(self)
        self._build_header(root)
        body = QHBoxLayout(); root.addLayout(body, 1)
        body.addWidget(self._build_form(), 0)
        body.addWidget(self._build_preview(), 1)
        self._build_footer(root)
        self._add_arg_row("n", "int")

    # -- header ----------------------------------------------------------- #
    def _build_header(self, root) -> None:
        t = self.theme.theme
        head = QHBoxLayout()
        ic = QLabel(); ic.setPixmap(icons.render_pixmap("compile", t.accent_for("red"), 22))
        title = QLabel("  Add a system call to xv6")
        title.setStyleSheet(f"color:{t.text};font-size:16px;font-weight:600;")
        head.addWidget(ic); head.addWidget(title); head.addStretch(1)
        root.addLayout(head)
        hint = QLabel("Declare it, write the C body, and GINI generates the five real xv6 "
                      "edits. Applying recompiles the kernel (desktop build).")
        hint.setWordWrap(True); hint.setStyleSheet(f"color:{t.muted};font-size:11px;")
        root.addWidget(hint)

    def _panel(self, title) -> tuple[QFrame, QVBoxLayout]:
        t = self.theme.theme
        f = QFrame(); f.setStyleSheet(
            f"QFrame{{background:{t.panel2};border:1px solid {t.line};border-radius:10px;}}")
        v = QVBoxLayout(f); v.setContentsMargins(10, 8, 10, 10)
        h = QLabel(title); h.setStyleSheet(
            f"color:{t.muted};font-size:11px;font-weight:600;border:none;")
        v.addWidget(h)
        return f, v

    # -- the form --------------------------------------------------------- #
    def _build_form(self) -> QFrame:
        t = self.theme.theme
        f, v = self._panel("Declaration")
        f.setFixedWidth(380)

        row = QHBoxLayout()
        row.addWidget(self._lbl("name"))
        self.name_edit = QLineEdit(); self.name_edit.setPlaceholderText("e.g. trace, hello")
        self.name_edit.setStyleSheet(self._edit_css())
        self.name_edit.textChanged.connect(self._on_name_changed)
        row.addWidget(self.name_edit, 1)
        row.addWidget(self._lbl("returns"))
        self.ret_combo = QComboBox(); self.ret_combo.addItems(list(sb.RET_TYPES))
        self.ret_combo.setStyleSheet(self._edit_css())
        row.addWidget(self.ret_combo)
        v.addLayout(row)

        v.addWidget(self._lbl("arguments (up to 6)"))
        self.args_tbl = QTableWidget(0, 2)
        self.args_tbl.setHorizontalHeaderLabels(["name", "type"])
        self.args_tbl.verticalHeader().setVisible(False)
        self.args_tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.args_tbl.setStyleSheet(
            f"QTableWidget{{background:{t.panel};color:{t.text};border:1px solid {t.line};"
            f"gridline-color:{t.line};font-size:12px;}}"
            f"QHeaderView::section{{background:{t.panel2};color:{t.muted};border:none;"
            "padding:4px;}")
        v.addWidget(self.args_tbl, 1)
        arow = QHBoxLayout()
        add = QPushButton("  + arg"); add.setStyleSheet(self._btn_css())
        add.clicked.connect(lambda: self._add_arg_row())
        rm = QPushButton("  − arg"); rm.setStyleSheet(self._btn_css())
        rm.clicked.connect(self._remove_arg_row)
        arow.addWidget(add); arow.addWidget(rm); arow.addStretch(1)
        v.addLayout(arow)

        v.addWidget(self._lbl("C body (runs in the kernel)"))
        self.body_edit = QPlainTextEdit(sb.starter_body())
        self.body_edit.setStyleSheet(
            f"QPlainTextEdit{{background:{t.panel};color:{t.text};border:1px solid {t.line};"
            "border-radius:6px;font-family:monospace;font-size:12px;}")
        self.body_edit.setFixedHeight(150)
        v.addWidget(self.body_edit)
        return f

    def _build_preview(self) -> QFrame:
        t = self.theme.theme
        f, v = self._panel("Generated xv6 edits (5 files)")
        self.preview = QPlainTextEdit(); self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("Press Generate to see the code GINI will add.")
        self.preview.setStyleSheet(
            f"QPlainTextEdit{{background:{t.panel};color:{t.text};border:1px solid {t.line};"
            "border-radius:6px;font-family:monospace;font-size:12px;}")
        v.addWidget(self.preview, 1)
        return f

    def _build_footer(self, root) -> None:
        t = self.theme.theme
        bar = QHBoxLayout()
        self.status = QLabel("")
        self.status.setStyleSheet(f"color:{t.muted};font-size:12px;")
        bar.addWidget(self.status, 1)
        gen = QPushButton("  Generate"); gen.setIcon(icons.icon("compile", t.accent_for("blue"), 14))
        gen.setStyleSheet(self._btn_css()); gen.clicked.connect(self._on_generate)
        bar.addWidget(gen)
        self.apply_btn = QPushButton("  Apply & recompile")
        self.apply_btn.setIcon(icons.icon("save", t.accent_for("green"), 14))
        self.apply_btn.setStyleSheet(self._btn_css()); self.apply_btn.setEnabled(False)
        self.apply_btn.clicked.connect(self._on_apply)
        bar.addWidget(self.apply_btn)
        root.addLayout(bar)

    # -- helpers ---------------------------------------------------------- #
    def _lbl(self, text) -> QLabel:
        lb = QLabel(text)
        lb.setStyleSheet(f"color:{self.theme.theme.muted};font-size:11px;border:none;")
        return lb

    def _edit_css(self) -> str:
        t = self.theme.theme
        return (f"QLineEdit,QComboBox{{background:{t.panel};color:{t.text};"
                f"border:1px solid {t.line};border-radius:6px;padding:4px 6px;}}")

    def _btn_css(self) -> str:
        t = self.theme.theme
        return (f"QPushButton{{color:{t.text};background:{t.panel2};border:1px solid {t.line};"
                f"border-radius:8px;padding:6px 12px;}}"
                f"QPushButton:hover{{border-color:{t.accent};}}"
                f"QPushButton:disabled{{color:{t.faint};}}")

    def _add_arg_row(self, name="", typ="int") -> None:
        if self.args_tbl.rowCount() >= 6:
            return
        r = self.args_tbl.rowCount()
        self.args_tbl.insertRow(r)
        edit = QLineEdit(name); edit.setStyleSheet(self._edit_css())
        self.args_tbl.setCellWidget(r, 0, edit)
        combo = QComboBox(); combo.setStyleSheet(self._edit_css())
        for label, val in _ARG_LABELS:
            combo.addItem(label, val)
        combo.setCurrentIndex(next((i for i, (_l, v) in enumerate(_ARG_LABELS) if v == typ), 0))
        self.args_tbl.setCellWidget(r, 1, combo)

    def _remove_arg_row(self) -> None:
        n = self.args_tbl.rowCount()
        if n:
            self.args_tbl.removeRow(n - 1)

    def _on_name_changed(self, text) -> None:
        # keep the starter body's printf in sync with the name until the user edits it
        if self.body_edit.toPlainText().strip().startswith("// read your args"):
            self.body_edit.setPlainText(sb.starter_body(text))

    # -- spec + actions --------------------------------------------------- #
    def spec_from_form(self) -> sb.SyscallSpec:
        args = []
        for r in range(self.args_tbl.rowCount()):
            nm = self.args_tbl.cellWidget(r, 0).text().strip()
            tp = self.args_tbl.cellWidget(r, 1).currentData()
            if nm:
                args.append(sb.SyscallArg(nm, tp))
        return sb.SyscallSpec(name=self.name_edit.text().strip(),
                              ret=self.ret_combo.currentText(),
                              args=args, body=self.body_edit.toPlainText())

    def _on_generate(self) -> None:
        t = self.theme.theme
        spec = self.spec_from_form()
        errs = sb.validate(spec, existing=self._existing)
        if errs:
            self._codegen = None
            self.apply_btn.setEnabled(False)
            self.preview.setPlainText("")
            self.status.setText("⚠ " + errs[0])
            self.status.setStyleSheet(f"color:{t.danger};font-size:12px;")
            return
        cg = sb.generate(spec, number=sb.NEXT_FREE_NUMBER + self._added)
        self._codegen = cg
        self.preview.setPlainText(cg.preview())
        self.apply_btn.setEnabled(True)
        self.status.setText(f"✓ Ready — SYS_{spec.name} = {cg.number}. "
                            "Review the 5 edits, then Apply.")
        self.status.setStyleSheet(f"color:{t.success};font-size:12px;")

    def _on_apply(self) -> None:
        t = self.theme.theme
        if self._codegen is None:
            return
        if self.on_apply is not None:
            try:
                name = self.spec_from_form().name
                self.on_apply(self._codegen)
                self._added += 1
                self._existing = self._existing + (name,)   # block re-using this name
                self.status.setText("Applied — writing the 5 edits and recompiling xv6…")
                self.status.setStyleSheet(f"color:{t.success};font-size:12px;")
                self.apply_btn.setEnabled(False)
            except Exception as e:
                self.status.setText(f"⚠ Apply failed: {e}")
                self.status.setStyleSheet(f"color:{t.danger};font-size:12px;")
        else:
            self.status.setText("Applying writes these 5 edits and recompiles xv6 — available "
                                "on the desktop build. The generated code is shown at left.")
            self.status.setStyleSheet(f"color:{t.muted};font-size:12px;")
