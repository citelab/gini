"""The answer appears as it is written, and the indicator tells the truth about the wait.

Before this, a student asked a question and watched one unchanging line — "GINI is thinking" —
while up to three model calls and a network round-trip to their course server ran behind it. The
streaming machinery was all there: `answer_chunk` was declared, connected to a complete live-typing
handler, and emitted from nowhere. The backend streamed, `loop.send` forwarded every delta, and the
assistant appended them to a list nobody ever read.

Driven through the real `Assistant` against a fake backend, because the bug was precisely that two
correct halves were not joined: testing either half alone would have passed throughout.
"""
from __future__ import annotations

import time

import pytest

pytest.importorskip("PySide6")

from gini.agent import turn_events as te                      # noqa: E402
from gini.agent.api import GiniAPI                            # noqa: E402
from gini.app import AppContext                               # noqa: E402
from gini.ui.assistant import Assistant                       # noqa: E402
from gini.ui.theme import ThemeManager                        # noqa: E402


class FakeLoop:
    """Stands in for `AgentLoop`, streaming the way the real one does: `send` forwards each delta
    to `on_text` and returns the whole text."""

    extra_context = ""

    def __init__(self, deltas) -> None:
        self.deltas = list(deltas)
        self.saw_context = None

    def send(self, text, on_text=None):
        self.saw_context = self.extra_context
        for d in self.deltas:
            if on_text:
                on_text(d)
        return "".join(self.deltas)


@pytest.fixture
def chat(qtbot):
    from PySide6.QtWidgets import QApplication
    ctx = AppContext()
    a = Assistant(ctx, GiniAPI(ctx), ThemeManager(QApplication.instance(), "Dark"))
    qtbot.addWidget(a)
    return a


def _run(chat, qtbot, deltas, prompt="why can't my hosts talk?"):
    """One full turn, with the queued signals actually delivered."""
    chat._loop = FakeLoop(deltas)
    with qtbot.waitSignal(chat.answer_ready, timeout=5000):
        chat._ask_async(prompt, "")
    for _ in range(50):                       # drain the queued turn_event deliveries
        qtbot.wait(5)
        if not chat._busy:
            break
    return chat


# ---- the answer arrives while it is being written ----------------------------- #
def test_the_student_sees_the_answer_as_it_is_written(chat, qtbot):
    """The wire that was missing. Every delta the loop forwards must reach the pane."""
    _run(chat, qtbot, ["Two LANs ", "need ", "a router ", "between them."])
    assert "Two LANs need a router between them." in chat.log.toPlainText()


def test_the_spinner_gives_way_to_the_answer(chat, qtbot):
    """It used to sit there until the whole turn finished, then the answer appeared at once."""
    _run(chat, qtbot, ["Because ", "they are on ", "different subnets."])
    assert chat._spinner.isVisible() is False
    assert chat._busy is False


def test_the_finished_answer_is_kept_exactly_once(chat, qtbot):
    """The streamed text is also the persisted text. A turn that streamed and one that did not
    must end in the same place, or the transcript and the screen disagree."""
    before = len(chat._messages)               # the panel greets on construction
    _run(chat, qtbot, ["A ", "router ", "joins them."])
    said = [m[1] for m in chat._messages[before:] if m[0] == "GINI"]
    assert said == ["A router joins them."], "streamed and persisted must be the same once"


def test_tool_markup_is_never_shown_even_though_it_streams(chat, qtbot):
    """A model with no native tool support puts its actions in the prose. The student watches the
    prose; `loop.send` still gets every raw delta and parses the actions out of it."""
    _run(chat, qtbot, ['I will add one. <tool_call>{"tool": ', '"add_device", "args": {}}',
                       "</tool_call> There it is."])
    shown = chat.log.toPlainText()
    assert "add_device" not in shown and "tool_call" not in shown
    assert "I will add one." in shown and "There it is." in shown


# ---- the indicator ------------------------------------------------------------ #
def test_the_wait_says_which_step_it_is_on(chat, qtbot):
    """One label covering retrieval, a summariser, a course-server call and the model meant a slow
    course server and a slow model looked identical from the outside."""
    seen = []
    chat.turn_event.connect(lambda e: seen.append(e))
    _run(chat, qtbot, ["ok"])
    labels = [d["label"] for k, d in seen if k == te.PHASE]
    assert te.ANSWERING in labels


