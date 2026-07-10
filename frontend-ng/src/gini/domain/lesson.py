"""Lesson Pack parsing + validation.

A Lesson is the authored (or AI-resolved-then-ratified) unit a Mission plays. In Phase 1 a
Lesson is read from a dict/YAML with concrete objectives; the `intent` block is carried for the
game master to reason over (Phase 3). Validation catches authoring mistakes early — a structural
predicate that doesn't parse, or an element type that doesn't exist — so a broken lesson never
reaches a student.

Pure data + parsing; no Qt. YAML is optional (falls back to a dict) so tests need no file I/O.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import catalog as _catalog
from .objectives import Objective, check_ok, unknown_element_types

_HELP = ("none", "warmer_colder", "full_tutor_logged")
_PERSONA = ("coach", "challenger")
_KINDS = ("structural", "behavioral")


@dataclass
class Intent:
    """Teacher intent — the game master reasons over this; it is NOT a checklist."""
    concept: str = ""
    goal: str = ""
    spirit: str = ""
    misconceptions: list[str] = field(default_factory=list)


@dataclass
class Forbid:
    """A rule the student must NOT trip — a structural predicate that should stay FALSE. When it
    becomes true (e.g. `link(database, cloud)` — the DB wired to the Internet), the offending move
    is flagged (red badge) and the game master explains. `say` is the student-facing reason."""
    say: str
    check: str


@dataclass
class Step:
    """One beat of a GUIDED mission: an instruction, and how the student advances past it.
    `advance` is one of: 'reply'/'ack' (any student message — a read/reflect beat), a structural
    predicate (drop/connect/configure — advances when the canvas satisfies it), or a behavioral
    probe (run/observe — advances on a Run). The game master presents each beat and reasons about
    what the student did before moving on."""
    say: str
    advance: str = "reply"
    hint: str = ""

    def kind(self) -> str:
        return step_kind(self.advance)


def step_kind(advance: str) -> str:
    a = (advance or "reply").strip().lower()
    if a in ("reply", "ack", "say", "read", "observe", ""):
        return "reply"
    from . import probes as _probes
    if _probes.probe_ok(advance):
        return "behavioral"
    return "structural"


@dataclass
class Lesson:
    id: str
    title: str = ""
    brief: str = ""
    objectives: list[Objective] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)   # optional GUIDED beats (empty = free-form)
    forbid: list = field(default_factory=list)        # Forbid rules: predicates that must stay FALSE
    complete_when: str = "all"          # all | any | at_least(n)
    time_limit_s: int | None = None     # None = untimed
    attempts: int = 3
    help: str = "warmer_colder"
    persona: str = "coach"
    stage: str = ""                     # optional saved-canvas file to pre-build
    intent: Intent = field(default_factory=Intent)
    archetype: str = ""                 # the Game-Catalog archetype it was resolved from (if any)
    params: dict = field(default_factory=dict)
    genre: str = ""                     # experience | expedition | challenge (defaults from assembly)
    level: int | None = None            # quest level 0..3 (defaults from assembly; may be pinned)
    fragments: list = field(default_factory=list)   # fragment ids this lesson was assembled from

    def behavioral_ids(self) -> list[str]:
        return [o.id for o in self.objectives if o.is_behavioral()]

    @property
    def guided(self) -> bool:
        """True when the lesson leads the student through beats turn-by-turn (vs. free-form)."""
        return bool(self.steps)


class LessonError(ValueError):
    pass


# -- time parsing ----------------------------------------------------------- #
def parse_duration(v) -> int | None:
    """'25m' / '90s' / '1h' / 1500 (seconds) → seconds. None/'' → untimed."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip().lower()
    units = {"s": 1, "m": 60, "h": 3600}
    if s and s[-1] in units:
        try:
            return int(float(s[:-1]) * units[s[-1]])
        except ValueError as e:
            raise LessonError(f"bad duration {v!r}") from e
    try:
        return int(s)
    except ValueError as e:
        raise LessonError(f"bad duration {v!r}") from e


def _objective_from(d: dict) -> Objective:
    if "id" not in d:
        raise LessonError("objective missing 'id'")
    kind = d.get("kind", "structural")
    if kind not in _KINDS:
        raise LessonError(f"objective {d['id']}: bad kind {kind!r}")
    return Objective(id=d["id"], say=d.get("say", d["id"]), kind=kind,
                     check=d.get("check", ""), probe=d.get("probe", ""))


