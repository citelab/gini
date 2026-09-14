"""Running the ladder over what people actually asked, and printing what happened.

The calibration tool, and the reason step 2 says "answering nobody" rather than "not finished".
Every threshold in `ladder.py` was set against invented questions or a handful of real ones; this
puts the whole observation log through it and prints where each question stopped.

**Read it for the shape, not the score.** What matters is the distribution: if almost everything
lands on L3 the corpus does not cover this community's questions, and no threshold fixes that — it
is a signal to add material, not to lower a bar. If almost everything reaches L2 the bar is too low
and the model is composing on thin ground. The useful state is in between, with the L1 rows being
questions a teacher recognises as genuinely borderline.

It reads the bot's own SQLite rather than going through the Teaching Center, on purpose: this is a
diagnostic, it should work on a laptop with a copy of the file and no server running at all.
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

from gini.domain.similarity import cluster

from . import audit
from .ladder import answer


def questions(db: str | Path = "", *, days: int = 3650, limit: int = 40,
              kinds=("question", "problem")) -> list:
    """The most-asked distinct things in the log, most people first.

    Clustered by the same rule the bot and the console use — `gini.domain.similarity.cluster` — so
    "the top 20 questions" means the same thing here as it does on the teacher's screen.
    """
    path = Path(db or os.environ.get("GINI_BOT_DB", "~/.gini-bot/observations.db")).expanduser()
    if not path.exists():
        return []
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT at, who, kind, text, terms FROM observations WHERE at >= ? AND terms != '' "
        "ORDER BY at ASC", (time.time() - days * 86400,))]
    con.close()

    rows = [r for r in rows if not kinds or r["kind"] in kinds]
    groups = cluster(rows, lambda r: r["terms"])
    out = [{"text": g[0]["text"], "kind": g[0]["kind"],
            "people": len({r["who"] for r in g}), "count": len(g)} for g in groups]
    out.sort(key=lambda g: (-g["people"], -g["count"]))
    return out[:limit]


def run(db: str | Path = "", *, days: int = 3650, limit: int = 40, llm=None) -> dict:
    """Answer each one and tally where they landed."""
    qs = questions(db, days=days, limit=limit)
    rows, tally = [], {"L1": 0, "L2": 0, "L3": 0}
    for q in qs:
        d = answer(q["text"], llm=llm,
                   audit=lambda qq, g, t: audit.flags(audit.review(qq, g, t)))
        tally[d.rung] = tally.get(d.rung, 0) + 1
        rows.append({**q, "rung": d.rung, "score": d.score, "flags": d.flags,
                     "cites": d.citations, "answer": d.text})
    return {"rows": rows, "tally": tally, "asked": len(qs)}


def report(result: dict, *, verbose: bool = False) -> str:
    t, n = result["tally"], result["asked"]
    if not n:
        return ("No questions in the log. Point GINI_BOT_DB at the bot's observations.db, or run\n"
                "`gini-bot backfill 300` first.\n")
    out = [f"{n} distinct questions from the log\n",
           f"  L2  model answered      {t.get('L2', 0):>3}   "
           f"({t.get('L2', 0) * 100 // n}%)",
           f"  L1  pointed at material {t.get('L1', 0):>3}   "
           f"({t.get('L1', 0) * 100 // n}%)",
           f"  L3  nothing covers it   {t.get('L3', 0):>3}   "
           f"({t.get('L3', 0) * 100 // n}%)",
           "",
           "  A large L3 share is a gap in the MATERIAL, not a threshold to lower.",
           ""]
    for r in result["rows"]:
        mark = {"L2": "✓", "L1": "·", "L3": " "}.get(r["rung"], "?")
        out.append(f"  {mark} {r['rung']} {r['score']:.2f}  {r['people']:>2}p  "
                   f"{r['text'][:74]}")
        if verbose and r["flags"]:
            out += [f"        ! {f}" for f in r["flags"]]
        if verbose and r["rung"] == "L2":
            out.append(f"        {r['answer'][:150]}")
    return "\n".join(out) + "\n"
