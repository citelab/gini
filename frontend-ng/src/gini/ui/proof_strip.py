"""The proof-of-activity control at the left of the dashboard strip.

States:

  * **unarmed** — a code box and *Record*: no trace of whatever code was last recorded.
  * **paused** — the code box, a ``⏸ PAUSED`` block naming the paused code, and *Resume*. Typing
    a different code and pressing it records that one instead.
  * **armed** — a large ``● REC`` block, the code, the event counter, *Generate proof*, *Pause*.
    For a lab the course declared *one sitting* there is no Pause button.
  * **sending** — the same, plus a progress bar while the package goes to the course server.

The recording indicator is the whole reason this lives on the always-visible strip rather than
behind a menu. A student must never do three hours of work and only then discover that nothing was
recorded, so the state is on screen the entire time and the event counter moves as they work. It is
deliberately loud: recording is a mode, and a mode you cannot see is a mode you forget you are in.

Two ways out of recording, and they are different. *Pause* keeps the code: the chain stays on
disk, the strip remembers which code it was (across a restart — the bookmark is a file beside the
chains), and *Resume* carries on in the same chain with the gap recorded. *Generate proof* hands
the work in and finishes the code: the strip goes back to a bare code box, and typing that code
again is refused, because a handed-in recording cannot be reopened. A student who wants another go
at the lab takes a new code.

This replaced a *Cancel* button, which did the same thing as Pause underneath (the chain was always
kept) and was chosen over a pause so the strip would not keep naming the code after the student
asked to stop being recorded. Students read "Cancel" as "throw my work away" and asked for Pause by
name; the surveillance argument lost to the plainer reading. All the decisions still belong to
`services.proof_recorder`; this widget only paints them.

Work that has not reached the Teaching Center is shown here too, with how long it has been waiting
and what went wrong. The outbox has always recorded that; none of it ever reached the screen, so a
submission retried and refused eleven times looked exactly like one that had never been tried.

Thin by design: every decision belongs to `services.proof_recorder`, which is testable without Qt.
"""
from __future__ import annotations


from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

from ..services import outbox, tc_submit
from .worker_host import run_off_gui


def _preview_confirm(parent, recorder) -> bool:
    from .handin_preview import confirm
    return confirm(parent, recorder)


def _tell(parent, title: str, text: str) -> None:
    from .handin_preview import tell
    tell(parent, title, text)


