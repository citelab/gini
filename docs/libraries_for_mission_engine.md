# External Libraries for the Mission Engine — an Assessment Carried Over from GINI

**Audience:** the session/agent working on the Mission Engine (ME).
**Author context:** written from inside the GINI codebase after (a) a full audit of GINI's LLM
reasoning subsystem, (b) the Reasoning 2.0 (deterministic Reasoning Twin) design
(`docs/REASONING_2.0_DESIGN.md`), and (c) a build-vs-buy evaluation of SymbolicAI, Temporal.io,
and Guidance for GINI (§10 of that doc).
**Relationship:** ME is an enterprise offshoot of GINI built on the same core invariant — **the
LLM is never the judge; a deterministic oracle owns every verdict.** GINI serves as the proving
ground for reasoning ideas that graduate to ME.

**How to read this:** the GINI verdicts were driven by GINI's constraints (single-user desktop
app, offline-first, stdlib-lean, tiny workloads). ME's constraints are presumed to be roughly
opposite (server-side, multi-tenant, durable, at scale, compliance-bearing). **Several GINI
rejections therefore INVERT for ME.** Each section states which rationale carries over, which
flips, and what I'd recommend the ME team actually do. Where I assume something about ME's
architecture, the assumption is stated explicitly — correct it and re-derive.

---

## 1. The evaluation lens, and why it flips

| dimension | GINI | ME (assumed) |
|---|---|---|
| deployment | student laptop, desktop app | servers / cloud, service architecture |
| availability | must degrade fully offline | HA expected; offline mostly irrelevant |
| duration of a "reasoning episode" | seconds, ephemeral by design | potentially hours–weeks (enterprise missions, approvals, humans in the loop) |
| state on crash | correct to lose (re-derive from live state) | usually must survive (auditability, resumability) |
| scale | one user, ≤5 concerns, 2 dialectic rounds | many tenants, many concurrent missions, possibly large rule sets |
| dependency posture | stdlib-lean, every dep ships to a laptop | normal enterprise dependency budget |
| audit/compliance | instructor-visible logs (light) | evidence trails, retention, tamper-resistance (heavy) |
| model | one small local model (Ollama) | presumably tiered: local + hosted frontier models |

Two GINI principles that must survive translation regardless of scale, because they are the
product's identity, not artifacts of its size:

1. **The oracle/LLM split is structural.** The model is never handed the grading function; the
   verdict path is deterministic code the model cannot touch. In ME this likely becomes a
   *service boundary* (a verdict service the LLM tier cannot write to) rather than a module
   boundary — stronger, and easier to certify.
2. **No LLM polices itself.** Every LLM output that matters is checked by a deterministic
   mechanism (GINI: narration false-claims scan, claim-vs-blackboard verification, the planned
   Twin). ME should keep this shape even with frontier models — a stronger model reduces the
   *frequency* of failures, not the *need* for the floor.

---

## 2. Temporal.io — REJECTED for GINI, **STRONG CANDIDATE for ME**

### What it is
Durable workflow orchestration: workflows-as-code with event-sourced state, automatic retries,
timers/timeouts that survive process death, signals for human-in-the-loop steps, versioned
workflow definitions, and a server (self-hosted or Temporal Cloud) coordinating worker fleets.

### Why GINI rejected it (and why that rationale does NOT carry over)
GINI's dialectic is in-process, sub-4-seconds, and *deliberately* ephemeral — grounding is
regenerated every turn, and replaying a stale reasoning episode against changed live state
would be a correctness bug. A desktop app also cannot host the server. **Every clause of that
rationale is GINI-specific.**

### Why ME's shape fits Temporal unusually well
An enterprise "mission" is plausibly: long-running (days+), multi-step, mixing automated
verification with human approvals, requiring resumability after failures, and demanding an
audit trail of exactly what happened when. That is Temporal's home turf. Three fits are worth
calling out precisely:

