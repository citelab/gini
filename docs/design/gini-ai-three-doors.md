# Three doors to GINI AI — the model, and what each surface may say

**Status: proposal. Nothing here is implemented.** Written 2026-09-13, after
[`gini-ai-discord.md`](gini-ai-discord.md), whose steps 1 and 2 are built.
Rendered: <https://claude.ai/code/artifact/2d8bbe44-4d3a-4d55-8473-9c2c8fa7ef2d>

The intent, from the maintainer: an LLM is now reachable from the Teaching Center host
(`citelab-1.cs.mcgill.ca`, an RTX 3080, over a tunnel). Integrate the bot, the model and GINI AI;
publish release notes and announcements; let students query GINI AI and get an answer immediately;
and bring gBuilder's **Chat** into it too — *"Chat access is private. No one else sees that message.
Discord is public."*

## 1. The thesis

**One engine, three doors, and the door decides how much the model may say.** A wrong answer to one
student in private costs a follow-up question. The same answer posted to the class, in the course's
name, permanently, has no follow-up. The asymmetry the maintainer named is the design.

Everything here keeps working with the GPU off — not as a fallback, but because the ordering puts
the deterministic rungs first.

## 2. What changed

- **A model exists**, reachable as if local from the Center's host.
- **The seam already exists.** `ui/assistant.py:2276` already calls `tc_ask.ask(url, course,
  question)` against the Center's `/api/ask` and folds the result into its context. Routing Chat
  through GINI AI extends a call gBuilder already makes.
- **The corpus is collected.** A term of real questions, grouped, in `observation`.

## 3. The ladder

Tried in order. The model is third, never first, and composes only from what the rungs above found.

| | | |
|---|---|---|
| **L0** | the teacher's answer bank | **built.** Exact words, term containment, no model. Stops here on a match. |
| **L1** | retrieval + a confidence | **built, unused.** `agent/recall.py` over the manual, concepts, recipes and `search.py`. Returns `strength ∈ {strong, thin, empty}`. |
| **L2** | the model, grounded | composes *from what L1 retrieved*, never from its weights — the shape `agent/reasoning.py` was built for. Runs only at `strong`, **and is audited by the Reasoning Twin before it counts as an answer — see §4a.** |
| **L3** | a person | at `thin`/`empty`: say what *is* known and tag a human. Not a hedge — a bot that says "ask a TA" is noise, and it teaches people to ignore the bot. |

The ladder is also the outage plan. GPU off: L0 and L1 still answer, the console still works, the
bot still collects.

## 4. The three doors

- **gBuilder Chat** — private, one student. All four rungs, immediately. Nobody else sees it, the
  student can push back, and this is how Ask GINI already behaves.
- **Discord** — public, permanent, in the course's name. Immediate answers from **L0 only**. L2
  answers are **drafted to the staff channel** and posted on a click.
- **The console** — staff. Sees everything including Chat questions. Where a draft becomes a post,
  an answer becomes a bank entry, and a release note gets published.

It decays the right way: every approved draft can become a bank entry in one click, so the *public*
door improves without the model ever being trusted in public.

## 4a. The Reasoning Twin is the L2 gate

`strength == strong` is a statement about RETRIEVAL, not about the answer. It says the knowledge
base covered the question; it says nothing about whether the model used what it was handed. That
hole is already named in this tree, one layer down, by the module written to close it:

> gBuilder already asks the course server what it holds on a question and pastes the answer into
> the prompt as a few lines of context. **Nothing then checks whether the model used it.** A student
> asks about connecting two LANs, their course has a released activity called "Multi-LAN routing"
> that is the very lab they are recording, and the tutor may answer out of general knowledge and
> never mention it — with no trace anywhere that it did. The material was DELIVERED and nobody was
> ANSWERABLE for it.
> — `agent/twin/course.py`

The same sentence describes GINI AI with L1 in place of `tc_ask`. So L2's gate is not a threshold,
it is the dialectic: enumerate the concerns the retrieval produced, require the model to report
coverage against them, diff EXACTLY (a set diff, no NLP), adjudicate each claimed omission against
ground truth, object to silent misses, allow one bounded revision, and turn what survives into a
visible flag — never a silent ship, never suppression.

**All of it runs headless.** `twin.{contracts,dialectic,justify,salience,course,harness}` import
with PySide6 blocked — checked, not assumed — so the reason service uses them as they are.

### What transfers, and what does not

| | |
|---|---|
| `contracts.py` | Concern / Coverage / Objection. A concern is only ever emitted with deterministic evidence: **the Twin can only cite what GINI can prove.** |
| `dialectic.py` | The exact diff, the bounded revision, the flag. |
| `justify.py` | An objection is defeated only by a *validated* justification — five checkable patterns, ground truth as referee. |
| `salience.py` | `MAX_CONCERNS = 5`, `MUST_ADDRESS = 2` — "the Twin whispers, it does not checklist". |
| `harness.py` | Golden turns and a false-objection rate: a regression gate for prompt and model changes, **before** anything is pointed at a class. |

What does *not* transfer is five of the six concern sources. They read a live canvas — unmet
objectives, legality flags, watcher events, authoring gaps, the learner model — and a Discord
question has no canvas. **`course.py` is the one that carries over directly**, and it is the one
built on `tc_ask` hits, which is exactly what L1 produces.

### The two sources GINI AI has to add

