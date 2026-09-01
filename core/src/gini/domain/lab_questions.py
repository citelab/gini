"""The lab's questions, and what the student has said to them so far.

The Teaching Center hands gBuilder a set of prompts when a code is armed; the student writes
answers; the answers go into the proof chain as `answer` entries and are marked by a person reading
the transcript. This module is all of the thinking in that, kept away from Qt so it can be tested
without a widget or an event loop — the same shape as `proof_events` and `turn_events`.

**The chain is the state.** There is no separate record of what has been answered: `answers_in`
reads it back out of the entries. That is not a shortcut, it is the only version that can be right.
The chain is what gets submitted and what gets marked, so anything held beside it is a second copy
that can disagree with the one that counts — and it survives a restart, a resumed code and a
crash for free, because it is the same file the recorder was already persisting.

**Answering again appends.** A student may think better of an answer, and the chain is append-only,
so the last one is the answer and the earlier passes stay visible to a marker. Nothing here edits.

**Nothing here marks.** There is no key on this side of the wire — the Teaching Center strips it
from the arm reply — so gBuilder could not score an answer if it wanted to, and no code here should
ever start trying.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import proof_events as ev

#: What a student can type into one answer box. Generous for a sentence or two, and the chain
#: clips at 2000 anyway (`proof_events.answer`) — this is so the UI can say so BEFORE they lose
#: the end of a paragraph, rather than after.
MAX_ANSWER = 2000


@dataclass(frozen=True)
class Question:
    """One prompt, as it arrived from the course server.

    No `answer` field, deliberately. The key never crosses the wire — the Teaching Center strips it
    in the handler — and a field for it here would be an invitation to start carrying one.
    """
    id: str
    prompt: str


def questions_from(reply: dict | None) -> list[Question]:
    """The questions in an arm reply (`/api/activity?code=…`), if it carried any.

    Tolerant on purpose: this parses a reply from a server that may be older than this gBuilder,
    and a missing or malformed `questions` key means "no questions", never an exception. A student
    mid-lab must not meet a traceback because their course is a version behind.
    """
    out: list[Question] = []
    for q in (reply or {}).get("questions") or []:
        if not isinstance(q, dict):
            continue
        qid, prompt = str(q.get("id") or ""), str(q.get("prompt") or "").strip()
        if qid and prompt:
            out.append(Question(qid, prompt))
    return out


def answers_in(entries) -> dict[str, str]:
    """What the student has answered so far, by question id — the LAST pass of each.

    Accepts `Entry` objects or the plain dicts a loaded chain yields, because this is called both
    on a live chain and on one read back from disk.
    """
    said: dict[str, str] = {}
    for e in entries or []:
        kind = e.get("kind") if isinstance(e, dict) else getattr(e, "kind", "")
        if kind != ev.ANSWER:
            continue
        data = (e.get("data") if isinstance(e, dict) else getattr(e, "data", None)) or {}
        qid = str(data.get("id") or "")
        if qid:
            said[qid] = str(data.get("text") or "")
    return said


def unanswered(questions, answers: dict[str, str]) -> list[Question]:
    """The ones with nothing against them at all.

    An empty string counts as ANSWERED. A student who deliberately submitted a blank has said
    something — that they had nothing — and nagging them about it at hand-in would be treating
    their decision as an oversight.
    """
    return [q for q in questions if q.id not in answers]


def summary(questions, answers: dict[str, str]) -> str:
    """One line for the tab: "Ask Questions · 1 of 2". Empty when there is nothing to say.

    A beep is gone in a second; this is not, and it is what a student sees when they come back to
    the window twenty minutes later.
    """
    total = len(questions)
    if not total:
        return ""
    done = total - len(unanswered(questions, answers))
    return "all answered" if done == total else f"{done} of {total}"


def nudge(questions, answers: dict[str, str]) -> str:
    """What to say at hand-in, or "" when there is nothing to say.

    A WARNING and never a refusal — that was settled in the design: a student who ran out of time
    still hands in the work they did, and an unanswered question is a fact about the attempt that
    the marker gets to see. This exists so nobody submits having simply never noticed the tab.
    """
    left = unanswered(questions, answers)
    if not left:
        return ""
    if len(left) == 1:
        return f"One question is still unanswered: “{left[0].prompt}”"
    return (f"{len(left)} questions are still unanswered: "
            + "; ".join(f"“{q.prompt}”" for q in left))


def missing_because_offline(ticket_says_questions: bool, questions) -> bool:
    """The code says this lab asks questions and we have none in hand.

    The case the bit in the code exists for: gBuilder arms offline when the course server cannot be
    reached, and without this a student works the whole lab, hands in, and nobody finds out until a
    marker sees blanks caused by hotel wifi. It is also true after a restart while armed — the arm
    reply is not persisted, so the questions have to be fetched again — and the answer is the same
    either way: say so, and offer to fetch.
    """
    return bool(ticket_says_questions) and not questions
