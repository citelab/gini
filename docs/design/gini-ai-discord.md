# GINI AI on Discord — a bot, and a reasoning engine in the Teaching Center

**Status: proposal. Nothing here is implemented.** Written 2026-09-13 against `v6.12.0`, at the
maintainer's request, and paused immediately after — pick it up from §6 (Build order).

A rendered version of this document, with the same content:
<https://claude.ai/code/artifact/52561c2d-9b79-430b-be84-a8232e9faf16>

The intent, in the maintainer's words: *a GINI AI bot that works in the GINI Discord server, reads
the activity there and brings it to a reasoning engine running in the Teaching Center (changed
plans). GINI AI is a combined entity — bot, reasoning engine. It answers questions, announces
releases, uses the release notes to tell students about features that address problems they have
hit, finds the pressing issues, and posts FAQs and solutions.*

---

## 1. The finding that sets the size of the job

**The reasoning engine already exists, and it already runs without a screen.** Verified, not
assumed: with `PySide6` blocked at the import hook, every one of these imports cleanly —

    gini.agent.recall   gini.agent.kb        gini.agent.ask     gini.agent.loop
    gini.agent.reasoning    gini.agent.understand    gini.agent.llm.ollama

Only `GiniAPI` pulls Qt, and `agent/__init__.py` already documents that it is exposed lazily
because importing it reaches `app.context` → PySide6, "and headless consumers" should not pay that.

So this is not building an AI system. It is three things GINI does not have — a **packaging split**,
a **new event source**, and a **publish gate** — bolted onto 6,801 lines of reasoning it does.

## 2. What is already there, and what it solves

Thirty modules under `frontend-ng/src/gini/agent/`. Two of them matter more than the rest here, and
neither is obvious from its name.

- **`notifier.py` (116 lines) already solves the hardest problem in ingesting a chat server.** Its
  salience layer is *rules-only by decision* — "a fixed table, no LLM on the hot path". A Discord
  server producing thousands of messages a week cannot be triaged by a model; it can be triaged by
  that table. This is the same seam the in-app swarm uses to decide when to wake the Reasoning
  persona, pointed at a new kind of event.
- **`ask.grounding_stance` already tells us when we are guessing.** It returns `strong` / `thin` /
  `empty` from what `recall` actually found. A bot that answers in public needs exactly that number,
  and nothing else in the tree computes it.

Supporting cast: `recall.py` (three-layer retrieval, lexical → model expansion → embeddings),
`kb.py` (the grounding corpus), `understand.py` (question → Intent), `loop.py` (tool-calling with a
JSON fallback), `blackboard.py` (truth cache), `llm/backend.py` (a `Protocol`, so the model is a
config choice), `mcp_server.py` (already publishes the tool registry to external agents).

## 3. Three corpora, all already indexed

- **The domain KB** — 21 concept notes and 44 recipes in `gini.domain`; what Ask GINI answers from.
- **The OS manual** — 17 pages, 2,420 lines. `docs/manual/README.md` states it is "written to be
  indexable by GINI AI — every page carries YAML frontmatter and the same heading skeleton, so a
  retrieval system can answer 'what is this number?', 'where does it come from?', and 'what are its
  limits?' from a single page." Every page carries `keywords`, `endpoints`, `kernel_files`.
  **Nothing has ever read it.** That corpus was written for this bot before the bot existed.
- **Course material** — and the seam exists: `GET /api/ask` on the Teaching Center, backed by
  `search.py`, course-scoped and released-only by construction.

## 4. Shape

    Discord ──▶ observation log ──▶ salience ──▶ recall ──▶ reasoning ──▶ publish gate ──▶ Discord
     [new]          [new]         notifier.py  recall.py   loop.py         [new]
                                                  kb.py    reasoning.py

**The bot holds no intelligence.** It reads Discord, writes rows, and posts what it is told to post.
Every decision happens behind the Teaching Center. That split is what lets the bot be restarted,
rate-limited, banned or rewritten without touching reasoning — and it is what stops a Discord token
becoming a key to the course.

**The engine is a sidecar, not a route.** Its own process, its own port, its own store. See §5.

**Naming, before it bites.** In this codebase an *activity* is a lab with a vended code, a deadline
and a receipt (`activities.py`, `/api/activity/submit`). Discord traffic must not be called that.
Call it **observations**. The distinction will be load-bearing in every conversation about this.

## 5. Four tensions — each is a recorded decision being reversed or strained

