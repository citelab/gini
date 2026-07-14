"""Mission narration — the LLM writes the story, GINI checks it against the truth.

A mission has two faces. The **objectives** are the truth: they come from verified fragments, they
are what gets graded, and the model never writes them. The **title and description** are the story
told about that truth — and prose is exactly what a small model is good at.

The failure this module exists to prevent: the model narrating a mission that isn't the one being
graded (a "decouple your web tier with a message queue" title sitting on top of VPC-isolation
objectives). Note that title and brief agreeing with *each other* proves nothing — they're written
in one breath, so they always agree. The only agreement worth checking is narrative ⟷ objectives.

So we check it, deterministically:

    the elements the objectives grade  =  the only elements the prose may claim

If the story mentions a queue and no objective grades a queue, the story is false — reject it and
ask again, or fall back to the fragment's own summary. The model gets to be eloquent; it does not
get to be wrong. No LLM is asked to police itself.
"""
from __future__ import annotations

import re

from ..domain import devices as _devices
from ..domain import objectives as _obj
from ..domain import probes as _probes

# words that name an element but are also ordinary English — only count them as a CLAIM about an
# element when the objectives don't already license them (checked below), never as free-text.
_STOP = {"internet", "cloud", "network", "host", "machine", "function", "gateway"}


def graded_types(lesson) -> set[str]:
    """Every element type the lesson's objectives actually grade — the licensed vocabulary."""
    out: set[str] = set()
    for o in lesson.objectives:
        if o.check:
            out.update(_obj.element_types_in_check(o.check))
        if o.probe:
            try:
                p = _probes.parse(o.probe)
            except _probes.ProbeError:
                continue
            out.update(x for x in (p.src, p.dst) if x)
    return {t for t in out if _devices.get(t) is not None}


def _phrases() -> list[tuple[str, str]]:
    """(phrase, type_key) for every element, longest phrase first so 'load balancer' beats 'balancer'."""
    out: list[tuple[str, str]] = []
    for dt in _devices.all_devices():
        out.append((dt.label.lower(), dt.key))
        out.append((dt.key.replace("_", " ").lower(), dt.key))
    return sorted(set(out), key=lambda p: -len(p[0]))


def false_claims(text: str, allowed: set[str]) -> list[str]:
    """Element types the prose names that NO objective grades. These are the lies."""
    low = " " + re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower()) + " "
    bad: list[str] = []
    for phrase, key in _phrases():
        if key in allowed or key in bad:
            continue
        if phrase in _STOP:
            continue                      # too ordinary to read as a claim about an element
        if re.search(rf"\b{re.escape(phrase)}s?\b", low):
            bad.append(key)
    return bad


_PROMPT = (
    "You are writing the brief for a hands-on lab mission a student is about to play.\n\n"
    "The student asked for: {intent!r}\n\n"
    "These are the EXACT tasks they will be graded on — the mission IS these and nothing else:\n"
    "{tasks}\n\n"
    "Write:\n"
    "  title — a SHORT name, at most 6 words, no trailing period. Name the idea, not the steps.\n"
    "  description — 3 to 4 sentences. Say what they're building, WHY an engineer would want it, "
    "and what 'done' looks like. Speak to the student as 'you'. Do not number the tasks or repeat "
    "them one by one — they can already see them.\n\n"
    "HARD RULE: mention ONLY these components: {allowed}. Do not mention any other technology "
    "(no queues, caches, dashboards, load balancers, etc. unless listed). Naming something that "
    "isn't in that list makes the brief WRONG.\n\n"
    'Reply ONLY as JSON: {{"title": "...", "description": "..."}}'
)


def narrate(lesson, intent: str, llm, *, retries: int = 1) -> tuple[str, str]:
    """Ask the model for a short title + a longer description, and REFUSE anything that claims an
    element the objectives don't grade. Returns ("", "") if the model can't tell the truth — the
    caller then falls back to the fragment's own summary, which is always true by construction."""
    from .personas import first_json
    allowed = graded_types(lesson)
    if not allowed or llm is None:
        return "", ""
    labels = sorted((_devices.get(t).label for t in allowed), key=str.lower)
    tasks = "\n".join(f"  - {o.say}" for o in lesson.objectives)
    prompt = _PROMPT.format(intent=intent, tasks=tasks, allowed=", ".join(labels))

    for _ in range(retries + 1):
        try:
            obj = first_json(llm(prompt))
        except Exception:                                    # noqa: BLE001
            return "", ""
        if not isinstance(obj, dict):
            continue
        title = " ".join(str(obj.get("title", "")).split()).strip(" .")
        desc = " ".join(str(obj.get("description", "")).split())
        if not title or not desc:
            continue
        if len(title.split()) > 8:                           # "short" is part of the brief
            continue
        if false_claims(title, allowed) or false_claims(desc, allowed):
            continue                                         # it narrated a mission we aren't grading
        return title, desc
    return "", ""
