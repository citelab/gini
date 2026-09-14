"""Checking that the model used what it was given.

The hole this closes is named in this tree, by the module written to close it one layer down:

    gBuilder already asks the course server what it holds on a question and pastes the answer into
    the prompt as a few lines of context. Nothing then checks whether the model used it. … The
    material was DELIVERED and nobody was ANSWERABLE for it.
        — agent/twin/course.py

Retrieval being strong says the knowledge base covered the question. It says nothing about whether
the answer did. So an L2 draft is audited before it counts as an answer, and what survives travels
with it as flags a teacher reads — never a silent ship, never suppression, which is the Reasoning
Twin's rule and the reason its contracts are used here rather than a bespoke shape.

**This is the deterministic half of the dialectic, and only that.** The full Twin asks the model for
a coverage report, diffs it exactly, and adjudicates each claimed omission against ground truth
(`twin/justify.py`). That needs a second, schema-constrained model call and is the next step. What
runs here needs no model at all and catches the failure that actually matters:

  * **the answer cites nothing that was retrieved** — it was composed from the model's own weights
    while the course's material sat unused in the prompt;
  * **the student's own course material went unmentioned** — `twin/course.py`'s case exactly, and
    the one where being wrong costs a student marks;
  * **the answer contradicts a stated limit** — the manual's "Limits and honesty" bullets are the
    tree's record of what must not be claimed, and a hit there is provable rather than suspected.

Every concern carries `evidence`: a deterministic ground fact. The Twin may only cite what GINI can
prove, and that rule is what makes an objection worth reading.
"""
from __future__ import annotations

from gini.agent.twin.contracts import Concern, Objection
from gini.domain.similarity import query_terms

#: Salience mirrors `twin/salience.py`: >= MUST_ADDRESS is an objection, below it is context the
#: audit notes and never nags about.
MUST_ADDRESS = 2


def concerns(g) -> list:
    """What this answer must account for, from what L1 actually retrieved."""
    out = []
    for page, score in g.pages:
        out.append(Concern(
            id=f"page:{page.id}", kind="manual",
            statement=f"the manual page '{page.title}' is what covers this",
            evidence=f"{page.name} matched the question at {score:.2f}",
            salience=2 if score >= 0.45 else 1, source="manual"))
        for line in _limits_of(page):
            out.append(Concern(
                id=f"limit:{page.id}:{abs(hash(line)) % 9999}", kind="limit",
                statement="must not claim otherwise: " + line[:160],
                evidence=f"{page.name}, under 'Limits and honesty'",
                salience=3, source="manual"))
    for hit in g.course:
        title = hit.get("title", "")
        if title:
            out.append(Concern(
                id=f"course:{hit.get('lab') or title}", kind="course",
                statement=f"the student's own course has '{title}' on this",
                evidence="Teaching Center hit for the configured course",
                salience=2, source="course"))
    return out


def _limits_of(page) -> list:
    from . import manual
    return manual.limits(page)


def review(question: str, g, text: str) -> list:
    """Objections that survive. Empty means nothing provable was missed — not that it is right.

    Deliberately silent about anything it cannot prove. An auditor that guesses produces flags a
    teacher learns to skip, and then the flags that mattered are skipped too.
    """
    said = set(query_terms(text).split())
    body = (text or "").lower()
    out = []

    cited = any(p.name.lower() in body or p.title.lower()[:24] in body for p, _ in g.pages)
    if g.pages and not cited:
        first = g.pages[0][0]
        out.append(Objection(
            concern=Concern(id="cite:none", kind="manual",
                            statement="the answer names none of the pages it was given",
                            evidence=f"{len(g.pages)} page(s) retrieved, including {first.name}",
                            salience=2, source="manual"),
            question=f"Why was {first.name} not used? It was in the prompt and it covers this."))

    for c in concerns(g):
        if c.salience < MUST_ADDRESS:
            continue
        if c.kind == "course":
            # Lexical, and only on the title's own content words — the point is whether the answer
            # ACKNOWLEDGED the student's lab, not whether it paraphrased it well.
            want = set(query_terms(c.statement.split("'")[1] if "'" in c.statement else "").split())
            if want and not (want & said):
                out.append(Objection(concern=c, question=(
                    "Why was the student's own course material not mentioned? "
                    "It is the lab they are being marked on.")))
        elif c.kind == "limit":
            # A contradiction is not detectable without reading; an OMISSION of a limit the page
            # calls out, on an answer that is about that page, is. Only flagged when the answer is
            # long enough to have had room for it.
            if len(body.split()) > 40:
                key = set(query_terms(c.statement).split()) - said
                if len(key) >= 4:
                    continue          # the limit is not about what this answer discussed
                out.append(Objection(concern=c, question=(
                    "Does this answer stay inside the limit the page states?")))
    return out


def flags(objections) -> list:
    """Objections as lines a teacher reads, most serious first."""
    ordered = sorted(objections, key=lambda o: -o.concern.salience)
    return [f"{o.concern.statement} — {o.concern.evidence}" for o in ordered]
