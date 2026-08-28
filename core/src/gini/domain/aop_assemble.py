"""Selection → AOP. The deterministic half of plan generation.

The model's entire output is a `Selection`: which certified patterns, with which parameters. This
module expands that into the plan. No LLM is imported here and none may ever be — the split is the
design's central claim, and it is what lets a plan be reproduced, reviewed and defended.

**Why not let the model write the plan directly?** Because then nothing could be checked. A
selection is finite (patterns come from a catalogue), small enough for a person to read, and
expands the same way every time. The expansion is where correctness lives; the model only chooses
among options that were correct before it ran.

Deterministic here means literally reproducible: the same `Selection` always yields the same
`plan_hash`, so a teacher can regenerate a plan and confirm it is the one their codes were minted
against. Anything that would vary — timestamps, versions — is an explicit argument rather than
something read from the clock inside the expansion.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import aop_patterns as _patterns
from .aop import (BLANK, Aop, Expectation, Header, Observation, plan_hash,
                  validate, validate_or_raise)
from .proof import canonical_json


@dataclass(frozen=True)
class PatternRef:
    """One chosen pattern and the parameters bound to it."""
    key: str
    params: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"key": self.key, "params": dict(self.params)}


@dataclass(frozen=True)
class Selection:
    """Everything the model decided — the whole of its output, and nothing else.

    Small on purpose. A teacher can read a selection in a few seconds and see what was chosen;
    they could not read three hundred expectations. Review happens here, and the expansion is
    trusted because it is mechanical.
    """
    intent: str = ""
    patterns: tuple = ()                        # (PatternRef, …), in the order they apply
    params: dict = field(default_factory=dict)  # activity-level: starting_point, guidance, …
    answers: tuple = ()                         # ({"q":…, "a":…}, …) from the clarification loop
    deadline_s: float | None = None

    def to_dict(self) -> dict:
        return {"intent": self.intent,
                "patterns": [p.to_dict() for p in self.patterns],
                "params": dict(self.params),
                "answers": [dict(a) for a in self.answers],
                # Coerced, because 1800 and 1800.0 serialize differently and a digest that changed
                # across a save/load round trip would be worthless for confirming that a
                # regenerated plan is the one the codes were minted against.
                "deadline_s": None if self.deadline_s is None else float(self.deadline_s)}

    @classmethod
    def from_dict(cls, d: dict) -> "Selection":
        return cls(intent=str(d.get("intent", "")),
                   patterns=tuple(PatternRef(key=str(p.get("key", "")),
                                             params=dict(p.get("params") or {}))
                                  for p in (d.get("patterns") or ())),
                   params=dict(d.get("params") or {}),
                   answers=tuple(dict(a) for a in (d.get("answers") or ())),
                   deadline_s=(None if d.get("deadline_s") in (None, "")
                               else float(d["deadline_s"])))

    def digest(self) -> str:
        """A stable fingerprint of the choice itself, useful for caching a generation and for
        showing a teacher that two plans came from the same decisions."""
        import hashlib
        return hashlib.sha256(canonical_json(self.to_dict()).encode("utf-8")).hexdigest()


class SelectionError(ValueError):
    """A selection that cannot be expanded — an unknown pattern, or an unknown parameter.

    Unknown *parameters* are refused rather than ignored, because a model that invents
    `params={"routers": 3, "subnets": 4}` is telling you it believes the plan will constrain
    subnets. Silently dropping the key would produce a plan that quietly means something else than
    the teacher was shown.
    """


def expand(selection: Selection) -> list[Expectation]:
    """The selection's expectations, in pattern order, merged and with unique ids.

    Patterns overlap on purpose. Selecting `single-lan` *and* `multi-lan` is how a teacher assigns
    the whole of Chapter 16, and both assert that every station reaches every other. Two responses
    were possible and only one is right:

    * **Refuse the selection** — wrong. It is a reasonable thing to ask for, and the teacher would
      have no way to express "both sections" without hitting the error.
    * **Merge identical observations** — right. `reach(host -> host, all) == ok` asserted twice
      tells you nothing the first assertion did not, always agrees with itself, and costs a second
      `docker exec` per cycle to learn the same fact.

    So identical (observation, sense) pairs collapse to the first, and anything that depended on a
    collapsed expectation is rewired to the survivor. Distinct expectations that merely share an
    *id* are suffixed with their pattern key instead, so nothing is silently overwritten.

    The survivor keeps the first pattern's `requires`. That is the conservative choice: the two
    make the same observation, so their preconditions are about the same facts, and taking the
    earlier (lower-layer) pattern's dependencies cannot make the expectation fire sooner than
    either pattern intended.
    """
    out: list[Expectation] = []
    by_id: set[str] = set()
    by_observation: dict[tuple, str] = {}
    rewire: dict[str, str] = {}          # dropped-or-renamed id -> the id that survived

    for ref in selection.patterns:
        pattern = _patterns.get(ref.key)                      # raises KeyError if uncertified
        unknown = set(ref.params) - set(pattern.params)
        if unknown:
            raise SelectionError(
                f"pattern {ref.key!r} has no parameter(s) {', '.join(sorted(unknown))}; "
                f"it accepts: {', '.join(sorted(pattern.params)) or '(none)'}")

        for e in pattern.expectations(**ref.params):
            observation = (e.probe or e.check, e.sense)
            kept = by_observation.get(observation)
            if kept is not None:                    # same fact, already being observed
                rewire[e.id] = kept
                continue
            eid = e.id
            if eid in by_id:                        # different fact, colliding id
                eid = f"{e.id}-{ref.key}"
                rewire[e.id] = eid
            by_id.add(eid)
            by_observation[observation] = eid
            out.append(e if eid == e.id
                       else Expectation(**{**e.to_dict(), "id": eid,
                                           "requires": list(e.requires)}))

    if rewire:
        # One pass at the end rather than per pattern: a dependency may name an expectation that
        # was collapsed by a *later* pattern, which a per-batch rewrite would miss.
        resolved = []
        for e in out:
            deps = [rewire.get(d, d) for d in e.requires]
            deps = [d for d in dict.fromkeys(deps) if d != e.id]   # dedupe; drop self-edges
            resolved.append(e if list(e.requires) == deps
                            else Expectation(**{**e.to_dict(), "requires": deps}))
        out = resolved
    return out


def assemble(selection: Selection, *, created: float = 0.0, gini_version: str = "",
             cadence_s: float = 20.0, validate_plan: bool = True) -> Aop:
    """A selection → a validated, hashable AOP.

    `created` and `gini_version` are arguments rather than looked up here, so the expansion stays
    reproducible: assembling the same selection twice with the same arguments gives the same
    `plan_hash`. The Teaching Center supplies the real values once, at freeze time.

    `validate_plan=False` is for tests that want to inspect a deliberately broken plan. Production
    callers must leave it on — the validator is the gate that keeps a defective plan away from a
    student (design §5.1).
    """
    expectations = expand(selection)
    keys = [p.key for p in selection.patterns]
    header = Header(
        intent=selection.intent,
        params=dict(selection.params),
        patterns=tuple(keys),
        answers=tuple(selection.answers),
        created=created,
        gini_version=gini_version,
        observation=Observation(cadence_s=cadence_s),
        deadline_s=selection.deadline_s,
        # Pattern-level disclosure only. The one place student-facing text is produced, and it
        # comes from the pattern's own summary — never from an expectation (design §11).
        guidance=(tuple(_patterns.summaries(keys))
                  if selection.params.get("guidance") else ()),
    )
    plan = Aop(header=header, expectations=tuple(expectations))
    return validate_or_raise(plan) if validate_plan else plan


def starting_point(selection: Selection) -> str:
    return str(selection.params.get("starting_point", BLANK) or BLANK)


def dry_run(selection: Selection) -> list:
    """Defects this selection would produce, without raising — for the authoring loop, where a
    teacher should see what is wrong rather than an exception."""
    try:
        return validate(assemble(selection, validate_plan=False))
    except (SelectionError, KeyError) as e:
        from .aop import Defect
        return [Defect("selection", "", str(e))]


def describe(plan: Aop) -> str:
    """The plan as a teacher reads it during review: ordered, with dependencies shown.

    Deliberately plain text and model-free. The LLM's back-translation (design §3.2) is a separate,
    friendlier rendering; this one is the literal truth to check it against.
    """
    from .aop import evaluation_order
    lines = [f"{len(plan.expectations)} expectations · plan {plan_hash(plan)[:12]}…"]
    if plan.header.guidance:
        lines.append("")
        lines.append("The student is told:")
        lines += [f"  • {g}" for g in plan.header.guidance]
    lines.append("")
    for i, e in enumerate(evaluation_order(plan), 1):
        dep = f"   (after {', '.join(e.requires)})" if e.requires else ""
        lines.append(f"{i:2}. [{e.layer:6}] {e.say}{dep}")
        lines.append(f"     {'probe' if e.is_behavioural else 'check'}  {e.probe or e.check}")
    return "\n".join(lines)