**5.1 The Teaching Center says "No AI".** `server.py`'s docstring: *"No AI. No model client is
imported and no outbound model call is made. That removes an entire class of failure — 'the model
timed out', 'the model chose badly' — from a system teachers depend on at deadline time."* That is a
position with a reason, and the reason is still correct: a student submitting at 23:58 must not be
behind a model call. **Proposal:** keep the sentence true *of the server*. The engine is a separate
process with its own store, reachable over a local socket; the submission path never calls it and it
cannot hold a lock the deadline path needs. Then amend that docstring to say so — do not delete it,
because the reason it gives is the design constraint.

**5.2 The code is Qt-free; the distribution is not.** The reasoning modules ship inside
`gini-toolkit`, which *requires* PySide6, so a Teaching Center importing them pip-installs Qt onto a
headless server. **Proposal:** split the Qt-free agent core out, exactly as `core/` was split out of
`frontend-ng/` on 2026-08-28 — into `gini-core` or a fourth distribution. This is the largest single
piece of work in the proposal, and it is a move, not a rewrite.

**5.3 Discord has names; the Teaching Center refuses to.** `server.py` again: *"No student accounts.
A vended code is the whole interaction. The portal never learns who did the work."* A Discord bot is
identity-bearing by nature. **Proposal:** the observation log stores a salted hash of the Discord
user id and never the handle — recurrence and clustering still work ("this is the fourth person to
hit this") while the store stays unable to name anyone. The bot resolves the hash to a mention only
in the moment it replies, in its own process. No join between a Discord identity and a submission
receipt, ever.

**5.4 A bot is wrong in public.** Inside gBuilder a poor answer reaches one student; in the server it
reaches the class, permanently, attributed to the course. **Proposal:** two gates. Answers publish
only at `strength == strong`, otherwise the bot says what it does know and tags a human.
Announcements and FAQs never auto-publish — drafted to a staff channel, posted on a click. The bot
may be uncertain in public; it may not be confidently wrong.

## 6. Release announcements have no source — except the one that is already excellent

**There are no release notes.** No `CHANGELOG`, no GitHub releases, and every tag from `v6.8.0` to
`v6.12.0` is annotated with nothing but its own version string.

But the commit messages are, by house rule, sentences about behaviour with long bodies explaining
the why — v6.12.0 contains *"An owned window is not a window Alt+Tab can see"*, *"Closing a panel
should not be a one-way door"*, *"The live bridge was never hidden — we were looking on the wrong
plane"*, at ~400 words of body each. Better release-note material than most projects write
deliberately. So `git log <prev-tag>..<tag>` is the input, and the bot's job is **selection and
translation, not invention**.

**The part that is actually new** is closing the loop. The observation log accumulates clustered
problems; a release is a set of changes; matching one against the other produces the post no
changelog can:

> Three of you hit windows disappearing behind gBuilder on Windows in the last two weeks. 6.12.0
> fixes it — labs are now real windows, and there is a Window menu if one goes behind.
> `pipx upgrade gini-toolkit`.

That is only possible because one engine saw both sides, and it is the reason to build this as a
single entity rather than a bot beside a chatbot.

**FAQs on a threshold:** a question becomes an FAQ when asked by *N* distinct hashes AND a `strong`
grounded answer exists. Never on one asking, never without grounding.

## 7. Build order

Ordered so each step is useful alone, and the riskiest thing is proven before the largest is paid
for.

1. **Read-only bot, no reasoning.** Ingest to the observation log, post nothing. Run two weeks and
   read what accumulates — that alone answers "what are the pressing issues", and costs nothing to
   abandon.
2. **Release announcer, human-gated.** Commits between tags → draft in a staff channel → posted on a
   click. No retrieval, no model risk in public, and it delivers the feature asked for first.
3. **Split the Qt-free agent core** (§5.2). Nothing above needs it; everything below does.
4. **Answer questions at `strong` only.** Index the manual first — it was written for this.
5. **Cluster, then FAQ.** Recurrence thresholds, and the problem-to-release matching from §6.
6. **Bring `ai.proxy` home.** The parked feature — "letting the tutor answer on your behalf when you
   are away" (`app/features.py`) — *is* this system on someone else's transport. Unparking it in
   gBuilder becomes a small change rather than a new idea.

## 8. Why this is worth it

Today the tutor is confined to one student's canvas and forgets everything when the window closes.
Every insight is discarded and the same question is answered from scratch a hundred times across a
class. The bot is not really a chat interface: it is the first thing that gives GINI AI **a memory
of the class**, and a way to tell students that something they complained about has been fixed. That
is why the reasoning belongs in the Teaching Center — it is the only component that already sees a
whole course.

---

### Related

`app/features.py` (`ai.proxy`, `messaging` — the parked endpoints are part of the spec) ·
`agent/notifier.py` · `agent/ask.py` · `docs/manual/README.md` ·
`teaching-center/src/gini_teaching_center/{server,search,activities}.py`
