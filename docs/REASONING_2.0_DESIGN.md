# Reasoning 2.0 — The Deterministic Reasoning Twin

**Status:** phases A–E IMPLEMENTED (2026-08-13, `agent/twin/`; feature-flag `Settings.twin_enabled`,
default off; D consumes the learner model via a stub-tested seam pending the parallel track;
the coach surface has the feed-forward half — see `twin/os_coach.py` docstring for the deferred
audit half). Cumulative Mac validation pending. · **Owner:** Mahesh · **Scope:** GINI's LLM
reasoning subsystem
**Companion docs:** `GINI Project/SHADOW_FRAMEWORK.md` (the kernel shadow framework — a different
"shadow"; unrelated mechanism, similar philosophy), `ASK_GINI_AGENT_DESIGN.md`,
`ASK_GINI_HYBRID_RETRIEVAL_DESIGN.md`.

---

## 1. Motivation

GINI engages an LLM as its reasoning engine across seven surfaces (Ask GINI, the Mission
game-master, Wizard, Coach, lesson resolution/authoring, narration, retrieval expansion). The
architecture is deliberately built around a **small local model** with a hard invariant:

> A deterministic oracle produces every verdict; the LLM interprets intent, ranks relevance,
> writes prose, and coaches — it is never the judge.

This gives GINI robustness most AI-education tools lack (no hallucinated verdicts, full offline
degradation). But an audit of the reasoning subsystem (2026-08) found the remaining weaknesses
cluster around what the deterministic layer does NOT yet do:

1. **Verification is contradiction-only.** `Critic.verify_claims` catches a claim that
   *contradicts* a blackboard verdict; an ungrounded-but-plausible claim with no matching verdict
   passes silently. The LLM critic fails *open* on a parse error. The mission loop allows exactly
   one revision — if the revised line is still wrong, it ships.
2. **Recall is best-effort.** Retrieval (lexical → LLM-expand → embeddings) improves what the
   model *sees*, but nothing guarantees the model *addresses* what matters. A small model can be
   handed the right context and still silently miss the main point.
3. **Reasoning depth is single-shot.** Most surfaces are one prompt → one response. There is no
   mechanism that asks the model "why not X?" or "is it OK to leave Y out?".
4. **No coverage concept exists.** GINI can check what the model *said*; it cannot check what the
   model *failed to say*.

**Reasoning 2.0 closes this by adding a Reasoning Twin: a deterministic (non-LLM) reasoning
system that runs in the background, twins each structured reasoning turn, and interrogates the
LLM — "why not…?", "how about…?", "is it OK to leave this out?" — never reasoning independently
to the user, but guaranteeing the LLM-based reasoner catches the main points.**

The key insight that makes this feasible in GINI and nearly nowhere else: GINI already owns a
rich symbolic substrate — the connection grammar, objective predicates + `domain/explain.py`
(which already generates the concrete counterexample for every red objective), blackboard
verdicts, `StateWatcher` pedagogical events, composition `requires→provides` closure, and the
probes oracle. Today that substrate checks LLM *outputs*. The Twin evolves it into a proactive
check on LLM *reasoning coverage*.

## 2. Position in the architecture

```
                        ┌────────────────────────────┐
                        │   symbolic substrate        │
                        │  grammar · objectives ·     │
                        │  explain · blackboard ·     │
                        │  watcher · composition ·    │
                        │  (learner model — future)   │
                        └──────┬──────────────┬──────┘
                               │              │
                     enumerate │              │ adjudicate
                               ▼              ▼
   student ──► LLM reasoner ──► answer + coverage object
                   ▲                   │
                   │    REASONING TWIN │  (deterministic)
                   │                   ▼
                   │      1. concern set (complete, salience-gated)
                   │      2. coverage diff (exact set-diff, no NLP)
                   │      3. objections: "why not X?" "OK to omit Y?"
                   └──────4. justification → validated against ground truth
                          5. undefeated objection → revise (bounded) or flag
```

