"""Three commands: run the bot, read what it collected, or check it is collecting.

    python -m gini_bot            # connect and observe
    python -m gini_bot backfill   # read the last 30 days of history, then exit
    python -m gini_bot report     # the last 14 days, by how many people hit each thing
    python -m gini_bot tune       # how the grouping threshold behaves on this server
    python -m gini_bot tail       # the last few rows, verbatim

`report` is the actual deliverable of step 1. Run the bot for a fortnight, read this, and decide
whether any of the rest of `docs/design/gini-ai-discord.md` is worth building.

`tail` exists for the first five minutes, and it answers the one thing that can be silently wrong:
the **Message Content Intent** is privileged and off by default, and without it every message
arrives with empty `content`. The row count still climbs, the log still looks healthy, and a
fortnight later there is nothing in it. Rows with empty text are called out here for that reason.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")
    path = Path(os.environ.get("GINI_BOT_DB", "~/.gini-bot/observations.db")).expanduser()

    if args and args[0] == "tune":
        from .log import Log
        if not path.exists():
            print(f"No log at {path} yet.")
            return 1
        print(Log(path).tune(days=int(args[1]) if len(args) > 1 else 30))
        return 0

    if args and args[0] == "backfill":
        from .client import backfill
        return backfill(days=int(args[1]) if len(args) > 1 else 30, db=path)

    if args and args[0] == "tail":
        from .log import Log
        n = int(args[1]) if len(args) > 1 else 10
        if not path.exists():
            print(f"No log at {path} yet — nothing has been said in a watched channel.")
            return 1
        rows = Log(path).recent(limit=n)
        if not rows:
            print("The log exists but is empty. Has anyone posted in a watched channel?")
            return 1
        blank = 0
        for o in reversed(rows):
            when = time.strftime("%H:%M:%S", time.localtime(o.at))
            blank += not o.text
            print(f"  {when}  #{o.channel:<12} {o.kind:<8} {o.who[:8]}  {o.text[:70]!r}")
        if blank == len(rows):
            print("\nEvery row is EMPTY. The Message Content Intent is off — turn it on at\n"
                  "https://discord.com/developers/applications -> your app -> Bot ->\n"
                  "Privileged Gateway Intents -> MESSAGE CONTENT INTENT, then restart the bot.")
            return 2
        return 0

    if args and args[0] == "report":
        from .log import Log
        days = int(args[1]) if len(args) > 1 else 14
        if not path.exists():
            print(f"No log at {path} yet.")
            return 1
        from gini.domain.similarity import SAME
        same = float(os.environ.get("GINI_BOT_SAME", SAME))
        print(Log(path).report(days=days, same=same))
        return 0

    from .client import run
    return run(db=path)


if __name__ == "__main__":
    raise SystemExit(main())
