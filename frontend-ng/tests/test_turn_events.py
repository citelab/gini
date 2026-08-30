"""A turn that narrates itself, and an indicator that cannot lie about it.

The property under test throughout is that progress comes from the MODEL, not from a clock. A
spinner on a QTimer keeps spinning over a dead Ollama, which is worse than showing nothing at all
— it is confidently wrong, and it sends a student to their wifi when the model has hung.

No Qt here, and none needed: the vocabulary and the rules that decide what a student sees live in
`agent/turn_events.py`, and only the painting lives in the widget.
"""
from __future__ import annotations

import pytest

from gini.agent import turn_events as te


# ---- the vocabulary ---------------------------------------------------------- #
def test_every_builder_returns_a_kind_and_a_dict():
    """The same (kind, data) shape as `domain/proof_events.py`, so a consumer can dispatch on
    kind alone and never has to know which builder produced a line."""
    for built in (te.phase("x"), te.tick(3), te.say("hi"), te.done({"ok": True}),
                  te.asking_course("comp535"), te.reconsidering(2)):
        kind, data = built
        assert isinstance(kind, str) and isinstance(data, dict)


def test_a_composite_builder_is_already_an_event():
    """`asking_course` and `reconsidering` BUILD a phase — they are not labels to pass to
    `phase()`. Wrapping one again put the repr of a tuple in front of the student, and the
    indicator painted it happily because a label is just a string to it."""
    for built in (te.asking_course("comp535"), te.reconsidering(2)):
        label = built[1]["label"]
        assert not label.startswith("("), f"{label!r} looks like a wrapped event"
        assert "phase" not in label and "{" not in label


def test_done_carries_the_whole_result():
    """The discipline that keeps streaming an overlay instead of a second code path: a caller
    that ignores every other kind still receives exactly what the non-streaming path returns, so
    the two answers cannot drift apart."""
    result = {"ok": True, "text": "a full answer", "objections": []}
    kind, data = te.done(result)
    assert kind == te.DONE and data["result"] is result


def test_naming_the_course_makes_the_pause_actionable():
    """The Teaching Center call is the one step that can be slow for a reason the student can fix
    — the wrong course in Settings. Saying which course is asked is what lets them notice."""
    assert "comp535" in te.asking_course("comp535")[1]["label"]
    assert te.asking_course("")[1]["label"] == te.ASKING_COURSE   # no course, no empty brackets


def test_a_negative_or_junk_count_does_not_poison_the_pulse():
    assert te.tick(-5)[1]["chars"] == 0
    assert te.tick(None)[1]["chars"] == 0


# ---- the indicator ----------------------------------------------------------- #
@pytest.fixture
def clock():
    class Clock:
        t = 1000.0

        def __call__(self):
            return self.t
    return Clock()


def test_the_pulse_moves_only_when_the_model_does(clock):
    """THE test. Time passing must not advance the pulse; tokens must."""
    p = te.Progress(now=clock)
    still = p.pulse
    clock.t += 600                                   # ten minutes of nothing
    assert p.pulse == still, "the pulse advanced without a single token"
    p.feed(te.tick(12))
    assert p.pulse != still


def test_a_stalled_model_shows_a_stalled_indicator(clock):
    """A frozen indicator is information. This is the whole reason the pulse is not on a timer."""
    p = te.Progress(now=clock)
    for _ in range(5):
        p.feed(te.tick(20))
    frozen = p.line()
    clock.t += 30                                    # the model hangs for half a minute
    moved = p.line()
    assert p.pulse == frozen.split()[0], "the pulse kept animating over a hung model"
    assert moved != frozen, "the elapsed seconds must still climb — time really did pass"


def test_the_seconds_do_come_from_the_clock(clock):
    p = te.Progress(now=clock)
    assert p.seconds == 0
    clock.t += 7.6
    assert p.seconds == 7


def test_nothing_yet_is_distinguishable_from_working(clock):
    """The opening pause is exactly when a student most wants to know which of the two it is."""
    p = te.Progress(now=clock)
    assert p.alive is False
    p.feed(te.tick(1))
    assert p.alive is True


