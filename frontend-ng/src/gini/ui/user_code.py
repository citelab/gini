"""User Code — the student's own kernel files: what they own, what they have changed, and Load.

Every other face in the Machine Lab looks at a layer of the machine. This one looks at the
student's work, which is not a layer of anything — so it sits above USER SPACE on the home page
rather than inside a band, and it does not move when the assignment is about paging instead of
system calls. Only its file list changes.

Four things, in the order a student needs them:

1. **Which assignment, and where the files are.** They lose that folder. There is a button that
   opens it in Finder or Explorer.
2. **What they have touched.** `edited` against `untouched`, by comparing their file to the copy
   GINI took from the image on the first Run — host-side, so it still answers with the machine
   stopped. Revert is per file: throwing away a broken kalloc.c must not cost them the syscall.c
   they finally got working.
3. **The wiring checklist.** The handout's failure table, live, read from their own source. It
   names the GAP and never the fix — "you have not written `entry("sysinfo")` yet" teaches better
   than `undefined reference` at link time, which is where that mistake surfaces otherwise.
4. **Load**, with the compile log underneath.

The checklist is a tracker and not a grade, and the difference is load-bearing: every check is a
pattern over their source, so it answers "have you written the line" and never "does it work".
A line that is present but wrong ticks its box and then fails to build, which is the right order
for a student to meet those two facts in.
"""
from __future__ import annotations

import os
import subprocess
import sys

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout,
    QWidget,
)

from ..domain import lab_spec as _ls
from ..services import xv6_lab as _lab
from .theme import ThemeManager, icons
from .theme.manager import scale_css as _scss
from .windowing import standalone
from .worker_host import run_off_gui

_STATE_WORD = {"edited": "edited", "untouched": "untouched",
               "missing": "not there", "unknown": "—"}


def reveal(path) -> bool:
    """Open the lab folder in the desktop's file manager. False when there is nothing to open."""
    p = str(path)
    if not os.path.isdir(p):
        return False
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", p])
        elif os.name == "nt":
            os.startfile(p)               # noqa: S606 — the platform's own opener
        else:
            subprocess.Popen(["xdg-open", p])
        return True
    except Exception:                     # noqa: BLE001 — a missing file manager is not an error
        return False


