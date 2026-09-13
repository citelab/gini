"""Two commands: run the bot, or read what it has collected.

    python -m gini_bot            # connect and observe
    python -m gini_bot report     # the last 14 days, by how many people hit each thing

`report` is the actual deliverable of step 1. Run the bot for a fortnight, read this, and decide
whether any of the rest of `docs/design/gini-ai-discord.md` is worth building.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")
    path = Path(os.environ.get("GINI_BOT_DB", "~/.gini-bot/observations.db")).expanduser()

    if args and args[0] == "report":
        from .log import Log
        days = int(args[1]) if len(args) > 1 else 14
        if not path.exists():
            print(f"No log at {path} yet.")
            return 1
        print(Log(path).report(days=days))
        return 0

    from .client import run
    return run(db=path)


if __name__ == "__main__":
    raise SystemExit(main())
