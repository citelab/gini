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


def test_the_wait_becomes_the_answer_in_the_same_place(chat, qtbot):
    """The live line is written INTO the conversation, so the answer replaces it under the same
    "GINI:" rather than appearing in a different part of the panel. Nothing of the wait survives
    once the answer is there."""
    _run(chat, qtbot, ["Because ", "they are on ", "different subnets."])
    shown = chat.log.toPlainText()
    assert "Because they are on different subnets." in shown
    assert "GINI is thinking" not in shown and "Answering" not in shown
    assert chat._live_anchor is None
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


# ---- one turn at a time -------------------------------------------------------- #
# gBuilder used to accept every request the instant it arrived. `_send` checked nothing, the input
# was never disabled, and clicking a device while an answer streamed started a SECOND worker.
# Both then mutated one AgentLoop — an unlocked history list and a single `extra_context` slot
# that each cleared in its own `finally` — so two turns could interleave into one transcript, and
# one could answer with the other's grounding.
class SlowLoop:
    """A turn that does not finish until it is released."""

    extra_context = ""

    def __init__(self) -> None:
        import threading
        self.gate = threading.Event()
        self.prompts = []

    def send(self, text, on_text=None):
        self.prompts.append(text)
        self.gate.wait(5)
        return f"answered {text}"


def test_a_second_question_waits_instead_of_starting_a_second_turn(chat, qtbot):
    slow = SlowLoop()
    chat._loop = slow
    chat._ask_async("first", "")
    qtbot.wait(30)
    assert chat._busy is True
    chat._ask_async("second", "")
    qtbot.wait(30)
    assert slow.prompts == ["first"], "the second question started its own turn"
    assert chat._queued is not None
    slow.gate.set()
    qtbot.waitUntil(lambda: len(slow.prompts) == 2, timeout=5000)
    assert chat._queued is None


def test_the_student_is_told_when_the_queue_is_full(chat, qtbot):
    """The answer to 'how does a student know the limit has been reached'. A queue nobody can see
    is a queue that looks like a hang, and dropping the question silently is worse than either."""
    slow = SlowLoop()
    chat._loop = slow
    chat._ask_async("first", "")
    qtbot.wait(30)
    chat._ask_async("second", "")
    before = len(chat._messages)
    chat._ask_async("third", "")
    said = [m[1] for m in chat._messages[before:]]
    assert any("already waiting" in t for t in said), said
    assert chat._queued[1] != "third"        # the one that was waiting is untouched
    slow.gate.set()
    qtbot.waitUntil(lambda: not chat._busy, timeout=5000)


def test_what_is_waiting_is_shown_while_it_waits(chat, qtbot):
    slow = SlowLoop()
    chat._loop = slow
    chat._ask_async("first", "")
    qtbot.wait(30)
    chat._ask_async("explain this", "R1")
    assert "next: R1" in chat.log.toPlainText()
    slow.gate.set()
    qtbot.waitUntil(lambda: not chat._busy, timeout=5000)


def test_the_indicator_stays_up_while_anything_is_outstanding(chat, qtbot):
    """The bug the counter fixes. `_busy` used to mean 'the most recent answer has landed', so the
    first of two overlapping turns cleared it — the tutor said it was idle with a request still
    running, then dropped an answer in unannounced."""
    chat._llm_active = 2                                # two engagements in flight
    chat._end_turn()
    assert chat._busy is True, "one finishing must not declare the tutor idle"
    chat._end_turn()
    assert chat._busy is False


def test_a_queued_question_runs_against_the_canvas_as_it_is_then(chat, qtbot):
    """Deferred as a CALLABLE, not a captured prompt: a student who asks 'why can't they talk?'
    and adds a router while waiting should get an answer about the canvas with the router in it."""
    seen = []
    chat._ask_gini = lambda text: seen.append((text, len(chat.ctx.topology.devices)))
    slow = SlowLoop()
    chat._loop = slow
    chat._ask_async("first", "")
    qtbot.wait(30)
    assert chat._defer(lambda: chat._ask_gini("why?"), "why?") is True
    chat.ctx.topology.add_device("router")              # built while waiting
    slow.gate.set()
    qtbot.waitUntil(lambda: bool(seen), timeout=5000)
    assert seen[0][1] == 1, "the queued turn ran against the older canvas"