class UserCode(QDialog):
    load_result = Signal(bool, str)       # (ok, log) from the Load worker thread

    def __init__(self, parent, theme: ThemeManager, device=None, provider=None,
                 spec=None, live: bool = True) -> None:
        super().__init__(parent)
        self.theme = theme
        self.device = device
        self.provider = provider
        self.live = live
        self.spec = spec or _lab.active_spec()
        self._rows: dict[str, QLabel] = {}
        self._checks: list[tuple] = []

        t = theme.theme
        title = getattr(self.spec, "title", "User Code")
        self.setWindowTitle(f"User Code — {getattr(device, 'name', 'xv6')}")
        self.resize(760, 720)
        self.setStyleSheet(f"QDialog{{background:{t.bg};}}")
        root = QVBoxLayout(self)
        self._build_header(root, title)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget(); self._col = QVBoxLayout(body)
        self._build_files(self._col)
        self._build_wiring(self._col)
        self._col.addStretch(1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)
        self._build_build_bar(root)
        self.load_result.connect(self._on_load_result)
        standalone(self, f"User Code — {getattr(device, 'name', 'xv6')}")
        self.refresh()

    # -- header ----------------------------------------------------------- #
    def _build_header(self, root, title: str) -> None:
        t = self.theme.theme
        head = QHBoxLayout()
        ic = QLabel(); ic.setPixmap(icons.render_pixmap("compile", t.accent_for("red"), 22))
        lab = QLabel(f"  {title}")
        lab.setStyleSheet(_scss(f"color:{t.text};font-size:16px;font-weight:600;"))
        head.addWidget(ic); head.addWidget(lab); head.addStretch(1)
        btn = QPushButton("  Reveal folder")
        btn.setStyleSheet(self._btn_css()); btn.clicked.connect(self._reveal)
        head.addWidget(btn)
        root.addLayout(head)
        self._path_lab = QLabel(str(self._dir()))
        self._path_lab.setStyleSheet(_scss(f"color:{t.muted};font-size:11px;"))
        self._path_lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(self._path_lab)

    def _dir(self):
        return _lab.lab_dir(str(getattr(self.device, "name", "") or ""),
                            getattr(self.spec, "machine_folder", "xv6-lab"))

    def _reveal(self) -> None:
        if not reveal(self._dir()):
            self._say("The folder is not there yet — press Run once and it will be created.",
                      "warn")

    # -- the files ---------------------------------------------------------- #
    def _build_files(self, col) -> None:
        t = self.theme.theme
        col.addWidget(self._section("FILES YOU OWN"))
        for f in getattr(self.spec, "files", ()):
            row = QFrame(); lay = QHBoxLayout(row); lay.setContentsMargins(8, 2, 8, 2)
            name = QLabel(f.name)
            name.setStyleSheet(_scss(f"color:{t.text};font-family:monospace;font-size:12px;"))
            name.setMinimumWidth(150)
            where = QLabel(f.tree)
            where.setStyleSheet(_scss(f"color:{t.faint};font-size:11px;"))
            state = QLabel("—")
            state.setStyleSheet(_scss(f"color:{t.muted};font-size:11px;"))
            state.setMinimumWidth(80)
            rev = QPushButton("Revert"); rev.setStyleSheet(self._btn_css())
            rev.clicked.connect(lambda _c=False, n=f.name: self._revert(n))
            lay.addWidget(name); lay.addWidget(state); lay.addWidget(where, 1); lay.addWidget(rev)
            self._rows[f.name] = state
            col.addWidget(row)

    def _revert(self, name: str) -> None:
        ok, msg = _lab.revert(self.spec, str(getattr(self.device, "name", "") or ""), name)
        self._say(msg, "ok" if ok else "warn")
        self.refresh()

    # -- the wiring checklist ------------------------------------------------ #
    def _build_wiring(self, col) -> None:
        t = self.theme.theme
        self._wire_head = self._section("WIRING")
        col.addWidget(self._wire_head)
        for c in getattr(self.spec, "checks", ()):
            row = QFrame(); lay = QHBoxLayout(row); lay.setContentsMargins(8, 2, 8, 2)
            mark = QLabel("·"); mark.setMinimumWidth(18)
            mark.setStyleSheet(_scss(f"color:{t.muted};font-size:13px;font-weight:600;"))
            label = QLabel(c.label)
            label.setStyleSheet(_scss(f"color:{t.text};font-size:12px;"))
            where = QLabel(c.where or c.file)
            where.setStyleSheet(_scss(f"color:{t.faint};font-size:11px;font-family:monospace;"))
            lay.addWidget(mark); lay.addWidget(label, 1); lay.addWidget(where)
            col.addWidget(row)
            hint = QLabel("    " + (c.hint or ""))
            hint.setWordWrap(True)
            hint.setStyleSheet(_scss(f"color:{t.muted};font-size:11px;"))
            hint.setVisible(False)
            col.addWidget(hint)
            self._checks.append((c, mark, hint))

    # -- build bar ----------------------------------------------------------- #
    def _build_build_bar(self, root) -> None:
        t = self.theme.theme
        bar = QHBoxLayout()
        self._load_btn = QPushButton("  Load")
        self._load_btn.setStyleSheet(self._btn_css())
        self._load_btn.clicked.connect(self._load)
        self._load_btn.setEnabled(bool(self.live and self.provider is not None))
        bar.addWidget(self._load_btn)
        self._progress = QLabel("")
        self._progress.setStyleSheet(_scss(f"color:{t.muted};font-size:12px;"))
        bar.addWidget(self._progress); bar.addStretch(1)
        root.addLayout(bar)
        self._log = QPlainTextEdit(); self._log.setReadOnly(True)
        self._log.setMaximumHeight(150)
        self._log.setStyleSheet(
            _scss(f"QPlainTextEdit{{background:{t.panel2};color:{t.muted};border:1px solid "
                  f"{t.line};border-radius:8px;font-family:monospace;font-size:11px;}}"))
        self._log.setPlainText(
            "Press Load to compile your kernel and restart the machine with it."
            if self.live else "Start the machine to build your code.")
        root.addWidget(self._log)

    def _load(self) -> None:
        if self.provider is None:
            return
        self._load_btn.setEnabled(False)
        self._log.setPlainText("Building…")
        prov = self.provider

        def work():
            try:
                ok, log = prov.load()
            except Exception as e:            # noqa: BLE001 — a build must not take the face down
                ok, log = False, f"{type(e).__name__}: {e}"
            self.load_result.emit(bool(ok), str(log or ""))

        run_off_gui(self, work)

    def _on_load_result(self, ok: bool, log: str) -> None:
        self._load_btn.setEnabled(bool(self.live and self.provider is not None))
        if ok:
            self._log.setPlainText("Loaded — the machine is running your kernel.\n"
                                   "Run your program at the Keyboard, then look at the "
                                   "System Calls Lab.")
        else:
            self._log.setPlainText(log or "the build failed and said nothing")
        self.refresh()

    # -- state --------------------------------------------------------------- #
    def refresh(self) -> None:
        """Re-read the student's files: states, checklist, progress. Cheap — a few small files."""
        t = self.theme.theme
        machine = str(getattr(self.device, "name", "") or "")
        if self.spec is None:
            return
        for f in getattr(self.spec, "files", ()):
            st = _lab.state_of(self.spec, machine, f.name)
            lab = self._rows.get(f.name)
            if lab is None:
                continue
            lab.setText(_STATE_WORD.get(st, st))
            col = {"edited": t.accent_for("amber"), "untouched": t.muted,
                   "missing": t.danger}.get(st, t.faint)
            lab.setStyleSheet(_scss(f"color:{col};font-size:11px;"))

        results = _ls.evaluate(self.spec, _lab.reader_for(self.spec, machine),
                               _lab.pristine_reader_for(self.spec, machine))
        by_id = {r.check.id: r for r in results}
        for c, mark, hint in self._checks:
            r = by_id.get(c.id)
            passed = bool(r and r.passed)
            mark.setText("✓" if passed else "✗")
            mark.setStyleSheet(_scss(
                f"color:{t.success if passed else t.faint};font-size:13px;font-weight:600;"))
            hint.setVisible(bool(c.hint) and not passed)
        p = _ls.progress(results)
        parts = "   ".join(f"{k}: {v[0]}/{v[1]}" for k, v in sorted(p["parts"].items()))
        nxt = _ls.next_step(results)
        self._progress.setText(
            f"{p['passed']}/{p['total']} done      {parts}"
            + (f"      next: {nxt.label}" if nxt else "      — all wired"))

    def _say(self, text: str, tone: str = "muted") -> None:
        t = self.theme.theme
        col = {"ok": t.success, "warn": t.accent_for("amber")}.get(tone, t.muted)
        self._log.setPlainText(text)
        self._log.setStyleSheet(
            _scss(f"QPlainTextEdit{{background:{t.panel2};color:{col};border:1px solid "
                  f"{t.line};border-radius:8px;font-family:monospace;font-size:11px;}}"))

    def _section(self, text: str) -> QLabel:
        t = self.theme.theme
        lab = QLabel(text)
        lab.setStyleSheet(_scss(f"color:{t.faint};font-size:11px;font-weight:600;"
                                f"letter-spacing:1px;padding-top:10px;"))
        return lab

    def _btn_css(self) -> str:
        t = self.theme.theme
        return (f"QPushButton{{color:{t.text};background:{t.panel2};border:1px solid {t.line};"
                f"border-radius:8px;padding:5px 10px;}}"
                f"QPushButton:hover{{border-color:{t.accent};}}"
                f"QPushButton:disabled{{color:{t.faint};}}")
