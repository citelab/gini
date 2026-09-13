"""The answer bank: a teacher writes an answer once, and the course stops answering it again.

The problem this solves is the teacher's time, not the student's. On a busy Discord the same
question arrives every term, from a different person each time, and the answer already exists —
three hundred messages up, in a thread nobody will find. So it gets answered again. And again.

So: the console shows what the server is actually asking, ordered by how many DIFFERENT people asked
it; the teacher writes the answer once; and the bot posts it, verbatim, the next time anyone asks.
The student gets the answer in the channel they asked in, which is the other half — nobody should
have to scroll a busy server to find out whether their question was already settled.

**There is no model here, and that is the point.** The Teaching Center's own docstring says "No AI.
No model client is imported and no outbound model call is made", and this does not change it: the
teacher wrote every word the bot will ever say. Matching is the same term overlap the rest of GINI
uses for retrieval. That removes the entire class of risk that comes with a bot speaking in a
student community — it cannot be confidently wrong, because it is not composing anything.

The rules live here rather than in `store.py` or `server.py` so they can be tested with no database,
no HTTP and no model — the same reason `activities.py` is a module of its own.

Three ideas carry it.

**An answer is published only when the match is clearly better than a guess.** `ANSWER_FLOOR` is
deliberately above `similarity.SAME`, the threshold used for grouping reports. Those two thresholds
face opposite costs: missing a cluster under-counts a problem on a page a teacher reads and can
correct for, while a wrong answer is posted publicly, in the course's name, to a student who has no
way to know it is wrong. The stricter number is the cheap side of that asymmetry.

**Silence is a valid answer.** No match means the bot says nothing at all, rather than a hedge or an
apology. A "I'm not sure, ask a TA" from a bot is noise on a busy server, and it trains people to
ignore it — which costs the answers that ARE good.

**An answer is a thing with an author and a date.** It is posted with both, so a student can see it
came from the course rather than from a machine, and can tell when it was written — an answer from
two terms ago may be about a version that no longer exists.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from gini.domain.similarity import covers, terms

#: How well a question must match a banked answer before it is posted. Above `SAME` (0.5) on
#: purpose — see the module docstring on the asymmetry between a missed cluster and a wrong answer.
ANSWER_FLOOR = 0.62

#: How long the bot stays quiet about one answer in one channel after posting it. When six people
#: pile onto the same outage, the answer is useful once and spam five times.
COOLDOWN_S = 30 * 60


@dataclass
class Answer:
    """One thing the course has settled, in the teacher's words.

    `triggers` is a LIST, because one settled question arrives in many shapes and the bank should
    collect them rather than have the teacher guess them up front. When the console shows a cluster
    that is obviously the same thing but did not match, `attach` adds that phrasing to the existing
    answer — one click, no rewording, and the bank gets better at the job without anything learning
    anything. That is the whole mechanism by which this stops being asked again.
    """
    id: str
    question: str                 # the canonical form, shown in the console
    answer: str                   # exactly what the bot will post. The teacher's words.
    triggers: list = field(default_factory=list)   # normalised term strings; question when empty
    author: str = ""              # staff email, for the byline
    created_at: float = field(default_factory=time.time)
    updated_at: float = 0.0
    uses: int = 0                 # how many times it has saved someone answering by hand
    enabled: bool = True
    course: str = ""              # "" means every course — a shared server is not course-scoped

    def matchable(self) -> list:
        """Every phrasing this answer responds to. The question itself is always one of them."""
        return [t for t in ([terms(self.question)] + list(self.triggers)) if t]


def from_cluster(sample: str, answer: str, *, author: str = "", course: str = "",
                 ident: str = "") -> Answer:
    """Build an answer from what the console is showing — the question in a real person's words.

    Triggers taken from what was actually asked, not from a canonical rewording, because the next
    person to ask will phrase it like the last person did, not like a textbook.
    """
    return Answer(id=ident or f"a{int(time.time() * 1000)}", question=sample.strip(),
                  answer=answer.strip(), triggers=[], author=author, course=course)


def attach(answer: Answer, sample: str) -> Answer:
    """Teach an existing answer one more way the question gets asked.

    The console's second verb, after "write an answer": a cluster that is plainly the same thing but
    scored below the floor becomes one click, and the next person phrasing it that way is answered.
    No-op for a phrasing already covered, so clicking twice is harmless.
    """
    t = terms(sample)
    if t and t not in answer.matchable():
        answer.triggers = list(answer.triggers) + [t]
        answer.updated_at = time.time()
    return answer


def best_match(question: str, answers, *, course: str = "", floor: float = ANSWER_FLOOR):
    """The banked answer to post, or None. Returns `(Answer, score)` when there is one.

    `None` is the common case and the correct one — see "silence is a valid answer".
    """
    q = terms(question)
    if not q:
        return None
    best, score = None, 0.0
    for a in answers:
        if not a.enabled:
            continue
        if a.course and course and a.course != course:
            continue
        for known in a.matchable():
            s = covers(known, q)          # asymmetric: see similarity.covers
            if s > score:
                best, score = a, s
    return (best, score) if best is not None and score >= floor else None


def should_post(answer_id: str, channel: str, now: float, posted: dict) -> bool:
    """Has this answer been said in this channel recently enough to stay quiet?

    `posted` is `{(answer_id, channel): when}` and is the caller's to keep — this stays pure so the
    rule is testable without a clock or a store.
    """
    last = posted.get((answer_id, channel), 0.0)
    return (now - last) >= COOLDOWN_S


def render(answer: Answer, *, now: float | None = None) -> str:
    """Exactly what goes into the channel.

    The byline is not decoration. A student needs to know this is the course's answer rather than a
    machine's opinion, and how old it is — GINI ships every few weeks, and an answer from two terms
    ago may be about a version that no longer exists.
    """
    when = time.strftime("%-d %b %Y", time.localtime(answer.updated_at or answer.created_at))
    who = answer.author.split("@")[0] if answer.author else "the course"
    return f"{answer.answer.strip()}\n\n— answered by {who}, {when}"


def unanswered(clusters, answers, *, course: str = "", floor: float = ANSWER_FLOOR) -> list:
    """The clusters the bank does not cover yet — the teacher's to-do list, in priority order.

    This is what makes the console page worth opening: not "here is everything said on Discord", but
    "here is what people keep asking that you have not answered yet, most-asked first".
    """
    out = []
    for c in clusters:
        if best_match(getattr(c, "sample", ""), answers, course=course, floor=floor) is None:
            out.append(c)
    return out
