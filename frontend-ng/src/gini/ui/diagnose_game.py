"""Generic Diagnose-game UI — one widget that drives ANY game via a GameSpec + a case source + a
signature renderer. The confusion matrix, buttons, modes (practice/graded), and scoring are shared;
each game only supplies its classes, its cases (real telemetry), and how to draw a signature.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QPushButton, QVBoxLayout, QWidget,
)

from ..domain.diagnose import GRADED, PRACTICE, DiagnoseSession
from .theme.manager import scale_css as _scss


class ConfusionGrid(QWidget):
    """true(row) × predicted(col) matrix; diagonal = correct. Axis ticks use the spec's abbrevs."""

    def __init__(self, theme, parent=None) -> None:
        super().__init__(parent)
        self.theme = theme
        self._m: dict = {}
        self._classes: list = []
        self._abbr: dict = {}
        self.setMinimumSize(300, 240)

    def set_matrix(self, m, classes, abbrev=None) -> None:
        self._m = m or {}
        self._classes = classes or []
        self._abbr = abbrev or {}
        self.update()

    def _tick(self, cls) -> str:
        return self._abbr.get(cls, cls)

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = self.theme.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        cls = self._classes
        n = len(cls)
        if n == 0:
            return
        lab = 46
        cell = min((self.width() - lab) / n, (self.height() - lab) / n)
        mx = max([v for v in self._m.values()] + [1])
        for r, tru in enumerate(cls):
            p.setPen(QColor(t.muted))
            p.drawText(0, int(lab + r * cell), lab - 4, int(cell),
                       Qt.AlignRight | Qt.AlignVCenter, self._tick(tru))
            for c, pred in enumerate(cls):
                v = self._m.get((tru, pred), 0)
                x, y = lab + c * cell, lab + r * cell
                base = t.accent_for("green" if r == c else "red")
                col = QColor(base); col.setAlpha(int(30 + 200 * (v / mx)) if v else 12)
                p.setBrush(col)
                p.setPen(QColor(t.accent if r == c else t.line))
                p.drawRect(int(x), int(y), int(cell) - 2, int(cell) - 2)
                if v:
                    p.setPen(QColor(t.text))
                    p.drawText(int(x), int(y), int(cell) - 2, int(cell) - 2,
                               Qt.AlignCenter, str(v))
        p.save()
        p.translate(lab, lab - 6)
        for c, pred in enumerate(cls):
            p.setPen(QColor(t.muted))
            p.drawText(int(c * cell), -14, int(cell), 14, Qt.AlignCenter, self._tick(pred))
        p.restore()