Two directions of the same machinery:

- **Twin-as-context (feed-forward):** the concern set is injected into the LLM's prompt up front
  — *guaranteed recall* of the points that matter (advances priority #4).
- **Twin-as-critic (feed-back):** the coverage diff + dialectic audits the answer
  (advances #3, #2).

The Twin is a **challenger, not a judge**. Verdicts still come only from the existing oracle.
The Twin's authority is limited to: injecting concerns, posing objections, validating
justifications against ground truth, and triggering bounded revision or a visible flag.

## 3. Core contracts (new module: `agent/twin/`)

All pure Python, no Qt, LLM used only where marked. Mirrors the style of `agent/contracts.py`.

### 3.1 Concern

The unit of "a point that matters this turn."

```python
@dataclass(frozen=True)
class Concern:
    id: str            # stable, e.g. "objective:web-reach", "watcher:starvation:4"
    kind: str          # objective | legality | grammar-option | watcher-event |
                       # composition-gap | misconception (future) | recipe-alt
    statement: str     # human-readable: "pid 4 has been RUNNABLE for 9 slices without running"
    evidence: str      # the deterministic ground fact (from explain/blackboard/watcher)
    salience: int      # 0..3, from the notifier-style rules table; ≥2 = must-address
    source: str        # which substrate produced it (for eval/debugging)
```

### 3.2 ConcernSet enumeration

`enumerate_concerns(surface, world) -> list[Concern]` — per surface, fully deterministic:

| surface        | concern sources                                                              |
|----------------|-------------------------------------------------------------------------------|
| mission GM     | red objectives (+ `explain.py` counterexample), legality verdicts, off-task,  |
|                | flipped verdicts this turn, forbidden elements near-miss                       |
| OS coach       | active `StateWatcher` events (starvation/monopoly/zombie/idle), sched-flag     |
|                | badges, policy/quantum deltas, shadow manifest state (faulted shadow etc.)     |
| compose/author | unfilled `requires`, deterministic exclusion scan hits, infeasibility,        |
|                | strong-lexical-vs-model-pick disagreement                                      |
| ask (scoped)   | red objectives if a mission is live; canvas lint issues; isolated devices      |

Salience reuses the notifier philosophy: a **rules-only table** (`twin/salience.py`), never a
model. Caps: max N concerns per turn (default 5), must-address = salience ≥ 2 only.

### 3.3 Coverage

The LLM's reasoning surfaces already emit structured output (JSON moves, picks, hints). Each
twinned surface adds one field to its output contract:

```json
{ "...existing move fields...",
  "coverage": {
    "addressed": ["objective:web-reach"],
    "omitted":   [{"id": "watcher:starvation:4",
                   "why": "the student asked about lottery, starvation is priority-mode"}]
  }
}
```

Rules: the model must list every must-address concern in either `addressed` or `omitted`; an
omission requires a one-line justification. Parsing is tolerant (same `first_json` style as
`personas.py`); a missing/unparsable coverage object is treated as **coverage-silent** (see 3.5)
— it must not crash the turn.

### 3.4 The coverage check + dialectic

`twin/dialectic.py` — the loop, all deterministic control flow:

1. **Diff** (exact, no NLP): `missing = {must-address} − {addressed ∪ omitted}`.
2. **Adjudicate omissions**: for each claimed omission, validate the justification against
   ground truth — *deterministically where possible* (see 3.6), via one focused LLM
   interpretation call only when the justification needs mapping onto a checkable predicate.
3. **Objection**: for each `missing` concern and each omission whose justification failed,
   emit an `Objection(concern, question)` — the question is template-generated from the concern
   kind: *"You did not address {statement}. Why not?"* / *"Is it OK to leave {statement}
   unaddressed while {evidence}?"* / *"How about {alternative}?"*
4. **Revision**: objections are appended to the persona's revision note (the existing
   `react(note=…)` mechanism) → the model revises. Bounded: `max_rounds` (default 2, vs. the
   current hard-coded 1), and an objection that survives all rounds becomes a **flag**, not a
   silent ship.
5. **Flag**: a surviving objection is surfaced honestly — appended to the move as a one-liner
   ("Coach note: also worth looking at: pid 4 has been waiting 9 slices") or logged for the
   instructor, per-surface configuration. The Twin never suppresses the LLM's answer; it
   annotates or triggers revision.

### 3.5 Failure posture (explicit)

- Coverage object missing/malformed → **coverage-silent**: Twin skips the diff, runs objections
  only for salience-3 concerns, and records the silence (metric). Never blocks the turn.
- The Twin itself erroring → the turn proceeds exactly as today (the Twin is strictly additive;
  its absence = current behavior). Every Twin step is wrapped and budgeted.
- No LLM at all → the Twin's concern set still renders deterministically (it *is* the offline
  fallback content: "things worth looking at: …"). This strengthens, not weakens, offline mode.