def test_a_nudge_nobody_asked_for_is_dropped_rather_than_queued(chat, qtbot):
    """A queued nudge would be shown minutes after the kernel event it is about — a tutor
    commenting on the past reads as a tutor that is confused."""
    slow = SlowLoop()
    chat._loop = slow
    chat._ask_async("first", "")
    qtbot.wait(30)
    chat._ask_async("a nudge about a scheduling event", "", proactive=True)
    assert chat._queued is None, "a proactive turn should yield, not take the slot"
    slow.gate.set()
    qtbot.waitUntil(lambda: not chat._busy, timeout=5000)


def test_a_queued_request_that_fails_does_not_block_the_next_one(chat, qtbot):
    """Cleared before it runs. A stuck entry would refuse every question after it, for ever."""
    slow = SlowLoop()
    chat._loop = slow
    chat._ask_async("first", "")
    qtbot.wait(30)
    def boom():
        raise RuntimeError("that went wrong")
    chat._defer(boom, "a doomed question")
    slow.gate.set()
    qtbot.waitUntil(lambda: not chat._busy, timeout=5000)
    assert chat._queued is None
    assert any("that went wrong" in m[1] for m in chat._messages)


def test_prose_replaces_the_live_line_without_ending_the_turn(chat, qtbot):
    """The model may still be working through tool round-trips after its first words. Treating
    the first token as the end of the turn would let a queued question start alongside it."""
    chat._llm_active = 1
    chat._paint_progress(reset_to="Answering")
    assert "Answering" in chat.log.toPlainText()
    chat._on_chunk("Two LANs ")
    shown = chat.log.toPlainText()
    assert "Two LANs" in shown and "Answering" not in shown
    assert shown.count("GINI:") == chat.log.toPlainText().count("GINI:")   # no second label
    assert chat._busy is True
    chat._streaming = False


def test_a_long_question_does_not_flood_the_indicator(chat, qtbot):
    """It shares one line with the phase and the elapsed time."""
    slow = SlowLoop()
    chat._loop = slow
    chat._ask_async("first", "")
    qtbot.wait(30)
    chat._defer(lambda: None, "why can't the machine on my first LAN reach the one on the second "
                              "LAN when both of them have addresses and the router is running?")
    assert len(chat._queued[1]) <= 40 and chat._queued[1].endswith("…")
    slow.gate.set()
    qtbot.waitUntil(lambda: not chat._busy, timeout=5000)


def _grounded(chat):
    """The tuple `_ask_gini` builds — the grounded path is the only one that consults a course."""
    from gini.agent import kb
    from gini.agent import understand as U
    intent = U.understand("how do I connect two LANs?", canvas_names=[], mode="chat")
    return (intent, kb.retrieve(intent, topology=chat.ctx.topology), "")


# ---- the course, audited ------------------------------------------------------- #
# The course's material was pasted into the prompt and nothing checked that the model used any of
# it. These drive the audit half through the real assistant: the concerns reach the grounding, the
# coverage call runs after the answer, and a silent miss is counted.
class _CoveredLoop(SlowLoop):
    def __init__(self, reply="Two LANs need a router."):
        super().__init__()
        self.gate.set()
        self.reply = reply
        self.grounding = ""

    def send(self, text, on_text=None):
        self.grounding = self.extra_context
        if on_text:
            on_text(self.reply)
        return self.reply


def _with_course(chat, hits, lab="comp535/lab1", coverage=None):
    """Stand in for the Teaching Center and the coverage call."""
    from gini.agent.twin.course import course_concerns
    chat._course_context = lambda q: ("From this course's material:\n- Activity “Multi-LAN”",
                                      course_concerns(hits, current_lab=lab))
    chat._quick_llm = lambda prompt, schema=None: coverage or ""
    return chat


ACTIVITY = {"kind": "activity", "id": "comp535/lab1", "title": "Multi-LAN routing",
            "brief": "Join two LANs with a router.", "score": 6.0}


def test_the_course_reaches_the_model_as_concerns_not_only_as_context(chat, qtbot):
    """Twin-as-context, the cheap half: the concern set rides in the GROUNDING so the substrate's
    guaranteed recall shapes the draft, rather than only auditing it afterwards."""
    loop = _CoveredLoop()
    chat._loop = loop
    _with_course(chat, [ACTIVITY])
    with qtbot.waitSignal(chat.answer_ready, timeout=5000):
        chat._ask_async("how do I connect two LANs?", "", grounded=_grounded(chat))
    qtbot.wait(60)
    assert "Things that matter right now" in loop.grounding
    assert "Multi-LAN routing" in loop.grounding


