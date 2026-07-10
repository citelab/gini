"""The Game Catalog — an ADAPTER over the foundational fragments (now YAML packs under `missions/`).

Historically this module held the archetypes as Python literals. Those foundational templates now live
as data (YAML) and are loaded by `fragments`; this module presents the pickable/standalone fragments as
`Archetype`s so the older call sites (lesson.from_archetype, lesson_resolver, the mission picker) keep
working unchanged. Content moved to data; the API is stable.

`ObjectiveTemplate` is re-exported from `fragments` for back-compat. Objectives use the same type-based,
name-agnostic predicates (`exists`, `count`, `link`, `path`, `through`, `contains_type`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import fragments as _frag
from .fragments import ObjectiveTemplate  # noqa: F401  (re-export for back-compat)
from .objectives import Objective


@dataclass(frozen=True)
class Archetype:
    id: str
    teaches: str
    spirit: str
    summary: str
    params: tuple[str, ...] = ()
    objectives: tuple[ObjectiveTemplate, ...] = ()
    misconceptions: tuple[str, ...] = ()
    complete_when: str = "all"
    difficulty: dict = field(default_factory=dict)


# Type-based objectives need no name bindings, so archetypes launch with no params.
DEMO_PARAMS: dict[str, dict] = {}


def _arch_of(frag) -> Archetype:
    return Archetype(id=frag.id, teaches=frag.teaches, spirit=frag.spirit, summary=frag.summary,
                     params=(), objectives=tuple(frag.objectives),
                     misconceptions=tuple(frag.misconceptions), complete_when=frag.complete_when)


def get(archetype_id: str) -> Archetype | None:
    frag = _frag.get(archetype_id)
    return _arch_of(frag) if (frag is not None and frag.catalog) else None


def all_archetypes() -> list[Archetype]:
    """The standalone, pickable missions (fragments flagged `catalog`), preserving fragment order."""
    return [_arch_of(f) for f in _frag.all_fragments() if f.catalog]


def demo_params(archetype_id: str) -> dict:
    return dict(DEMO_PARAMS.get(archetype_id, {}))


# -- instantiation ---------------------------------------------------------- #
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _bind(text: str, params: dict) -> str:
    """Replace {ref} placeholders with concrete names (a no-op for type-based checks)."""
    return _PLACEHOLDER.sub(lambda m: str(params.get(m.group(1), m.group(0))), text)


def instantiate(archetype: Archetype, params: dict) -> list[Objective]:
    out: list[Objective] = []
    for t in archetype.objectives:
        out.append(Objective(id=t.id, say=t.say, kind=t.kind,
                             check=_bind(t.check, params) if t.check else "",
                             probe=_bind(t.probe, params) if t.probe else ""))
    return out


def unbound_refs(archetype: Archetype, params: dict) -> list[str]:
    refs: set[str] = set()
    for t in archetype.objectives:
        refs.update(_PLACEHOLDER.findall(t.check))
        refs.update(_PLACEHOLDER.findall(t.probe))
    return sorted(r for r in refs if r not in params)
