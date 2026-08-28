"""The proof-of-activity control at the left of the dashboard strip.

Two states and nothing else:

  * **unarmed** — a code box and one line saying what it is for.
  * **armed** — ``● recording · A3K7 · 47 events`` and a *Generate proof* button.

The recording indicator is the whole reason this lives on the always-visible strip rather than
behind a menu. A student must never do three hours of work and only then discover that nothing was
recorded, so the state is on screen the entire time and the event counter moves as they work.

Thin by design: every decision belongs to `services.proof_recorder`, which is testable without Qt.
"""
from __future__ import annotations

import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from ..services import outbox, tc_submit


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

    def __init__(self, theme, recorder, parent=None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.recorder = recorder
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
        row.addStretch(1)
        root.addLayout(row)

        self.hint = QLabel("")
        self.hint.setObjectName("ProofHint")
        self.hint.setTextFormat(Qt.RichText)
        root.addWidget(self.hint)

        if hasattr(theme, "themeChanged"):
            theme.themeChanged.connect(self._restyle)
        self.changed.connect(self._on_recorder_changed)
        self.armChecked.connect(self._on_arm_checked)
        self.handedIn.connect(self._on_handed_in)
        self.flushed.connect(self._on_flushed)
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

    def _tc_url(self) -> str:
        """Where the Teaching Center is, or "" if this gBuilder is not attached to a course.

        Read through the recorder, which already holds the app context — the strip does not need
        one of its own, and an offline gBuilder must keep working exactly as it does today.
        """
        try:
            return (self.recorder.ctx.settings.tc_url or "").strip()
        except AttributeError:
            return ""

    def _arm(self) -> None:
        if self.recorder is None:
            return
        typed = self.code.text()
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
            except tc_submit.Unreachable:
                answer = {}                       # empty == could not ask, NOT a refusal
            self.armChecked.emit(typed, answer)

        threading.Thread(target=work, daemon=True).start()

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
        self._arm_locally(typed)

    def _arm_locally(self, typed: str, keep_hint: bool = False) -> None:
        ok, message = self.recorder.arm(typed)
        if ok:
            self.code.clear()
        # The refusal is shown in the strip, not in a modal: a mistyped code is an everyday
        # slip, and a dialog for it would train students to dismiss dialogs without reading.
        if not (keep_hint and ok):
            self._say(message, bad=not ok)
        self.refresh(keep_hint=True)
        if ok:
            self.flush_outbox()

    def _generate(self) -> None:
        if self.recorder is None:
            return
        result = self.recorder.generate_proof()
        if not result.get("ok"):
            self._say(result.get("message", "Could not generate a proof."), bad=True)
            self.refresh(keep_hint=True)
            return
        receipt = result.get("receipt", "")
        self._say(f"Proof generated · receipt <b>{receipt}</b>", bad=False)
        self.refresh(keep_hint=True)
        self._hand_in(result, receipt)

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
            outbox.queue(proof, result.get("topology"))
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

        self._say(f"Sending your work · receipt <b>{receipt}</b>", bad=False)

        def work():
            try:
                answer = tc_submit.submit(url, str(proof.get("ticket", "")), proof,
                                          result.get("topology"))
            except tc_submit.Unreachable as e:
                answer = {"ok": False, "unreachable": True, "error": str(e)}
            self.handedIn.emit(result, answer)

        threading.Thread(target=work, daemon=True).start()

    def _on_handed_in(self, result: dict, answer: dict) -> None:
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
                f"A copy is also on disk at:\n{path}{late}")
            return

        if answer.get("reason") in outbox.SETTLED:
            outbox.forget(receipt)               # an earlier attempt already landed

        # It did NOT go. The receipt is still correct and still theirs, and gBuilder will keep
        # trying — so the message has to prevent the one action that would make it worse, which is
        # a panicked student redoing the lab under a new code.
        self._say(f"Not sent yet · receipt <b>{receipt}</b> · will retry", bad=True)
        QMessageBox.warning(
            self, "Saved — not sent yet",
            f"Your work is safe. The proof is on disk at:\n{path}\n\n"
            f"Receipt code: {receipt}\n\n"
            f"It could not reach the course server: {answer.get('error', 'refused')}\n\n"
            f"gBuilder will try again automatically next time it starts or you enter a code. "
            f"This receipt stays valid — give it to your instructor either way, and do not redo "
            f"the lab.")

    # -- the outbox ---------------------------------------------------------- #
    def flush_outbox(self) -> None:
        """Try anything that never made it. Safe to call often: it is a no-op when empty."""
        url = self._tc_url()
        if not url or not outbox.pending():
            return

        def work():
            self.flushed.emit(outbox.flush(url, tc_submit.submit))

        threading.Thread(target=work, daemon=True).start()

    def _on_flushed(self, summary: dict) -> None:
        sent, kept = summary.get("sent") or [], summary.get("kept") or []
        if sent:
            # Worth a word: the student was last told it had NOT been sent, and silence would
            # leave them believing that.
            self._say(f"Caught up · {len(sent)} earlier submission"
                      f"{'' if len(sent) == 1 else 's'} sent", bad=False)
        elif kept:
            self._say(f"{len(kept)} submission{'' if len(kept) == 1 else 's'} still waiting to "
                      f"send", bad=True)

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
        if s.get("armed"):
            self.code.hide()
            self.state.show()
            self.state.setText(
                f'<span style="color:{t.accent_for("red")}">●</span> '
                f'<span style="color:{t.text};font-weight:700">recording</span> '
                f'<span style="color:{t.faint}">· {s.get("short", "")} · '
                f'{s.get("count", 0)} events</span>')
            self.button.setText("Generate proof")
            if not keep_hint:
                self._say("Your work is being recorded under this code.")
        else:
            self.state.hide()
            self.code.show()
            self.button.setText("Record")
            if not keep_hint:
                self._say("Enter your assignment code to record proof of your work.")

    def _say(self, text: str, bad: bool = False) -> None:
        t = self.theme.theme
        colour = t.danger if bad else t.faint
        self.hint.setText(f'<span style="color:{colour}">{text}</span>')

    def _restyle(self) -> None:
        t = self.theme.theme
        from .theme.manager import sp
        self.setStyleSheet(f"""
            QWidget#ProofStrip {{ border-right: 1px solid {t.line2}; }}
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
