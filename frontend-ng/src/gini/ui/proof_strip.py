"""The proof-of-activity control at the left of the dashboard strip.

States:

  * **unarmed** — a code box, and a *Resume* button when a code was paused rather than finished.
  * **armed** — a large ``● REC`` block, the code, the event counter, *Generate proof*, *Pause*.
  * **sending** — the same, plus a progress bar while the package goes to the course server.

The recording indicator is the whole reason this lives on the always-visible strip rather than
behind a menu. A student must never do three hours of work and only then discover that nothing was
recorded, so the state is on screen the entire time and the event counter moves as they work. It is
deliberately loud: recording is a mode, and a mode you cannot see is a mode you forget you are in.

Recording used to be a one-way door. Nothing disarmed — not even generating a proof, which left the
strip saying "recording" for ever — so arming the wrong code meant restarting gBuilder. *Pause*
stops it and keeps the code; the chain is already on disk, so resuming appends to the same chain
rather than starting a second one.

Work that has not reached the Teaching Center is shown here too, with how long it has been waiting
and what went wrong. The outbox has always recorded that; none of it ever reached the screen, so a
submission retried and refused eleven times looked exactly like one that had never been tried.

Thin by design: every decision belongs to `services.proof_recorder`, which is testable without Qt.
"""
from __future__ import annotations

import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

from ..services import outbox, tc_submit


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

        # Offered only when a paused code's chain is still on disk, so it can never advertise
        # resuming something that is not there.
        self.resume = QPushButton("Resume")
        self.resume.setObjectName("ProofResume")
        self.resume.clicked.connect(self._resume)
        self.resume.hide()
        row.addWidget(self.resume)

        self.button = QPushButton("Record")
        self.button.setObjectName("ProofButton")
        self.button.clicked.connect(self._clicked)
        row.addWidget(self.button)

        self.pause = QPushButton("Pause")
        self.pause.setObjectName("ProofPause")
        self.pause.setToolTip("Stop recording. Your work so far is kept and you can resume "
                              "under the same code.")
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

    def _pause(self) -> None:
        """Stop recording without finishing. The chain stays on disk and the code is remembered."""
        if self.recorder is None or not self.recorder.armed:
            return
        pretty = (self.recorder.status() or {}).get("ticket", "")
        self.recorder.disarm()
        self._say(f"Paused · {pretty} · your work is kept — press Resume to carry on.")
        self.refresh(keep_hint=True)

    def _resume(self) -> None:
        if self.recorder is None:
            return
        ok, message = self.recorder.resume()
        self._say(message, bad=not ok)
        self.refresh(keep_hint=True)
        if ok:
            self.flush_outbox()

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
            except tc_submit.Untrusted as e:
                # A certificate problem is NOT a flaky network: retrying will never help, and
                # recording locally under a code we could not verify hides a real misconfiguration.
                answer = {"ok": False, "error": str(e)}
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

        self._say(f"Sending your work to the course server · receipt <b>{receipt}</b>", bad=False)
        self._busy(True)

        def work():
            try:
                answer = tc_submit.submit(url, str(proof.get("ticket", "")), proof,
                                          result.get("topology"))
            except tc_submit.Untrusted as e:
                answer = {"ok": False, "untrusted": True, "error": str(e)}
            except tc_submit.Unreachable as e:
                answer = {"ok": False, "unreachable": True, "error": str(e)}
            self.handedIn.emit(result, answer)

        threading.Thread(target=work, daemon=True).start()

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
                f"A copy is also on disk at:\n{path}{late}")
            return

        if answer.get("reason") in outbox.SETTLED:
            outbox.forget(receipt)               # an earlier attempt already landed

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
            f"gBuilder will try again automatically next time it starts or you enter a code. "
            f"This receipt stays valid — give it to your instructor either way, and do not redo "
            f"the lab.")

    # -- the outbox ---------------------------------------------------------- #
    def flush_outbox(self) -> None:
        """Try anything that never made it. Safe to call often: it is a no-op when empty."""
        url = self._tc_url()
        if not url or not outbox.pending():
            return

        self._busy(True)
        self._say("Sending work that has not reached the course server yet…")

        def work():
            self.flushed.emit(outbox.flush(url, tc_submit.submit))

        threading.Thread(target=work, daemon=True).start()

    def _on_flushed(self, summary: dict) -> None:
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
        self.setProperty("recording", "yes" if armed else "no")
        # A dynamic property only repaints if the style is re-evaluated.
        self.style().unpolish(self); self.style().polish(self)
        if armed:
            self.code.hide()
            self.resume.hide()
            self.state.show()
            self.pause.show()
            # Deliberately loud. Recording is a MODE, and a mode you cannot see is a mode you
            # forget you are in — a student who never notices it is on cannot tell you why their
            # afternoon is in a chain, and one who never notices it is off loses the afternoon.
            done = s.get("submitted")
            self.state.setText(
                f'<span style="color:{t.accent_for("red")};font-size:15px">●</span> '
                f'<span style="color:{t.text};font-weight:800;letter-spacing:1px">REC</span> '
                f'<span style="color:{t.faint}">· {s.get("short", "")} · '
                f'{s.get("count", 0)} events{" · proof generated" if done else ""}</span>')
            self.button.setText("Generate proof")
            if not keep_hint:
                self._say("Your work is being recorded under this code.")
        else:
            self.state.hide()
            self.pause.hide()
            self.code.show()
            self.button.setText("Record")
            # Only when the chain is really still there — see recorder.can_resume.
            if s.get("can_resume"):
                self.resume.setText(f"Resume {s.get('paused_short', '')}")
                self.resume.setToolTip(f"Carry on recording under {s.get('paused', '')}. "
                                       f"Your earlier work is still in that chain.")
                self.resume.show()
            else:
                self.resume.hide()
            if not keep_hint:
                self._say("Enter your assignment code to record proof of your work."
                          if not s.get("can_resume") else
                          f"Paused. Resume {s.get('paused', '')}, or enter a different code.")
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
            + "Your receipts stay valid. gBuilder retries on launch, when you enter a code, "
              "and when you press Retry now.")
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
            QLabel#ProofPending {{ font-size: {sp(9)}px; }}
            QProgressBar#ProofBar {{ background: {t.bg3}; border: none; border-radius: 2px;
                                     max-width: 120px; }}
            QProgressBar#ProofBar::chunk {{ background: {t.accent}; border-radius: 2px; }}
            QPushButton#ProofPause, QPushButton#ProofResume, QPushButton#ProofRetry {{
                background: {t.panel2}; color: {t.text}; border: 1px solid {t.line};
                border-radius: 5px; padding: 3px 10px; font-size: {sp(11)}px; }}
            QPushButton#ProofPause:hover, QPushButton#ProofResume:hover,
            QPushButton#ProofRetry:hover {{ border-color: {t.accent}; }}
            QPushButton#ProofResume {{ border-color: {t.accent}; }}
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