### 3.6 Justification validation — the crux

The Twin must never take the model's word for an omission. Validation is a small library of
checkable justification patterns (`twin/justify.py`), mapped per concern kind:

- *scope justification* ("student asked about X, concern is Y-mode") → check: concern's kind or
  mode tag ≠ the intent topic (deterministic — intent already parsed by `understand`).
- *already-addressed justification* ("covered in the previous hint") → check: concern id in the
  session/mission memory's addressed history (deterministic).
- *state justification* ("not needed — single subnet") → map to a predicate and evaluate against
  the world (`objectives`/`TopologyWorld`/kernel card). One focused LLM call may be used to
  *translate* the free-text justification into a predicate from a whitelist; the *evaluation* is
  deterministic. Untranslatable → justification unvalidated → objection stands.
- *pedagogical justification* ("would give away the answer") → allowed for coach surfaces by
  policy (rules table), since Socratic withholding is legitimate; logged.

An objection is **defeated** only by a validated justification. This is a defeasible-reasoning
(argumentation) structure: LLM answer accepted unless an undefeated, grounded objection exists.

## 4. Logic forms (deliberately layered, all reusing existing code)

1. **Enumerative/consequence layer** — Datalog-ish derivation over blackboard verdicts, grammar
   adjacency, watcher state, composition closure. Implementation: plain Python over existing
   modules (no new engine dependency; the substrate is small).
2. **Defeasible/argumentation layer** — the objection/justification/defeat loop of §3.4–3.6.
   Implementation: explicit state machine, not a solver; the argument graph per turn is tiny
   (≤5 concerns × ≤2 rounds).
3. **Case-based layer** (later phase) — "how about" alternatives from the recipe/fragment
   library: a deterministic comparator that spots when the student's structure is ε-close to a
   known-good recipe and surfaces the delta as a concern (`recipe-alt`).

The LLM's only roles inside the Twin: translating a free-text justification onto the predicate
whitelist (3.6) and, of course, being the party interrogated. All enumeration, diffing,
salience, adjudication control-flow, and defeat rules are deterministic and unit-testable with
golden fixtures.

## 5. Surface integration plan

### First surface: the Mission game-master (decided rationale — richest substrate)

- Concern sources are already computed every turn (blackboard verdicts, `explain` whys,
  legality) — enumeration is a projection, not new sensing.
- The persona stack already has the revision mechanism (`react(note=…)`) and a deterministic
  critic (`verify_claims`) to compose with: the Twin *subsumes* `verify_claims` (contradiction =
  a special case of a failed justification) but keeps it running as the safety floor.
- Wiring point: `MissionAgent.turn()` (`agent/meaning.py`) — after Reasoning, before ship:
  `twin.review(move, concerns) -> (move', flags)`.

### Second: the OS Coach

- Concerns: StateWatcher events + scheduling flags + kernel-card facts + shadow manifest.
- Special handling: the coach *deliberately* withholds (Socratic) — pedagogical justification is
  auto-granted for "don't reveal the answer" omissions; the Twin's job here is "did the hint
  target the most salient event?" not "did it explain everything."
