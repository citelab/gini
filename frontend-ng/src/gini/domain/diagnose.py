"""Diagnose game engine — the reusable core behind "diagnose from the signature" games.

The Process-Fingerprint classify game generalizes: show a real telemetry signature, the student
names the hidden cause, the ORACLE grades against deterministic ground truth, a confusion matrix
scores the run. This module owns that loop once; each subsystem supplies data + a renderer.

Everything here is pure and deterministic (seeded case selection), so it is fully unit-tested and the
UI stays a thin renderer. The label on every Case is derived from real kernel state — never guessed,
never from an LLM.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

PRACTICE, GRADED = "practice", "graded"


@dataclass(frozen=True)
class Case:
    """One labeled instance: a real signature + its deterministic ground truth."""
    id: str
    signature: object          # opaque payload the game's renderer understands (fp / gantt / event)
    truth: object              # class/spot: a label; estimate: a number; rank: an ordered list
    subtitle: str = ""         # revealed after guessing ("writer", "lazy alloc at 0x1330")
    hint: str | None = None    # rule-classifier baseline, shown in practice mode only
    options: list | None = None  # per-case candidates for spot/rank (buttons/chips vary per case)


@dataclass(frozen=True)
class GameSpec:
    """Static definition of a game: its answer kind + presentation.

    answer="class"    → pick one of `classes` (predict-outcome is just a 2/3-class game); scored by a
                        confusion matrix.
    answer="estimate" → type a number; scored by closeness. `tolerance` is absolute unless
                        `relative` (a fraction of the truth); tolerance 0 means an EXACT answer.
    """
    id: str
    title: str
    prompt: str
    classes: list
    abbrev: dict = field(default_factory=dict)     # short labels for the confusion-matrix axes
    answer: str = "class"                          # "class" | "estimate" | "spot" | "rank"
    tolerance: float = 0.0
    relative: bool = False
    unit: str = ""                                 # shown next to the numeric input (e.g. "faults")


def confusion_matrix(pairs, classes) -> dict:
    """[(true, predicted), …] -> {(true, pred): count} over the class set. Off-diagonal = confusion."""
    m = {(t, p): 0 for t in classes for p in classes}
    for true, pred in pairs:
        if (true, pred) in m:
            m[(true, pred)] += 1
    return m


def accuracy(pairs) -> float:
    """Fraction of (true, pred) pairs on the diagonal."""
    pairs = list(pairs)
    if not pairs:
        return 0.0
    return sum(1 for t, p in pairs if t == p) / len(pairs)


def order_score(answer, truth) -> float:
    """Rank games: fraction of ordered pairs in `truth` that appear in the same order in `answer`
    (a normalized Kendall agreement, 0..1; 1.0 = a perfect ordering). Tolerant of items the student
    didn't place."""
    truth = list(truth or [])
    idx = {x: i for i, x in enumerate(answer or [])}
    good = pairs = 0
    for i in range(len(truth)):
        for j in range(i + 1, len(truth)):
            a, b = truth[i], truth[j]
            if a in idx and b in idx:
                pairs += 1
                if idx[a] < idx[b]:
                    good += 1
    return good / pairs if pairs else 0.0


def per_class(pairs, classes) -> dict:
    """{class: {'recall': r, 'precision': p, 'n': support}} from the pairs — for a richer scoreboard."""
    out = {}
    for c in classes:
        tp = sum(1 for t, g in pairs if t == c and g == c)
        support = sum(1 for t, _ in pairs if t == c)
        called = sum(1 for _, g in pairs if g == c)
        out[c] = {"recall": tp / support if support else 0.0,
                  "precision": tp / called if called else 0.0, "n": support}
    return out


class DiagnoseSession:
    """The pure game state machine. Feed it Cases; it serves mysteries, records guesses, and scores.

    Modes: PRACTICE serves cases forever with an immediate reveal + hint; GRADED serves a fixed
    `deck` of cases with the hint suppressed, then finishes. Case selection is seeded for
    reproducibility (a mission can replay the same deck)."""

    def __init__(self, spec: GameSpec, cases=(), mode: str = PRACTICE, deck: int = 10,
                 seed: int = 0) -> None:
        self.spec = spec
        self.mode = mode
        self.deck = deck
        self._rng = random.Random(seed)
        self._cases = list(cases)
        self.pairs: list = []
        self.current: Case | None = None
        self.finished = False
        self._served = 0

    # -- case pool --------------------------------------------------------- #
    def set_cases(self, cases) -> None:
        """Refresh the available cases (e.g. live telemetry changed) without losing the score."""
        self._cases = list(cases)

    def has_cases(self) -> bool:
        return bool(self._cases)

    # -- flow -------------------------------------------------------------- #
    def next(self) -> Case | None:
        """Serve the next mystery, or None when a graded run is complete / no cases exist."""
        if self.mode == GRADED and self._served >= self.deck:
            self.finished = True
            self.current = None
            return None
        if not self._cases:
            self.current = None
            return None
        self.current = self._rng.choice(self._cases)
        self._served += 1
        return self.current

    def guess(self, label) -> dict:
        """Record a guess against the current mystery; return the reveal (correct?/truth/subtitle/
        hint). Correctness is per-kind (class equality, or numeric closeness for estimate). Hint is
        only surfaced in practice mode."""
        if self.current is None:
            return {}
        truth = self.current.truth
        self.pairs.append((truth, label))
        part = order_score(label, truth) if self.spec.answer == "rank" else None
        return {"correct": self._hit(truth, label), "truth": truth, "label": label,
                "subtitle": self.current.subtitle, "partial": part,
                "hint": self.current.hint if self.mode == PRACTICE else None,
                "complete": self.mode == GRADED and self._served >= self.deck}

    def _hit(self, truth, answer) -> bool:
        """Correct? Class/spot = exact match; estimate = within tolerance; rank = perfect order."""
        kind = self.spec.answer
        if kind == "estimate":
            try:
                t, a = float(truth), float(answer)
            except (TypeError, ValueError):
                return False
            tol = abs(t) * self.spec.tolerance if self.spec.relative else self.spec.tolerance
            return abs(a - t) <= tol
        if kind == "rank":
            return order_score(answer, truth) == 1.0
        return answer == truth

    def reset(self) -> None:
        self.pairs = []
        self.current = None
        self.finished = False
        self._served = 0

    # -- scoreboard -------------------------------------------------------- #
    def matrix(self) -> dict:
        return confusion_matrix(self.pairs, self.spec.classes)

    def accuracy(self) -> float:
        if not self.pairs:
            return 0.0
        return sum(1 for t, a in self.pairs if self._hit(t, a)) / len(self.pairs)

    def score(self) -> tuple[int, int]:
        return sum(1 for t, a in self.pairs if self._hit(t, a)), len(self.pairs)

    def mean_abs_error(self) -> float:
        """Estimate games: average |guess − truth| over the run (0 for a perfect run)."""
        errs = []
        for t, a in self.pairs:
            try:
                errs.append(abs(float(a) - float(t)))
            except (TypeError, ValueError):
                pass
        return sum(errs) / len(errs) if errs else 0.0

    def mean_order_score(self) -> float:
        """Rank games: average pairwise-order agreement over the run (partial credit)."""
        if not self.pairs:
            return 0.0
        return sum(order_score(a, t) for t, a in self.pairs) / len(self.pairs)

    def remaining(self) -> int | None:
        """Cases left in a graded run (None in practice)."""
        return None if self.mode == PRACTICE else max(0, self.deck - self._served)
