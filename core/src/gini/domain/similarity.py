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


#: Two messages must share at least this many content words before their proportions mean anything.
#: Without it, a three-word message that happens to sit inside a long one scores 1.0 and joins it.
MIN_SHARED = 3


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
