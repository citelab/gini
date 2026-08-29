"""The student Profile — a transcript of Mission outcomes plus a lightweight mastery model.

One profile per student, keyed to identity. In Phase 4 it's a local working copy (JSON on disk);
Phase 5 makes the Teaching Center its authoritative home and syncs this copy git-style. The data
is deliberately **append-only / monotonic** (best-band is a max, attempts accumulate, mastery is a
union), so two machines merge deterministically — the property that makes checkout/checkin work.

Contents:
  • transcript — per lesson: best band, attempts used, best (fastest) completing time, when last
    played, and an optional canvas snapshot for audit / server re-grade.
  • mastery    — per concept: the best band achieved across lessons that teach it → a coarse level
    (proficient / demonstrated / attempted), which the rest of the agent can use to adapt.

Pure data + JSON (de)serialization; no Qt, no network.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field

# band ranking (higher is better) — a completed lesson's best_band is a MAX over attempts
_BAND_RANK = {"": 0, "incomplete": 1, "partial": 2, "pass": 3, "gold": 4}
# concept mastery level derived from the best band on lessons teaching that concept
_MASTERY = {4: "proficient", 3: "demonstrated", 2: "attempted", 1: "attempted", 0: "none"}


def band_rank(band: str) -> int:
    return _BAND_RANK.get(band or "", 0)


def better_band(a: str, b: str) -> str:
    return a if band_rank(a) >= band_rank(b) else b


@dataclass
class LessonRecord:
    lesson_id: str
    concept: str = ""                 # what the lesson teaches (for mastery)
    best_band: str = ""
    attempts_used: int = 0            # cumulative across sessions
    best_time_s: float | None = None  # fastest COMPLETING time (None if never completed)
    completed: bool = False
    last_played: float = 0.0          # epoch seconds
    snapshot: str = ""                # optional serialized canvas (audit / re-grade)

    def merge_outcome(self, band: str, attempts: int, time_s: float | None,
                      completed: bool, snapshot: str = "", now: float | None = None) -> None:
        self.best_band = better_band(self.best_band, band)
        self.attempts_used += max(0, attempts)
        self.completed = self.completed or completed
        if completed and time_s is not None:
            self.best_time_s = time_s if self.best_time_s is None else min(self.best_time_s, time_s)
        if snapshot:
            self.snapshot = snapshot
        self.last_played = now if now is not None else time.time()


@dataclass
class Profile:
    student_id: str
    lessons: dict = field(default_factory=dict)     # lesson_id -> LessonRecord

    # -- recording ---------------------------------------------------------- #
    def record(self, lesson, mission, *, snapshot: str = "", now: float | None = None) -> LessonRecord:
        """Fold a witnessed Mission's outcome into the transcript (monotonic merge)."""
        rec = self.lessons.get(lesson.id)
        if rec is None:
            rec = LessonRecord(lesson_id=lesson.id, concept=getattr(lesson.intent, "concept", ""))
            self.lessons[lesson.id] = rec
        rec.merge_outcome(
            band=mission.last_band or mission.score().band,
            attempts=mission.attempt,
            time_s=mission.elapsed() if mission.complete else None,
            completed=mission.complete, snapshot=snapshot, now=now)
        return rec

    # -- mastery model ------------------------------------------------------ #
    def mastery(self) -> dict:
        """concept -> coarse level, from the best band achieved on lessons teaching it."""
        best: dict[str, int] = {}
        for rec in self.lessons.values():
            if rec.concept:
                best[rec.concept] = max(best.get(rec.concept, 0), band_rank(rec.best_band))
        return {c: _MASTERY[r] for c, r in best.items()}

    def demonstrated_concepts(self) -> list[str]:
        """Concepts the student has demonstrated (>= pass) — feeds adaptivity."""
        return [c for c, lvl in self.mastery().items() if lvl in ("proficient", "demonstrated")]

    def summary(self) -> str:
        done = sum(1 for r in self.lessons.values() if r.completed)
        return f"{done}/{len(self.lessons)} lessons completed"

    # -- persistence (local working copy) ---------------------------------- #
    def to_dict(self) -> dict:
        return {"student_id": self.student_id,
                "lessons": {lid: asdict(rec) for lid, rec in self.lessons.items()}}

    @classmethod
    def from_dict(cls, d: dict) -> "Profile":
        p = cls(student_id=d.get("student_id", ""))
        for lid, rd in (d.get("lessons", {}) or {}).items():
            p.lessons[lid] = LessonRecord(**rd)
        return p

    def save(self, path) -> None:
        from pathlib import Path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path, *, student_id: str = "") -> "Profile":
        from pathlib import Path
        try:
            return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            return cls(student_id=student_id)


def merge(a: Profile, b: Profile) -> Profile:
    """Deterministic union merge of two working copies (offline reconciliation). Because the data
    is monotonic, this is well-defined without conflict: best_band = max, attempts = sum of the
    *newer* record's extra, times = min. Here we take the record-wise best/most-complete."""
    out = Profile(student_id=a.student_id or b.student_id)
    for lid in set(a.lessons) | set(b.lessons):
        ra, rb = a.lessons.get(lid), b.lessons.get(lid)
        if ra is None:
            out.lessons[lid] = rb
        elif rb is None:
            out.lessons[lid] = ra
        else:
            out.lessons[lid] = LessonRecord(
                lesson_id=lid, concept=ra.concept or rb.concept,
                best_band=better_band(ra.best_band, rb.best_band),
                attempts_used=max(ra.attempts_used, rb.attempts_used),
                best_time_s=min([t for t in (ra.best_time_s, rb.best_time_s) if t is not None],
                                default=None),
                completed=ra.completed or rb.completed,
                last_played=max(ra.last_played, rb.last_played),
                snapshot=ra.snapshot if ra.last_played >= rb.last_played else rb.snapshot)
    return out
