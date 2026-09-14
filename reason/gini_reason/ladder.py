"""The ladder: what GINI AI knows, what it is willing to say, and who has to look first.

Four rungs, tried in order (docs/design/gini-ai-three-doors.md §2). The model is the third, never
the first, and it composes only from what the rungs above it found.

    L0  the teacher's answer bank      decided by the Teaching Center, before this is called
    L1  retrieval + a confidence       here: the manual, concepts, recipes, the course's own hits
    L2  the model, grounded            here: composed from L1, then AUDITED before it counts
    L3  a person                       here: the honest refusal, with what IS known attached

**L2 is not gated on a threshold.** `strength == strong` says the knowledge base covered the
question; it says nothing about whether the model used what it was handed. That hole is named in
this tree by `agent/twin/course.py`, on the tutor pasting course material into a prompt: *"Nothing
then checks whether the model used it… The material was DELIVERED and nobody was ANSWERABLE for
it."* So an L2 answer is audited — concerns enumerated from what L1 retrieved, coverage reported,
diffed exactly, silent misses objected to — and what survives travels with the answer as flags.

**Nothing here posts anything.** It returns a draft and its flags. Who may see it, and whether it
goes out, is the Teaching Center's decision and depends on which door asked.

Pure except the injected `llm`, so every rung below L2 is testable with no model at all — which is
also the outage plan: with the GPU off, L1 still answers and L3 still refuses honestly.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from gini.domain.similarity import query_terms

from . import manual

#: Weighted coverage at or above this counts as solid grounding. Matches `agent/recall.py`'s own
#: thresholds rather than inventing a second scale for the same judgement.
STRONG = 0.45
THIN = 0.15


@dataclass
class Grounding:
    """What L1 found, and how much of the question it actually covers."""
    question: str
    pages: list = field(default_factory=list)        # [(manual.Page, score)]
    concepts: list = field(default_factory=list)
    recipes: list = field(default_factory=list)
    course: list = field(default_factory=list)       # hits handed in by the Center
    strength: str = "empty"                          # strong | thin | empty

    def citations(self) -> list:
        out = [f"{p.name} — {p.title}" for p, _ in self.pages]
        out += [f"course: {h.get('title', '')}" for h in self.course if h.get("title")]
        return out

    def limits(self) -> list:
        """Every "must not be claimed" the retrieved pages carry, with the page that says so."""
        return [(p.name, line) for p, _ in self.pages for line in manual.limits(p)]


@dataclass
class Draft:
    """An answer that has not been said to anybody yet."""
    text: str = ""
    rung: str = "L3"                                 # L1 | L2 | L3
    strength: str = "empty"
    citations: list = field(default_factory=list)
    flags: list = field(default_factory=list)        # what the audit objected to and kept
    used_model: bool = False


def retrieve(question: str, *, course_hits=(), root: str = "") -> Grounding:
    """L1. Deterministic, offline, and the reason the GPU is optional rather than load-bearing."""
    g = Grounding(question=question, course=list(course_hits or ()))
    g.pages = manual.find(question, limit=3, root=root)

    try:                                             # concepts and recipes, via the existing recall
        from gini.agent import recall as _recall
        r = _recall.recall(question)
        g.concepts = list(getattr(r, "concepts", []) or [])
        g.recipes = list(getattr(r, "recipes", []) or [])
        kb_strength = getattr(r, "strength", "empty")
    except Exception:                                # noqa: BLE001 — a thin KB is not an outage
        kb_strength = "empty"

    best_page = max((s for _p, s in g.pages), default=0.0)
    page_strength = "strong" if best_page >= STRONG else "thin" if best_page >= THIN else "empty"
    # The course's own material outranks GINI's general knowledge: it is the thing the student is
    # actually being marked on, which is `twin/course.py`'s whole argument.
    course_strength = "strong" if g.course else "empty"
    order = {"empty": 0, "thin": 1, "strong": 2}
    g.strength = max((page_strength, kb_strength, course_strength), key=lambda s: order.get(s, 0))
    return g


def context(g: Grounding) -> str:
    """Everything the model is allowed to answer from, as text. Nothing else goes in the prompt."""
    parts = []
    for p, _score in g.pages:
        body = p.section("What it is doing") or p.section("What is on the screen")
        parts.append(f"### {p.title}  ({p.name})\n{body[:1400]}")
        lim = manual.limits(p)
        if lim:
            parts.append("Limits this page states:\n" + "\n".join(f"- {x}" for x in lim))
    for c in g.concepts[:3]:
        parts.append(f"### concept: {getattr(c, 'key', '')}\n{getattr(c, 'note', '')[:800]}")
    for h in g.course[:3]:
        parts.append(f"### this course: {h.get('title', '')}\n{h.get('brief', '')[:600]}")
    return "\n\n".join(parts)


def answer(question: str, *, llm=None, course_hits=(), root: str = "", audit=None) -> Draft:
    """The whole ladder. Returns a draft; says nothing to anybody.

    `audit` is injected so the Twin can be wired in without this module importing it — and so the
    ladder stays testable with a scripted auditor, which is what `twin/harness.py` exists to do.
    """
    g = retrieve(question, course_hits=course_hits, root=root)
    d = Draft(strength=g.strength, citations=g.citations())

    if g.strength == "empty":
        d.rung, d.text = "L3", _refusal(g)
        return d

    if llm is None or g.strength != "strong":
        # L1 on its own: name what the course has on this rather than composing around it. This is
        # also exactly what happens with the GPU off, which is why it is a rung and not a fallback.
        d.rung, d.text = "L1", _pointer(g)
        return d

    try:
        d.text = _compose(question, g, llm)
        d.rung, d.used_model = "L2", True
    except Exception:                                # noqa: BLE001 — a model failure drops a rung
        d.rung, d.text = "L1", _pointer(g)
        return d

    if audit is not None:
        d.flags = list(audit(question, g, d.text) or [])
    return d


def _refusal(g: Grounding) -> str:
    """L3. What is known, and a human — never a hedge.

    A bot that says "I'm not sure, ask a TA" and nothing else is noise on a busy server, and it
    teaches people to ignore it, which costs the answers that were good.
    """
    return ("I don't have anything in the course material on that. "
            "Someone on the teaching team will need to pick this up.")


def _pointer(g: Grounding) -> str:
    cites = g.citations()
    where = "; ".join(cites[:2]) if cites else "the course material"
    return (f"The course has material on this: {where}. "
            f"I'm not confident enough to answer it directly.")


def _compose(question: str, g: Grounding, llm) -> str:
    from gini.agent.llm.backend import Message

    system = ("You are GINI AI, answering a student of this course. Answer ONLY from the material "
              "below. If it does not settle the question, say so plainly. Never contradict a "
              "stated limit. Be brief: a few sentences. Name the page you used.")
    msgs = [Message(role="system", content=system),
            Message(role="user", content=f"{context(g)}\n\nQuestion: {question}")]
    out = []
    for chunk in llm.chat(msgs):
        out.append(getattr(chunk, "text", "") or "")
    return "".join(out).strip()
