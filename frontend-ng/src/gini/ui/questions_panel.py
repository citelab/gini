"""Ask Questions — the lab's own questions, answered where the work is happening.

A tab beside Inspector / Ask GINI / GINI Source / Terminal, because that is where a student already
is. The alternative was a web form on the Teaching Center, which would have meant leaving gBuilder
mid-lab, signing in to something (students have no account, by design), and producing an answer
with no connection at all to the work it is about.

Answers go into the **proof chain**, so they are part of the submission rather than beside it: one
artefact, one hash chain, one thing for a marker to read, and an answer that cannot be separated
from the lab it was given during.

Three things this panel deliberately does not do:

  * **It does not mark.** The key never crosses the wire — the Teaching Center strips it from the
    arm reply — so there is nothing here to compare against and no code that could start trying.
  * **It does not block.** An unanswered question is a fact about the attempt for the marker to
    see, not a gate on handing in. A student who ran out of time still submits their work.
  * **It does not lecture.** No countdown, no "2 questions remaining!" nag. A count on the tab and
    one beep when they arrive is the whole of the pressure it applies.

Live only while recording is in progress, which is the whole of its lifetime: before arming there
is no lab, and after handing in an answer would land past the `submit` entry, in a chain nobody
will read again. Both states say so rather than presenting a dead box.
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..domain import lab_questions as lq
from .theme.manager import sp as _sp

#: A short answer, and the box is sized to say so. The chain clips at 2000 either way; this is so
#: a student is told BEFORE they lose the end of a paragraph rather than after.
ROWS = 3


class _AnswerCard(QFrame):
    """One question, its box, and the one button that puts it in the chain."""

    submitted = Signal(str, str)                      # (question id, text)

    def __init__(self, question: lq.Question, theme, parent=None) -> None:
        super().__init__(parent)
        self.q = question
        self.theme = theme
        self._saved = ""

        lay = QVBoxLayout(self)
        lay.setContentsMargins(_sp(12), _sp(10), _sp(12), _sp(10))
        lay.setSpacing(_sp(6))

        self._prompt = QLabel(question.prompt)
        self._prompt.setWordWrap(True)                # or one long question sets the panel's width
        lay.addWidget(self._prompt)

        self._box = QTextEdit()
        self._box.setAcceptRichText(False)            # a paste from a browser must arrive as text
        self._box.setPlaceholderText("Your answer, in a sentence or two…")
        self._box.setTabChangesFocus(True)            # Tab moves on; nobody wants a tab character
        self._box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._box.textChanged.connect(self._on_typed)
        lay.addWidget(self._box)

        row = QHBoxLayout()
        row.setSpacing(_sp(8))
        self._state = QLabel("")
        self._state.setWordWrap(True)
        self._save = QPushButton("Record this answer")
        self._save.clicked.connect(self._emit)
        row.addWidget(self._state, 1)
        row.addWidget(self._save)
        lay.addLayout(row)

        self.refresh_theme()
        self._sync()

    # -- state ---------------------------------------------------------------- #
    def set_answer(self, text: str) -> None:
        """Show what is already in the chain for this question.

        UNSAVED TYPING WINS. The panel is refreshed on every recorder change — which is every
        device placed, every link made and every command run — so a version of this that always
        pushed the chain's value into the box would wipe a half-written answer every few seconds
        while the student was still writing it. Nothing else writes answers, so a box that differs
        from the chain differs because the person is typing in it.
        """
        dirty = self._box.toPlainText().strip() != (self._saved or "").strip()
        self._saved = text
        if not dirty and self._box.toPlainText() != text:
            self._box.setPlainText(text)
        self._sync()

    def text(self) -> str:
        return self._box.toPlainText().strip()

    def set_live(self, live: bool) -> None:
        self._box.setReadOnly(not live)
        self._sync()

    def _on_typed(self) -> None:
        # The chain clips at MAX_ANSWER. Saying so while they type beats truncating in silence.
        raw = self._box.toPlainText()
        if len(raw) > lq.MAX_ANSWER:
            cur = self._box.textCursor()
            at = cur.position()
            self._box.setPlainText(raw[:lq.MAX_ANSWER])
            cur.setPosition(min(at, lq.MAX_ANSWER))
            self._box.setTextCursor(cur)
        self._sync()

    def _emit(self) -> None:
        self.submitted.emit(self.q.id, self.text())

    def _sync(self) -> None:
        t = getattr(self.theme, "theme", None)
        muted = getattr(t, "muted", "#6b7280")
        ok = getattr(t, "success", "#0a7f4f")
        now, live = self.text(), not self._box.isReadOnly()
        changed = now != (self._saved or "").strip()
        if not live:
            # Handed in, or not recording. Neither is a failure, so neither is red.
            self._state.setText("Answered." if self._saved else "")
            self._state.setStyleSheet(f"color:{muted};")
            self._save.setEnabled(False)
            return
        self._save.setEnabled(bool(changed))
        if self._saved and not changed:
            self._state.setText("Recorded — you can change it.")
            self._state.setStyleSheet(f"color:{ok};font-weight:600;")
        elif self._saved:
            self._state.setText("Edited. Record it again to keep this version.")
            self._state.setStyleSheet(f"color:{muted};")
        else:
            left = lq.MAX_ANSWER - len(now)
            self._state.setText("" if left > 200 else f"{left} characters left")
            self._state.setStyleSheet(f"color:{muted};")

    def refresh_theme(self, *_a) -> None:
        t = getattr(self.theme, "theme", None)
        line = getattr(t, "line", "#cfd6e0")
        panel = getattr(t, "panel", "#ffffff")
        text = getattr(t, "text", "#131a23")
        self.setStyleSheet(
            f"_AnswerCard{{background:{panel};border:1px solid {line};border-radius:8px;}}")
        self._prompt.setStyleSheet(f"font-weight:700;color:{text};")
        self._box.setFixedHeight(self._box.fontMetrics().lineSpacing() * ROWS + _sp(16))
        self._sync()


class QuestionsPanel(QWidget):
    """The tab. Owns no state — it renders the recorder's and writes back through it."""

    #: (question id, text). The window connects this to the recorder, so this panel never
    #: imports it and can be built in a test with nothing behind it.
    answered = Signal(str, str)
    #: Asked for the lab's questions again: armed offline, or restarted while armed.
    refetch = Signal()

    def __init__(self, theme, parent=None) -> None:
        super().__init__(parent)
        self.theme = theme
        self._cards: dict[str, _AnswerCard] = {}
        self._questions: list[lq.Question] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(_sp(10), _sp(10), _sp(10), _sp(10))
        root.setSpacing(_sp(8))

        self._title = QLabel("Ask Questions")
        root.addWidget(self._title)

        self._sub = QLabel("")
        self._sub.setWordWrap(True)                   # the empty states are sentences, not labels
        root.addWidget(self._sub)

        self._fetch = QPushButton("Fetch them now")
        self._fetch.clicked.connect(self.refetch.emit)
        self._fetch.hide()
        row = QHBoxLayout()
        row.addWidget(self._fetch)
        row.addStretch(1)
        root.addLayout(row)

        # Scrolled, because three questions with three-line boxes is taller than a docked pane and
        # a student must never be unable to reach the last one.
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._holder = QWidget()
        self._stack = QVBoxLayout(self._holder)
        self._stack.setContentsMargins(0, 0, 0, 0)
        self._stack.setSpacing(_sp(10))
        self._stack.addStretch(1)
        self._scroll.setWidget(self._holder)
        root.addWidget(self._scroll, 1)

        if hasattr(theme, "themeChanged"):
            theme.themeChanged.connect(self.refresh_theme)
        self.refresh_theme()

    # -- the one entry point --------------------------------------------------- #
    def show_state(self, *, armed: bool, submitted: bool, questions, answers,
                   expects_questions: bool = False) -> None:
        """Render everything from the recorder's state in one call.

        One entry point rather than a set of setters: every one of these facts changes what the
        others should say, and a panel updated field by field spends most of its life displaying a
        combination that was never true.
        """
        t = getattr(self.theme, "theme", None)
        muted = getattr(t, "muted", "#6b7280")
        warn = getattr(t, "warning", "#b46b00")
        self._sub.setStyleSheet(f"color:{muted};")
        self._fetch.hide()

        if not armed:
            self._render([], {})
            self._sub.setText("Not recording. Enter your assignment code in the strip below the "
                              "canvas — if this lab asks questions, they will appear here.")
            self._retitle(0, 0)
            return

        if lq.missing_because_offline(expects_questions, questions):
            # The bit in the code earned its keep: we know there ARE questions and we do not have
            # them. Saying so is the whole point — the alternative is a student handing in with
            # blanks caused by hotel wifi and nobody finding out until it is marked.
            self._render([], {})
            self._sub.setText(
                "This lab has questions, but your course server could not be reached, so they "
                "have not arrived yet. Connect and fetch them — you can still answer before you "
                "hand in.")
            self._sub.setStyleSheet(f"color:{warn};font-weight:600;")
            self._fetch.show()
            self._retitle(0, 0)
            return

        if not questions:
            self._render([], {})
            self._sub.setText("This lab does not ask any questions. Nothing to do here.")
            self._retitle(0, 0)
            return

        self._render(questions, answers)
        done = len(questions) - len(lq.unanswered(questions, answers))
        if submitted:
            self._sub.setText("Handed in. These are the answers that went with your work — they "
                              "are in the proof chain and cannot be changed now.")
        else:
            self._sub.setText(
                "Your answers go into the recording with the rest of your work. Nothing here is "
                "marked automatically — your instructor reads them.")
        self._retitle(done, len(questions))

    # -- rendering -------------------------------------------------------------- #
    def _render(self, questions, answers) -> None:
        ids = [q.id for q in questions]
        if ids != [q.id for q in self._questions]:
            # Rebuild only when the SET changes. Rebuilding on every state change would destroy the
            # box a student is typing in, which is the one thing a panel like this must never do.
            for card in self._cards.values():
                card.setParent(None)
                card.deleteLater()
            self._cards.clear()
            for q in questions:
                card = _AnswerCard(q, self.theme)
                card.submitted.connect(self.answered.emit)
                self._stack.insertWidget(self._stack.count() - 1, card)
                self._cards[q.id] = card
            self._questions = list(questions)
        for q in questions:
            self._cards[q.id].set_answer(answers.get(q.id, ""))

    def set_live(self, live: bool) -> None:
        for card in self._cards.values():
            card.set_live(live)

    def _retitle(self, done: int, total: int) -> None:
        t = getattr(self.theme, "theme", None)
        text = getattr(t, "text", "#131a23")
        tail = f"  ·  {done} of {total}" if total else ""
        self._title.setText(f"Ask Questions{tail}")
        self._title.setStyleSheet(f"font-weight:800;font-size:15px;color:{text};")

    def refresh_theme(self, *_a) -> None:
        for card in self._cards.values():
            card.refresh_theme()
        self._retitle(0, 0)


def announce(questions, answers) -> str:
    """The dock tab's title: "Ask Questions (2)" while any are outstanding.

    A beep is gone in a second. A student who came back to the window twenty minutes later needs
    something still on screen, and the tab is the only part of this panel visible when another tab
    is in front of it.
    """
    left = len(lq.unanswered(questions, answers))
    return f"Ask Questions ({left})" if left else "Ask Questions"


def beep() -> None:
    """Once, when the questions arrive — not per question, and never again.

    The only sound gBuilder makes besides the one on the OS HUD. It is here because a student arms
    a code and immediately looks at the canvas, and a tab that quietly grew a number is a tab
    nobody looked at.
    """
    try:
        QApplication.beep()
    except Exception:                                  # noqa: BLE001 — a missing bell is not a bug
        pass
    try:
        QGuiApplication.alert(QApplication.activeWindow(), 0)   # taskbar/dock flash on some platforms
    except Exception:                                  # noqa: BLE001
        pass
