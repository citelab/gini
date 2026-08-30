"""What one answer is doing, while it is doing it.

A student asks a question and a single label — "GINI is thinking" — covers up to three model calls
and a network round-trip to their course server. From outside, a model producing beautifully looks
exactly like a hung one, and on a laptop running Ollama the second is common.

So a turn narrates itself. Four kinds of line, deliberately the same four the Teaching Center's
plan-drafting console used, because the problem is the same one:

    PHASE   a new step began; carries the label a person should read
    TICK    real characters arrived from the model — the LIVENESS signal
    SAY     prose meant for the human, streamed as it is written
    DONE    the turn finished, carrying exactly what the non-streaming path returns

`DONE` carrying the whole result is what keeps streaming an OVERLAY rather than a second code path:
a caller that ignores every other kind still gets the same answer it always got, so there is no
version of gBuilder where the streaming and non-streaming answers can drift apart.

**Progress is driven by TICKs, never by a timer.** A spinner animated by a QTimer keeps spinning
over a dead model, which is worse than no indicator at all because it is confidently wrong. The
pulse here advances per token, so it stalls exactly when generation stalls — and a frozen indicator
is information. Only the elapsed seconds may come from a clock, because seconds really do pass.

Qt-free on purpose, like `domain/proof_events.py`: the vocabulary and the phase labels are decided
here and tested with no widget, no event loop and no model.
"""
from __future__ import annotations

PHASE, TICK, SAY, DONE = "phase", "tick", "say", "done"

#: How the liveness pulse is drawn. Advanced by TICK, never by a clock.
PULSE = "·•●•"

# The labels for gBuilder's own call graph. A grounded turn really does run these steps in this
# order, so the labels are not decoration — each one names a thing that is genuinely happening,
# and a student watching them learns what the tutor actually consults.
LOOKING = "Looking through what GINI knows"
CATCHING_UP = "Catching up on this conversation"
ASKING_COURSE = "Asking your course"           # + " (comp535)" — see `asking_course`
ANSWERING = "Answering"
CHECKING = "Checking what it might have missed"
USING_TOOL = "Looking at your topology"


def phase(label: str) -> tuple[str, dict]:
    """A step began. The label is shown verbatim, so it is written for a student to read."""
    return PHASE, {"label": str(label or "")}


def tick(chars: int) -> tuple[str, dict]:
    """`chars` characters of real model output arrived. The only thing that moves the pulse."""
    return TICK, {"chars": max(0, int(chars or 0))}


def say(text: str) -> tuple[str, dict]:
    """Prose for the student, as it is written."""
    return SAY, {"text": str(text or "")}


def done(result) -> tuple[str, dict]:
    """The turn is over. `result` is what the non-streaming path would have returned."""
    return DONE, {"result": result}


def asking_course(course: str) -> tuple[str, dict]:
    """Naming the course turns an unexplained pause into a visible dependency.

    The Teaching Center call is the one step in the chain that can be slow for a reason the student
    can act on — the wrong course in Settings, or a VPN that dropped. Saying which course is being
    asked is what lets them notice it is the wrong one.
    """
    return phase(f"{ASKING_COURSE} ({course})" if course else ASKING_COURSE)


# What a model may emit into its prose that a student must never watch arrive. `loop.visible_text`
# strips all of it from the finished reply; this is the same rule applied one delta at a time.
# Opener -> the closer that ends it. Written out rather than derived, because `<|think|>` closes
# with `<|/think|>` and no rule that turns "<" into "</" gets that right.
_SUPPRESSED = {
    "<tool_call>": "</tool_call>",
    "<json>": "</json>",
    # A reasoning model's chain of thought. `ollama.strip_thinking` removes it from a whole reply,
    # but it runs per streamed chunk and a chunk is a FRAGMENT — split across deltas, the block
    # regex never matches and the reasoning flows straight through. Suppressing it here works
    # because this filter is stateful across deltas, which is the one thing the per-chunk strip
    # cannot be.
    "<think>": "</think>",
    "<|think|>": "<|/think|>",
    "```": "```",
}
_OPENERS = tuple(_SUPPRESSED)
#: The longest thing we may have to hold before knowing whether it is markup or prose.
_HOLD = max(len(o) for o in _OPENERS)