def from_dict(d: dict) -> Lesson:
    """Build a Lesson from a plain dict (already-parsed YAML)."""
    if "id" not in d:
        raise LessonError("lesson missing 'id'")
    it = d.get("intent", {}) or {}
    intent = Intent(concept=it.get("concept", ""), goal=it.get("goal", ""),
                    spirit=it.get("spirit", ""),
                    misconceptions=list(it.get("misconceptions", []) or []))
    objs = [_objective_from(o) for o in (d.get("objectives", []) or [])]
    steps = [Step(say=s.get("say", ""), advance=s.get("advance", "reply"), hint=s.get("hint", ""))
             for s in (d.get("steps", []) or [])]
    forbid = [Forbid(say=f.get("say", ""), check=f.get("check", ""))
              for f in (d.get("forbid", []) or [])]
    return Lesson(
        id=d["id"], title=d.get("title", ""), brief=d.get("brief", ""),
        objectives=objs, steps=steps, forbid=forbid, complete_when=d.get("complete_when", "all"),
        time_limit_s=parse_duration(d.get("time_limit")),
        attempts=int(d.get("attempts", 3)), help=d.get("help", "warmer_colder"),
        persona=d.get("persona", "coach"), stage=d.get("stage", ""), intent=intent,
        archetype=d.get("archetype", ""), params=dict(d.get("params", {}) or {}),
        genre=d.get("genre", ""), level=d.get("level"),
        fragments=list(d.get("fragments", []) or []),
    )


def from_yaml(text: str) -> Lesson:
    import yaml            # PyYAML ships with the app; imported lazily
    return from_dict(yaml.safe_load(text))


def from_archetype(archetype_id: str, params: dict, *, id: str, title: str = "",
                   brief: str = "", **over) -> Lesson:
    """Build a concrete Lesson by instantiating a Game-Catalog archetype (the shortcut the
    resolver will use; handy for explicit authoring + tests)."""
    arch = _catalog.get(archetype_id)
    if arch is None:
        raise LessonError(f"unknown archetype {archetype_id!r}")
    missing = _catalog.unbound_refs(arch, params)
    if missing:
        raise LessonError(f"archetype {archetype_id!r} missing params: {missing}")
    objs = _catalog.instantiate(arch, params)
    intent = Intent(concept=arch.teaches, goal=arch.summary, spirit=arch.spirit,
                    misconceptions=list(arch.misconceptions))
    return Lesson(id=id, title=title or arch.summary, brief=brief or arch.summary,
                  objectives=objs, complete_when=over.get("complete_when", arch.complete_when),
                  time_limit_s=parse_duration(over.get("time_limit")),
                  attempts=int(over.get("attempts", 3)), help=over.get("help", "warmer_colder"),
                  persona=over.get("persona", "coach"),
                  stage=over.get("stage") or getattr(arch, "stage", "") or "",
                  intent=intent, archetype=archetype_id, params=dict(params))


# -- validation ------------------------------------------------------------- #
def validate(lesson: Lesson) -> list[str]:
    """Return a list of problems (empty = valid). Catches bad predicates, unknown element
    types, empty objectives, and bad enum values — the authoring safety net."""
    problems: list[str] = []
    if not lesson.objectives:
        problems.append("lesson has no objectives")
    if lesson.help not in _HELP:
        problems.append(f"bad help level {lesson.help!r}")
    if lesson.persona not in _PERSONA:
        problems.append(f"bad persona {lesson.persona!r}")
    cw = lesson.complete_when
    if cw not in ("all", "any") and not cw.startswith("at_least("):
        problems.append(f"bad complete_when {cw!r}")
    if lesson.attempts < 1:
        problems.append("attempts must be >= 1")
    seen = set()
    for o in lesson.objectives:
        if o.id in seen:
            problems.append(f"duplicate objective id {o.id!r}")
        seen.add(o.id)
        if o.kind == "structural":
            if not o.check:
                problems.append(f"structural objective {o.id!r} has no check")
            elif not check_ok(o.check):
                problems.append(f"objective {o.id!r}: check does not parse: {o.check!r}")
            else:
                bad = unknown_element_types(o.check)
                if bad:
                    problems.append(f"objective {o.id!r}: unknown element types {bad}")
        elif o.kind == "behavioral" and not o.probe:
            problems.append(f"behavioral objective {o.id!r} has no probe")
    for i, s in enumerate(lesson.steps):
        if not s.say:
            problems.append(f"step {i + 1} has no instruction ('say')")
        k = s.kind()
        if k == "structural" and not check_ok(s.advance):
            problems.append(f"step {i + 1}: advance predicate does not parse: {s.advance!r}")
        elif k == "structural":
            bad = unknown_element_types(s.advance)
            if bad:
                problems.append(f"step {i + 1}: unknown element types {bad}")
    return problems


def is_valid(lesson: Lesson) -> bool:
    return not validate(lesson)
