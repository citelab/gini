"""Deciding that two people said the same thing.

Small, and load-bearing in two places that must agree: the Discord bot asks "is this the same
problem somebody already reported?", and the Teaching Center asks "is this a question the teacher
has already answered?". If those two used different notions of sameness, a cluster the console
showed as answered would keep being asked, and nobody would be able to see why.

Built on `lexicon.normalize`, the same normaliser `agent.recall` uses to turn a student's phrasing
into GINI vocabulary — so "the same thing" means the same thing here as it does everywhere else in
GINI.

**A hash of the term set was the first design and it does not work.** It was the obvious mechanism:
normalise, sort, digest, compare for equality. Measured on a real pair of reports —

    "gbuilder core dumped on the lab machine, help?"
    "the lab machine core dumped gbuilder"

— the sets differ by the single term `help`, so one incident was filed as two. That is not a
cosmetic fault where this is used: counting how many DIFFERENT people hit something is the entire
purpose, and a rule that splits every incident by whichever filler word somebody typed reports a
quiet server no matter what is happening in it. So sameness is decided by OVERLAP, and callers keep
the terms rather than a digest.

The ranking is deliberately simple and the CONTRACT is what matters, in the same spirit as the
Teaching Center's `search.py`: what a caller depends on is that the same complaint scores high and a
different one does not, and that survives whatever replaces Jaccard later.
"""
from __future__ import annotations

#: Below this many content words there is nothing to compare on, and matching would put unrelated
#: one-liners ("it broke", "same here") together.
MIN_TERMS = 3

#: How alike two messages must be to count as the same report, as `resemblance` measures it.
#: Higher than the old Jaccard threshold because the measure is more generous by construction —
#: and paired with MIN_SHARED, which is what stops a short message joining anything that contains
#: its three words.
SAME = 0.6


def terms(text: str) -> str:
    """The content words of a message, normalised and sorted. "" when there are too few.

    "" is a real answer, not a failure: a message with nothing to match on is still worth recording,
    it simply never joins anything.
    """
    from .lexicon import normalize

    found = sorted(set(normalize(text or "", query=True)))
    return " ".join(found) if len(found) >= MIN_TERMS else ""


