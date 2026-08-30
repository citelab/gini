"""The course concern source — what the student's OWN course says about what they just asked.

The other five sources read GINI's symbolic substrate: unmet objectives, legality flags, watcher
events, authoring gaps, the learner model. This one reads the Teaching Center, and it exists to
close a gap that is not a bug in any single line of code.

gBuilder already asks the course server what it holds on a question and pastes the answer into the
prompt as a few lines of context. Nothing then checks whether the model used it. A student asks
about connecting two LANs, their course has a released activity called "Multi-LAN routing" that is
the very lab they are recording, and the tutor may answer out of general knowledge and never
mention it — with no trace anywhere that it did. The material was DELIVERED and nobody was
ANSWERABLE for it.

As a concern it becomes auditable: the model must account for it in its coverage report, and a
silent miss draws an objection. That is the whole difference between telling a tutor something and
being able to tell whether it listened.

**Salience is a rule, never a model** (twin/salience.py). The lab the student is recording is the
one they are being marked on, so a strong hit on it must be addressed; anything else in the course
is context the Twin tracks and never objects about. A course with nothing relevant produces no
concerns at all, and a course server that is unreachable produces no hits and therefore no
concerns — a network failure must never become an accusation that the model ignored the course.
"""
from __future__ import annotations

from .contracts import Concern
from .salience import cap

#: Below this, a hit is not evidence of anything. `search.rank` scores 3.0 for a query word in a
#: title and 1.0 for one in a brief, so this is "one title word, or three words of the brief".
#: A RULE, tuned here when it proves noisy — never a judgement made by a model.
MIN_SCORE = 3.0


def course_concerns(hits, current_lab: str = "") -> list[Concern]:
    """What this course says that the answer should not silently skip.

    `hits` are `services.tc_ask.Answer.hits` — the ranked activities and materials the Teaching
    Center returned. `current_lab` is the activity the student is recording (``course/lab``), which
    gBuilder learns when they arm a code; empty when they are not recording, in which case nothing
    here is must-address, because nothing here is what they are being marked on.
    """
    concerns: list[Concern] = []
    for h in hits or []:
        if not isinstance(h, dict):
            continue
        try:
            score = float(h.get("score") or 0)
        except (TypeError, ValueError):
            continue                         # a network response, not a promise about types
        title = str(h.get("title") or "").strip()
        if score < MIN_SCORE or not title:
            continue                         # no evidence, no concern
        kind = str(h.get("kind") or "")
        hid = str(h.get("id") or title)
        theirs = bool(current_lab) and kind == "activity" and hid == current_lab
        if kind == "activity":
            brief = str(h.get("brief") or "").strip()
            statement = (f"this course's activity “{title}” is about exactly this"
                         + (f" — {brief}" if brief else ""))
        elif kind == "reference":
            # A book is never must-address, whatever it scores. It is not what the student is
            # being marked on, and an answer is not wrong for having explained something in its
            # own words instead of the book's. Salience 1: the Twin tracks it and never objects.
            statement = f"the course's library covers this in {title}"
        else:
            statement = f"this course posts “{title}” on this"
        concerns.append(Concern(
            id=f"course:{hid}",
            kind="course",
            statement=("the lab you are recording, " + statement) if theirs else statement,
            # Every other source cites a deterministic ground fact; this one cites the match that
            # produced the hit, which is exactly what the server computed.
            evidence=f"{kind} in {current_lab.split('/')[0] or 'this course'}, match score {score:g}",
            # 2 = must be accounted for. Only for the lab they are being marked on: a tutor that
            # had to name every handout that shares a word with the question would be a nag, and
            # salience 1 is precisely the "track it, never object" tier.
            salience=2 if theirs else 1,
            source="teaching-center"))
    return cap(concerns)


def current_lab_of(recorder) -> str:
    """The activity the student is recording, or "" — duck-typed, and never raises.

    Read from the proof recorder rather than from Settings, because Settings holds the COURSE and
    this needs the LAB. gBuilder learns it from the Teaching Center when a code is armed.
    """
    try:
        return str((recorder.status() or {}).get("activity") or "")
    except Exception:                        # noqa: BLE001 — no recorder, or an older one
        return ""


__all__ = ["course_concerns", "current_lab_of", "MIN_SCORE"]
