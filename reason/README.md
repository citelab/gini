# GINI AI — the reason service

Step 2 of [`docs/design/gini-ai-three-doors.md`](../docs/design/gini-ai-three-doors.md). It drafts
answers and **posts nothing to anybody**. Whether a draft reaches a student, a staff channel, or the
bin is the Teaching Center's decision and depends on which door asked — and this process
deliberately has no idea which door that was.

## Reading what it would say

The way step 2 is meant to be used first: against real questions out of the observation log, before
any student can reach it.

```bash
export PYTHONPATH=/path/to/gini/core/src:/path/to/gini/frontend-ng/src:/path/to/gini/reason
python3 -m gini_reason ask "why is my process stuck in the scheduler"
```

```
[L1] strength=thin model=no

The course has material on this: os-02-scheduler.md — Scheduler face. I'm not confident
enough to answer it directly.

from:
  · os-02-scheduler.md — Scheduler face — gini_pick, policies, quantum, Gantt
```

With a model attached it climbs to `[L2]` and the audit runs. Without one it stays on the lower
rungs — which is the outage plan, not a degraded mode.

## Running it

```bash
export GINI_LLM_URL=http://127.0.0.1:11434   # optional — this is already the default
export GINI_LLM_MODEL=gemma3:4b              # NOT optional: it must be a model the server has
python3 -m gini_reason                       # 127.0.0.1:8765
curl -s localhost:8765/health
```

`GINI_LLM_MODEL` is the one that bites. The server answering is not the model existing: Ollama's
`/api/tags` replies happily whatever you ask for, so a wrong name passes every check and then fails
at the first question with a bare `HTTP Error 404: Not Found` — which names nothing, and reads as
the tunnel having dropped. The service checks the name against the server's list and, when it is
wrong, says so and prints what the server actually serves.

`ask` says which rung it stopped on and why:

```
[L1] strength=thin (0.39)  model not used
      model: gemma3:4b at http://127.0.0.1:11434 (from the default)
      grounding 0.39 is below 0.45, so the model was not asked (GINI_REASON_STRONG overrides)
```

Those are two different facts and the first version ran them together into `model=no`: *is there a
model* and *was it used*. A live tunnel with thin grounding answers yes and no, which looks exactly
like a broken connection.

`GINI_REASON_STRONG` moves the bar L2 needs. It is borrowed from `agent/recall.py`'s scale and
measured on ten plausible questions — six reach `strong` — but it is borrowed, which is the move
that made `MIN_SHARED` wrong, so it is worth checking against your own questions before trusting.

**Loopback only, and no authentication.** It has no user: the only thing that may call it is the
Center on the same host. A service like this on a public interface would be an unauthenticated
model endpoint on a university network.

## The ladder

| | | |
|---|---|---|
| **L0** | the answer bank | decided by the Center before this is called |
| **L1** | retrieval + a confidence | the manual, concepts, recipes, the course's own hits |
| **L2** | the model, grounded | composed from L1 only, then **audited** |
| **L3** | a person | the honest refusal, with what *is* known attached |

**L2 is not gated on a threshold.** `strength == strong` says the knowledge base covered the
question; it says nothing about whether the answer used it. `audit.py` is that check, and the hole
it closes is named in this tree by `agent/twin/course.py`: *"Nothing then checks whether the model
used it… The material was DELIVERED and nobody was ANSWERABLE for it."*

Three things it can prove, with no second model call: the answer cites none of the pages it was
given; the student's own course material went unmentioned; an answer strayed outside a limit the
page states. Every objection carries deterministic evidence, because the Twin may only cite what
GINI can prove.

The full dialectic — a schema-constrained coverage report, the exact set diff, adjudication through
`twin/justify.py` — is the next step. What is here is its deterministic half.

## The manual, finally read

`manual.py` indexes `docs/manual/` — 17 pages whose README says they were *"written to be indexable
by GINI AI"*, with `keywords`, `endpoints` and `kernel_files` on every one. Nothing had ever opened
them. Matching runs against the frontmatter rather than the prose: a page's body mentions everything
it touches, so scoring the whole text makes every page match every question.

`limits()` reads each page's required **"Limits and honesty"** section. Those bullets are the tree's
own record of what must not be claimed, and they are what the audit's objections stand on.

## Why there is no `pyproject.toml`

Same reason as `bot/` — one would make this a fourth distribution and `test_packaging.py` would
then, correctly, demand a publish workflow and a place in every release. It runs from a checkout on
the Center's host.

It also does not need one yet for a subtler reason: the agent modules it imports live in
`gini-toolkit`, which *requires* PySide6 — but every one of them imports with PySide6 absent, so a
checkout on `PYTHONPATH` works today. Packaging is what would force the Qt split, and packaging is
what is being deferred.
