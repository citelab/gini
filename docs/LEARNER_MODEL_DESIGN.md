# The GINI Learner Model — Design

**Status:** design ratified (four core decisions below), ready for a hand-off session to build.
**Owner:** Mahesh · **Companion docs:** `REASONING_2.0_DESIGN.md` (consumes this at phase R2.0-D),
`libraries_for_mission_engine.md` (ME inherits the consent boundary defined here).

**Ratified decisions (2026-08-13):**
1. Mastery = **evidence counters + time decay**, behind a swappable update seam (BKT later).
2. Evidence policy = **deterministic-only writes** in v1; LLM reads, never writes.
3. Storage = **local per-student file + opt-in Teaching Center sync** of summaries.
4. v1 ships the **authored misconception library + deterministic detectors** (not mastery-only).

---

## 1. Motivation and the invariant, extended

GINI models the *machine* richly (topology digests, kernel cards) but has no persistent model of
the *learner* — memory today is per-session working memory. Coaching is grounded in system state
but blind to who the student is: what they've mastered, what they persistently get wrong, how
they use help. The learner model closes that gap, and it is the designated concern source for
the Reasoning Twin's R2.0-D phase ("the student holds misconception M" becomes an enumerable,
evidence-backed concern).

The GINI invariant extends naturally and non-negotiably:

> **The LLM never judges the student.** Mastery, misconceptions, and habits are evidence-based
> and deterministically updated — from oracle verdicts, watcher events, and ledger usage —
> never from the model's impression of a conversation. The LLM *reads* the learner model to
> adapt its coaching; it never *writes* it.