def overlap(a: str, b: str) -> float:
    """Jaccard similarity of two `terms` strings.

    0.0 when either is empty — an unmatchable message joins nothing, rather than joining everything,
    which is what an empty set would do under most other measures.
    """
    sa, sb = set((a or "").split()), set((b or "").split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def covers(known: str, message: str) -> float:
    """How much of a KNOWN question appears in a message. Asymmetric, and deliberately so.

    Grouping two reports and matching a message against a banked answer are different questions, and
    using one measure for both gets the second one wrong. `overlap` asks "are these the same
    message?", which is right when neither side is privileged. Answering asks "does this message
    contain the question we already have an answer for?" — and there the extra words a student adds
    are not evidence against a match, they are just the rest of their sentence.

    Measured: against the banked question "gbuilder core dumped on the lab machine", the report
    "gbuilder wont start on the lab box, core dump" scores 0.50 by `overlap` — below any sane
    posting floor — and 0.80 here, because every content word of the known question is present. The
    student said more, not less. Penalising them for it is what left a real answer unposted.

    The cost is that a SHORT known question matches loosely, which is why `MIN_TERMS` applies to
    triggers as much as to anything else: three content words is the floor for being matchable at
    all.
    """
    k, m = set((known or "").split()), set((message or "").split())
    if not k or not m:
        return 0.0
    return len(k & m) / len(k)


#: Two messages must share at least this many content words before their proportion means anything.
#:
#: Two, not three, and the reason is `MIN_TERMS` above. Nothing shorter than three content words
#: ever gets a `terms` string at all, so both sides of any comparison already carry at least three —
#: and demanding three SHARED then means demanding that the shorter message match entirely. Real
#: questions do not: "Where do we send the receipt code?" and "sorry, where does the receipt code
#: go" reduce to `code receipt send` and `code go receipt sorry`, share {receipt, code}, and are
#: plainly one question. At three they were filed as two.
#:
#: So the floor that does the work is MIN_TERMS, which stops a scrap from being matchable in the
#: first place. This one only rules out a single incidental word, which is what it should do.
MIN_SHARED = 2


def resemblance(a: str, b: str, *, min_shared: int = MIN_SHARED) -> float:
    """How alike two messages are, measured against the SHORTER of them.

    `overlap` (Jaccard) divides by the union, which makes it a length comparison as much as a
    content one: a terse question and a long paragraph about the same thing score badly no matter
    how completely the short one sits inside the long one. That is fine when both sides are the
    same kind of object and fatal when they are not — and on a real Discord they never are. People
    ask "where do we send the receipt code?" and people write three sentences of context, and both
    are the same question.

    Measured on a term of real traffic: 480 messages, 130 of them questions, and Jaccard at 0.5
    found **three** recurring things. That is not a quiet server, it is a measure reporting on
    message length.

    Dividing by the smaller set (Szymkiewicz–Simpson) asks the question that actually matters: is
    the shorter message's content present in the longer one? `min_shared` is what keeps that honest,
    because a proportion over a tiny set is not evidence — three shared content words is the floor
    for the ratio to mean anything at all.
    """
    sa, sb = set((a or "").split()), set((b or "").split())
    if not sa or not sb:
        return 0.0
    shared = len(sa & sb)
    if shared < min_shared:
        return 0.0
    return shared / min(len(sa), len(sb))


def cluster(items, terms_of, *, same: float = SAME) -> list[list]:
    """Group things that say the same thing. Returns groups in the order they were first seen.

    Lives here, beside the measure, because two components need the same answer: the bot groups a
    channel's traffic, and the Teaching Center groups what the console shows. Two implementations
    would drift, and the failure would be the confusing kind — the console reporting a different
    number of recurring problems than the bot's own report, with nothing on either screen to
    explain why.

    **Greedy and order-dependent**, and that is a choice rather than an oversight. Each item joins
    the first group its terms reach, or starts one; a proper clustering would not depend on input
    order. At class scale the input is hundreds of rows a week, the alternative costs a pairwise
    matrix and a second threshold nobody can defend either, and what comes out is a list for a
    human to read rather than a number anything depends on.

    The representative stays the FIRST member's terms rather than drifting to the union of the
    group, because a union grows with every join and eventually matches everything.
    """
    groups: list[list] = []
    reps: list[str] = []
    for item in items:
        t = terms_of(item)
        if not t:
            continue                    # nothing to compare on: it joins nothing, see `terms`
        for i, rep in enumerate(reps):
            if resemblance(rep, t) >= same:
                groups[i].append(item)
                break
        else:
            groups.append([item])
            reps.append(t)
    return groups


# --- weighting, for ranking one question against a corpus --------------------------------- #

def idf_table(docs) -> dict:
    """Inverse document frequency over a corpus of `terms` strings.

    Plain coverage cannot tell a rare word from a common one, and on a small curated corpus that is
    not a subtlety — it is the difference between an answer and a coin toss. Measured on the OS
    manual: "what does scause 13 mean" scored `os-storage` and `os-traps` identically at 0.33,
    because each contributed exactly one matching term out of three, and the tie broke
    alphabetically. Weighted, `scause` is worth several times `mean` and the right page wins.

    `docs` is any iterable of terms strings. Same formula as `agent/recall.py`, which computes it
    over concepts and recipes and caches it at import against that fixed corpus; it is left alone
    rather than rewired through here, because unifying them would refactor a live retrieval path
    for no change in behaviour.
    """
    df: dict = {}
    n = 0
    for d in docs:
        n += 1
        for t in set((d or "").split()):
            df[t] = df.get(t, 0) + 1
    import math
    return {t: math.log((n + 1) / (c + 0.5)) for t, c in df.items()}


def unknown_idf(table: dict) -> float:
    """The weight for a query term the corpus has never seen. Rare is informative, so this is the
    ceiling rather than zero — the same reading `recall.py` takes."""
    return max(table.values(), default=1.0)


def weighted_coverage(question: str, doc: str, table: dict) -> float:
    """How much of the QUESTION's meaning this document addresses, 0..1.

    The direction matters and is the same one `covers` takes: a page that speaks to more of what
    was asked ranks higher, rather than a page that merely resembles the question as a string. A
    document about everything would win the second measure.
    """
    q = [t for t in (question or "").split()]
    if not q:
        return 0.0
    seen = set((doc or "").split())
    ceiling = unknown_idf(table)
    total = sum(table.get(t, ceiling) for t in q)
    hit = sum(table.get(t, ceiling) for t in q if t in seen)
    return (hit / total) if total else 0.0


def query_terms(text: str) -> str:
    """Normalised terms for a SEARCH, with no minimum length.

    `terms()` returns "" below `MIN_TERMS`, which is right where it is used — a scrap of a message
    must not cluster with everything — and wrong here. "lock contention" is two content words and a
    perfectly good question; putting it through `terms()` produced "" and the manual returned
    nothing at all for a subject it has a whole page on.

    Two jobs, two helpers. Anything ranking a question against a corpus wants this one.
    """
    from .lexicon import normalize

    return " ".join(sorted(set(normalize(text or "", query=True))))
