"""The observation log — what the bot writes down, and the one question it can answer.

SQLite for the same reason the Teaching Center uses it: a flat file corrupts under concurrent
writes, and this is written from an async event handler while a human may be reading it. WAL mode,
one connection, one lock. It ships with Python, so there is nothing to deploy.

**The salt is not in here.** It sits beside the database in its own file, so a copy of the log taken
away to be read — which is the entire point of step 1 — carries no way to link a hash back to a
person. Losing the salt is not a disaster either; it makes the past unlinkable, which is the
intended way to forget.

`clusters()` is the payoff and the reason this is worth running before anything else exists. It is
the machine answer to "what are the pressing issues": every distinct problem seen in a window,
ordered by how many DIFFERENT people hit it. That ordering is why `who` must be a stable hash — one
person saying the same thing five times is one problem, and five people saying it once is a
different kind of fact entirely.
"""
from __future__ import annotations

import os
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from gini.domain.similarity import resemblance

from .observations import PROBLEM, QUESTION, SAME, Observation

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
  at        REAL    NOT NULL,
  channel   TEXT    NOT NULL,
  who       TEXT    NOT NULL,
  kind      TEXT    NOT NULL,
  text      TEXT    NOT NULL,
  terms     TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS obs_at   ON observations(at);
CREATE INDEX IF NOT EXISTS obs_kind ON observations(kind);
"""

#: Re-reading a channel's history must not invent traffic. `at` is the moment the message was SENT,
#: to millisecond precision, so (when, who, what) identifies a message as surely as its id would —
#: and unlike an id it is not a way back to it, which is the property `observations` is built on.
#: Two genuinely different messages colliding would need the same author to post identical text in
#: the same millisecond.
#:
#: The DELETE runs first because an index cannot be added over rows that already violate it: a log
#: written before this existed may hold duplicates from a backfill that ran twice.
DEDUP = """
DELETE FROM observations WHERE rowid NOT IN
  (SELECT MIN(rowid) FROM observations GROUP BY at, who, text);
