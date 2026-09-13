"""The Discord side. It reads, it writes rows, and it says nothing.

Everything this file could be tempted to decide lives in `observations.py` instead, and everything
it could be tempted to say is not built yet. That is step 1 of `docs/design/gini-ai-discord.md`: a
bot that posts nothing cannot embarrass the course, cannot be wrong in public, and can be deleted
on the fourteenth day having cost nothing but a token.

`discord.py` is imported inside `run()` rather than at module import, so the pure half of this
package can be tested — and the report read — on a machine that has never installed it.

**Three things are deliberately not recorded**, and each would undo something:

* **Direct messages.** Only channels the whole class can already read are observed. A DM is a
  private conversation and no amount of hashing makes recording one acceptable.
* **Other bots.** Including this one, if it ever gains a voice. A bot answering a bot is how a log
  fills with its own echo.
* **Message ids.** See `observations` — an id is a way back to the author, which is the thing the
  hashing exists to prevent.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from .log import Log, salt_for
from .observations import observe

log = logging.getLogger("gini_bot")


def _channels_from_env() -> set[str]:
    """Channel names to watch, or an empty set meaning every channel the bot can see.

    A deliberate allow-list is the safer default for a first run in somebody's community — start
    with `#help`, widen once the log looks like what you expected.
    """
    raw = os.environ.get("GINI_BOT_CHANNELS", "").strip()
    return {c.strip().lstrip("#") for c in raw.split(",") if c.strip()}


def run(token: str = "", db: str | Path = "") -> int:
    """Connect, and write down what is said. Returns a process exit code.

    Requires the **Message Content Intent**, which is privileged and off by default: enable it on
    the bot in Discord's developer portal, or `message.content` arrives empty and the log fills with
    blank rows that look like a bug in this file.
    """
    try:
        import discord
    except ImportError:
        print("discord.py is not installed:  pip install --user 'discord.py>=2.3'")
        return 2

    token = token or os.environ.get("GINI_BOT_TOKEN", "")
    if not token:
        print("No bot token. Set GINI_BOT_TOKEN (developer portal -> Bot -> Reset Token).")
        return 2

    path = Path(db or os.environ.get("GINI_BOT_DB", "~/.gini-bot/observations.db")).expanduser()
    store = Log(path)
    salt = salt_for(path.parent)
    watch = _channels_from_env()
    log.info("logging to %s; channels: %s", path, ", ".join(sorted(watch)) or "(all)")

    intents = discord.Intents.default()
    intents.message_content = True          # privileged — see the docstring
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        log.info("connected as %s; observing only, posting nothing", client.user)

    @client.event
    async def on_message(message):
        if message.author.bot:
            return                          # including ourselves, if we ever gain a voice
        channel = getattr(message.channel, "name", "")
        if not channel:
            return                          # a DM has no channel name: never recorded
        if watch and channel not in watch:
            return
        try:
            store.append(observe(
                at=time.time(), channel=channel, user_id=message.author.id, salt=salt,
                text=message.content or "",
                is_reply=message.reference is not None))
        except Exception:                   # noqa: BLE001 — a bad row must not kill the connection
            log.exception("could not record a message")

    client.run(token, log_handler=None)
    return 0