def _ago(seconds: float) -> str:
    """A duration a student can act on. Minutes matter here; seconds do not."""
    m = int(max(0.0, seconds) // 60)
    if m < 1:
        return "just now"
    if m < 60:
        return f"{m} min"
    h = m // 60
    if h < 24:
        return f"{h}h {m % 60:02d}m"
    return f"{h // 24}d {h % 24}h"


class ProofStrip(QWidget):
    """Arm recording, show that it is recording, and generate the proof."""

    # The recorder is deliberately Qt-free and some of the signals it records arrive on worker
    # threads (a rider's reader thread, the mission worker). Its "chain grew" callback therefore
    # goes through a Signal, which Qt queues onto the GUI thread — touching a widget from the
    # emitting thread would be a crash waiting for a slow afternoon.
    changed = Signal()
    # Network work happens on a worker thread and comes back through these. Touching a widget from
    # the emitting thread is a crash waiting for a slow afternoon, and every one of these calls can
    # block for twenty seconds on campus wifi.
    armChecked = Signal(str, dict)          # typed code, server's answer ({} == unreachable)
    handedIn = Signal(dict, dict)           # generate_proof result, server's answer
    flushed = Signal(dict)                  # outbox.flush summary
    questionsArrived = Signal()             # this lab's questions just landed from the server
    questionsFetched = Signal(dict)         # a re-ask came back ({} == still unreachable)
    answerFirst = Signal()                  # they chose to answer before handing in
    graded = Signal()                       # pre-submit measuring finished, back on the GUI thread

    def __init__(self, theme, recorder, parent=None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.recorder = recorder
        # Work that must land in the chain BEFORE the submit entry commits to it — measuring the
        # armed A-Labs. Set by MainWindow, which is the only thing that can reach a running
        # machine; None in tests and wherever there is no topology.
        self.before_generate = None
        self._generating = False
        # How many times Generate has been refused for unanswered questions on THIS code. Reset
        # by arming, so a new lab starts its own count.
        self._reminders = 0
        self.setObjectName("ProofStrip")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 14, 0)
        root.setSpacing(2)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.code = QLineEdit()
        self.code.setObjectName("ProofCode")
        self.code.setPlaceholderText("assignment code")
        self.code.setMaxLength(20)                 # 12 symbols + the two printed hyphens, with room
        self.code.setFixedWidth(150)
        self.code.returnPressed.connect(self._arm)
        row.addWidget(self.code)

        self.state = QLabel("")
        self.state.setObjectName("ProofState")
        self.state.setTextFormat(Qt.RichText)
        self.state.hide()
        row.addWidget(self.state)

        self.button = QPushButton("Record")
        self.button.setObjectName("ProofButton")
        self.button.clicked.connect(self._clicked)
        row.addWidget(self.button)

        self.pause = QPushButton("Pause")
        self.pause.setObjectName("ProofPause")
        self.pause.setToolTip("Stop recording for now. Your work so far is kept, and the strip "
                              "will offer to resume this code — even after you close gBuilder.")
        self.pause.clicked.connect(self._pause)
        self.pause.hide()
        row.addWidget(self.pause)

        # Shown only while something is actually in flight to the course server. Indeterminate:
        # an HTTP POST gives no usable percentage, and a fake one would be a lie about progress.
        self.bar = QProgressBar()
        self.bar.setObjectName("ProofBar")
        self.bar.setRange(0, 0)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(3)
        self.bar.hide()
        row.addWidget(self.bar, 1)
        row.addStretch(1)
        root.addLayout(row)

        self.hint = QLabel("")
        self.hint.setObjectName("ProofHint")
        self.hint.setTextFormat(Qt.RichText)
        root.addWidget(self.hint)

        # Work that has not landed yet — persistent, because a student who closed the dialog has
        # no other way to find out that something is still owed.
        pend = QHBoxLayout()
        pend.setSpacing(6)
        self.pending = QLabel("")
        self.pending.setObjectName("ProofPending")
        self.pending.setTextFormat(Qt.RichText)
        self.pending.hide()
        pend.addWidget(self.pending)
        self.retry = QPushButton("Retry now")
        self.retry.setObjectName("ProofRetry")
        self.retry.clicked.connect(self.flush_outbox)
        self.retry.hide()
        pend.addWidget(self.retry)
        pend.addStretch(1)
        root.addLayout(pend)

        if hasattr(theme, "themeChanged"):
            theme.themeChanged.connect(self._restyle)
        self.changed.connect(self._on_recorder_changed)
        self.graded.connect(self._generate)   # measuring done -> carry on and submit
        self.armChecked.connect(self._on_arm_checked)
        self.handedIn.connect(self._on_handed_in)
        self.flushed.connect(self._on_flushed)
        self._flushing = False            # an outbox flush is on the worker right now
        self._report_flush = False        # ...and the student asked for it, so it must answer
        # Emitted from the worker thread, handled here — the same crossing armChecked already makes.
        self.questionsFetched.connect(self._on_questions_fetched)
        if recorder is not None and hasattr(recorder, "set_on_change"):
            recorder.set_on_change(self.changed.emit)
        self._restyle()
        self.refresh()

    # -- actions ------------------------------------------------------------ #
    def _clicked(self) -> None:
        if self.recorder is not None and self.recorder.armed:
            self._generate()
        else:
            self._arm()

    def _pause(self) -> None:
        """Stop recording for now, keeping the code to come back to.

        The code stays on screen, by design: a student pausing for the night should see tomorrow
        morning exactly what they are resuming, and one who armed the wrong code can still type
        the right one over it.
        """
        if self.recorder is None or not self.recorder.armed:
            return
        self.recorder.pause()
        self.code.clear()
        self.refresh()

    def _code_to_arm(self) -> str:
        """What Record/Resume arms: whatever was typed, else the paused code. Typing wins, so a
        student with a paused lab can still start a different one without hunting for a way to
        forget the first — it stays on disk and can be typed back in at any time."""
        typed = self.code.text().strip()
        if typed or self.recorder is None:
            return typed
        return self.recorder.paused_code()

    def _tc_url(self) -> str:
        """Where the Teaching Center is, or "" if this gBuilder is not attached to a course.

        Read through the recorder, which already holds the app context — the strip does not need
        one of its own, and an offline gBuilder must keep working exactly as it does today.
        """
        try:
            return (self.recorder.ctx.settings.tc_url or "").strip()
        except AttributeError:
            return ""

    def _tc_course(self) -> str:
        """The course this gBuilder is configured for, or "". Advisory only — see `_announce`."""
        try:
            return (self.recorder.ctx.settings.tc_course or "").strip()
        except AttributeError:
            return ""

    def _arm(self) -> None:
        if self.recorder is None:
            return
        typed = self._code_to_arm()
        url = self._tc_url()
        if not (url and typed.strip()):
            self._arm_locally(typed)
            return

        # Ask the course server FIRST. A GINI code is self-checking, so a code the course never
        # issued — or one that expired last night — arms perfectly well offline, and the student
        # finds out only when they try to hand in.
        #
        # On a WORKER thread: this is a network call with a twenty-second timeout, and freezing the
        # canvas while a lab is running would be a worse bug than the one it prevents.
        self._say("Checking that code with the course server…", bad=False)

        def work():
            try:
                answer = tc_submit.check_code(url, typed)
            except tc_submit.Insecure as e:
                # A wrong ADDRESS, not a wrong network: retrying cannot fix it and the server may
                # be perfectly up. Refuse the same way a bad code is refused, in the strip.
                answer = {"ok": False, "error": str(e)}
            except tc_submit.Untrusted as e:
                # A certificate problem is NOT a flaky network: retrying will never help, and
                # recording locally under a code we could not verify hides a real misconfiguration.
                answer = {"ok": False, "error": str(e)}
            except tc_submit.Unreachable:
                answer = {}                       # empty == could not ask, NOT a refusal
            self.armChecked.emit(typed, answer)

        run_off_gui(self, work)

    def _on_arm_checked(self, typed: str, answer: dict) -> None:
        if answer and not answer.get("ok"):
            self._say(answer.get("error", "That code cannot be used."), bad=True)
            self.refresh(keep_hint=True)
            return
        if not answer:
            # The code may be perfectly good and the wifi bad. Recording locally is the only
            # answer that cannot cost a student their evening.
            self._say("Could not reach the course server — recording locally.", bad=False)
            self._arm_locally(typed, keep_hint=True)
            return
        if self._arm_locally(typed, keep_hint=True):
            self._announce(answer)

    def fetch_questions(self) -> None:
        """Ask the course server for the armed code's questions again.

        Two situations need it and they are the same situation: a code armed while the server was
        unreachable never received them, and the arm reply is not persisted, so a gBuilder
        restarted mid-lab has none either. Both are "we know there are questions and we do not
        have them", and both are fixed by asking again.

        On a WORKER thread, like arming — this is a network call with a twenty-second timeout and
        the canvas must not freeze behind it.
        """
        tk = self.recorder.ticket if self.recorder is not None else None
        url = self._tc_url()
        if tk is None:
            self._say("Nothing is being recorded.", bad=True)
            return
        if not url:
            self._say("No course server is configured — check Settings.", bad=True)
            return
        self._say("Asking your course for this lab's questions…", bad=False)

        def work():
            try:
                answer = tc_submit.check_code(url, tk.code)
            except (tc_submit.Insecure, tc_submit.Untrusted) as e:
                answer = {"ok": False, "error": str(e)}
            except tc_submit.Unreachable:
                answer = {}
            self.questionsFetched.emit(answer)

        run_off_gui(self, work)

    def _on_questions_fetched(self, answer: dict) -> None:
        if not answer:
            self._say("Still could not reach the course server.", bad=True)
            return
        if not answer.get("ok"):
            self._say(answer.get("error", "The course server refused that code."), bad=True)
            return
        from ..domain import lab_questions as _lq
        qs = _lq.questions_from(answer)
        if self.recorder is not None and hasattr(self.recorder, "note_questions"):
            self.recorder.note_questions(qs)
        if qs:
            self._say(f"Got {len(qs)} question(s) — see the Ask Questions tab.", bad=False)
            self.questionsArrived.emit()
        else:
            # A real answer, and the answer is none. Worth saying: the student was told to expect
            # some, and silence would leave them pressing the button again.
            self._say("Your course says this lab has no questions after all.", bad=False)
        self.changed.emit()

    def _announce(self, answer: dict) -> None:
        """Say WHICH activity is now being recorded, and whose course it belongs to.

        The server has always sent this back — `activity` is `course/lab`, plus the title — and the
        strip threw all of it away, checking only `ok`. So a student who pasted a code from their
        other course armed silently, worked for two hours, and handed in there. Correctly, and with
        nothing on screen ever having said so.

        A mismatch is a WARNING, never a refusal. A student legitimately enrolled in two courses
        must still be able to hand in to the one this code belongs to, and a hard check would
        produce a false rejection at deadline time — the worst possible moment to be wrong.
        """
        activity = str(answer.get("activity") or "").strip()
        title = str(answer.get("title") or "").strip()
        brief = str(answer.get("brief") or "").strip()
        # Kept, not just printed. The tutor asks the Teaching Center what the course says about a
        # question, and knowing WHICH lab is being recorded is what separates "your course mentions
        # this somewhere" from "this is the lab you are being marked on".
        if self.recorder is not None and hasattr(self.recorder, "note_activity"):
            self.recorder.note_activity(activity, title, brief)
        # Whether the teacher allows this lab to be paused. Absent from an older server's reply,
        # and absent means pausable — that is what every lab was before the option existed.
        pausable = bool(answer.get("pausable", True))
        if self.recorder is not None and hasattr(self.recorder, "note_pausable"):
            self.recorder.note_pausable(pausable)
        # The lab's questions ride in on the same reply — prompts only; the server strips the key.
        # Kept on the recorder rather than emitted onward, so the panel reads one source of truth
        # and a restart or a resumed code finds the same place to look.
        if self.recorder is not None and hasattr(self.recorder, "note_questions"):
            from ..domain import lab_questions as _lq
            qs = _lq.questions_from(answer)
            self.recorder.note_questions(qs)
            if qs:
                self.questionsArrived.emit()
        what = " · ".join([b for b in (f"<b>{activity}</b>" if activity else "",
                                       f"“{title}”" if title else "") if b]) or "this code"
        due = self._due_phrase(answer)
        # Said at arm time, because by the time a student looks for the Pause button and does not
        # find one they have already decided to stop — and the hint is the only place that says
        # why. A pausable lab says nothing: that is the ordinary case.
        sitting = "" if pausable else " This lab is done in <b>one sitting</b> — there is no Pause."
        course = activity.split("/")[0] if "/" in activity else ""
        mine = self._tc_course()
        if course and mine and course.lower() != mine.lower():
            self._say(f"Recording for {what} — note this code is for <b>{course}</b>, but your "
                      f"course is set to <b>{mine}</b>.{due}{sitting}", bad=True)
        else:
            self._say(f"Recording for {what}.{due}{sitting}")

    @staticmethod
    def _due_phrase(answer: dict) -> str:
        """When this code stops being accepted, as a sentence to append to the arm message.

        The server has always sent `valid_until` and `session_minutes`, and the strip threw both
        away — so a student armed a code and was told WHICH lab was being recorded but never WHEN
        it was due. That was survivable while every lab was a timed attempt, because the answer was
        "however many minutes the page told you". It stops being survivable now that a duration of
        0 means the lab is due at a FIXED WALL-CLOCK TIME: the deadline is then the only thing that
        matters, and nothing in gBuilder said it.

        Returns "" when the lab has no deadline at all — inventing one would be worse than silence.
        """
        try:
            valid_until = float(answer.get("valid_until") or 0)
        except (TypeError, ValueError):
            return ""
        if valid_until <= 0:
            return ""                                # no vending deadline ⇒ no absolute expiry
        import datetime as _dt
        when = _dt.datetime.fromtimestamp(valid_until).strftime("%a %d %b, %H:%M")
        try:
            mins = int(float(answer.get("session_minutes") or 0))
        except (TypeError, ValueError):
            mins = 0
        if mins > 0:
            # A timed attempt: the minutes are what the student acts on, the expiry is the backstop.
            return f" You have <b>{mins} min</b> — this code expires <b>{when}</b>."
        # A fixed hand-in time: starting earlier buys nothing, so the moment is the whole story.
        return f" Due <b>{when}</b>."

    def _arm_locally(self, typed: str, keep_hint: bool = False) -> bool:
        ok, message = self.recorder.arm(typed)
        if ok:
            self.code.clear()
            self._reminders = 0            # a new lab, a fresh count — see _questions_checked
        # The refusal is shown in the strip, not in a modal: a mistyped code is an everyday
        # slip, and a dialog for it would train students to dismiss dialogs without reading.
        if not (keep_hint and ok):
            self._say(message, bad=not ok)
        self.refresh(keep_hint=True)
        if ok:
            self.flush_outbox()
        return ok

    def _generate(self) -> None:
        if self.recorder is None:
            return
        if not self._questions_checked():
            return
        if self.before_generate is not None and not self._generating:
            # Measure first, and OFF the GUI thread: a grading run takes about fifteen seconds
            # because it has to watch memory actually move. The student is told what is happening
            # in the strip, but nothing is asked of them — they follow the handout, press Hand in,
            # and the metrics are taken as part of that.
            self._generating = True
            self._say("Checking your work…")
            fn = self.before_generate

            def work():
                try:
                    fn()
                except Exception:              # noqa: BLE001 — a check must never cost a hand-in
                    pass
                self.graded.emit()
            run_off_gui(self, work)
            return
        self._generating = False
        # Show what the marker will see while it can still be fixed — generate_proof seals it.
        if not type(self).confirm_handin(self, self.recorder):
            # Said in a dialog, not only in the strip. The preview LOOKS like the end of a hand-in
            # — a summary with a warning icon — and its ✕ counts as "Not yet", so a student who
            # closed it believing they were done walked away with nothing sent and no receipt,
            # told so only by a line at the bottom of the dock they never looked at.
            self._say("Not handed in. Your work is still being recorded — press Generate when "
                      "you are ready.", bad=False)
            self.refresh(keep_hint=True)
            type(self).tell_student(
                self, "Not handed in yet",
                "Nothing has been sent, and you do not have a receipt code yet.\n\n"
                "Your work is still being recorded. When you are ready, press Generate proof "
                "again and choose Hand in — a receipt code is shown once it is sealed.")
            return
        result = self.recorder.generate_proof()
        if not result.get("ok"):
            # The one failure that used to get a strip line instead of a dialog — every upload
            # outcome below gets a dialog with the receipt, but a proof that could not be SEALED
            # left the student with no receipt and nothing on screen to say why.
            why = result.get("message", "Could not generate a proof.")
            self._say(why, bad=True)
            self.refresh(keep_hint=True)
            type(self).tell_student(
                self, "Not handed in — the proof could not be sealed",
                f"{why}\n\nNothing has been sent, and no receipt code was made.\n\n"
                "If your work is still being recorded, press Generate proof and Hand in again. "
                "If this keeps happening, tell your instructor and include the message above.")
            return
        receipt = result.get("receipt", "")
        self._say(f"Proof generated · receipt <b>{receipt}</b>", bad=False)
        self.refresh(keep_hint=True)
        self._hand_in(result, receipt)

    #: The hand-in preview (ui/handin_preview). A class attribute so a headless test can answer
    #: it without a modal dialog that nobody is there to click.
    confirm_handin = staticmethod(lambda parent, recorder: _preview_confirm(parent, recorder))

    #: A dialog that says why nothing was handed in. A class attribute for the same reason.
    tell_student = staticmethod(lambda parent, title, text: _tell(parent, title, text))

    #: How many times Generate is REFUSED before the warning becomes a choice. Three is enough
    #: that nobody arrives at a blank by accident and few enough that a student who has run out of
    #: time is not trapped — the last reminder says so, and the fourth press goes through.
    REMINDERS = 3

    def _questions_checked(self) -> bool:
        """Stand between Generate and an unanswered lab. Returns whether to go ahead.

        THE FIRST THREE PRESSES ARE REFUSED, with a bell. Then it becomes the old warning, with
        "Hand in anyway" as the default, and the student decides.

        This used to warn once and default to proceeding, on the principle that a student who ran
        out of time still hands in the work they did. That principle survives — the fourth press
        goes through, and a blank is still a fact about the attempt rather than grounds to refuse
        an evening's work. What it did not survive was the student who never noticed the tab, for
        whom one dismissible dialog was one dismissible dialog.

        The last reminder SAYS the next press will go through. Announcing that up front would just
        teach three clicks; withholding it entirely would leave somebody at a deadline believing
        they are locked out, which is worse than the thing this is trying to prevent.

        The dialog is skipped entirely when everything is answered, so the ordinary case is
        unchanged: press Generate, get a proof.
        """
        from ..domain import lab_questions as _lq
        r = self.recorder
        qs = getattr(r, "questions", [])
        tk = r.ticket
        if _lq.missing_because_offline(bool(tk and tk.questions), qs):
            # We KNOW there are questions and never got them. Worse than an unanswered one, because
            # the student was never given the chance, so it is said plainly and separately.
            ask = ("This lab has questions and gBuilder never managed to fetch them, so none of "
                   "them have been answered.\n\nYou can hand in anyway — your instructor will "
                   "see that they were not answered — or connect to your course, fetch them in "
                   "the GINI Labs tab, and answer first.")
        else:
            said = _lq.nudge(qs, _lq.answers_in(r._chain.entries if r._chain else []))
            if not said:
                return True
            ask = (said + "\n\nYou can hand in anyway — an unanswered question does not stop "
                          "you, and your instructor will see it was left blank.")
        left = self._reminders
        if left < self.REMINDERS:
            self._reminders = left + 1
            from .questions_panel import beep
            beep()
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("Answer the lab's questions first")
            last = self._reminders >= self.REMINDERS
            box.setText(ask.split("\n\nYou can hand in anyway")[0] + "\n\n" + (
                "This is the last reminder — press Generate again and it will go through with "
                "them left blank."
                if last else
                "Answer them in the GINI Labs tab, then press Generate again."))
            box.addButton("Back to the questions", QMessageBox.RejectRole)
            box.exec()
            self.answerFirst.emit()
            return False

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("Questions not answered")
        box.setText(ask)
        go = box.addButton("Hand in anyway", QMessageBox.AcceptRole)
        box.addButton("Answer them first", QMessageBox.RejectRole)
        box.setDefaultButton(go)     # the default is to PROCEED: this warns, it does not obstruct
        box.exec()
        if box.clickedButton() is not go:
            self.answerFirst.emit()
            return False
        return True

    def _hand_in(self, result: dict, receipt: str) -> None:
        """Queue the work, then try to send it.

        Queued FIRST, always. The receipt the student is about to be shown is computed from the
        proof's MAC, and the server computes it the same way — so a receipt handed to an instructor
        before the upload lands is still the right one afterwards. That is the property that makes
        a retry safe, and the reason the student is never asked to come back for a new one.
        """
        proof = result.get("proof") or {}
        path = result.get("path", "")
        try:
            outbox.queue(proof, result.get("topology"), result.get("shadows"))
        except Exception as e:                                   # noqa: BLE001
            # The proof file is still on disk; say so rather than pretending nothing happened.
            self._say(f"Could not queue the submission: {e}", bad=True)

        url = self._tc_url()
        if not url:
            QMessageBox.information(
                self, "Proof generated",
                f"Your proof was written to:\n{path}\n\nReceipt code: {receipt}\n\n"
                "This gBuilder is not attached to a course server, so nothing was sent. Hand the "
                "proof file to your instructor.")
            return

        self._say(f"Sending your work to the course server · receipt <b>{receipt}</b>", bad=False)
        self._busy(True)

        def work():
            try:
                answer = tc_submit.submit(url, str(proof.get("ticket", "")), proof,
                                          result.get("topology"), result.get("shadows"))
            except tc_submit.Insecure as e:
                # The work is already queued, so this is safe to report and leave: fixing the
                # address and pressing Retry now sends it. An escaping exception here would kill
                # this thread and the student would be told nothing at all.
                answer = {"ok": False, "insecure": True, "error": str(e)}
            except tc_submit.Untrusted as e:
                answer = {"ok": False, "untrusted": True, "error": str(e)}
            except tc_submit.Unreachable as e:
                answer = {"ok": False, "unreachable": True, "error": str(e)}
            self.handedIn.emit(result, answer)

        run_off_gui(self, work)

    def _busy(self, on: bool) -> None:
        """Show that something is in flight. The bar is the only honest progress we have."""
        self.bar.setVisible(on)

    def _on_handed_in(self, result: dict, answer: dict) -> None:
        self._busy(False)
        receipt = result.get("receipt", "")
        path = result.get("path", "")

        if answer.get("ok"):
            outbox.forget(answer.get("receipt", receipt))
            receipt = answer.get("receipt", receipt)
            late = "" if answer.get("within_session", True) else (
                "\n\nNote: this took longer than the time the code allowed. Your instructor can "
                "see that, and can still mark it.")
            self._say(f"Handed in · receipt <b>{receipt}</b>", bad=False)
            QMessageBox.information(
                self, "Handed in",
                f"Your work has been sent to the course server.\n\n"
                f"Receipt code: {receipt}\n\n"
                f"Give this receipt to your instructor — it is how they find your work and record "
                f"it as yours. Nothing else needs to be handed in.\n\n"
                f"This code is now finished and cannot be recorded under again. If you want "
                f"another go at the lab, take a new code from your course page.\n\n"
                f"A copy is also on disk at:\n{path}{late}")
            return

        if answer.get("reason") in outbox.SETTLED:
            outbox.forget(receipt)               # an earlier attempt already landed

        if answer.get("insecure"):
            self._say(f"Not sent · the course server address is not HTTPS · "
                      f"receipt <b>{receipt}</b>", bad=True)
            QMessageBox.warning(
                self, "The course server address is not HTTPS",
                f"Your work is safe. The proof is on disk at:\n{path}\n\n"
                f"Receipt code: {receipt}\n\n{answer.get('error', '')}\n\n"
                f"It is queued and will be sent once the address is corrected in "
                f"Settings → Teaching Center. Give your instructor the receipt either way.")
            return

        if answer.get("untrusted"):
            # Saying "is it still running?" here would send a student chasing a server that is up,
            # and no amount of retrying fixes a certificate. Name it, and point at the instructor.
            self._say(f"Not sent · certificate not trusted · receipt <b>{receipt}</b>", bad=True)
            QMessageBox.warning(
                self, "The course server's certificate is not trusted",
                f"Your work is safe. The proof is on disk at:\n{path}\n\n"
                f"Receipt code: {receipt}\n\n{answer.get('error', '')}\n\n"
                f"gBuilder will keep the submission and try again, but this will not clear up on "
                f"its own. Give your instructor the receipt either way.")
            return

        # It did NOT go. The receipt is still correct and still theirs, and gBuilder will keep
        # trying — so the message has to prevent the one action that would make it worse, which is
        # a panicked student redoing the lab under a new code.
        self._say(f"Not sent yet · receipt <b>{receipt}</b> · will retry", bad=True)
        QMessageBox.warning(
            self, "Saved — not sent yet",
            f"Your work is safe. The proof is on disk at:\n{path}\n\n"
            f"Receipt code: {receipt}\n\n"
            f"It could not reach the course server: {answer.get('error', 'refused')}\n\n"
            f"gBuilder will try again automatically while it is open, and next time it starts. "
            f"To send it yourself — once you are connected, on the campus VPN if your course "
            f"needs it — choose Course → Resubmit to Course Server from the menu bar.\n\n"
            f"This receipt stays valid — give it to your instructor either way, and do not redo "
            f"the lab.")

    # -- the outbox ---------------------------------------------------------- #
    def flush_outbox(self) -> None:
        """Try anything that never made it. Safe to call often: it is a no-op when empty, and a
        no-op while one is already running — the launch flush, the five-minute timer and the
        student's own Resubmit can all land on the same worker, and two flushes of the same
        entry would only make the server say `duplicate` to the second."""
        url = self._tc_url()
        if not url or not outbox.pending():
            return
        if self._flushing:
            return
        self._flushing = True
        self._busy(True)
        self._say("Sending work that has not reached the course server yet…")

        def work():
            self.flushed.emit(outbox.flush(url, tc_submit.submit))

        run_off_gui(self, work)

    def resubmit(self) -> None:
        """Course → Resubmit to Course Server: send what is owed, now, and SAY what happened.

        The strip already had a Retry button, in the dock, visible only while something was
        pending — which is to say findable only by a student who already knew it existed. The
        one who needs it has a "Saved — not sent yet" dialog on screen and a menu bar, so it is
        in the menu bar. Unlike the automatic flushes this one always answers, including when
        there is nothing to send: "nothing is waiting" is exactly what a worried student came to
        hear.
        """
        if not self._tc_url():
            QMessageBox.information(
                self, "No course server",
                "No course server is configured, so there is nowhere to send work to. Set the "
                "address your instructor gave you in Settings → Teaching Center, then choose "
                "Resubmit again.")
            return
        if not outbox.pending():
            QMessageBox.information(
                self, "Nothing to resubmit",
                "Every submission made from this computer has reached the course server. "
                "Your receipts are the proof — there is nothing waiting to send.")
            return
        self._report_flush = True
        self.flush_outbox()

    def _on_flushed(self, summary: dict) -> None:
        self._flushing = False
        self._busy(False)
        sent, kept = summary.get("sent") or [], summary.get("kept") or []
        if sent:
            # Worth a word: the student was last told it had NOT been sent, and silence would
            # leave them believing that.
            self._say(f"Caught up · {len(sent)} earlier submission"
                      f"{'' if len(sent) == 1 else 's'} sent", bad=False)
        elif kept:
            self._say(f"{len(kept)} submission{'' if len(kept) == 1 else 's'} still waiting to "
                      f"send", bad=True)
        self._refresh_pending()
        if self._report_flush:
            self._report_flush = False
            self._report(sent, kept)

    def _report(self, sent: list, kept: list) -> None:
        """What Resubmit did, receipt by receipt — with the last error, because that line is
        what an instructor will ask for, and until now it lived in a tooltip."""
        lines = []
        if sent:
            lines.append("Sent to the course server:\n" + "\n".join(f"  • {r}" for r in sent))
        if kept:
            still = []
            for e in outbox.pending():
                if e.get("receipt") not in kept:
                    continue
                tries = int(e.get("attempts", 0) or 0)
                why = str(e.get("last_error") or "no reply")
                still.append(f"  • {e.get('receipt')} · {tries} attempt{'' if tries == 1 else 's'}"
                             f"\n    {why}")
            lines.append("Still waiting:\n" + "\n".join(still))
            lines.append("Your receipts stay valid. If this keeps failing, check that you are "
                         "connected (on the campus VPN, if your course needs it), and give your "
                         "instructor the receipt and the error above.")
        else:
            lines.append("Everything has now reached the course server.")
        if kept:
            QMessageBox.warning(self, "Resubmit", "\n\n".join(lines))
        else:
            QMessageBox.information(self, "Resubmit", "\n\n".join(lines))

    # -- rendering ----------------------------------------------------------- #
    def _on_recorder_changed(self) -> None:
        """The chain grew. Keeps whatever the strip was last saying — an entry recorded a moment
        after 'Proof generated' must not wipe the receipt off the screen."""
        self.refresh(keep_hint=True)

    def refresh(self, keep_hint: bool = False) -> None:
        """Repaint from the recorder's state. Called on every appended entry, so it stays cheap:
        two label writes and a visibility flip."""
        t = self.theme.theme
        s = self.recorder.status() if self.recorder is not None else {"armed": False}
        armed = bool(s.get("armed"))
        paused = str(s.get("paused") or "") if not armed else ""
        self.setProperty("recording", "yes" if armed else "paused" if paused else "no")
        # A dynamic property only repaints if the style is re-evaluated.
        self.style().unpolish(self); self.style().polish(self)
        if armed:
            self.code.hide()
            self.state.show()
            # No Pause for a lab the course declared one sitting. Hidden rather than disabled: a
            # greyed button invites hovering for a reason, and the reason was given at arm time.
            self.pause.setVisible(bool(s.get("pausable", True)))
            # Deliberately loud. Recording is a MODE, and a mode you cannot see is a mode you
            # forget you are in — a student who never notices it is on cannot tell you why their
            # afternoon is in a chain, and one who never notices it is off loses the afternoon.
            self.state.setText(
                f'<span style="color:{t.accent_for("red")};font-size:15px">●</span> '
                f'<span style="color:{t.text};font-weight:800;letter-spacing:1px">REC</span> '
                f'<span style="color:{t.faint}">· {s.get("short", "")} · '
                f'{s.get("count", 0)} events</span>')
            self.button.setText("Generate proof")
            if not keep_hint:
                self._say("Your work is being recorded under this code.")
        elif paused:
            # Paused names the code on purpose — it is what the student is coming back to. The
            # code box stays, so a different code can be typed over the top; Resume means the
            # paused one unless something was typed (see _code_to_arm).
            self.code.show()
            self.state.show()
            self.pause.hide()
            self.state.setText(
                f'<span style="color:{t.text};font-weight:800;letter-spacing:1px">⏸ PAUSED</span> '
                f'<span style="color:{t.faint}">· {s.get("paused_short", "")}</span>')
            self.state.setToolTip(f"Recording under {paused} is paused.")
            self.button.setText("Resume")
            if not keep_hint:
                self._say(f"Recording under <b>{paused}</b> is paused. Press Resume to carry on "
                          f"where you left off, or enter a different code.")
        else:
            # Unarmed is unarmed: one code box and the ordinary prompt, whether the student has
            # never recorded anything or handed a lab in ten seconds ago.
            self.state.hide()
            self.state.setToolTip("")
            self.pause.hide()
            self.code.show()
            self.button.setText("Record")
            if not keep_hint:
                self._say("Enter your assignment code to record proof of your work.")
        self._refresh_pending()

    def _refresh_pending(self) -> None:
        """Say what is still owed to the course server, and for how long.

        Persistent on purpose: the hand-in dialog says "will retry", and once it is dismissed there
        was nothing anywhere on screen to say whether that ever happened.
        """
        t = self.theme.theme
        try:
            info = outbox.summary()
        except Exception:                                        # noqa: BLE001
            self.pending.hide(); self.retry.hide()
            return
        n = info.get("count", 0)
        if not n:
            self.pending.hide()
            self.retry.hide()
            return
        age = _ago(info.get("oldest_age_s", 0.0))
        tries = info.get("attempts", 0)
        bits = [f"{n} submission{'' if n == 1 else 's'} waiting to send", f"oldest {age}"]
        if tries:
            bits.append(f"{tries} attempt{'' if tries == 1 else 's'}")
        self.pending.setText(
            f'<span style="color:{t.danger}">⇧ {" · ".join(bits)}</span>')
        err = info.get("last_error", "")
        self.pending.setToolTip(
            (f"Last error: {err}\n\n" if err else "")
            + "Your receipts stay valid. gBuilder retries on launch, every few minutes while it "
              "is open, when you enter a code, and when you choose Course → Resubmit to Course "
              "Server.")
        self.pending.show()
        self.retry.setVisible(bool(self._tc_url()))

    def _say(self, text: str, bad: bool = False) -> None:
        t = self.theme.theme
        colour = t.danger if bad else t.faint
        self.hint.setText(f'<span style="color:{colour}">{text}</span>')

    def _restyle(self) -> None:
        t = self.theme.theme
        from .theme.manager import sp
        rec = t.accent_for("red")
        self.setStyleSheet(f"""
            QWidget#ProofStrip {{ border-right: 1px solid {t.line2}; }}
            /* Recording is a mode; the whole control changes, not just a dot. */
            QWidget#ProofStrip[recording="yes"] {{ border-right: 1px solid {t.line2};
                                                   border-left: 3px solid {rec};
                                                   background: {t.bg3}; }}
            /* Paused is a state too, quieter than recording: a bar in the ordinary line colour
               says "something is held here" without the red that means "being watched". */
            QWidget#ProofStrip[recording="paused"] {{ border-right: 1px solid {t.line2};
                                                      border-left: 3px solid {t.line}; }}
            QLabel#ProofPending {{ font-size: {sp(9)}px; }}
            QProgressBar#ProofBar {{ background: {t.bg3}; border: none; border-radius: 2px;
                                     max-width: 120px; }}
            QProgressBar#ProofBar::chunk {{ background: {t.accent}; border-radius: 2px; }}
            QPushButton#ProofPause, QPushButton#ProofRetry {{
                background: {t.panel2}; color: {t.text}; border: 1px solid {t.line};
                border-radius: 5px; padding: 3px 10px; font-size: {sp(11)}px; }}
            QPushButton#ProofPause:hover,
            QPushButton#ProofRetry:hover {{ border-color: {t.accent}; }}
            QLineEdit#ProofCode {{ background: {t.bg3}; color: {t.text};
                                   border: 1px solid {t.line}; border-radius: 5px;
                                   padding: 3px 6px; font-size: {sp(12)}px;
                                   letter-spacing: 1px; }}
            QLabel#ProofState {{ font-size: {sp(12)}px; }}
            QLabel#ProofHint {{ font-size: {sp(9)}px; }}
            QPushButton#ProofButton {{ background: {t.panel2}; color: {t.text};
                                       border: 1px solid {t.line}; border-radius: 5px;
                                       padding: 3px 10px; font-size: {sp(11)}px; }}
            QPushButton#ProofButton:hover {{ border-color: {t.accent}; }}
        """)
        self.refresh(keep_hint=True)