- **The oracle loop as a workflow.** GINI's mission loop (observe → verify → notify → coach)
  is a polling loop in a Qt app. In ME, `run_mission()` as a durable workflow gives you: exact
  once-per-step semantics for verdict recording, `wait_for_signal("human_approval")` for the
  human-in-the-loop steps, and a *complete event history per mission* — which is not just ops
  hygiene, it IS the enterprise audit artifact ("show me every verdict, every hint, every
  model call for mission M").
- **The determinism discipline is philosophically aligned.** Temporal *requires* workflow code
  to be deterministic (all side effects pushed into activities). That constraint maps exactly
  onto GINI's split: **workflow = the deterministic orchestration + verdict logic; activities =
  LLM calls, probes against live systems, notifications.** The framework then *enforces* at
  runtime the boundary GINI enforces by convention. This is the single strongest argument for
  Temporal in ME: the architecture invariant becomes machine-checked.
- **Retries/timeouts for flaky substrate.** Enterprise behavioral probes (hitting real customer
  systems) will be far flakier than GINI's local Docker probes; Temporal's activity retry
  policies are the right tool, versus hand-rolled retry loops.

### The honest costs
- **Operational weight**: a Temporal cluster (or Temporal Cloud spend) + worker fleets +
  the learning curve of workflow versioning (changing a workflow definition while missions are
  in flight is a real discipline, with `patch`/versioning APIs that teams routinely get wrong
  at first).
- **Don't put the dialectic INSIDE a workflow step-by-step.** The Reasoning-Twin dialectic
  (concern diff → objection → revision) is still a seconds-scale, ephemeral computation in ME.
  Model it as ONE activity ("reason about X now, against current state"), not as N workflow
  steps — otherwise you event-source rapid LLM chatter, bloat history, and recreate GINI's
  stale-replay bug at enterprise scale. Rule of thumb: **durable = the mission and its
  verdicts; ephemeral = any single reasoning episode.**
- **Alternatives to price-compare**: Restate, AWS Step Functions (if all-in on AWS),
  Inngest, or a plain outbox + job queue if ME's missions turn out to be short-lived. If
  missions are genuinely long-lived and human-gated, Temporal (or Restate) earns the weight;
  if they're minutes-scale automation, a queue may suffice. The mission *duration distribution*
  is the deciding datum — measure it before committing.

**Recommendation for ME:** adopt durable execution for the mission lifecycle, most likely
Temporal; enforce the oracle/LLM split as workflow-vs-activity; keep reasoning episodes
ephemeral inside single activities; treat per-mission event history as the audit artifact.

---

## 3. Guidance / constrained generation — capability ADOPTED in GINI, **BROADER in ME**

### What it is
Constrained decoding: the model's sampler is restricted so output *must* match a grammar/JSON
schema (Guidance, Outlines, lm-format-enforcer, llguidance; also server-native structured
outputs in Ollama, vLLM, TGI, and the hosted-API equivalents).

### GINI's verdict (carries over directly)
The *capability* is essential wherever a small/medium model must emit machine-parseable
structure (GINI: the Twin's coverage object, classifier/critic JSON). GINI adopted it with
zero dependencies via Ollama's native `format`/JSON-schema support rather than importing a
decoding framework. The principle that carries over: **prefer server-native structured outputs
at the inference boundary over an in-process decoding library** — it keeps the model tier
swappable and the app free of heavyweight inference deps.

### What changes for ME
- **Tiered models change the mechanism, not the need.** Hosted frontier APIs have their own
  structured-output/tool-schema features; local fleet inference (vLLM/TGI) has
  grammar-constrained decoding. ME should specify structure **once** (JSON Schema, versioned,
  per persona/contract) and implement it per backend — i.e. a `schema=` parameter on ME's
  backend interface, exactly the GINI amendment, generalized across tiers.
- **Enterprise contract discipline**: ME's structured outputs (verdict-adjacent claims,
  coverage objects, action requests) should be schema-versioned and validated **twice** —
  constrained at the decoder AND re-validated at the service boundary (never trust the
  inference tier's config; belt-and-braces is cheap and auditors like it).
- **When an in-process library IS justified in ME:** if ME runs its own inference fleet and
  needs constraints richer than JSON Schema (context-free grammars for DSLs, e.g. having the
  model emit *predicate expressions* in ME's oracle language), Outlines/llguidance on the
  serving tier is the right home — on the *server*, still never in the client.

**Recommendation for ME:** structured outputs everywhere a model feeds a machine-consumer;
schema-versioned contracts; server-native first; a grammar library only on a self-hosted
serving tier if DSL-level constraints emerge.

---

## 4. SymbolicAI and logic engines — the nuanced one

### SymbolicAI (the library): rejected for GINI, **still rejected for ME, same reason**
Its symbolic operations are semantically evaluated *by the LLM*. Any component whose job is to
check the model must not be built from model-backed primitives — this is invariant-level, not
scale-dependent. If ME wants a neuro-symbolic *application* layer for non-verdict features
(report drafting, semantic search over mission artifacts), SymbolicAI is defensible there; it
must simply never sit in the verdict or verification path. (Assess its maintenance
health/maturity at adoption time — it is a research-adjacent framework, and enterprise
dependency bars are higher.)

### Logic engines (Datalog / ASP / SMT): "not yet" for GINI, **plausibly YES for ME**
GINI's rejection was purely a scale argument: ≤5 concerns and a 2-round defeat loop don't
justify a solver, and hand-written Python stays debuggable. ME may cross every threshold in
that argument:

- **Rule volume and provenance.** Enterprise compliance/mission rules could number in the
  thousands, be authored by multiple teams, and require explanations ("*why* was this flagged")
  — exactly where Datalog-family engines (Soufflé; embeddable options like Mangle, or
  differential/incremental Datalog) beat hand-written derivations, and where ASP (clingo)
  pays off if rules are genuinely defeasible/conflicting (exceptions-to-exceptions,
  preference orders between policies).
- **The Reasoning Twin at enterprise scale.** The Twin's concern enumeration is a projection in
  GINI; in ME, "enumerate everything that matters about this mission state" over a large rule
  base *is* a Datalog query, and incremental evaluation (only re-derive what changed) mirrors
  the blackboard's `deps()` trick at a scale where the hand-rolled version breaks down.
- **SMT (Z3)** only if ME's oracle predicates grow arithmetic/temporal constraints ("SLA
  breached for >3h across ≥2 regions") that a graph-walk evaluator can't express — otherwise
  skip; SMT is the most debuggability-expensive of the family.

**Two hard-won cautions that carry over from GINI regardless of engine choice:**
1. **One predicate semantics.** GINI refused Z3 partly because the oracle already owns a
   predicate language, and a second evaluator that could *disagree* with it is a correctness
   hazard. In ME: if a solver is adopted, the solver becomes THE evaluator for that rule class,
   or it compiles from the same single source of truth — never two parallel semantics.
2. **Keep it behind the seam.** GINI's design isolates `enumerate_concerns()` so an engine can
   slot in later without touching the dialectic. ME should keep the same seam: dialectic and
   adjudication logic engine-agnostic; the engine is an implementation of enumeration/derivation.

**Recommendation for ME:** start with the GINI-style hand-written derivation behind the seam;
adopt an embeddable Datalog the moment rule authorship goes multi-team or explanations become a
compliance requirement; treat ASP as the specialist tool for genuinely defeasible policy sets;
SMT only on demonstrated expressiveness need.

---

## 5. Categories GINI didn't need that ME will ask about

Brief positions, since the ME session will face these next:

- **Agent frameworks (LangChain/LangGraph, CrewAI, AutoGen, DSPy):** GINI hand-rolled its loop
  (~200 lines) and multi-persona stack, and that was the right call at its size — frameworks
  add abstraction debt precisely where GINI needed control (grounding injection, guardrails).
  For ME: the *orchestration* value of these frameworks is largely displaced by Temporal (if
  adopted); their *abstraction* value is lowest exactly where ME is strongest (ME already has
  contracts: personas, oracle, blackboard). DSPy is the interesting exception — not an agent
  framework but prompt/program optimization; potentially valuable for tuning ME's personas
  against the eval harness (§6) rather than hand-tuning prompts. Recommendation: no agent
  framework for the core; evaluate DSPy once an eval harness exists to optimize against.
- **Eval harnesses (promptfoo, DeepEval, LangSmith/Langfuse-style tracing):** GINI's R2.0-E
  plans golden-turn replay. ME should adopt *observability/tracing* early (enterprise debugging
  of LLM behavior without traces is misery) and can buy rather than build the harness runner —
  but the *golden sets* (known concern sets, expected coverage, correct verdicts) are the
  irreplaceable asset and are domain work, not tooling.
- **Rule engines of the business-rules sort (Drools-style, OPA/Rego):** if ME's enterprise
  customers already express policy in OPA/Rego or similar, meeting them there (compiling ME
  oracle predicates to/from the customer's policy language) may matter commercially. OPA is
  effectively a Datalog dialect — this folds into the §4 decision.

---

## 6. What GINI proves for ME (the proving-ground summary)

The ideas being de-risked in GINI, in the order they'll be proven, and what each means for ME:

1. **Structured outputs at the inference boundary** (adopted; trivial to port — a `schema=`
   contract per persona).
2. **The Reasoning Twin** (`docs/REASONING_2.0_DESIGN.md`): concern enumeration from a symbolic
   substrate → exact coverage diff → "why not X?" dialectic → justification validated against
   ground truth → bounded revision → visible flags. If it works on GINI's small substrate, ME
   gets the architecture with an upgraded enumerator (possibly Datalog-backed, §4) and the
   dialectic running as a single durable-workflow activity (§2).
3. **Measured help** (budgeted, logged assistance) — in ME this likely becomes a governance
   feature (bounded AI autonomy with an audit trail) rather than an anti-cheating feature.
4. **The eval harness** (R2.0-E) — the golden-set discipline transfers; the runner can be
   bought.

The single sentence to carry into the ME session: **keep the GINI invariant (deterministic
oracle judges, LLM never), let Temporal-style durable execution enforce that invariant
structurally at the mission level, adopt constrained outputs at every model boundary, keep
reasoning episodes ephemeral, and postpone logic engines until rule volume or compliance
explanations force them — behind a seam that GINI has already shown how to cut.**