class DiagnoseGameWidget(QWidget):
    """spec + source()->[Case] + a renderer widget (show_signature(sig)). Reused by every game."""

    def __init__(self, theme, spec, source, renderer, live=False, deck: int = 10) -> None:
        super().__init__()
        self.theme = theme
        self.spec = spec
        self._source = source
        self._renderer = renderer
        self.live = live
        self._deck = deck
        self._answer = spec.answer
        self._estimate = (spec.answer == "estimate")
        self._dynamic = spec.answer in ("spot", "rank")   # buttons/chips rebuilt per case
        self._has_matrix = (spec.answer == "class")
        self._session = DiagnoseSession(spec, source(), mode=PRACTICE, deck=deck)
        self._class_btns: list = []
        self._opt_btns: list = []
        self._opt_by: dict = {}
        self._order: list = []

        t = theme.theme
        lay = QHBoxLayout(self)
        # left: mystery + controls
        col = QVBoxLayout()
        col.addWidget(self._lbl(spec.prompt, t.accent_for("purple"), 12, True))
        col.addWidget(renderer, 1)
        self._msg = QLabel(spec.prompt)
        self._msg.setWordWrap(True)
        self._msg.setStyleSheet(_scss(f"color:{t.muted};font-size:12px;"))
        col.addWidget(self._msg)
        if self._estimate:
            row = QHBoxLayout(); row.setSpacing(6)
            self._input = QLineEdit()
            self._input.setPlaceholderText("your estimate" + (f" ({spec.unit})" if spec.unit else ""))
            self._input.setStyleSheet(
                f"QLineEdit{{color:{t.text};background:{t.panel};border:1px solid {t.line};"
                "border-radius:7px;padding:5px 9px;}")
            self._input.returnPressed.connect(self._submit_estimate)
            self._submit = QPushButton("Submit"); self._submit.setStyleSheet(self._btn_css())
            self._submit.clicked.connect(self._submit_estimate)
            row.addWidget(self._input, 1); row.addWidget(self._submit)
            col.addLayout(row)
        elif self._dynamic:
            # candidates come from each case; a host widget we clear + refill per mystery
            self._opts_host = QWidget()
            self._opts_row = QHBoxLayout(self._opts_host)
            self._opts_row.setContentsMargins(0, 0, 0, 0); self._opts_row.setSpacing(5)
            col.addWidget(self._opts_host)
            if self._answer == "rank":
                self._order_lbl = QLabel("your order:  —")
                self._order_lbl.setStyleSheet(_scss(f"color:{t.text};font-size:12px;"))
                col.addWidget(self._order_lbl)
                self._clear_order_btn = QPushButton("Clear order")
                self._clear_order_btn.setStyleSheet(self._btn_css())
                self._clear_order_btn.clicked.connect(self._rank_clear)
                col.addWidget(self._clear_order_btn, 0, Qt.AlignLeft)
        else:                                              # class: fixed buttons
            btns = QHBoxLayout(); btns.setSpacing(5)
            for cls in spec.classes:
                b = QPushButton(cls); b.setStyleSheet(self._btn_css())
                b.clicked.connect(lambda _c=False, k=cls: self._guess(k))
                btns.addWidget(b); self._class_btns.append(b)
            self._opt_btns = self._class_btns
            col.addLayout(btns)
        mode_row = QHBoxLayout(); mode_row.setSpacing(5)
        mode_row.addWidget(self._lbl("mode:", t.muted, 11))
        self._mode_btns = {}
        for m in (PRACTICE, GRADED):
            b = QPushButton(m); b.setCheckable(True); b.setStyleSheet(self._toggle_css())
            b.clicked.connect(lambda _c=False, mm=m: self._set_mode(mm))
            mode_row.addWidget(b); self._mode_btns[m] = b
        self._mode_btns[PRACTICE].setChecked(True)
        mode_row.addStretch(1)
        col.addLayout(mode_row)
        lay.addLayout(col, 1)

        # right: score + scoreboard (confusion matrix for class, recent-guesses for estimate)
        right = QVBoxLayout()
        self._score = QLabel("score 0 / 0")
        self._score.setStyleSheet(_scss(f"color:{t.text};font-size:13px;font-weight:600;"))
        right.addWidget(self._score)
        if self._has_matrix:
            right.addWidget(self._lbl("confusion matrix  ·  true (row) × your guess (col)",
                                      t.muted, 11))
            self._conf = ConfusionGrid(theme)
            right.addWidget(self._conf, 1)
        else:
            right.addWidget(self._lbl("recent  ·  your answer vs actual", t.muted, 11))
            self._recent = QListWidget()
            self._recent.setStyleSheet(
                f"QListWidget{{background:{t.panel};color:{t.text};border:1px solid {t.line};"
                "border-radius:8px;font-family:monospace;font-size:12px;padding:4px;}")
            right.addWidget(self._recent, 1)
            self._conf = None
        self._reset_btn = QPushButton("Reset")
        self._reset_btn.setStyleSheet(self._btn_css())
        self._reset_btn.clicked.connect(self._reset)
        right.addWidget(self._reset_btn, 0, Qt.AlignRight)
        lay.addLayout(right, 1)

        self._advance = QTimer(self); self._advance.setSingleShot(True)
        self._advance.timeout.connect(self._new_mystery)
        self._new_mystery()

    # -- styling ----------------------------------------------------------- #
    def _lbl(self, text, color, size=11, bold=False) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(_scss(f"color:{color};font-size:{size}px;"
                                + ("font-weight:600;" if bold else "")))
        return lbl

    def _btn_css(self) -> str:
        t = self.theme.theme
        return (f"QPushButton{{color:{t.text};background:{t.panel2};border:1px solid {t.line};"
                "border-radius:7px;padding:5px 11px;}"
                f"QPushButton:hover{{border-color:{t.accent};}}"
                f"QPushButton:disabled{{color:{t.faint};}}")

    def _toggle_css(self) -> str:
        t = self.theme.theme
        return (f"QPushButton{{color:{t.muted};background:{t.panel2};border:1px solid {t.line};"
                "border-radius:7px;padding:3px 10px;font-size:11px;}"
                f"QPushButton:checked{{color:{t.text};border-color:{t.accent};}}")

    # -- flow -------------------------------------------------------------- #
    @staticmethod
    def _clear_layout(layout) -> None:
        while layout.count():
            it = layout.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)

    def _render_options(self, case) -> None:
        """Rebuild the per-case candidate buttons/chips (spot + rank)."""
        self._clear_layout(self._opts_row)
        self._opt_btns = []; self._opt_by = {}; self._order = []
        opts = case.options if case.options else self.spec.classes
        for opt in opts:
            b = QPushButton(str(opt)); b.setStyleSheet(self._btn_css())
            if self._answer == "rank":
                b.clicked.connect(lambda _c=False, o=opt: self._rank_click(o))
            else:
                b.clicked.connect(lambda _c=False, o=opt: self._guess(o))
            self._opts_row.addWidget(b)
            self._opt_btns.append(b); self._opt_by[opt] = b
        self._opts_row.addStretch(1)
        if self._answer == "rank":
            self._order_lbl.setText("your order:  —")

    def _rank_click(self, opt) -> None:
        if opt in self._order:
            return
        self._order.append(opt)
        b = self._opt_by.get(opt)
        if b is not None:
            b.setEnabled(False)
        self._order_lbl.setText("your order:  " + "  →  ".join(str(o) for o in self._order))
        if len(self._order) == len(self._opt_btns):     # complete -> submit
            self._guess(list(self._order))

    def _rank_clear(self) -> None:
        self._order = []
        for b in self._opt_btns:
            b.setEnabled(True)
        self._order_lbl.setText("your order:  —")

    def _set_answer_enabled(self, on: bool) -> None:
        if self._estimate:
            self._input.setEnabled(on); self._submit.setEnabled(on)
            if on:
                self._input.clear(); self._input.setFocus()
            return
        for b in self._opt_btns:
            b.setEnabled(on)
        if self._answer == "rank":
            self._clear_order_btn.setEnabled(on)

    def _set_mode(self, mode) -> None:
        for m, b in self._mode_btns.items():
            b.setChecked(m == mode)
        self._session = DiagnoseSession(self.spec, self._source(), mode=mode, deck=self._deck)
        self._clear_board()
        self._score.setText("score 0 / 0")
        self._new_mystery()

    def _new_mystery(self) -> None:
        self._session.set_cases(self._source())
        case = self._session.next()
        if case is not None:
            self._renderer.show_signature(case.signature)
            self._msg.setText(self.spec.prompt)
            if self._dynamic:
                self._render_options(case)
            self._set_answer_enabled(True)
            return
        self._renderer.show_signature(None)
        self._set_answer_enabled(False)
        if self._session.finished:
            hit, tot = self._session.score()
            self._msg.setText(f"Run complete — {hit}/{tot}  "
                              f"({round(self._session.accuracy() * 100)}%). Reset for a new run.")
        else:
            self._msg.setText("No cases yet — launch some programs to play, or switch to Demo.")

    def _submit_estimate(self) -> None:
        raw = self._input.text().strip()
        try:
            val = int(raw, 0)                          # accepts 0x… hex and decimal
        except ValueError:
            try:
                val = float(raw)
            except ValueError:
                self._msg.setText("Enter a number (decimal or 0x… hex).")
                return
        self._guess(val)

    def _guess(self, answer) -> None:
        v = self._session.guess(answer)
        if not v:
            return
        sub = f"  ({v['subtitle']})" if v.get("subtitle") else ""
        hint = f"   ·   hint: {v['hint']}" if v.get("hint") else ""
        if self._has_matrix:
            verdict = "✓ correct" if v["correct"] else f"✗ it was {v['truth']}"
            self._msg.setText(verdict + sub + hint)
            self._conf.set_matrix(self._session.matrix(), self.spec.classes, self.spec.abbrev)
        elif self._answer == "rank":
            pct = round((v.get("partial") or 0) * 100)
            verdict = "✓ perfect order" if v["correct"] else f"✗ ({pct}% ordered)"
            self._msg.setText(f"{verdict} — actual: {'  →  '.join(map(str, v['truth']))}{hint}")
            self._recent.insertItem(0, f"{'✓' if v['correct'] else f'{pct}%'}  "
                                       f"{'  '.join(map(str, answer))}")
        else:                                          # estimate + spot
            verdict = "✓ correct" if v["correct"] else f"✗ it was {v['truth']}"
            self._msg.setText(f"{verdict} — you said {answer}{sub}{hint}")
            self._recent.insertItem(0, f"{'✓' if v['correct'] else '✗'}  you {answer}   "
                                       f"actual {v['truth']}")
        self._update_score()
        self._advance.start(950)

    def _update_score(self) -> None:
        hit, tot = self._session.score()
        txt = f"score {hit} / {tot}   ·   accuracy {round(self._session.accuracy() * 100)}%"
        if self._estimate:
            txt += f"   ·   mean error {round(self._session.mean_abs_error(), 1)}"
        elif self._answer == "rank":
            txt += f"   ·   order {round(self._session.mean_order_score() * 100)}%"
        rem = self._session.remaining()
        if rem is not None:
            txt += f"   ·   {rem} left"
        self._score.setText(txt)

    def _clear_board(self) -> None:
        if self._has_matrix:
            self._conf.set_matrix({}, self.spec.classes, self.spec.abbrev)
        else:
            self._recent.clear()

    def _reset(self) -> None:
        self._session.reset()
        self._clear_board()
        self._score.setText("score 0 / 0")
        self._new_mystery()