def test_an_answer_that_ignores_your_own_lab_is_counted(chat, qtbot):
    """The whole point. The model answered without accounting for the lab the student is being
    marked on, and until now nothing anywhere noticed."""
    chat._loop = _CoveredLoop()
    _with_course(chat, [ACTIVITY],
                 coverage='{"text": "x", "coverage": {"addressed": [], "omitted": []}}')
    with qtbot.waitSignal(chat.answer_ready, timeout=5000):
        chat._ask_async("how do I connect two LANs?", "", grounded=_grounded(chat))
    qtbot.wait(80)
    assert chat._twin.metrics["objections"] == 1
    assert chat._twin.metrics["turns"] == 1


def test_an_answer_that_uses_the_course_draws_no_objection(chat, qtbot):
    chat._loop = _CoveredLoop()
    _with_course(chat, [ACTIVITY],
                 coverage='{"text": "x", "coverage": '
                          '{"addressed": ["course:comp535/lab1"], "omitted": []}}')
    with qtbot.waitSignal(chat.answer_ready, timeout=5000):
        chat._ask_async("how do I connect two LANs?", "", grounded=_grounded(chat))
    qtbot.wait(80)
    assert chat._twin.metrics["objections"] == 0


def test_a_model_that_cannot_report_coverage_is_a_posture_not_a_failure(chat, qtbot):
    """Coverage-silent: a model without structured outputs, or one that ignored the shape. The
    Twin then objects only about the urgent tier rather than guessing what the prose covered —
    and a course concern is salience 2, so it stays quiet."""
    chat._loop = _CoveredLoop()
    _with_course(chat, [ACTIVITY], coverage="I am afraid I cannot do that")
    with qtbot.waitSignal(chat.answer_ready, timeout=5000):
        chat._ask_async("how do I connect two LANs?", "", grounded=_grounded(chat))
    qtbot.wait(80)
    assert chat._twin.metrics["coverage_silent"] == 1
    assert chat._twin.metrics["objections"] == 0


def test_nothing_obligatory_means_no_second_call_at_all(chat, qtbot):
    """A student who is not recording owes nothing, so the extra round-trip must not happen. An
    audit that always runs is a latency cost on every question for no obligation."""
    calls = []
    chat._loop = _CoveredLoop()
    _with_course(chat, [ACTIVITY], lab="")           # not recording
    chat._quick_llm = lambda prompt, schema=None: calls.append(1) or ""
    with qtbot.waitSignal(chat.answer_ready, timeout=5000):
        chat._ask_async("how do I connect two LANs?", "", grounded=_grounded(chat))
    qtbot.wait(80)
    assert calls == []


def test_the_audit_never_costs_the_student_their_answer(chat, qtbot):
    """It runs after the reply is already on screen. A coverage call that throws must not take the
    turn down with it."""
    chat._loop = _CoveredLoop()
    _with_course(chat, [ACTIVITY])
    def boom(prompt, schema=None):
        raise RuntimeError("the audit fell over")
    chat._quick_llm = boom
    with qtbot.waitSignal(chat.answer_ready, timeout=5000):
        chat._ask_async("how do I connect two LANs?", "", grounded=_grounded(chat))
    qtbot.wait(80)
    assert "Two LANs need a router." in chat.log.toPlainText()
    assert chat._busy is False


def test_the_audit_is_a_visible_phase(chat, qtbot):
    """It is a second model call and it takes time; an unexplained pause after the answer has
    finished arriving would read as a hang."""
    seen = []
    chat.turn_event.connect(lambda e: seen.append(e))
    chat._loop = _CoveredLoop()
    _with_course(chat, [ACTIVITY],
                 coverage='{"text": "x", "coverage": {"addressed": [], "omitted": []}}')
    with qtbot.waitSignal(chat.answer_ready, timeout=5000):
        chat._ask_async("how do I connect two LANs?", "", grounded=_grounded(chat))
    qtbot.wait(80)
    assert te.CHECKING in [d["label"] for k, d in seen if k == te.PHASE]


def test_the_audit_reports_and_does_not_act(chat, qtbot):
    """This step deliberately stops short of revising. Nothing about an objection reaches the
    student yet — the point is to learn how often it happens before building on the answer."""
    chat._loop = _CoveredLoop()
    _with_course(chat, [ACTIVITY],
                 coverage='{"text": "x", "coverage": {"addressed": [], "omitted": []}}')
    with qtbot.waitSignal(chat.answer_ready, timeout=5000):
        chat._ask_async("how do I connect two LANs?", "", grounded=_grounded(chat))
    qtbot.wait(80)
    shown = chat.log.toPlainText()
    assert chat._twin.metrics["objections"] == 1
    assert "did not account" not in shown and "Multi-LAN" not in shown
