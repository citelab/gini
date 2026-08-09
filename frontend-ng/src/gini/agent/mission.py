"""The Mission runtime — a per-attempt state machine (pure logic; the UI drives it).

One Mission plays one Lesson. It runs the clock and the attempt (life) counter, evaluates the
lesson's objectives against the live topology (structural objectives resolve instantly in Phase 1;
behavioral ones stay `pending` until Phase 2's probe harness), detects completion via the lesson's
`complete_when`, and computes the broad band. It persists (`to_dict`/`from_dict`) so a student can
close GINI mid-lab and resume.

No Qt, no LLM, no wall-clock coupling — the clock is an injected `now()` callable, so time and
completion are fully unit-testable. GINI's runtime/objective engine is the oracle; this module is
just the bookkeeping around it.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..domain import objectives as _obj
from ..domain import scoring as _scoring
from ..domain.lesson import Lesson

# states
STAGED = "staged"        # board pre-built, not yet started
BRIEFED = "briefed"      # brief shown, awaiting start
PLAYING = "playing"      # clock running, live evaluation
WITNESSED = "witnessed"  # an attempt was judged (band computed)
DONE = "done"            # complete, or out of lives/time


@dataclass
class Mission:
    lesson: Lesson
    now: callable = time.monotonic          # injected clock (seconds, monotonic)
    state: str = STAGED
    attempt: int = 0                        # lives spent
    started_at: float | None = None
    ended_at: float | None = None
    last_results: list = field(default_factory=list)
    last_band: str = ""
    step_index: int = 0                      # guided missions: which beat we're on

    # -- lifecycle ---------------------------------------------------------- #
    def brief(self) -> None:
        if self.state == STAGED:
            self.state = BRIEFED

    def start(self) -> None:
        """Begin (or begin the next) attempt: clock runs, a life is spent."""
        if self.state not in (STAGED, BRIEFED, WITNESSED):
            return
        if self.state == WITNESSED and not self.can_retry():
            return
        self.attempt += 1
        self.started_at = self.now()
        self.ended_at = None
        self.step_index = 0          # a (re)start walks the guided beats from the top
        self.state = PLAYING

    # -- clock -------------------------------------------------------------- #
    def elapsed(self) -> float:
        if self.started_at is None:
            return 0.0
        end = self.ended_at if self.ended_at is not None else self.now()
        return max(0.0, end - self.started_at)

    def remaining(self) -> float | None:
        """Seconds left, or None if the lesson is untimed."""
        if self.lesson.time_limit_s is None:
            return None
        return self.lesson.time_limit_s - self.elapsed()

    def expired(self) -> bool:
        r = self.remaining()
        return r is not None and r <= 0

    def on_time(self) -> bool:
        return not self.expired()

    # -- evaluation --------------------------------------------------------- #
    def evaluate(self, world, runner=None) -> list:
        """Re-evaluate objectives against the live world (structural = instant; behavioral resolve
        when a `runner`/runtime is supplied, else pending). Auto-witnesses when the mission genuinely
        completes or the clock expires — including from an already-WITNESSED attempt, so a band can
        still be *upgraded* if the student pushes on and finishes."""
        self.last_results = _obj.evaluate_all(self.lesson.objectives, world, runner)
        # difficulty forks are evaluated too, but they NEVER block completion — they only lift the
        # band. Kept in a side channel so the core ladder the panel shows is unchanged.
        self._fork_results = {}
        for fk in getattr(self.lesson, "forks", []) or []:
            objs = fk.get("objectives", [])
            self._fork_results[fk.get("id", "")] = _obj.evaluate_all(objs, world, runner) if objs else []
        if self.state in (PLAYING, WITNESSED) and self._should_finish():
            self._witness()
        return self.last_results

    def _should_finish(self) -> bool:
        """When to end the attempt: the clock expired, OR — for a GUIDED mission — the student
        finished all the beats, OR — for a free-form mission — the win conditions are met. (A
        guided mission is NOT ended early by objective completion, so read/reflect beats still run.)"""
        if self.expired():
            return True
        if self.guided:
            return self.steps_done
        return self._complete()

    def check(self, world, runner=None) -> "_scoring.Score":
        """Explicit 'Run/Check' — resolve the behavioral probes against the running system.

        It must NOT end the attempt on its own. The live checks are REQUIRED to finish, so the
        student presses Run precisely in order to complete — forcing a witness here froze a PARTIAL
        band the moment they ran, and the mission could never be re-judged once it finished
        ("PARTIAL — 9/9"). `evaluate()` witnesses when the mission is genuinely complete or expired."""
        self.evaluate(world, runner)
        return self.score()

    def _complete(self) -> bool:
        return _scoring.is_complete(self.last_results, self.lesson.complete_when)

    def _witness(self) -> None:
        self.ended_at = self.now()
        self.last_band = self.score().band
        self.state = WITNESSED
        if self._complete() or not self.can_retry():
            self.state = DONE

    def score(self) -> "_scoring.Score":
        forks = getattr(self.lesson, "forks", []) or []
        fr = getattr(self, "_fork_results", {})
        # a fork is "done" only when every objective in it is met (an empty fork can't count)
        done = sum(1 for fk in forks
                   if fr.get(fk.get("id", "")) and all(r.met for r in fr[fk.get("id", "")]))
        return _scoring.score(self.last_results, complete_when=self.lesson.complete_when,
                              on_time=self.on_time(), forks_done=done, forks_total=len(forks))

    # -- attempts ----------------------------------------------------------- #
    def lives_left(self) -> int:
        return max(0, self.lesson.attempts - self.attempt)

    def can_retry(self) -> bool:
        return self.lives_left() > 0 and not self._complete()

    def retry(self) -> None:
        if self.state == WITNESSED and self.can_retry():
            self.start()

    @property
    def complete(self) -> bool:
        return self._complete()

    # -- guided steps ------------------------------------------------------- #
    @property
    def guided(self) -> bool:
        return self.lesson.guided

    def current_step(self):
        steps = self.lesson.steps
        return steps[self.step_index] if 0 <= self.step_index < len(steps) else None

    @property
    def steps_done(self) -> bool:
        return self.step_index >= len(self.lesson.steps)

    def step_number(self) -> tuple[int, int]:
        return (min(self.step_index + 1, len(self.lesson.steps)), len(self.lesson.steps))

    def step_satisfied(self, world, runner=None, replied: bool = False) -> bool:
        """Is the CURRENT step's advance condition met? reply/ack → the student sent a message;
        structural → the canvas satisfies the predicate; behavioral → the probe passes (needs a
        runner/run, else not yet)."""
        step = self.current_step()
        if step is None:
            return False
        kind = step.kind()
        if kind == "reply":
            return replied
        if kind == "structural":
            try:
                return _obj.evaluate_check(step.advance, world)
            except _obj.PredicateError:
                return False
        # behavioral
        if runner is None or not getattr(runner, "available", lambda: False)():
            return False
        from ..domain import probes as _probes
        try:
            return _probes.evaluate(step.advance, runner)
        except _probes.ProbeError:
            return False

    def advance_step(self):
        """Move to the next beat; returns the new current step (or None when the guided path ends)."""
        if self.step_index < len(self.lesson.steps):
            self.step_index += 1
        return self.current_step()

    # -- persistence (save/resume) ----------------------------------------- #
    def to_dict(self) -> dict:
        return {"lesson_id": self.lesson.id, "state": self.state, "attempt": self.attempt,
                "started_at": self.started_at, "ended_at": self.ended_at,
                "last_band": self.last_band}

    @classmethod
    def from_dict(cls, d: dict, lesson: Lesson, now=time.monotonic) -> "Mission":
        m = cls(lesson=lesson, now=now)
        m.state = d.get("state", STAGED)
        m.attempt = int(d.get("attempt", 0))
        m.started_at = d.get("started_at")
        m.ended_at = d.get("ended_at")
        m.last_band = d.get("last_band", "")
        return m
