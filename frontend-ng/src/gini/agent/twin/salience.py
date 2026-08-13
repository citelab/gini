"""Salience — rules only, never a model (the notifier philosophy: tune the RULES if noisy).

The caps are the anti-noise guarantee: at most MAX_CONCERNS reach a turn, and only concerns at
or above MUST_ADDRESS must be covered by the model (lower ones are context the Twin tracks but
never objects about)."""
from __future__ import annotations

MAX_CONCERNS = 5     # hard cap per turn — the Twin whispers, it does not checklist
MUST_ADDRESS = 2     # salience >= this must appear in the coverage report (addressed or justified)

# base salience per concern kind (0..3). 3 = urgent (objection even under coverage-silence).
KIND_SALIENCE = {
    "legality": 3,          # off-task / illegal links — active rule violations
    "objective": 2,         # an unmet objective with a grounded why
    "watcher-event": 2,     # OS pedagogical events (starvation, monopoly…) — phase C
    "composition-gap": 2,   # unfilled requires / authoring disagreements — phase C
    "misconception": 2,     # learner-model concerns (3 when touching the current work) — phase D
    "grammar-option": 1,    # a valid-but-unused possibility — context, never an objection
}


def salience_for(kind: str) -> int:
    return KIND_SALIENCE.get(kind, 1)


def cap(concerns: list) -> list:
    """Highest-salience first, capped at MAX_CONCERNS; stable within a salience band so
    enumeration order (objectives before flags, etc.) is preserved deterministically."""
    ranked = sorted(concerns, key=lambda c: -c.salience)
    return ranked[:MAX_CONCERNS]
