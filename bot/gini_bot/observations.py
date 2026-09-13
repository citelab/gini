"""What the bot is allowed to remember about a message, and how it forgets who said it.

This is the pure half of the Discord ingest: a record, three rules, and no I/O. The Discord client
is in `client.py` and the store is in `log.py`; neither makes a judgement. The split is the house's
usual one (`domain.probes` ↔ `services.probe_runner`) and it exists so the part that decides what
gets written down can be tested without a token, a network or a database.

**Nobody's name is written down, and that is a design constraint rather than a courtesy.** The
Teaching Center's own docstring says "No student accounts. A vended code is the whole interaction.
The portal never learns who did the work." A Discord bot is identity-bearing by nature — handles,
user ids, a public history — so pointing one at that system would quietly make the sentence false
unless it is designed against. `who()` therefore stores a salted hash and never a handle. Recurrence
still works ("this is the fourth person to hit this"), because the same person hashes the same way;
naming them does not.

Three things follow from that, and each is easy to get wrong:

* **A mention is a user id in the text.** Hashing the author while storing `<@987654321>` in the
  body would leak precisely what the hash was protecting, from the other end. `redact` strips them,
  along with bare snowflakes people paste, e-mail addresses and invite links.
* **The log holds no message ids.** It is tempting to keep one so a reply can find the message
  later, and it would undo the whole scheme: a message id fetches the message, and the message
  names its author. Step 1 posts nothing, so there is nothing to reply to; when answering arrives it
  replies *live*, in the moment, from the event it is already holding. The log never needs a way
  back to Discord.
* **The salt lives outside the database.** A copy of the log taken away for analysis carries no
  salt, so it cannot be joined against a list of user ids. Rotating the salt severs the history
  deliberately — that is the intended way to forget.

`classify` is rules-only, for the same reason `agent/notifier.py` says its salience is: a server
producing thousands of messages a week cannot be triaged by a model, and a fixed table can. The
table is crude and is meant to be; what callers depend on is the set of kinds, not the accuracy of
any one call.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

#: What a message is, as far as this bot is concerned. Deliberately few: every extra kind is a
#: decision someone has to make about every message, and step 1 only needs to separate "somebody is
#: stuck" from "somebody is chatting".
QUESTION = "question"      # asking for help
PROBLEM = "problem"        # reporting something broken — the ones worth counting
ANSWER = "answer"          # replying to someone else
CHATTER = "chatter"        # everything else

KINDS = (QUESTION, PROBLEM, ANSWER, CHATTER)


@dataclass(frozen=True)
class Observation:
    """One message, stripped of everyone's identity and of any way back to the original.

    `who` is a hash, `channel` is a NAME (a channel is not a person), `text` is redacted, and
    `terms` is what makes two people reporting the same thing countable as one problem.
    """
    at: float                  # unix seconds
    channel: str               # "help", not an id
    who: str                   # salted hash, 16 hex chars — never a handle
    kind: str                  # one of KINDS
    text: str                  # redacted
    terms: str                 # normalised content words, sorted; "" when there are too few


# --- forgetting who ---------------------------------------------------------------- #

def who(user_id, salt: str) -> str:
    """A stable, unlinkable handle for one person.

    Salted because a Discord snowflake is not a secret: they are visible to anyone in the server
    and enumerable, so a bare `sha256(user_id)` is reversible by anyone who can list the members —
    which is the entire class. With a salt held outside the database, the hash is only linkable by
    whoever has both, and rotating the salt makes the past unlinkable on purpose.

    Sixteen hex characters: enough that a collision in a class-sized server is not a practical
    concern, short enough to read in a report.
    """
    return hashlib.sha256(f"{salt}:{user_id}".encode()).hexdigest()[:16]


# A mention carries the user id verbatim, which is the thing `who()` exists to remove.
_MENTION = re.compile(r"<@[!&]?\d+>")
_CHANNEL_REF = re.compile(r"<#\d+>")
_CUSTOM_EMOJI = re.compile(r"<a?:\w+:\d+>")
_SNOWFLAKE = re.compile(r"\b\d{17,20}\b")          # pasted ids, outside a mention
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_INVITE = re.compile(r"https?://(?:\w+\.)?discord(?:\.gg|app\.com/invite)/\S+", re.I)


def redact(text: str) -> str:
    """Remove every identifier the text carries, leaving the words that make it a report.

    Order matters: custom emoji and channel references are stripped before the bare-snowflake rule,
    or that rule would eat the digits inside them and leave the wrapper behind.
    """
    t = _CUSTOM_EMOJI.sub(":emoji:", text or "")
    t = _MENTION.sub("@someone", t)
    t = _CHANNEL_REF.sub("#channel", t)
    t = _INVITE.sub("<invite>", t)
    t = _EMAIL.sub("<email>", t)
    t = _SNOWFLAKE.sub("<id>", t)
    return t.strip()


# --- what kind of message it is ---------------------------------------------------- #

#: Signals that something is broken, as SUBSTRING STEMS rather than whole words.
#:
#: Whole words were the first version and they under-count in the way that matters: a real pair of
#: reports read "gbuilder core dumped" and "my gbuilder core dumps", and a list containing
#: "core dumped" matched one and filed the other as chatter — so the message never reached a
#: cluster and one incident was reported by one fewer person than had actually hit it. Recurrence
#: is the whole point of this log, and a classifier that drops a report over a plural is worse than
#: one that occasionally over-matches.
#:
#: So each entry is the shortest stable prefix: "core dump" catches dumped/dumps/dumping, "crash"
#: catches crashed/crashes/crashing, "fail" catches failed/fails/failing. Deliberately the
#: vocabulary of a stuck student, not of a bug tracker. Note "dump" alone is NOT here and must not
#: be — a serial dump is ordinary GINI vocabulary, and every /procs read would look like a crash.
_BROKEN = ("error", "traceback", "crash", "core dump", "segfault", "abort", "fail", "broken",
           "stuck", "hang", "frozen", "freez", "refus", "denied", "no such", "not working",
           "wont start", "won't start", "doesnt work", "doesn't work", "does not work",
           "cannot", "can't", "cant ")

_ASKING = ("how", "why", "what", "where", "when", "which", "who", "can i", "do i", "does",
           "should i", "is there", "anyone know", "any idea")


def classify(text: str, *, is_reply: bool = False) -> str:
    """Which kind of message this is. Rules only — no model on this path.

    A problem beats a question when a message is both, because "how do I fix this traceback" is
    worth counting as an occurrence of the traceback. Counting it as a question would scatter one
    recurring failure across a dozen differently-worded askings, which is exactly the signal step 1
    exists to collect.
    """
    low = (text or "").lower()
    if not low.strip():
        return CHATTER
    if any(w in low for w in _BROKEN):
        return PROBLEM
    if low.rstrip().endswith("?") or any(low.startswith(w) or f" {w} " in low for w in _ASKING):
        return QUESTION
    if is_reply:
        return ANSWER
    return CHATTER


# --- what makes two messages the same problem -------------------------------------- #

#: Below this many content words there is nothing to cluster on, and grouping would put unrelated
#: one-liners ("it broke", "same here") into a fake incident.
MIN_TERMS = 3


def terms(text: str) -> str:
    """The content words of a message, normalised and sorted — the material clustering works on.

    Built with `gini.domain.lexicon.normalize`, the same normaliser `agent.recall` uses to turn a
    student's phrasing into GINI vocabulary, so the bot's idea of "the same thing" is the one the
    rest of GINI already has.

    **A hash of this set was the first design and it does not work.** Two people reporting one
    failure almost never use the same words: measured on a real pair —

        "gbuilder core dumped on the lab machine, help?"
        "the lab machine core dumped gbuilder"

    — the sets differ by the single term `help`, and an exact hash therefore filed one incident as
    two. Under-counting is not a cosmetic fault here; counting recurrence is the entire purpose of
    step 1, and a mechanism that splits every incident by whichever filler word somebody typed
    would report a quiet server no matter what was happening in it.

    So sameness is decided by OVERLAP at read time (`log.clusters`), and this returns the terms
    rather than a digest. "" when there is too little to go on, which is a real answer and not a
    failure: an unclusterable message is still worth recording.
    """
    from gini.domain.lexicon import normalize

    found = sorted(set(normalize(text or "", query=True)))
    return " ".join(found) if len(found) >= MIN_TERMS else ""


#: How much two messages must share to count as the same report. Jaccard over the term sets.
#: 0.5 was chosen against the pair in `terms()` above: it groups them, while leaving two different
#: complaints that merely share the word "gbuilder" apart.
SAME = 0.5


def overlap(a: str, b: str) -> float:
    """Jaccard similarity of two `terms` strings. 0.0 when either is empty — an unclusterable
    message never joins a cluster, rather than joining every cluster."""
    sa, sb = set(a.split()), set(b.split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def observe(*, at: float, channel: str, user_id, salt: str, text: str,
            is_reply: bool = False) -> Observation:
    """Build the record. The one place raw Discord data is allowed to become an Observation, so a
    caller cannot assemble one that skipped redaction."""
    body = redact(text)
    return Observation(at=float(at), channel=str(channel), who=who(user_id, salt),
                       kind=classify(body, is_reply=is_reply), text=body, terms=terms(body))
