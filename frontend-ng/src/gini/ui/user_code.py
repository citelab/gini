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
   stopped. LIVE: the panel notices a save in their own editor within a second (see
   `_poll_files`), so the list and the checklist keep up with them instead of with the moment the
   panel was opened. Revert is per file — throwing away a broken kalloc.c must not cost them the
   syscall.c they finally got working — and it asks first, because one misclick used to be an
   evening's work (see `_ask_revert`).
3. **The wiring checklist.** The handout's failure table, live, read from their own source. It
   names the GAP and never the fix — "you have not written `entry("sysinfo")` yet" teaches better
   than `undefined reference` at link time, which is where that mistake surfaces otherwise.
4. **Load**, with the compile log underneath.
5. **The checks.** For a lab with staged checks (A-Lab 03), Check Part A/B/C and Check all in
   place of "Check my work", and one row per stage: its verdict, the line that decided it, and
   what the command printed, a click away. A file GINI builds from its own copy is listed as
   read-only, with no Revert.

The checklist is a tracker and not a grade, and the difference is load-bearing: every check is a
pattern over their source, so it answers "have you written the line" and never "does it work".
A line that is present but wrong ticks its box and then fails to build, which is the right order
for a student to meet those two facts in.
"""
from __future__ import annotations

import os
import subprocess
import sys

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from ..domain import lab_spec as _ls
from ..services import xv6_lab as _lab
from .theme import ThemeManager, icons
from .theme.manager import scale_css as _scss
from .windowing import standalone
from .worker_host import run_off_gui

#: How often the panel looks for a save made in the student's own editor.
#:
#: A poll of file STATS, not a QFileSystemWatcher, on purpose. Most editors save by writing a new
#: file and renaming it over the old one (VS Code, vim, anything "atomic"), and a watcher on the
#: file silently drops the path at the rename — it would work for the first save and never again.
#: A stat of a dozen small local files costs microseconds, so it runs on the GUI thread with no
#: worker at all, which keeps this face out of the widget-lifetime class in manual §17.
WATCH_MS = 1000

_STATE_WORD = {"edited": "edited", "untouched": "untouched",
               "missing": "not there", "unknown": "—", "readonly": "read-only"}


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
    test_result = Signal(bool, object)    # (ok, printed lines) from the Run test worker
    grade_result = Signal(object)         # (results,) from the grading worker
    grade_step = Signal(str)              # progress, because a grading run takes ~15 seconds
    boot_seen = Signal(str, str)          # (state, panic line) once the loaded kernel boots or dies

    def __init__(self, parent, theme: ThemeManager, device=None, provider=None,
                 spec=None, live: bool = True, recorder=None, state=None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.device = device
        self.provider = provider
        # GINI's own view of the kernel, for grading. Read through the state rather than through
        # anything the student's program touches — that independence IS the measurement.
        self.state = state
        self.live = live
        # The chain. A student's Loads — the failures especially — ARE the assignment: an hour
        # spent on a kernel that would not compile is an hour of work, and a record showing only
        # successes cannot tell "never tried" from "tried nine times". Same reason the shadow bar
        # records its builds.
        self._recorder = recorder
        self.spec = spec or _lab.active_spec(str(getattr(device, "name", "") or ""))
        self._rows: dict[str, QLabel] = {}
        self._checks: list[tuple] = []
        self._stage_rows: dict[str, tuple] = {}     # id -> (verdict, summary, transcript, button)
        self._check_btns: list[QPushButton] = []

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
        self._build_stages(self._col)
        self._col.addStretch(1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)
        self._build_build_bar(root)
        self.load_result.connect(self._on_load_result)
        self.boot_seen.connect(self._on_boot_seen)
        self.test_result.connect(self._on_test_result)
        self.grade_result.connect(self._on_grade_result)
        self.grade_step.connect(self._on_grade_step)
        standalone(self, f"User Code — {getattr(device, 'name', 'xv6')}")
        self.refresh()
        # Live: re-read when a file (or its pristine copy, which appears on the first Run) changes.
        self._seen = self._signature()
        self._watch = QTimer(self)
        self._watch.setInterval(WATCH_MS)
        self._watch.timeout.connect(self._poll_files)
        self._watch.start()

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
                            getattr(self.spec, "id", ""))

    def _reveal(self) -> None:
        if not reveal(self._dir()):
            self._say("The folder is not there yet — press Run once and it will be created.",
                      "warn")

    # -- the files ---------------------------------------------------------- #
    def _build_files(self, col) -> None:
        t = self.theme.theme
        col.addWidget(self._section("FILES YOU OWN"))
        groups = (self.spec.files_by_part() if hasattr(self.spec, "files_by_part")
                  else (("", tuple(getattr(self.spec, "files", ()))),))
        grouped = len(groups) > 1 or (groups and groups[0][0])
        for part, files in groups:
            if grouped:
                # The handout introduces the files part by part, so the list does too. A file
                # with no part (the measuring program) closes the list rather than opening it.
                title = self.spec.part_title(part) if part else ""
                head = QLabel(f"Part {part} — {title}" if part and title
                              else f"Part {part}" if part else "Also yours")
                head.setStyleSheet(_scss(f"color:{t.muted};font-size:11px;padding:6px 8px 0 8px;"))
                col.addWidget(head)
            for f in files:
                self._build_file_row(col, f)

    def _build_file_row(self, col, f) -> None:
        t = self.theme.theme
        row = QFrame(); lay = QHBoxLayout(row); lay.setContentsMargins(8, 2, 8, 2)
        name = QLabel(f.name)
        name.setStyleSheet(_scss(f"color:{t.text};font-family:monospace;font-size:12px;"))
        name.setMinimumWidth(150)
        also = getattr(f, "also", "")
        where = QLabel(f.tree + (f"   · also Part {also}" if also else ""))
        where.setStyleSheet(_scss(f"color:{t.faint};font-size:11px;"))
        state = QLabel("—")
        state.setStyleSheet(_scss(f"color:{t.muted};font-size:11px;"))
        state.setMinimumWidth(80)
        lay.addWidget(name); lay.addWidget(state); lay.addWidget(where, 1)
        if getattr(f, "readonly", False):
            # GINI builds its own copy, so there is nothing of the student's to put back.
            ro = QLabel("GINI's copy — read it, it is not built from your folder")
            ro.setStyleSheet(_scss(f"color:{t.faint};font-size:11px;"))
            lay.addWidget(ro)
        else:
            rev = QPushButton("Revert"); rev.setStyleSheet(self._btn_css())
            rev.clicked.connect(lambda _c=False, n=f.name: self._ask_revert(n))
            lay.addWidget(rev)
        self._rows[f.name] = state
        col.addWidget(row)

    def _ask_revert(self, name: str) -> None:
        """Revert, but only once the student has said yes.

        It used to be one click. The button sits at the end of each file's row, beside the state
        label a student is watching, and it replaced their file with the image's copy on the
        spot — an evening's syscall.c gone on a misclick, with nothing to undo it. Revert is the
        right tool for a file they have broken beyond repair, which is exactly when they are
        least careful about where they click.

        No question for a file that is already untouched: there is nothing to lose, and a
        dialog that guards nothing teaches people to click through dialogs.
        """
        machine = str(getattr(self.device, "name", "") or "")
        if _lab.state_of(self.spec, machine, name) == "untouched":
            self._say(f"{name} is already the original — nothing to revert.")
            return
        answer = QMessageBox.question(
            self, f"Revert {name}?",
            f"Put {name} back to the original from the image?\n\n"
            f"Every change you have made to {name} will be lost. Your other files are not "
            f"touched, and the kernel does not change until you press Load.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if answer == QMessageBox.StandardButton.Yes:
            self._revert(name)

    def _revert(self, name: str) -> None:
        ok, msg = _lab.revert(self.spec, str(getattr(self.device, "name", "") or ""), name)
        self._record_build(ok, msg, f"revert {name}")
        self._say(msg, "ok" if ok else "warn")
        self.refresh()

    def _record_build(self, ok: bool, log: str, action: str) -> None:
        """One chain entry per Load or Revert, carrying the files AS THEY STOOD when it ran.

        Hashed here rather than at submission time: a marker opens the code that travels with the
        submission, and these hashes are what bind it to the build that was actually attempted.
        The lab's OWN file list, so an assignment's ten files are recorded rather than the three
        a shadow lab happens to have.

        The progress line goes in too — how far the checklist had got at that moment — because a
        chain of builds with no sense of progress cannot distinguish a student converging on an
        answer from one thrashing.
        """
        from .lab_record import record
        machine = str(getattr(self.device, "name", "") or "")
        try:
            sources = _lab.hashes_for(self.spec, machine)
        except Exception:                          # noqa: BLE001 — never block a build on this
            sources = {}
        checks = None
        try:
            res = _ls.evaluate(self.spec, _lab.reader_for(self.spec, machine),
                               _lab.pristine_reader_for(self.spec, machine))
            # The checklist as DATA, beside the prose line below — see proof_events.build.
            checks = [{"id": r.check.id, "part": r.check.part or "", "ok": bool(r.passed)}
                      for r in res]
            p = _ls.progress(res)
            # Per part as well as the total, so the grader's cooked view can say "Part C 2/4"
            # rather than "12/16" — the parts are what the handout and the marking scheme use.
            # The total stays first and in the same words, for anything that read it before.
            per = " · ".join(f"{k} {v[0]}/{v[1]}" for k, v in sorted((p.get("parts") or {}).items())
                             if k)
            done = f"wiring {p['passed']}/{p['total']}" + (f" · {per}" if per else "")
        except Exception:                          # noqa: BLE001
            done = ""
        tail = [ln for ln in str(log or "").splitlines() if ln.strip()][-6:]
        if done:
            tail = [done, *tail]
        # The prose line stays for a release, so a Teaching Center whose gini-core predates the
        # structured `checks` still shows wiring for submissions from this gBuilder.
        record(self._recorder, "note_build", machine,
               getattr(self.spec, "id", "lab"), bool(ok), sources, tail, action, checks=checks)

    # -- the wiring checklist ------------------------------------------------ #
    def _build_wiring(self, col) -> None:
        t = self.theme.theme
        col.addWidget(self._section("WIRING"))
        # Grouped by part, with the part's own title. A flat list under two bare letters made a
        # reader ask what A and B were — reasonably, since nothing on the face said.
        seen_part = None
        for c in getattr(self.spec, "checks", ()):
            if c.part and c.part != seen_part:
                seen_part = c.part
                head = QLabel(f"Part {c.part} — {self.spec.part_title(c.part) or ''}".rstrip(" —"))
                head.setStyleSheet(_scss(f"color:{t.text};font-size:12px;font-weight:600;"
                                         f"padding-top:8px;padding-left:4px;"))
                col.addWidget(head)
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

    # -- staged checks ------------------------------------------------------- #
    def _build_stages(self, col) -> None:
        """One row per stage, filled in by a Check: id, verdict, the line that decided it, and
        what the command printed behind a click. Before any Check the rows say what each stage
        tests, in the handout's words, so the list doubles as the plan of the lab."""
        stages = tuple(getattr(self.spec, "stages", ()) or ())
        if not stages:
            return
        t = self.theme.theme
        col.addWidget(self._section("STAGES"))
        for st in stages:
            box = QFrame(); v = QVBoxLayout(box); v.setContentsMargins(8, 2, 8, 2); v.setSpacing(1)
            top = QHBoxLayout()
            sid = QLabel(st.id)
            sid.setStyleSheet(_scss(f"color:{t.text};font-family:monospace;font-size:12px;"
                                    f"font-weight:600;"))
            sid.setMinimumWidth(28)
            verdict = QLabel("·")
            verdict.setStyleSheet(_scss(f"color:{t.faint};font-size:12px;font-weight:600;"))
            verdict.setMinimumWidth(64)
            cmd = QLabel(st.command.replace("{seed}", "<seed>"))
            cmd.setStyleSheet(_scss(f"color:{t.faint};font-size:11px;font-family:monospace;"))
            out_btn = QPushButton("output"); out_btn.setStyleSheet(self._btn_css())
            out_btn.setEnabled(False)
            top.addWidget(sid); top.addWidget(verdict); top.addWidget(cmd, 1); top.addWidget(out_btn)
            v.addLayout(top)
            summary = QLabel(st.label)
            summary.setWordWrap(True)
            summary.setStyleSheet(_scss(f"color:{t.muted};font-size:11px;padding-left:34px;"))
            v.addWidget(summary)
            transcript = QPlainTextEdit(); transcript.setReadOnly(True)
            transcript.setMaximumHeight(160); transcript.setVisible(False)
            transcript.setStyleSheet(
                _scss(f"QPlainTextEdit{{background:{t.panel2};color:{t.muted};border:1px solid "
                      f"{t.line};border-radius:6px;font-family:monospace;font-size:11px;}}"))
            v.addWidget(transcript)
            out_btn.clicked.connect(lambda _c=False, w=transcript: w.setVisible(not w.isVisible()))
            col.addWidget(box)
            self._stage_rows[st.id] = (verdict, summary, transcript, out_btn)

    def _show_stages(self, check) -> None:
        """Show one Check. Rows it did not run go back to blank: Check Part A after a Check all
        must not leave the older Part C verdicts on screen beside the new ones."""
        t = self.theme.theme
        ran_ids = {r.id for r in check}
        for st in getattr(self.spec, "stages", ()) or ():
            if st.id in ran_ids or st.id not in self._stage_rows:
                continue
            verdict, summary, transcript, out_btn = self._stage_rows[st.id]
            verdict.setText("·")
            verdict.setStyleSheet(_scss(f"color:{t.faint};font-size:12px;font-weight:600;"))
            summary.setText(st.label)
            summary.setStyleSheet(_scss(f"color:{t.muted};font-size:11px;padding-left:34px;"))
            transcript.setPlainText("")
            transcript.setVisible(False)
            out_btn.setEnabled(False)
        for r in check:
            row = self._stage_rows.get(r.id)
            if row is None:
                continue
            verdict, summary, transcript, out_btn = row
            colour = {"ok": t.success, "FAIL": t.danger}.get(r.verdict, t.faint)
            verdict.setText(r.verdict)
            verdict.setStyleSheet(_scss(f"color:{colour};font-size:12px;font-weight:600;"))
            summary.setText(r.summary if r.summary else r.stage.label)
            summary.setStyleSheet(_scss(
                f"color:{colour if r.verdict == 'FAIL' else t.muted};font-size:11px;"
                f"padding-left:34px;"))
            ran = r.verdict != "not run"
            transcript.setPlainText(("$ " + r.command + "\n" + "\n".join(r.lines)) if ran else "")
            transcript.setVisible(False)
            out_btn.setEnabled(ran)

    def _on_grade_step(self, msg: str) -> None:
        """Progress from the grading worker. For a staged check, the stage starting now is also
        marked on its row, so the student watches the list fill in rather than a log line."""
        self._log.setPlainText(f"Checking… {msg}")
        sid = str(msg).split(":", 1)[0].strip()
        row = self._stage_rows.get(sid)
        if row is not None:
            row[0].setText("running…")
            row[0].setStyleSheet(_scss(f"color:{self.theme.theme.accent};font-size:12px;"
                                       f"font-weight:600;"))

    # -- build bar ----------------------------------------------------------- #
    def _build_build_bar(self, root) -> None:
        t = self.theme.theme
        bar = QHBoxLayout()
        self._load_btn = QPushButton("  Load")
        self._load_btn.setStyleSheet(self._btn_css())
        self._load_btn.clicked.connect(self._load)
        self._load_btn.setEnabled(bool(self.live and self.provider is not None))
        bar.addWidget(self._load_btn)
        # The assignment's own test, run from here rather than typed at the Keyboard. That is what
        # makes its output recordable: GINI issues the command and waits for the prompt, so both
        # ends of the run are known and what it printed can be attached to the run that printed
        # it. A program the student types is the same program and an unattributable byte stream.
        self._test_btn = QPushButton(f"  Run {self._test_prog() or 'test'}")
        self._test_btn.setStyleSheet(self._btn_css())
        self._test_btn.clicked.connect(self._run_test)
        self._test_btn.setEnabled(bool(self.live and self.provider is not None
                                       and self._test_prog()))
        bar.addWidget(self._test_btn)
        # The wiring checklist asks "did you connect it up?" and a student can pass all of it with
        # a call that returns zero. This asks whether it tells the TRUTH, by comparing what their
        # program prints against what GINI reads from the kernel's own structures.
        self._grade_btn = QPushButton("  Check my work")
        self._grade_btn.setStyleSheet(self._btn_css())
        self._grade_btn.clicked.connect(self._grade)
        self._grade_btn.setEnabled(bool(self.live and self.provider is not None
                                        and getattr(self.spec, "grade", ())))
        bar.addWidget(self._grade_btn)
        # A staged lab checks part by part instead: each Check Part X runs that part's stages and
        # everything they need first; Check all runs every stage. They replace "Check my work".
        if getattr(self.spec, "stages", ()):
            self._grade_btn.setVisible(False)
            for part in (*self.spec.stage_parts(), "all"):
                b = QPushButton("  Check all" if part == "all" else f"  Check Part {part}")
                b.setStyleSheet(self._btn_css())
                b.clicked.connect(lambda _c=False, p=part: self._check(p))
                b.setEnabled(bool(self.live and self.provider is not None))
                bar.addWidget(b)
                self._check_btns.append(b)
        self._progress = QLabel("")
        self._progress.setStyleSheet(_scss(f"color:{t.muted};font-size:12px;"))
        bar.addWidget(self._progress); bar.addStretch(1)
        root.addLayout(bar)
        # The scheduler's verdict on the student's policy (A-Lab 02): every answer it could not
        # use. A picker that is WRONG rather than slow shows here first -- the machine keeps
        # running on round-robin, so nothing else on screen looks broken.
        self._rejects = QLabel("")
        self._rejects.setStyleSheet(_scss(f"color:{t.accent_for('amber')};font-size:12px;"))
        self._rejects.setVisible(False)
        root.addWidget(self._rejects)
        self._log = QPlainTextEdit(); self._log.setReadOnly(True)
        self._log.setMaximumHeight(150)
        self._log.setStyleSheet(
            _scss(f"QPlainTextEdit{{background:{t.panel2};color:{t.muted};border:1px solid "
                  f"{t.line};border-radius:8px;font-family:monospace;font-size:11px;}}"))
        self._log.setPlainText(
            "Press Load to compile your kernel and restart the machine with it."
            if self.live else "Start the machine to build your code.")
        root.addWidget(self._log)

    def _test_prog(self) -> str:
        return str(getattr(self.spec, "test_prog", "") or "")

    def _run_test(self) -> None:
        """Run the assignment's test and keep what it printed.

        For an A-Lab the output IS the deliverable — the assignment is a program that reports
        system information — so a chain that records the build and the launch but not the answer
        is a record of everything except whether it worked.
        """
        prog = self._test_prog()
        if self.provider is None or not prog:
            return
        if not hasattr(self.provider, "run_and_capture"):
            self._log.setPlainText(
                "This machine's agent is older than the test runner — rebuild the gini-xv6 image.")
            return
        self._test_btn.setEnabled(False)
        self._log.setPlainText(f"Running {prog}…")
        prov = self.provider

        def work():
            try:
                ok, out = _lab.run_test(prov, prog,
                                        timeout=getattr(self.spec, "test_timeout", 12.0))
            except Exception as e:        # noqa: BLE001 — a test must not take the face down
                ok, out = False, [f"{type(e).__name__}: {e}"]
            self.test_result.emit(bool(ok), list(out or []))

        run_off_gui(self, work)

    def _on_test_result(self, ok: bool, out) -> None:
        self._test_btn.setEnabled(bool(self.live and self.provider is not None
                                       and self._test_prog()))
        prog = self._test_prog()
        lines = list(out or [])
        if not ok:
            why = getattr(self.provider, "last_run_error", "") or "it did not start"
            self._log.setPlainText(f"{prog} did not run — {why}")
        elif not lines:
            self._log.setPlainText(f"{prog} ran and printed nothing.")
        else:
            self._log.setPlainText("\n".join(lines))
        # Recorded either way. "Ran it and it printed nothing" is a result a marker wants, and so
        # is a test that would not start — both are the student's evidence about their own code.
        from .lab_record import record
        record(self._recorder, "note_spawn", str(getattr(self.device, "name", "") or ""),
               prog, "launch", None, lines, True)

    def _grade(self) -> None:
        """Measure the assignment's declared metrics against the running kernel.

        Off the GUI thread and slow on purpose — it takes two readings with real work in between,
        because half the metrics are about MOVEMENT and a single reading cannot see any of it.
        """
        if self.provider is None or not getattr(self.spec, "grade", ()):
            return
        self._grade_btn.setEnabled(False)
        self._log.setPlainText("Checking…")
        prov, spec = self.provider, self.spec
        vm = getattr(self.state, "vm", None)
        machine = str(getattr(self.device, "name", "") or "")

        def work():
            try:
                res = _lab.grade_now(spec, machine, prov, vm, say=self.grade_step.emit)
            except Exception as e:        # noqa: BLE001 — a check must not take the face down
                res = e
            self.grade_result.emit(res)

        run_off_gui(self, work)

    def _check(self, part: str) -> None:
        """Run one part's stages (or all of them) against the running kernel, off the GUI thread.

        Fresh seed per press, shown with the results, so the student can type any stage's command
        with it and get the same run while they debug."""
        if self.provider is None or not getattr(self.spec, "stages", ()):
            return
        for b in self._check_btns:
            b.setEnabled(False)
        self._log.setPlainText("Checking…")
        prov, spec = self.provider, self.spec

        def work():
            try:
                res = _lab.check_stages(spec, prov, part, say=self.grade_step.emit)
            except Exception as e:        # noqa: BLE001 — a check must not take the face down
                res = e
            self.grade_result.emit(res)

        run_off_gui(self, work)

    def _on_stage_result(self, check) -> None:
        for b in self._check_btns:
            b.setEnabled(bool(self.live and self.provider is not None))
        self._show_stages(check)
        n = check.tally()
        what = "Check all" if check.part == "all" else f"Check Part {check.part}"
        lines = [f"{what} — seed {check.seed}: {n['ok']} ok"
                 + (f", {n['failed']} FAIL" if n["failed"] else "")
                 + (f", {n['not_run']} not run" if n["not_run"] else ""),
                 "Type any command with this seed to repeat its run.", ""]
        lines += [" " + r.line() for r in check]
        _lab.record_grades(self._recorder, self.spec, check,
                           device=str(getattr(self.device, "name", "") or ""))
        self._log.setPlainText("\n".join(lines))

    def _on_grade_result(self, res) -> None:
        self._grade_btn.setEnabled(bool(self.live and self.provider is not None
                                        and getattr(self.spec, "grade", ())))
        if isinstance(res, Exception):
            for b in self._check_btns:
                b.setEnabled(bool(self.live and self.provider is not None))
            self._log.setPlainText(f"The check could not run — {type(res).__name__}: {res}")
            return
        if hasattr(res, "seed") and hasattr(res, "results"):
            self._on_stage_result(res)
            return
        from ..domain import lab_grade as _g
        t = _g.tally(res)
        lines = [f"{t['ok']}/{t['total']} measured and agreed"
                 + (f", {t['failed']} disagreed" if t["failed"] else "")
                 + (f", {t['pending']} could not be measured" if t["pending"] else ""), ""]
        for r in res:
            mark = "·" if r.ok is None else ("✓" if r.ok else "✗")
            lines.append(f" {mark}  {r.describe}")
            lines.append(f"      {r.summary}")
        _lab.record_grades(self._recorder, self.spec, res,
                           device=str(getattr(self.device, "name", "") or ""))
        self._log.setPlainText("\n".join(lines))

    def _load(self) -> None:
        if self.provider is None:
            return
        self._load_btn.setEnabled(False)
        self._log.setPlainText("Building…")
        prov = self.provider

        def work():
            try:
                _, cursor = prov.console_since(0)  # before the restart: what follows is this boot
            except Exception:                 # noqa: BLE001 — no console, no boot watch
                cursor = None
            try:
                ok, log = prov.load()
            except Exception as e:            # noqa: BLE001 — a build must not take the face down
                ok, log = False, f"{type(e).__name__}: {e}"
            self.load_result.emit(bool(ok), str(log or ""))
            if ok and cursor is not None:
                try:
                    state, line = _lab.watch_boot(prov, cursor)
                except Exception:             # noqa: BLE001
                    state, line = "", ""
                self.boot_seen.emit(state, line)

        run_off_gui(self, work)

    def _on_load_result(self, ok: bool, log: str) -> None:
        self._load_btn.setEnabled(bool(self.live and self.provider is not None))
        self._record_build(ok, log, "load")
        if ok:
            # Points at the Run button first. It runs the assignment's own test, cleaned and
            # attributed to the run that printed it — the output a marker looks for. The console
            # is recorded too now, but as a transcript of whatever was typed. (This used to say
            # "run your program at the Keyboard", which until the console was tapped was the one
            # path that recorded nothing at all.)
            prog = self._test_prog()
            self._log.setPlainText(
                "Loaded — the machine is running your kernel.\n"
                + (f"Press Run {prog} to take a measurement that goes in your record. "
                   if prog else "")
                + "Anything you type at the machine's console is recorded too.")
        else:
            self._log.setPlainText(log or "the build failed and said nothing")
        self.refresh()

    def _on_boot_seen(self, state: str, line: str) -> None:
        """The loaded kernel built but did not boot. Say so, and say where to look: the handout
        tells the student to read the panic on the console, and "Loaded — the machine is running
        your kernel" sent them to a machine that was not running anything."""
        if state != "panic":
            return
        self._say("Loaded, but the kernel did not boot. It stopped with:\n"
                  f"    {line}\n"
                  "Read the machine's console (the Terminal) for the lines before it. If you cannot "
                  "find the cause, Revert the file you changed last and add your changes back one "
                  "function at a time.", "warn")

    # -- state --------------------------------------------------------------- #
    def _signature(self) -> tuple:
        """(mtime, size) of every file and its pristine copy — `None` where one is absent. Stats
        only: nothing is read unless this changes."""
        machine = str(getattr(self.device, "name", "") or "")
        folder = self._dir()
        out = []
        for f in getattr(self.spec, "files", ()) or ():
            for path in (folder / f.name,
                         _lab.pristine_path(machine, f.name, getattr(self.spec, "id", ""))):
                try:
                    st = path.stat()
                    out.append((st.st_mtime_ns, st.st_size))
                except OSError:
                    out.append(None)
        return tuple(out)

    def _poll_files(self) -> None:
        """Refresh when a save landed since the last look — and only then."""
        if self.spec is None or not self.isVisible():
            return
        sig = self._signature()
        if sig != self._seen:
            self._seen = sig
            self.refresh()
        self._show_rejects()

    def _show_rejects(self) -> None:
        """The kernel's reject count, as the Machine Lab's own poll last read it -- an attribute,
        not a read of the kernel: this runs on the GUI thread every second."""
        n = getattr(self.provider, "kernel_rejects", None) if self.provider is not None else None
        if not n:
            self._rejects.setVisible(False)
            return
        self._rejects.setText(f"{n} rejected pick{'s' if n != 1 else ''} — your policy returned "
                              f"something that was not runnable")
        self._rejects.setVisible(True)

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
        parts = "   ".join(f"Part {k} {v[0]}/{v[1]}" for k, v in sorted(p["parts"].items()))
        nxt = _ls.next_step(results)
        self._progress.setText(
            f"{p['passed']}/{p['total']} wired      {parts}"
            + (f"      next: {nxt.label}" if nxt else "      — all wired, press Load"))

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
