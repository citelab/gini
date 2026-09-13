# GINI AI — step 1: the bot that says nothing

Step 1 of [`docs/design/gini-ai-discord.md`](../docs/design/gini-ai-discord.md). It joins the GINI
Discord server, writes down what is said, and **posts nothing at all**.

That is the whole point. A bot that cannot speak cannot be wrong in public, cannot embarrass the
course, and can be deleted on the fourteenth day having cost a token and nothing else. What it
produces is one thing:

    python -m gini_bot report

— every distinct problem people hit in the last fortnight, ordered by how many *different* people
hit it. That is the machine answer to "what are the pressing issues", and it is worth having before
anything reasons about anything.

## Running it

```bash
pip install --user 'discord.py>=2.3'        # the only dependency beyond gini-core
export PYTHONPATH=/path/to/gini/core/src:/path/to/gini/bot
export GINI_BOT_TOKEN=...                   # developer portal -> Bot -> Reset Token
export GINI_BOT_CHANNELS=help,os-lab        # optional; empty means every channel it can see
export GINI_BOT_DB=~/.gini-bot/observations.db

python -m gini_bot                          # observe
python -m gini_bot report 14                # read
```

**Enable the Message Content Intent** on the bot in Discord's developer portal. It is privileged
and off by default, and without it `message.content` arrives empty — the log fills with blank rows
that look like a bug in `client.py` and are not.

Start with a narrow `GINI_BOT_CHANNELS`. Widen it once the log looks like what you expected.

## What it will not write down

The Teaching Center's own docstring says *"the portal never learns who did the work"*. A Discord bot
is identity-bearing by nature, so keeping that true from this end is a design constraint rather than
a courtesy:

- **No handles.** `who` is a salted hash. Recurrence still works — the same person hashes the same
  way — while the store stays unable to name anyone.
- **No mentions.** A `<@…>` is a user id in the message body; hashing the author while keeping their
  friend's id verbatim would leak from the other end exactly what the hash protects.
- **No message ids.** An id fetches the message, and the message names its author. Step 1 posts
  nothing, so it needs no way back — and when answering arrives it replies *live*, from the event it
  is already holding, never from the log.
- **No direct messages.** Only channels the class can already read.
- **The salt lives outside the database**, mode 0600. A copy of the log taken away to be read
  carries no way to join it against a list of user ids. Rotating the salt severs the history on
  purpose; that is the intended way to forget.

## Why there is no `pyproject.toml`

Adding one would make this a fourth distribution, and `test_packaging.py` would then — correctly —
demand a publish workflow and a place in every release. That is a commitment step 1 has not earned.
It is a service that runs from a checkout on one host, which is how you would deploy a bot anyway.

When it graduates, the pure half (`observations.py`) belongs in the Qt-free agent core that §5.2 of
the design proposes splitting out, not here.

## Layout

| file | what |
|---|---|
| `observations.py` | pure: the record, the redaction rules, the classifier, term overlap. No I/O. |
| `log.py` | SQLite, and `clusters()` — the read that answers the question above. |
| `client.py` | the Discord adapter. Reads, writes rows, says nothing. |
| `tests/` | run with `./scripts/dev.sh test`, or `pytest bot/tests` |