def test_a_new_phase_resets_the_volume_but_never_the_prose(clock):
    """Characters are per-phase, so the count means 'this step'. The prose is not cleared: an
    answer a student is reading must not vanish because the turn moved on to auditing it."""
    p = te.Progress(now=clock)
    p.feed(te.say("your router "))
    p.feed(te.tick(11))
    p.feed(te.phase("Checking what it might have missed"))
    assert p.chars == 0
    assert p.said == "your router "
    assert p.phase == "Checking what it might have missed"


def test_prose_accumulates_in_order(clock):
    p = te.Progress(now=clock)
    for word in ("Two ", "LANs ", "need ", "a router."):
        p.feed(te.say(word))
    assert p.said == "Two LANs need a router."
    assert p.beat == 4                               # prose is liveness too


def test_the_line_says_what_is_happening_and_for_how_long(clock):
    p = te.Progress(now=clock)
    p.feed(te.asking_course("comp535"))
    clock.t += 3
    line = p.line()
    assert "Asking your course (comp535)" in line and "3s" in line
    assert "chars" not in line, "no volume until there is some — a zero would read as stalled"
    p.feed(te.tick(240))
    assert "240 chars" in p.line()


def test_an_indicator_never_breaks_a_turn(clock):
    """It is the least important thing on screen. An unknown kind from a newer emitter, or an
    empty line, must be ignored rather than raise into the turn that is answering."""
    p = te.Progress(now=clock)
    for junk in (None, (), ("nonsense", {}), ("phase", {})):
        p.feed(junk)
    assert p.beat == 0 and p.chars == 0


def test_a_token_with_no_count_still_counts_as_alive(clock):
    """Deliberate, and the distinction is the point: a tick carrying no `chars` says the model
    produced SOMETHING we did not measure. The volume cannot move, but the pulse must — treating
    it as noise would freeze the indicator over a model that is running fine."""
    p = te.Progress(now=clock)
    p.feed(("tick", {}))
    assert p.alive is True and p.chars == 0


def test_reset_starts_a_fresh_turn(clock):
    p = te.Progress(now=clock)
    p.feed(te.say("old answer"))
    p.feed(te.tick(9))
    clock.t += 50
    p.reset("Looking through what GINI knows")
    assert (p.said, p.chars, p.beat, p.seconds) == ("", 0, 0, 0)
    assert p.phase == "Looking through what GINI knows"


def test_the_labels_are_written_for_a_student():
    """They are shown verbatim. A label naming a function is a label nobody outside this repo can
    read, and these are the only words a student sees during the wait."""
    for label in (te.LOOKING, te.CATCHING_UP, te.ASKING_COURSE, te.ANSWERING, te.USING_TOOL):
        assert label and label[0].isupper()
        assert "_" not in label and "()" not in label


# ---- what a student is allowed to watch arrive -------------------------------- #
# Native tool calls come back as `chunk.tool_call` and never touch the prose, but a model with no
# native tool support emits its actions as JSON inside the text and `loop.send` parses them out
# afterwards. Streamed raw, that reaches the student as `<tool_call>{"tool": "add_device"…` one
# letter at a time. `loop.visible_text` strips it from the finished reply; ProseFilter is the same
# rule applied one delta at a time, so the two cannot disagree about what is prose.
def _stream(raw, chunk=1):
    f = te.ProseFilter()
    parts = [raw[i:i + chunk] for i in range(0, len(raw), chunk)] or [""]
    return "".join(f.feed(p) for p in parts) + f.flush()


@pytest.mark.parametrize("chunk", [1, 2, 3, 7, 40, 10_000])
def test_tool_markup_never_reaches_the_student_however_it_is_split(chunk):
    """The delta boundary is the whole difficulty: a chunk can end in the middle of `<tool_call>`,
    so a filter that only inspects one delta at a time leaks the tail of the tag."""
    raw = ('Two LANs need a router. <tool_call>{"tool": "add_device", "args": {"t": "router"}}'
           '</tool_call> That is why.')
    out = _stream(raw, chunk)
    for forbidden in ("<tool_call>", "add_device", '{"tool"', "</tool_call>"):
        assert forbidden not in out, f"leaked {forbidden!r} at chunk size {chunk}"
    assert "Two LANs need a router." in out and "That is why." in out


@pytest.mark.parametrize("chunk", [1, 4, 10_000])
def test_a_bare_json_action_is_suppressed_too(chunk):
    """The fallback path emits actions with no tag around them at all."""
    raw = 'Adding it now. {"tool": "add_device", "args": {"type_key": "Router"}} Done.'
    out = _stream(raw, chunk)
    assert "add_device" not in out and '"tool"' not in out
    assert "Adding it now." in out and "Done." in out