- The Twin's concern set ALSO becomes the deterministic no-model fallback text (better than the
  current raw event list).

### Third: compose/authoring

- Concerns: unfilled requires, exclusion-scan hits, lexical-vs-model disagreement,
  infeasibility. The Twin's questions here run at *authoring* time (teacher-facing): "the model
  picked archetype X but the words strongly match Y — why not Y?" surfaces in the ratify UI.

### Explicitly out of scope

Free-form open Q&A (chit-chat, general explain with empty retrieval): the concern set is thin
and the Twin would be noise. `ask` gets the Twin only when a mission is live or lint issues
exist (scoped mode).

## 6. Phases

| phase | deliverable | notes |
|-------|-------------|-------|
| **R2.0-A** | Twin skeleton on the Mission GM: `agent/twin/` (contracts, salience rules, mission enumerator), coverage field in the Reasoning persona's output, exact diff, ONE objection round, flags. Golden-fixture tests. **Prerequisite: Ollama structured outputs (`schema=` on `OllamaBackend.chat`, see §10) so the coverage object is decoder-guaranteed.** | Prove the loop end-to-end. Feature-flagged (`Settings.twin_enabled`). |
| **R2.0-B** | Full dialectic: justification validation library (§3.6), defeat rules, bounded multi-round (2), Twin-as-context injection (concern set into the prompt up front), metrics (coverage-silence rate, objection rate, defeat rate). | Subsumes `verify_claims`; keep it as floor. |
| **R2.0-C** | OS Coach + compose surfaces; deterministic-fallback upgrade; ratify-UI surfacing for authoring objections. | |
| **R2.0-D** | Learner model as a concern source (separate track, consumed here): persistent per-student misconception/level store feeding `misconception` concerns. | Learner model design = its own doc. |
| **R2.0-E** | Reasoning eval harness (#8): golden turns with known concern sets + expected coverage; replay CI; prompt/model regression detection. Measures whether the Twin materially improves reasoning. | The harness tests the TWIN deterministically and the LLM statistically. |

Priorities mapping (user-ranked): #3 verification → A/B; #4 recall → B (twin-as-context);
#5 learner → D; #2 depth → B (bounded dialectic); #6 pedagogy → C (coach surface);
#7 generative → out of scope here (separate "Generative GINI" track; the Twin's adjudicator is
a prerequisite it will reuse); #8 eval → E; #1 model tiering → orthogonal, deferred.

## 7. Guardrails against the known failure modes

- **Noise:** hard caps (≤5 concerns, must-address = salience ≥2), rules-only salience, scoped
  surfaces. The Twin whispers; it does not checklist.
- **Rigidity:** coverage is *addressed-or-justified-omission*, never "recite everything." A
  validated pedagogical omission is a first-class success path.
- **Latency:** objections batched into ONE revision note per round; ≤2 rounds; Twin runs on the
  existing worker threads; per-turn time budget (default 2 rounds or 4s, whichever first) after
  which surviving objections downgrade to flags.
- **Gaming the coverage report:** the report is only an index for the diff; omission
  justifications are validated against ground truth (§3.6). Claiming `addressed` falsely is
  caught statistically by the eval harness (E) and — for claims with verdict subjects — by the
  retained `verify_claims` floor.
- **Twin wrongness:** every concern carries `evidence` from the substrate; a concern without
  checkable evidence is not emitted. The Twin can only cite what GINI can prove.

## 8. What this deliberately does NOT change

- The oracle stays the only judge (verdicts, grading, completion).
- The one-small-model architecture stays; the Twin reduces dependence on model quality rather
  than requiring a bigger model. (Model tiering remains a separate, orthogonal option.)
- Offline mode stays fully functional — strengthened, since concern sets render without a model.
- Existing guardrails (narration false-claims, exclusion scanning, persona isolation,
  measured-help ledger) remain untouched underneath.

## 9. Open decisions for review