def test_the_pulse_is_moved_by_tokens_not_by_the_clock(chat, qtbot):
    """The property worth protecting. A QTimer-driven spinner keeps dancing over a hung model and
    sends a student to check their wifi; this one stalls when the model stalls."""
    p = chat._progress
    p.reset("Answering")
    before = p.pulse
    time.sleep(0.05)
    chat._spin_tick()                                  # the timer fires with no tokens
    assert p.pulse == before
    chat._on_turn_event(te.tick(12))
    assert p.pulse != before


def test_every_delta_counts_as_liveness_even_when_it_is_hidden(chat, qtbot):
    """A model grinding through a long tool call is working. If only visible prose moved the
    pulse, the indicator would freeze exactly when the model was busiest — and a frozen pulse is
    supposed to mean something."""
    seen = []
    chat.turn_event.connect(lambda e: seen.append(e))
    _run(chat, qtbot, ['<tool_call>{"tool": "x", "args": {}}</tool_call>', "Done."])
    ticks = [d for k, d in seen if k == te.TICK]
    assert len(ticks) == 2, "a suppressed delta still has to register as alive"


def test_an_indicator_failure_never_costs_the_answer(chat, qtbot):
    """It is the least important thing on screen and must behave like it: an exception raised
    while painting progress lands in the Qt event loop, and a student who loses their answer
    because the PROGRESS LINE misbehaved is worse off than one who never had a progress line."""
    class Boom:                                    # __slots__ makes Progress unpatchable
        def feed(self, _event):
            raise RuntimeError("the indicator fell over")

        def reset(self, *_a):
            pass

        def line(self):
            raise RuntimeError("the indicator fell over")
    chat._progress = Boom()
    _run(chat, qtbot, ["Still ", "answered."])
    assert "Still answered." in chat.log.toPlainText()
    assert chat._busy is False


# ---- streaming stays an overlay ----------------------------------------------- #
def test_a_loop_that_cannot_stream_still_answers(chat, qtbot):
    """Older loop signature, no `on_text`. The turn falls back to the buffered path rather than
    failing — streaming is an overlay on the same route, never a fork of it."""
    class OldLoop:
        extra_context = ""

        def send(self, text):                          # no on_text parameter
            return "The buffered answer."
    chat._loop = OldLoop()
    with qtbot.waitSignal(chat.answer_ready, timeout=5000):
        chat._ask_async("why?", "")
    qtbot.wait(50)
    assert "The buffered answer." in chat.log.toPlainText()


def test_a_model_that_fails_mid_turn_says_so_rather_than_hanging(chat, qtbot):
    class Broken:
        extra_context = ""

        def send(self, text, on_text=None):
            on_text("Starting")
            raise RuntimeError("connection reset")
    chat._loop = Broken()
    with qtbot.waitSignal(chat.answer_ready, timeout=5000):
        chat._ask_async("why?", "")
    qtbot.wait(50)
    assert chat._busy is False
    assert "LLM error" in chat.log.toPlainText()


def test_a_streamed_answer_ends_up_formatted_like_a_buffered_one(chat, qtbot):
    """Streaming has to insert plain characters — `**bold` is not bold until the second `**`
    arrives — so a streamed answer would show a student the asterisks that the non-streaming path
    renders away. It settles into Markdown once, at the end, with the same words."""
    before = len(chat._messages)
    _run(chat, qtbot, ["A router **joins** ", "two LANs."])
    role, text, err, markdown = chat._messages[before:][-1]
    assert (role, err, markdown) == ("GINI", False, True)
    assert text == "A router **joins** two LANs."
    assert "**" not in chat.log.toPlainText().split("GINI:")[-1]


def test_the_settle_does_not_change_the_words(chat, qtbot):
    """The one thing streaming must never do is retract what a student already read. Formatting
    may resolve at the end; the text may not be rewritten."""
    seen = []
    chat.turn_event.connect(lambda e: seen.append(e))
    _run(chat, qtbot, ["Two ", "LANs ", "need ", "a router."])
    streamed = "".join(d["text"] for k, d in seen if k == te.SAY)
    assert chat._messages[-1][1] == streamed
