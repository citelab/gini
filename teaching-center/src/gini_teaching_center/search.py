"""Finding the course's own material for a student's question.

gBuilder's tutor asks the Teaching Center what this course says about something; this decides what
comes back. The rules live here rather than in `server.py` or `store.py` so they can be tested with
no database and no HTTP — the same reason `activities.py` is a module of its own.

Three ideas carry it.

**Scope is the course, and only the course.** The question arrives with the course the student has
configured in gBuilder. Nothing outside it is searched, which is what stops one class's material
answering another class's question.

**Released only, always.** An activity that is still a draft is the teacher's private working copy;
a brief leaking out of it early would give away an assignment nobody has been set yet. The `status`
column is the gate, and it is checked here rather than trusted from the caller.

**The ranking is deliberately simple; the CONTRACT is what matters.** Today this is term overlap
over titles and briefs. It is not pretending to be more: what a caller depends on is the shape of a
hit and the promise that drafts never appear, and both survive whatever replaces the ranking later.
gBuilder never has to change for that.

What is searchable is bounded by what the server stores: activity titles and briefs are prose;
a material is a title plus a filename or a link. No text is extracted from uploaded PDFs, so
nothing here can search inside one — and pretending otherwise would produce confident silence.
"""
from __future__ import annotations

import re

#: Words too common to say anything about which of a course's handouts is relevant.
_STOP = frozenset("""
a an the and or but if then than that this these those is are was were be been being am
do does did doing have has had having i me my we our you your it its of in on at to for
with from by as about into over after before between under above not no can could should
would will shall may might must how what when where which who whom why
""".split())

MAX_QUERY = 500          # a question, not a document. Anything longer is a mistake or an abuse.
MAX_HITS = 8


def _stem(w: str) -> str:
    """Crudest possible suffix stripping — a PLACEHOLDER, and marked as one.

    Without it "route" fails to match "Routing handout", which makes the tutor look broken over a
    difference no student would notice they had made. Real stemming, or something that replaces
    matching altogether, is the next phase's business; this exists so the plumbing behaves sensibly
    until then, and nothing outside this module depends on it.
    """
    # A trailing "e" is stripped too, or "route" and "routing" reduce to different things and the
    # pair that motivated this still misses. Longest suffix first, so "routes" -> "rout", not "route".
    for suffix in ("ing", "ers", "er", "ed", "es", "s", "e"):
        if len(w) - len(suffix) >= 4 and w.endswith(suffix):
            return w[: -len(suffix)]
    return w


def terms(text: str) -> set[str]:
    """The words worth matching on: lowercased, punctuation dropped, stop-words and noise removed."""
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {_stem(w) for w in words if len(w) > 2 and w not in _STOP}


def _score(q: set[str], *fields: str) -> float:
    """Overlap, with earlier fields counting for more.

    A question matching an activity's TITLE is more likely to be about that activity than one
    matching a word buried in its brief, so the title outweighs the body.
    """
    total = 0.0
    for weight, field in zip((3.0, 1.0, 0.5), fields):
        hit = q & terms(field)
        if hit:
            total += weight * len(hit)
    return total


#: How much of a matched section travels to the tutor. A whole section is around 1,100 words, and
#: several of those would crowd out the canvas, the conversation and the course's own activities in
#: a local model's context — while adding little, because the sentences that answer a question sit
#: near the words that matched it.
PASSAGE_WORDS = 90


def passage(body: str, query: str, *, words: int = PASSAGE_WORDS) -> str:
    """The part of a section worth quoting: a window around the first place the question lands.

    Quoted, never summarised. The server has the real text now, so there is no reason to invent a
    paraphrase — and a paraphrase of a book is exactly the confident wrongness the "never quoted"
    rule was written to prevent, back when the server held only a filename.

    Falls back to the opening of the section when nothing matches, because a section that ranked
    well on its title still has an opening worth reading.
    """
    body = " ".join((body or "").split())
    if not body:
        return ""
    tokens = body.split(" ")
    want = {w[:4] for w in terms(query)}
    start = 0
    if want:
        for i, tok in enumerate(tokens):
            bare = "".join(c for c in tok.lower() if c.isalnum())
            if bare[:4] in want:
                start = max(0, i - words // 3)     # a little of the run-up, mostly what follows
                break
    cut = tokens[start:start + words]
    text = " ".join(cut)
    return ("… " if start else "") + text + (" …" if start + words < len(tokens) else "")


def rank(query: str, activities: list[dict], materials: list[dict], *,
         limit: int = MAX_HITS) -> list[dict]:
    """Rank a course's activities and materials against a question.

    Pure: rows in, hits out. `activities` may contain drafts — they are dropped here, so a caller
    cannot forget to.
    """
    q = terms(query)
    if not q:
        return []
    hits = []
    for a in activities or []:
        if (a.get("status") or "").lower() != "released":
            continue                          # a draft is the teacher's private working copy
        s = _score(q, a.get("title", ""), a.get("brief", ""))
        if s:
            hits.append({"kind": "activity", "id": a.get("id", ""), "score": s,
                         "title": a.get("title", ""), "brief": a.get("brief", ""),
                         "lab": a.get("lab", "")})
    for m in materials or []:
        # A material has no prose — a title, and a filename or a link. Scored on what exists,
        # rather than on a body that was never stored.
        s = _score(q, m.get("title", ""), m.get("filename", ""), m.get("url", ""))
        if s:
            hits.append({"kind": "material", "id": m.get("id", ""), "score": s,
                         "title": m.get("title", ""),
                         "url": m.get("url", "") or f"/m/{m.get('id', '')}",
                         "filename": m.get("filename", "")})
    # Highest first; ties broken by title so the same question always answers the same way. A
    # ranking that reshuffles on equal scores makes a tutor look like it is guessing.
    hits.sort(key=lambda h: (-h["score"], h.get("title", ""), h.get("id", "")))
    return hits[:max(0, limit)]
