"""Watching a student work against a fixed plan — on a fixed schedule.

The Activity Observation Plan says *what* is looked for. This says *when*, and the schedule is as
much a part of the instrument as the expectations are: if one student's lab happened to be sampled
after it settled and another's at t=3 s, identical work would produce different reports. That is the
same bias a fixed plan exists to remove, reappearing in the time dimension — so the cadence comes
from `plan.header.observation`, never from this module's own preferences.

Two evaluation costs, six orders of magnitude apart, so two triggers:

* **structural** — predicates over the in-memory graph. Microseconds. Run on every topology change.
* **behavioural** — probes that shell into containers. Run on run-state transitions, on the fixed
  cadence, and when the student presses Check. Never on the GUI thread; `tick()` is written to be
  safe to call from a worker.

**First satisfaction, not a running poll.** What gets recorded is the moment an expectation flips —
that is what the narration wants, and it means an expectation costs its probe once rather than every
cycle. Later regressions are recorded too: a student who had it working and then broke it is a
different story from one who never got there, and only the transitions distinguish them.

Qt-free on purpose. The caller owns the timer; this owns the decision about whether enough time has
passed, so the schedule stays testable without a running lab.
"""
from __future__ import annotations

import time

from ..domain import aop_report as _report
from ..domain.aop import evaluation_order

#: AOP verdict -> the vocabulary `proof_recorder.note_check` already speaks. `defective` has no
#: mapping on purpose: it is a fault in the plan, and writing it into the student's chain would
#: leave a permanent record implying they failed something that was never askable.
_TO_OBJECTIVE_STATUS = {
    _report.MET: "met",
    _report.UNMET: "unmet",
    _report.BLOCKED: "pending",
    _report.UNOBSERVABLE: "pending",
}


class _Result:
    """The shape `proof_recorder.note_check` consumes — an `objectives.ObjectiveResult` duck."""

    __slots__ = ("id", "say", "kind", "status", "level")

    def __init__(self, eid, say, kind, status, level=0):
        self.id, self.say, self.kind, self.status, self.level = eid, say, kind, status, level


class AopObserver:
    """Evaluates a plan against a live session, on the plan's own schedule."""

    def __init__(self, plan, get_world, get_runner=None, *, recorder=None,
                 get_topology=None, observability=None, now=time.monotonic) -> None:
        self.plan = plan
        self._get_world = get_world
        self._get_runner = get_runner or (lambda: None)
        self._get_topology = get_topology
        self.recorder = recorder
        self.observability = observability
        self._now = now
        self._started = now()
        self._last_behavioural: float | None = None
        #: expectation id -> the verdict last seen, so only transitions are recorded
        self.verdicts: dict = {}
        #: expectation id -> when it FIRST reached `met`. Never overwritten by a later re-pass:
        #: "when did this start working" is the fact worth keeping.
        self.first_satisfied: dict = {}
        self.regressions: list = []

    # -- schedule ----------------------------------------------------------- #
    @property
    def cadence_s(self) -> float:
        return float(getattr(self.plan.header.observation, "cadence_s", 20.0) or 20.0)

    @property
    def deadline_s(self):
        return self.plan.header.deadline_s

    def within_deadline(self) -> bool:
        """Work past the deadline is still recorded — it is marked, never discarded, and the
        teacher decides what it is worth."""
        d = self.deadline_s
        return d is None or (self._now() - self._started) <= float(d)

    def due(self) -> bool:
        """Whether the cadence has elapsed since the last behavioural pass."""
        if self._last_behavioural is None:
            return True
        return (self._now() - self._last_behavioural) >= self.cadence_s

    # -- triggers ----------------------------------------------------------- #
    def on_topology_changed(self) -> dict:
        """Cheap pass: structural expectations only. Safe to call on every canvas edit."""
        return self._evaluate(behavioural=False)

    def on_run_state(self, *_a) -> dict:
        """A lab came up or went down — both change what is observable, so re-evaluate at once
        rather than waiting for the next tick."""
        return self._evaluate(behavioural=True)

    def on_check(self) -> dict:
        """The student pressed Check. Always a full pass: they asked."""
        return self._evaluate(behavioural=True)

    def tick(self) -> dict:
        """The cadence timer fired. A full pass only if the cadence has actually elapsed, so a
        caller that ticks too eagerly cannot make one student's lab sampled more often than
        another's."""
        if not self.due():
            return {}
        return self._evaluate(behavioural=True)

    # -- evaluation --------------------------------------------------------- #
    def _evaluate(self, *, behavioural: bool) -> dict:
        world = self._get_world()
        runner = self._get_runner() if behavioural else None
        if behavioural:
            self._last_behavioural = self._now()

        verdicts: dict = {}
        changed: dict = {}
        for exp in evaluation_order(self.plan):
            if exp.is_behavioural and not behavioural:
                # Skipped, not re-judged: carry the previous verdict rather than letting a cheap
                # structural pass overwrite a measured one with "unobservable".
                verdicts[exp.id] = self.verdicts.get(exp.id)
                continue
            unmet = [d for d in exp.requires if verdicts.get(d) != _report.MET]
            if unmet:
                verdict = _report.BLOCKED
            else:
                verdict, _detail = _report._evaluate_one(exp, world, runner, self.observability)
            verdicts[exp.id] = verdict
            if self.verdicts.get(exp.id) != verdict:
                changed[exp.id] = verdict

        now = self._now()
        for eid, verdict in changed.items():
            if verdict == _report.MET and eid not in self.first_satisfied:
                self.first_satisfied[eid] = now
            elif verdict == _report.UNMET and eid in self.first_satisfied:
                self.regressions.append((eid, now))

        self.verdicts.update({k: v for k, v in verdicts.items() if v is not None})
        if changed:
            self._record(changed)
        return changed

    def _record(self, changed: dict) -> None:
        """Push transitions into the proof chain, if one is being recorded.

        Only transitions: a chain logging "still unmet" every twenty seconds would be unreadable,
        and the interesting moment is the one where something started working. `note_check` already
        de-duplicates, so this is belt and braces rather than the only guard.
        """
        rec = self.recorder
        if rec is None or not getattr(rec, "armed", False):
            return
        by_id = {e.id: e for e in self.plan.expectations}
        results, objectives = [], []
        for eid, verdict in changed.items():
            status = _TO_OBJECTIVE_STATUS.get(verdict)
            if status is None:                 # `defective` — the plan's fault, not the student's
                continue
            exp = by_id.get(eid)
            if exp is None:
                continue
            results.append(_Result(eid, exp.say,
                                   "behavioral" if exp.is_behavioural else "structural", status))
            objectives.append(exp)
        if results:
            try:
                rec.note_check(results, objectives)
            except Exception:                  # noqa: BLE001 — observing must never break the app
                pass

    # -- output ------------------------------------------------------------- #
    def report(self):
        """A full report as of now. Runs a complete pass — including behavioural — because a
        submission should be judged on a fresh measurement rather than on whatever the last tick
        happened to catch."""
        topo = self._get_topology() if self._get_topology else None
        return _report.build(self.plan, self._get_world(), self._get_runner(),
                             topology=topo, observability=self.observability,
                             within_deadline=self.within_deadline())