- **The answer bank.** If the teacher has banked something adjacent, the model must account for it.
  High salience by construction: the course already settled this, and answering around it is worse
  than not answering.
- **The manual's own limits.** Every page in `docs/manual/` carries a required *"Limits and
  honesty"* heading. That is a ready-made source of what must not be claimed — an answer asserting
  something a page lists as a limit is a silent miss with deterministic evidence behind it.

### What it buys the public door

A draft reaching the staff channel arrives **annotated**: *did not mention the course's own lab on
this*, *claimed X, which os-06 lists as a limit*. A teacher approving a draft sees what the auditor
objected to instead of re-deriving it — which is the difference between reviewing and rubber-
stamping, and it is the whole reason the public door can eventually be trusted at all.

**Stated plainly:** the Twin is Phase A and feature-flagged off (`Settings.twin_enabled`). Phases
B–E of `docs/REASONING_2.0_DESIGN.md` are open and `twin/learner.py` duck-types against a shape
nothing yet produces. It is real, tested code — not battle-worn code. Using it here is the first
time it would face traffic, and the harness is what makes that a measurable risk rather than a
hopeful one.

## 5. Where the model runs

A separate process on the Center's host, talking to the tunnel. The Center calls it over loopback
with a short timeout and falls back down the ladder. The submission path never touches it, so
`server.py`'s "**No AI**" sentence stays true *of the server* — see §5.1 of the previous design.

**The Qt split is not needed yet.** §5.2 called it the largest piece of work; it is, but only to
*package* the agent core. Run from the checkout with `PYTHONPATH=core/src:frontend-ng/src`, every
reasoning module imports with PySide6 absent — measured before any of this was proposed. So the
service ships like `bot/` does, and the distribution split happens when it has earned one.

## 6. Chat through the Center

Today Ask GINI points at `GINI_LLM_URL` on the student's own machine, and almost no student has a
model there — so for most of the class the tutor has always run degraded. Pointing it at the Center
gives **one GPU for everybody**, with no new UI, and carries the teacher's answer bank into Chat.

The quieter win: a question asked privately still joins the anonymous corpus, so **the students who
never post appear in the clusters**, and "what is this class stuck on" stops measuring who is
willing to ask in public.

### The student chooses who is in the conversation

**Decided (2026-09-13), refining the above.** A Settings option in gBuilder, and it is the better
answer than recording everything and disclosing it:

- **GINI AI only** — the ladder answers, and the question is *not* recorded for staff. Private in
  the way the word normally means.
- **GINI AI and my instructor** — the ladder answers, *and* the question reaches the console, where
  a teacher can see it, see what it clusters with, and reply. The reply comes back into the
  student's own Chat.

The second mode is worth having as more than a permission: it is the quiet student's route to a
teacher. Asking in Discord means asking in front of everybody, and the students who will not do
that are exactly the ones nobody hears from. This gives them a private line that a human can
actually answer.

**Consequences to build.** A chat observation needs a way back to the session, the way a Discord
one needs a way back to the message — the same expiring reference, pointed at a Chat session rather
than a channel. And `reply_outbox` grows a destination, because a queued reply now goes to Discord
or to Chat.

**Default: shared, said plainly.** A Center with an empty console helps nobody, and the content is
anonymous by the time it lands. But it is a student-facing default that shares, so gBuilder states
it where they will read it rather than in a settings page they will not, and turning it off costs
them nothing — the ladder still answers.

**Decided earlier the same day: Chat routes through the Center, and what it records is visible to
the teaching staff only.** Same discipline as everything else — hashed author, no handle, no way back —
and gBuilder says so before a student types: *"Answers come from your course. Questions are
recorded, without your name, so your instructor can see what the class is stuck on."*

**The rule that decision creates.** "Staff only" is an access property, and clustering quietly
threatens it: a private Chat question joins a group, the group becomes an FAQ, and the FAQ is posted
to Discord in somebody's own words. Nobody would have decided that; it would simply happen.

So the SOURCE travels with the observation — `chat` or `discord` — and publication is gated on it.
A chat-sourced message counts toward every number a teacher sees, and **none of its words may ever
be quoted into a public channel**. A cluster mixing both is published from the Discord phrasing or
not at all. The teacher's own reply is unaffected: those are their words, and they choose to post
them.

## 7. Release notes and announcements

Still no `CHANGELOG` and no GitHub releases; commit messages are the source, and the model's job is
**selection and translation, not invention**. Then the part no changelog can do — match a release
against what the observation log says people actually hit:

> Three of you hit gBuilder core-dumping on the lab machines in the last two weeks. 6.12.1 ships
> the check that names the missing library instead of aborting. `pipx upgrade gini-toolkit`

Announcements are the same pipeline without the model. Both reuse the reply outbox. **Neither ever
auto-publishes** — a release note is the course speaking to every student at once.

## 8. Build order

1. **Store the answer bank and wire it into the tab.** L0 end to end, no GPU — the rung that answers
   in public gets a voice before the model has one.
2. **The reason service, answering nobody.** Wrap `agent/`, point it at the tunnel, one call. Read
   what it says from the console, against real logged questions, before anyone else can.
3. **Chat through the Center.** The private door, all four rungs, with the disclosure in gBuilder.
4. **Drafts in the console.** L2 answers for Discord questions; one click to send, one to bank.
5. **Release notes and announcements**, matched against what people actually hit.