CREATE UNIQUE INDEX IF NOT EXISTS obs_once ON observations(at, who, text);
"""


@dataclass(frozen=True)
class Cluster:
    """One thing several people ran into. `people` is the number that matters; `count` is how often
    it was said, which can be one insistent person."""
    terms: str
    count: int
    people: int
    first_at: float
    last_at: float
    sample: str            # the first message in the cluster, verbatim (already redacted)


def salt_for(directory: Path) -> str:
    """Read the log's salt, creating it on first use.

    Its own file, mode 0600, never the database — see the module docstring. `secrets` rather than
    `random`: this is the only thing standing between a hash and a class list of user ids.
    """
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / "salt"
    if p.exists():
        return p.read_text(encoding="utf-8").strip()
    value = secrets.token_hex(32)
    p.write_text(value, encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass                       # a filesystem without POSIX modes; the value is still fresh
    return value


class Log:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self._db.executescript(DEDUP)
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def append(self, obs: Observation) -> None:
        with self._lock:
            # OR IGNORE, so backfilling a channel twice — or backfilling one the live handler is
            # already watching — adds nothing the second time.
            self._db.execute(
                "INSERT OR IGNORE INTO observations (at, channel, who, kind, text, terms) "
                "VALUES (?,?,?,?,?,?)",
                (obs.at, obs.channel, obs.who, obs.kind, obs.text, obs.terms))
            self._db.commit()

    def count(self, kind: str = "") -> int:
        with self._lock:
            if kind:
                cur = self._db.execute(
                    "SELECT COUNT(*) FROM observations WHERE kind=?", (kind,))
            else:
                cur = self._db.execute("SELECT COUNT(*) FROM observations")
            return int(cur.fetchone()[0])

    def recent(self, limit: int = 50, since: float = 0.0) -> list[Observation]:
        with self._lock:
            rows = self._db.execute(
                "SELECT at, channel, who, kind, text, terms FROM observations "
                "WHERE at >= ? ORDER BY at DESC LIMIT ?", (since, limit)).fetchall()
        return [Observation(*r) for r in rows]

    def clusters(self, *, since: float = 0.0, kinds: tuple = (PROBLEM, QUESTION),
                 min_people: int = 2, same: float = SAME) -> list[Cluster]:
        """Distinct problems in a window, ordered by how many different people hit each.

        Greedy single-pass grouping: each message joins the first cluster its terms overlap by
        `SAME`, or starts one. Greedy is order-dependent and a proper clustering would not be —
        but at class scale the input is hundreds of rows a week, the alternative costs a pairwise
        matrix and a threshold nobody can defend either, and the thing being produced is a list for
        a human to read rather than a number anything depends on.

        The cluster's representative stays the FIRST member's terms rather than drifting to the
        union, because a union grows with every join and eventually matches everything.
        """
        with self._lock:
            rows = self._db.execute(
                "SELECT at, channel, who, kind, text, terms FROM observations "
                "WHERE at >= ? AND terms != '' ORDER BY at ASC", (since,)).fetchall()

        buckets: list[dict] = []
        for at, _channel, who, kind, text, terms in rows:
            if kinds and kind not in kinds:
                continue
            for b in buckets:
                if resemblance(b["terms"], terms) >= same:
                    b["count"] += 1
                    b["people"].add(who)
                    b["last_at"] = at
                    break
            else:
                buckets.append({"terms": terms, "count": 1, "people": {who},
                                "first_at": at, "last_at": at, "sample": text})

        out = [Cluster(terms=b["terms"], count=b["count"], people=len(b["people"]),
                       first_at=b["first_at"], last_at=b["last_at"], sample=b["sample"])
               for b in buckets if len(b["people"]) >= min_people]
        out.sort(key=lambda c: (-c.people, -c.count, c.first_at))
        return out

    def tune(self, *, days: int = 30) -> str:
        """How the grouping threshold behaves on THIS server's traffic.

        The number in `SAME` was chosen against invented examples, and the first contact with a real
        term of messages showed how little that is worth: Jaccard at 0.5 found three recurring
        things in 130 questions. A threshold is a judgement about a particular community's way of
        writing, so this prints the curve and lets whoever runs it look.

        Read it for the knee. Too strict and every paraphrase is its own incident; too loose and
        unrelated questions collapse into one bucket that says nothing.
        """
        since = time.time() - days * 86400
        out = [f"Grouping threshold against the last {days} days "
               f"({self.count(QUESTION)} questions, {self.count(PROBLEM)} problems):\n"]
        for t in (0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
            cl = self.clusters(since=since, min_people=2, same=t)
            biggest = max((c.people for c in cl), default=0)
            mark = "  <- in use" if abs(t - SAME) < 1e-9 else ""
            out.append(f"  {t:.1f}   {len(cl):>3} recurring   biggest {biggest:>2} people{mark}")
        out.append("\n  Set GINI_BOT_SAME=0.x to report at another threshold.")
        return "\n".join(out) + "\n"

    def report(self, *, days: int = 14, min_people: int = 2, same: float = SAME) -> str:
        """The two-week read, as plain text. This IS the deliverable of step 1 — run it, read it,
        and decide whether any of the rest is worth building."""
        since = time.time() - days * 86400
        cl = self.clusters(since=since, min_people=min_people, same=same)
        head = (f"{self.count()} observations, {self.count(PROBLEM)} problems, "
                f"{self.count(QUESTION)} questions\n"
                f"Last {days} days, seen by {min_people}+ different people:\n")
        if not cl:
            return head + "\n  (nothing recurring yet)\n"
        lines = [f"\n  {c.people:>2} people · {c.count:>2} messages   {c.sample[:96]}" for c in cl]
        return head + "".join(lines) + "\n"