@pytest.mark.parametrize("chunk", [1, 5, 10_000])
def test_a_code_fence_is_suppressed(chunk):
    raw = "Run this:\n```\nping 10.0.0.2\n```\nand watch the replies."
    out = _stream(raw, chunk)
    assert "ping 10.0.0.2" not in out
    assert "Run this:" in out and "watch the replies." in out


@pytest.mark.parametrize("chunk", [1, 2, 10_000])
def test_ordinary_prose_that_merely_looks_dangerous_survives(chunk):
    """The failure that would be worse than the one this prevents: silently eating an answer
    because a student's question was about subnet masks and comparisons."""
    raw = "Use a mask < /24 when the set {a, b} is small — `netmask` shows it. A < B, and 3 < 4."
    assert _stream(raw, chunk) == raw


def test_text_held_at_the_end_is_still_delivered(chunk=1):
    """An answer ending on a bare bracket must not lose its last characters. Held text that never
    became markup IS prose."""
    assert _stream("The set is {", chunk) == "The set is {"
    assert _stream("Compare with <", chunk) == "Compare with <"


def test_an_unclosed_tool_block_stays_suppressed():
    """A truncated reply — the model cut off mid-call. Flushing the remains would show the student
    exactly the markup this exists to hide."""
    out = _stream('Working on it. <tool_call>{"tool": "add_dev', 1)
    assert "tool" not in out and "add_dev" not in out
    assert "Working on it." in out


@pytest.mark.parametrize("chunk", [1, 3, 9])
def test_the_filter_agrees_with_visible_text_about_what_is_prose(chunk):
    """The invariant that keeps the streamed answer and the final answer from drifting: anything
    `loop.visible_text` removes must never have been shown in the first place."""
    from gini.agent.loop import visible_text
    raw = ('First, check the link. <tool_call>{"tool": "narrate", "args": {"text": "x"}}'
           '</tool_call> Then ping across it.')
    streamed = _stream(raw, chunk)
    final = visible_text(raw)
    for sentence in ("First, check the link.", "Then ping across it."):
        assert sentence in streamed and sentence in final
    assert "tool_call" not in streamed and "tool_call" not in final


def test_nothing_is_emitted_twice_across_deltas():
    """A filter that re-emits its buffer would double every held word — and the duplication would
    only appear on the split, so it would pass every single-chunk test."""
    raw = "A router joins two LANs. " * 8
    assert _stream(raw, 3) == raw


# ---- a reasoning model's private thinking -------------------------------------- #
# `ollama.strip_thinking` removes the chain of thought from a whole reply, but Ollama's `_parse`
# calls it PER CHUNK and a streamed chunk is a fragment: split across two deltas, the block regex
# never matches and the reasoning flows straight through. Before anything streamed that only
# affected the final text; now a student would watch the model think.
@pytest.mark.parametrize("chunk", [1, 3, 8, 10_000])
@pytest.mark.parametrize("tags", [("<think>", "</think>"), ("<|think|>", "<|/think|>")])
def test_a_models_reasoning_is_never_streamed_to_the_student(chunk, tags):
    open_t, close_t = tags
    raw = f"{open_t}they are on different subnets, so say router{close_t}Two LANs need a router."
    out = _stream(raw, chunk)
    assert "different subnets, so say router" not in out
    assert "think" not in out
    assert out.strip() == "Two LANs need a router."


def test_the_filter_and_the_finished_text_agree_about_thinking():
    """Both ends, or the difference gets appended back on. `_on_answer` adds anything the final
    text has that the stream did not show — so a clean stream and a dirty final would put the
    reasoning back at the bottom of the answer."""
    from gini.agent.loop import visible_text
    raw = "<think>private reasoning here</think>The answer."
    assert "private reasoning" not in _stream(raw, 4)
    assert "private reasoning" not in visible_text(raw)


def test_unclosed_thinking_does_not_swallow_the_whole_answer():
    """A model that opens a think block and never closes it. Nothing after it can be trusted as
    prose, but the words before it were already legitimate."""
    out = _stream("Here goes. <think>still reasoning and then the stream ends", 3)
    assert "Here goes." in out and "still reasoning" not in out