Every number in the model must be explainable to an instructor in one line ("3 missions passed,
1 hint, last seen 3 days ago") and traceable to verdicts. The learner model is auditable the
same way grading is.

## 2. Data model

New pure module: `domain/learner.py` (no Qt, no LLM). One `LearnerState` per student.

### 2.1 Mastery (per concept)

Keyed to the existing concept taxonomy (`domain/concepts.py` keys: `os-scheduling`,
`networking-basics`, …) so it composes with retrieval, fragments, and recipes for free.

```python
@dataclass
class ConceptMastery:
    concept: str                  # concepts.py key
    met: float = 0.0              # decayed weighted count of objectives met
    unmet: float = 0.0            # decayed weighted count of objectives failed
    hints: float = 0.0            # decayed count of coach hints spent on this concept
    last_seen: str = ""           # ISO date of last evidence
    trace: list = field(...)      # last N evidence entries (mission id, verdict, hint) — the audit line

    @property
    def mastery(self) -> float:   # 0..1, derived, never stored independently of evidence
        ...
```

### 2.2 Misconception register

The fragments pattern again — **content is data, detection is code**. A misconception is an
authored YAML entry; detection is a deterministic pattern-matcher over the evidence stream.

```yaml
# domain/misconceptions/os/swtch-saves-trapframe.yaml
id: swtch-saves-trapframe
concept: os-scheduling
statement: "believes swtch saves the trapframe (conflates a context switch with a trap)"
detector:                      # ALL clauses must match within the window (deterministic)
  window_days: 14
  min_evidence: 2
  patterns:
    - objective_unmet: "journey-*"        # failed a journey-related objective
    - coach_event: "asked-trap-vs-swtch"  # a tagged coach interaction kind
remediation:
  fragment: xv6-journey          # what to steer toward
  hint: "walk the preempt journey — watch WHICH save area moves at each stage"
```

Runtime record: `ActiveMisconception{id, evidence: [entries], first_seen, last_seen, status}`
with `status ∈ {active, resolving, resolved}` — resolved deterministically (the detector's
inverse: the related objectives now pass without hints).

v1 library: 10–20 per domain, hand-authored. Seed lists —
*OS:* priority-bigger-wins, swtch-saves-trapframe, scheduler-is-a-process,
zombie-means-crashed, syscall-is-a-function-call, lottery-is-round-robin.
*Networking:* switch-routes-subnets, ip-identifies-machine-not-interface,
gateway-optional-on-lan, dns-is-routing, hub-equals-switch.

### 2.3 Working habits

```python
@dataclass
class Habits:
    help_rate: float        # hints used / objectives attempted (from CoachLedger records)
    persistence: float      # retries after a failed objective before asking for help
    pace: float             # median missions per active week
    last_active: str
```

Habits inform coach *tone and hint depth*, never verdicts, never grades.

## 3. The evidence stream

One append-only, deterministic event contract feeds all updates
(`domain/learner.py: LearnerEvent`):

| event kind         | source (all existing, all deterministic)                    |
|--------------------|-------------------------------------------------------------|
| `objective_met` / `objective_unmet` | oracle verdicts (blackboard / `objectives.evaluate` / grader submissions) |
| `hint_spent`       | `CoachLedger.record` (already instructor-visible)            |
| `watcher_event`    | `StateWatcher` (starvation, monopoly, zombie_leak…)          |
| `mission_complete` | mission controller (band, duration, retries)                 |
| `coach_event`      | *tagged* coach interactions (kind tags authored in prompts' event taxonomy — the tag is data, not LLM judgment) |

Concept attribution: an objective's concept comes from its fragment's `teaches:` field —
already present in every fragment. No new authoring burden.

**Explicitly NOT evidence in v1:** chat content, LLM impressions, sentiment, time-on-page.
(v2 may add "LLM proposes a *candidate* misconception, status UNCONFIRMED until a deterministic
detector corroborates" — designed but not built; candidates never feed the Twin or the
instructor view while unconfirmed.)

## 4. Update math — counters + decay, behind a seam

`update(state, event) -> state` is the single write path.

- Each counter is a decayed sum: on update, prior mass is multiplied by
  `0.5 ** (days_since_last / HALF_LIFE)` (default half-life 21 days), then the new evidence adds
  weight 1.0 (weights configurable per event kind; a no-hint met counts more than a hinted one).
- `mastery = met_eff / (met_eff + unmet_eff + λ·hints_eff)` with λ≈0.5 — hints attenuate,
  never dominate. Bands for consumers: `weak < 0.4 ≤ developing < 0.7 ≤ solid`.
- **The seam:** `MasteryUpdater` protocol with `CounterUpdater` as v1. A future `BKTUpdater`
  implements the same protocol once real class data exists to tune slip/guess/learn parameters.
  Nothing upstream may depend on counter internals — consumers see `mastery`, `band`, `trace`.

Every update appends to `trace` (bounded ring) — the explainability guarantee.

## 5. Storage, sync, privacy

- **Local, student-owned:** `~/.gini/learner/state.yaml` (same `GINI_HOME` convention as
  content). Plain YAML — the student can read every byte of what GINI believes about them.
- **Opt-in course sync:** on enrollment (existing Teaching Center git-like sync), a **summary**
  syncs: per-concept mastery bands, active misconception ids + evidence counts, help_rate.
  **Never synced:** raw chat, the full event stream, traces. The instructor view answers
  "who is stuck, on what, holding which misconception" — not "what did they type."
- Local wipe = student right; a synced summary is versioned with the course and deleted with
  un-enrollment. This is the consent boundary ME inherits (see `libraries_for_mission_engine.md`).

## 6. Consumers (the contracts)

1. **The Reasoning Twin (R2.0-D)** — `learner_concerns(state, context) -> [Concern]`:
   active misconceptions relevant to the current mission/concept (salience 2–3), weak-band
   concepts touched by the current work (salience 1–2). The Twin then audits the tutor:
   *"the student holds priority-bigger-wins and just failed a priority objective — why didn't
   the hint address it?"* — the exact dialectic the Twin was built for.
2. **The Coach** — hint depth policy: weak band → more scaffolded hint; solid band → more
   Socratic; active misconception with a `remediation.hint` → the coach prompt carries it as
   ground truth ("the student likely believes X; do not assert it, probe it").
3. **The Composer / mission sequencing** — difficulty selection: prefer fragments whose
   `teaches` hits weak-band concepts; gate fork difficulty on band; misconception remediation
   fragments surface as "recommended next."
4. **Instructor view (Teaching Center)** — the synced summary per roster row; drill-down shows
   evidence counts, never transcripts.
5. **Ask GINI card (light)** — one line in the grounded context: "learner: os-scheduling weak;
   active misconception: swtch-saves-trapframe" — so even plain chat adapts. Budget: ≤2 lines.

## 7. Phases

| phase | deliverable |
|-------|-------------|
| **L-A** | `domain/learner.py`: LearnerState/ConceptMastery/Habits, LearnerEvent, CounterUpdater + seam, YAML load/save, full unit tests (pure). |
| **L-B** | Evidence wiring: emit LearnerEvents from the mission controller, grader, CoachLedger, StateWatcher; concept attribution via fragment `teaches`. |
| **L-C** | Misconception library v1 (YAML loader + detector matcher + resolution) + the OS/networking seed sets; detector golden-fixture tests. |
| **L-D** | Consumers: coach hint-depth policy + Ask GINI card line + composer difficulty preference. |
| **L-E** | Teaching Center summary sync + instructor roster view + wipe/unenroll lifecycle. |
| **L-F** | Hand-off to Reasoning 2.0: `learner_concerns()` (this is R2.0-D's consumption point). |

L-A through L-C are pure/sandbox-buildable and independently testable — a clean package for a
parallel session. L-E touches the Teaching Center (Mac/live validation).

## 8. Testing

- Pure golden tests: event sequences → expected mastery bands and traces; decay over synthetic
  clocks; detector fixtures (fires / doesn't fire / resolves) per misconception.
- Property tests: mastery ∈ [0,1]; trace always explains the current numbers; no write path
  besides `update()`; LLM-tagged events carry only whitelisted kind tags.
- Privacy tests: the sync serializer provably excludes raw events/traces/chat.

## 9. Open items (for the building session)

- Half-life and λ defaults: proposed 21d / 0.5 — tune against early usage; keep in Settings.
- Multi-student on one machine (lab computers): keyed by the Teaching Center identity when
  logged in; anonymous local profile otherwise.
- Cold start: all concepts unseen ≠ weak — `band = unknown` until min evidence (3 events);
  consumers must treat `unknown` as "no adaptation," not "remediate."
- Concept granularity: concepts.py keys may be coarse (one `os-scheduling` for all of
  scheduling). If too coarse for useful adaptation, add sub-concept tags to fragments —
  authoring-time decision, defer until evidence shows the need.
