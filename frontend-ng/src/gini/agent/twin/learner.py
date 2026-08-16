"""The learner-model concern source (REASONING_2.0_DESIGN.md phase D; LEARNER_MODEL_DESIGN.md
§6.1 / L-F). This is the CONSUMPTION seam: the learner model itself is built on a parallel
track (`domain/learner.py`); this module duck-types against its ratified shape so the Twin is
ready the moment it lands.

Expected learner-state shape (duck-typed, all optional):
  • .misconceptions — iterable of active misconceptions with .id, .concept, .statement and an
    evidence count (`.evidence` list or `.evidence_count`);
  • .band(concept) -> "unknown" | "weak" | "developing" | "solid" (counters+decay, auditable).

Rules (per the ratified design):
  • an ACTIVE misconception on a concept the current work touches → salience 3; other active
    misconceptions → salience 2;
  • a WEAK-band concept touched by the current work → salience 2;
  • `unknown` band = cold start = NO concern (unknown ≠ weak — no adaptation, not remediation);
  • every concern carries evidence (the misconception's evidence count / the band), same as
    every other Twin source: no evidence, no concern."""
from __future__ import annotations

from .contracts import Concern
from .salience import cap


def _evidence_count(m) -> int:
    ev = getattr(m, "evidence", None)
    if isinstance(ev, (list, tuple)):
        return len(ev)
    return int(getattr(m, "evidence_count", 0) or 0)


def learner_concerns(learner, *, concepts=()) -> list[Concern]:
    """Concerns about THIS student, for the current work. `concepts` = the concept keys the
    current mission/lab teaches (e.g. from the fragment's `teaches:`)."""
    if learner is None:
        return []
    touched = set(concepts or ())
    concerns: list[Concern] = []

    for m in getattr(learner, "misconceptions", None) or []:
        n = _evidence_count(m)
        if n <= 0:
            continue                                   # no evidence, no concern
        relevant = getattr(m, "concept", "") in touched
        concerns.append(Concern(
            id=f"misconception:{m.id}", kind="misconception",
            statement=f"the student likely {m.statement}",
            evidence=f"detector evidence x{n} on {getattr(m, 'concept', '?')}",
            salience=3 if relevant else 2, source="learner-model"))

    band_of = getattr(learner, "band", None)
    if callable(band_of):
        for concept in sorted(touched):
            try:
                band = band_of(concept)
            except Exception:
                continue
            if band == "weak":                          # unknown = cold start = NO concern
                concerns.append(Concern(
                    id=f"learner:weak:{concept}", kind="misconception",
                    statement=f"the student is weak on {concept}",
                    evidence=f"mastery band: weak on {concept}",
                    salience=2, source="learner-model"))

    return cap(concerns)
