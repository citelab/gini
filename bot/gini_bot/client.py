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

import asyncio
import logging
import os
from pathlib import Path

from .center import Center
from .log import Log, salt_for
from .observations import observe

log = logging.getLogger("gini_bot")

#: How often to ask the Center whether a teacher has written a reply. A minute is invisible to
#: somebody who has just pressed a button and costs the Center one request.
POLL_S = 60


def _channels_from_env() -> set[str]:
    """Channel names to watch, or an empty set meaning every channel the bot can see.

    A deliberate allow-list is the safer default for a first run in somebody's community — start
    with `#help`, widen once the log looks like what you expected.
    """
    raw = os.environ.get("GINI_BOT_CHANNELS", "").strip()
    return {c.strip().lstrip("#") for c in raw.split(",") if c.strip()}


def backfill(days: int = 30, token: str = "", db: str | Path = "") -> int:
    """Read what was already said, before the bot existed.

    A server that has been running a course for a term already holds the thing step 1 was going to
    spend a fortnight collecting — real questions, from real people, about a real assignment. There
    is no reason to wait for it to happen again.

    Safe to run repeatedly, and safe to run while the live bot is connected: `Log.append` ignores a
    row it already has, and a message's identity here is (when it was sent, who sent it, what it
    said), which is the same whether it arrives live or out of history. So overlapping a backfill
    with live ingestion double-counts nothing, and neither does running this twice by accident.

    Needs **Read Message History**, which is one of the two permissions the bot was invited with.
    Channels it cannot read are named and skipped rather than failing the run — a forum or a
    staff-only channel it has no business in should not stop it reading the ones it does.
    """
    try:
        import discord
    except ImportError:
        print("discord.py is not installed:  pip install --user 'discord.py>=2.3'")
        return 2

    token = token or os.environ.get("GINI_BOT_TOKEN", "")
    if not token:
        print("No bot token. Set GINI_BOT_TOKEN.")
        return 2

    from datetime import datetime, timedelta, timezone

    path = Path(db or os.environ.get("GINI_BOT_DB", "~/.gini-bot/observations.db")).expanduser()
    store = Log(path)
    salt = salt_for(path.parent)
    watch = _channels_from_env()
    since = datetime.now(timezone.utc) - timedelta(days=days)

    center = Center()
    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)
    seen = {"read": 0, "kept": 0}
    batch: list = []

    @client.event
    async def on_ready():
        before = store.count()
        log.info("reading back %d days as %s", days, client.user)
        for guild in client.guilds:
            for ch in getattr(guild, "text_channels", []):
                if watch and ch.name not in watch:
                    continue
                try:
                    async for m in ch.history(limit=None, after=since, oldest_first=True):
                        seen["read"] += 1
                        if m.author.bot:
                            continue
                        o = observe(
                            at=m.created_at.timestamp(), channel=ch.name, user_id=m.author.id,
                            salt=salt, text=m.content or "", is_reply=m.reference is not None)
                        store.append(o)
                        batch.append({**o.__dict__, "channel_id": str(ch.id),
                                      "message_id": str(m.id)})
                        if len(batch) >= 200:     # batched: a term of history is thousands of rows
                            center.push(batch)
                            batch.clear()
                except discord.Forbidden:
                    log.warning("no history access to #%s — skipped", ch.name)
                except Exception:            # noqa: BLE001 — one bad channel must not end the run
                    log.exception("could not read #%s", ch.name)
                else:
                    log.info("#%s done", ch.name)
        if batch:
            center.push(batch)
            batch.clear()
        seen["kept"] = store.count() - before
        log.info("read %d messages; %d new rows (the rest were already recorded)",
                 seen["read"], seen["kept"])
        if center.configured:
            log.info("pushed to the Teaching Center — open the GINI AI tab")
        await client.close()

    client.run(token, log_handler=None)
    return 0


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

    center = Center()
    if center.configured:
        log.info("pushing to the Teaching Center at %s", center.url)
    else:
        log.info("no Teaching Center configured (GINI_TC_URL / GINI_BOT_KEY) — logging locally only")

    intents = discord.Intents.default()
    intents.message_content = True          # privileged — see the docstring
    client = discord.Client(intents=intents)

    async def _deliver_replies():
        """Post what teachers have written, every POLL_S.

        A poll rather than a push from the Center, and the direction matters: the Center never
        needs to reach the bot, so the bot can sit behind anything and hold no listener of its own.
        It is also what makes a Center restart invisible — the next poll simply succeeds.
        """
        await client.wait_until_ready()
        while not client.is_closed():
            for item in center.outbox():
                try:
                    ch = client.get_channel(int(item["channel_id"]))
                    if ch is None:
                        ch = await client.fetch_channel(int(item["channel_id"]))
                    msg = await ch.fetch_message(int(item["message_id"]))
                    await msg.reply(item["body"])
                    center.settle(item["id"])
                    log.info("posted a reply in #%s", getattr(ch, "name", "?"))
                except Exception as e:            # noqa: BLE001 — record it, do not retry for ever
                    log.warning("could not post a reply: %s", e)
                    center.settle(item["id"], f"{type(e).__name__}: {e}")
            await asyncio.sleep(POLL_S)

    @client.event
    async def on_ready():
        log.info("connected as %s; %s", client.user,
                 "replies from the Teaching Center will be posted"
                 if center.configured else "observing only, posting nothing")
        if center.configured:
            client.loop.create_task(_deliver_replies())
        # Say what is actually being watched, against what is actually there. A watch list naming a
        # channel this server does not have records nothing at all, and every other signal looks
        # healthy: it connects, it stays up, the log file exists and stays empty. That reads as a
        # quiet server rather than a typo, and the two are indistinguishable without this line.
        here = {c.name for g in client.guilds for c in getattr(g, "text_channels", [])}
        if not watch:
            log.info("watching all %d channels: %s", len(here), ", ".join(sorted(here)))
            return
        seen, missing = watch & here, watch - here
        log.info("watching %s", ", ".join(f"#{c}" for c in sorted(seen)) or "NOTHING")
        if missing:
            log.warning("GINI_BOT_CHANNELS names %s, which this server does not have — nothing "
                        "will be recorded from %s. Channels here: %s",
                        ", ".join(f"#{c}" for c in sorted(missing)),
                        "them" if len(missing) > 1 else "it",
                        ", ".join(sorted(here)) or "(none visible)")

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
            obs = observe(
                # When it was SAID, not when we happened to read it. It matters for more than
                # tidiness: it is what makes a live message and the same message seen again in
                # history identical, and therefore counted once.
                at=message.created_at.timestamp(), channel=channel,
                user_id=message.author.id, salt=salt,
                text=message.content or "",
                is_reply=message.reference is not None)
            store.append(obs)
            # The Center gets the message AND the way back to it. That reference expires there;
            # see the observation_ref table. Failing to reach the Center is not an error worth
            # stopping for — the row is already safe locally and pushes again next time.
            answer = center.push_one({**obs.__dict__,
                                      "channel_id": str(message.channel.id),
                                      "message_id": str(message.id)})
            if answer:
                # The Center decided; this posts. Whether the bank covers it, whether it has been
                # said in this channel recently, and what words to use are all decisions, and the
                # bot holds none of them.
                await message.reply(answer)
                log.info("answered from the bank in #%s", channel)
        except Exception:                   # noqa: BLE001 — a bad row must not kill the connection
            log.exception("could not record a message")

    client.run(token, log_handler=None)
    return 0