1. **Flag surfacing UX** — surviving objections: append to the tutor line, a separate quiet
   "also worth a look" chip, or instructor-log only? (Proposal: per-surface; coach=chip,
   GM=append, authoring=ratify-UI.)
2. **Twin-as-context default** — inject the concern set into the prompt from day one (B), or
   only after A proves the audit loop? (Proposal: A audits only; B injects.)
3. **Learner model track** — confirmed separate design doc + parallel track consumed at D?
4. **`verify_claims` subsumption** — keep both permanently (belt and braces) or retire the old
   path once the Twin's adjudicator covers contradiction? (Proposal: keep permanently; it is
   ~50 lines and fails closed.)

## 10. Dependencies — build vs. buy (evaluated 2026-08)

Evaluation lens: GINI is a desktop app on student laptops, offline-degradable, stdlib-lean
(`OllamaBackend` is plain urllib), deterministically testable; the Twin's workload is tiny
(≤5 concerns, ≤2 rounds). A library earns its place only if it solves a problem we actually
have, at our scale, without breaking offline/determinism/deployability.

- **SymbolicAI (library): rejected.** Its symbolic operations are semantically evaluated BY the
  LLM — the inverse of the Twin, whose value is being non-LLM. Building the checker from
  model-backed primitives dissolves the guarantee.
- **Logic engines (pyDatalog / clingo-ASP / Z3, the category): not yet — door kept open.**
  Solvers pay off at combinatorial scale; the Twin's derivations are small projections over
  Python objects, and the defeat loop is a two-round state machine. Encoding/decoding would
  exceed the reasoning itself and hurt golden-fixture debuggability. Also: the justification
  validator must reuse GINI's existing predicate semantics (`objectives.py` AST whitelist), not
  introduce a second semantics that could disagree with the oracle. **Trigger to revisit:** if
  R2.0-D learner-model rules create chained inference (misconception × curriculum × domain)
  that hand-written derivations can't keep provably correct, adopt a mini-Datalog BEHIND the
  `enumerate_concerns()` seam — the dialectic is untouched.
- **Temporal.io: rejected.** Durable workflow orchestration (server + DB + workers) for
  distributed long-running pipelines. The dialectic is in-process, <4s, and deliberately
  ephemeral — replaying a stale dialectic against changed live state is a bug, not a feature.
  A student laptop cannot host a Temporal server. Even future Teaching-Center re-grade queues
  are plain-job-queue-shaped.
- **Guidance / Outlines / lm-format-enforcer: library rejected, capability ADOPTED.**
  Constrained generation fixes the Twin's weakest joint — reliance on small models emitting
  parseable JSON (today: tolerant `first_json` everywhere, critic fails OPEN on parse error,
  coverage-silent fallback). But these libraries want in-process decoder control, displacing
  the thin HTTP backend. The same guarantee is available with zero dependencies:
  **Ollama's native structured outputs** (a JSON schema in the `format` field of `/api/chat`).

**Amendment to §3.3/§3.5 (structured outputs):** `OllamaBackend.chat` gains an optional
`schema=` parameter (~20 lines), plumbed through `PersonaRunner` for every structured persona
(coverage object, classifier, critic, understand-refine, resolver/compose JSON). Free-prose
surfaces stay unconstrained. Effects: coverage-silence becomes a rare transport error rather
than a routine small-model failure; the critic's fail-open parse path closes; all `first_json`
tolerance layers demote to fallbacks (kept — other backends may not support schemas).
Added to R2.0-A as a prerequisite task.

## 11. Test strategy

- Golden fixtures: (world state, move, coverage) triples with expected concern sets, diffs,
  objections, and defeat outcomes — pure, deterministic, CI-fast.
- Property tests: every emitted concern has evidence; no concern without substrate backing;
  objection count ≤ caps; Twin-disabled turn ≡ current behavior byte-for-byte.
- Statistical (E): replay a bank of recorded turns with/without the Twin; measure addressed-rate
  of must-address concerns, false-objection rate, latency.