class ProseFilter:
    """Deltas in, safe prose out — the streaming twin of `loop.visible_text`.

    Native tool calls arrive as `chunk.tool_call` and never touch the text, but a model without
    native tool support emits its actions as JSON inside the prose, and `loop.send` falls back to
    parsing them out. Streamed raw, that reaches the student as `<tool_call>{"tool": "add_device"…`
    appearing letter by letter. The Teaching Center's console solved the same problem by streaming
    only the call whose output was meant for a human; gBuilder has one call that does both, so the
    filtering happens here instead.

    Holds back any tail that could still turn into markup and releases it the moment it cannot.
    A brief pause in the typing is the cost, and it is invisible next to showing JSON.
    """

    __slots__ = ("_buf", "_muted")

    def __init__(self) -> None:
        self._buf = ""        # text held back: it might be the start of an opener
        self._muted = ""      # the closer we are waiting for, or "" when passing text through

    def feed(self, delta: str) -> str:
        """One delta in; whatever is now provably safe to show, out (often "")."""
        self._buf += str(delta or "")
        out = []
        while self._buf:
            if self._muted:
                end = self._buf.find(self._muted)
                if end < 0:
                    # Keep only enough to recognise the closer if it straddles two deltas.
                    self._buf = self._buf[-len(self._muted):]
                    break
                self._buf = self._buf[end + len(self._muted):]
                self._muted = ""
                continue
            cut = min((i for i in (self._buf.find(c) for c in "<`{") if i >= 0), default=-1)
            if cut < 0:
                out.append(self._buf)
                self._buf = ""
                break
            out.append(self._buf[:cut])
            self._buf = self._buf[cut:]
            opener = next((o for o in _OPENERS if self._buf.startswith(o)), "")
            if opener:
                self._muted = _SUPPRESSED[opener]
                self._buf = self._buf[len(opener):]
                continue
            if self._buf.startswith("{") and _maybe_action(self._buf):
                if _action_end(self._buf) < 0:
                    break                       # incomplete — hold and wait for more
                self._buf = self._buf[_action_end(self._buf):]
                continue
            if len(self._buf) <= _HOLD and any(o.startswith(self._buf) for o in _OPENERS):
                break                           # could still become an opener — hold
            out.append(self._buf[0])            # a plain "<" or "`" in ordinary prose
            self._buf = self._buf[1:]
        return "".join(out)

    def flush(self) -> str:
        """Whatever was held when the turn ended. Held text that never became markup IS prose,
        and dropping it would silently truncate the answer at a stray bracket."""
        out = "" if self._muted else self._buf
        self._buf = self._muted = ""
        return out


def _maybe_action(text: str) -> bool:
    """Could this `{` be the start of a bare {"tool": …} action rather than prose?"""
    head = text[:24].replace(" ", "").replace("\n", "")
    return head.startswith('{"tool"') or '{"tool"'.startswith(head)


def _action_end(text: str) -> int:
    """Index just past a complete top-level JSON object, or -1 while it is still arriving."""
    depth, in_str, esc = 0, False, False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    return -1


class Progress:
    """The state a live indicator paints from: which phase, how alive, how long.

    Holds no widgets and imports nothing, so the rules that decide what the student sees are
    testable on their own. A UI feeds it events and reads `line()`.
    """

    __slots__ = ("phase", "chars", "beat", "said", "started", "_now")

    def __init__(self, now=None) -> None:
        import time
        self._now = now or time.monotonic
        self.reset()

    def reset(self, label: str = "") -> None:
        self.phase = label
        self.chars = 0            # characters in the CURRENT phase
        self.beat = 0             # advanced by TICK and SAY only — this is the liveness
        self.said = ""            # streamed prose for this turn
        self.started = self._now()

    def feed(self, event) -> None:
        """Fold one (kind, data) pair in. Unknown kinds are ignored rather than raising: an
        indicator must never be the thing that breaks a turn."""
        if not event:
            return
        kind, data = event
        if kind == PHASE:
            # Characters are per-phase, so "1.2k chars" means this step, not the whole turn.
            # The prose is NOT cleared: a student reading an answer must not have it vanish
            # because the turn moved on to auditing it.
            self.phase, self.chars = str(data.get("label", "")), 0
        elif kind == TICK:
            self.chars += int(data.get("chars", 0) or 0)
            self.beat += 1
        elif kind == SAY:
            self.said += str(data.get("text", ""))
            self.beat += 1

    @property
    def seconds(self) -> int:
        return int(max(0.0, self._now() - self.started))

    @property
    def pulse(self) -> str:
        return PULSE[self.beat % len(PULSE)]

    @property
    def alive(self) -> bool:
        """Has the model produced anything at all yet? False through the whole opening pause,
        which is exactly when a student most wants to know the difference."""
        return self.beat > 0

    def line(self) -> str:
        """One line for the indicator: pulse, phase, elapsed, and volume once there is some.

        The character count is not for the student to read as a number — it is there so that a
        stalled model is visibly stalled. A count that stops climbing says what a spinner cannot.
        """
        bits = [self.pulse, self.phase or "Thinking", f"· {self.seconds}s"]
        if self.chars:
            bits.append(f"· {self.chars} chars")
        return " ".join(bits)


__all__ = ["PHASE", "TICK", "SAY", "DONE", "PULSE", "Progress", "ProseFilter",
           "phase", "tick", "say", "done", "asking_course",
           "LOOKING", "CATCHING_UP", "ASKING_COURSE", "ANSWERING", "CHECKING", "USING_TOOL"]
