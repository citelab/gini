"""Mission fragments — the composable building blocks.

A *fragment* is smaller than a whole mission: a labelled piece (core / exercise / observe) that
declares a capability contract (`provides` / `requires`) so the assembler can join fragments into a
complete mission by graph closure. The 12 seed Game-Catalog archetypes are re-exposed here as
`core` fragments (with capability metadata attached — the catalog stays the source of their
objectives, so nothing regresses), plus a handful of *enrichment* fragments (the exercise/observe
layers) that make a bare skeleton educational.

See GINI_MISSIONS_COMPOSABLE_DESIGN.md §3–4. Pure data; no Qt, no LLM.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from .objectives import Objective

CORE, EXERCISE, OBSERVE = "core", "exercise", "observe"

# where the local foundational fragment YAMLs live (content-as-data). The Teaching Center distributes
# COMPOSITIONS that reference these ids — never new foundational fragments (see fragment_yaml).
_MISSIONS_DIR = os.path.join(os.path.dirname(__file__), "missions", "networking")


@dataclass(frozen=True)
class ObjectiveTemplate:
    id: str
    say: str
    kind: str = "structural"     # structural | behavioral
    check: str = ""              # structural predicate (may contain {ref} placeholders)
    probe: str = ""              # behavioral probe (may contain {ref} placeholders)


@dataclass(frozen=True)
class Fragment:
    id: str
    layer: str                              # core | exercise | observe
    teaches: str = ""                       # concepts.Concept.key
    spirit: str = ""                        # mechanism-free success description (game master reasons on it)
    summary: str = ""
    provides: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    objectives: tuple[ObjectiveTemplate, ...] = ()
    misconceptions: tuple[str, ...] = ()
    complete_when: str = "all"
    parent: str = ""                        # domain-taxonomy node (browsing / coarse selection)
    peers: tuple[str, ...] = ()             # horizontal "goes-with" hints
    step: str = ""                          # optional guided beat this fragment contributes
    catalog: bool = True                    # a standalone, pickable mission? (False = pure layer)
    stage: dict = field(default_factory=dict)   # optional pre-built board (M3 staging)

    def instantiate(self) -> list[Objective]:
        return [Objective(id=t.id, say=t.say, kind=t.kind, check=t.check, probe=t.probe)
                for t in self.objectives]


# -- registry: the foundational fragments, loaded from local YAML packs ------ #
# Content is data (YAML), behaviour is code. Editing a mission = editing a .yaml under missions/;
# the engine (assembly, verifiers, composer, the multi-agent stack) is unchanged.
def _load() -> dict[str, Fragment]:
    from . import fragment_yaml as _fy          # deferred: fragment_yaml imports names from here
    return _fy.load_dir(_MISSIONS_DIR)


FRAGMENTS: dict[str, Fragment] = _load()


def reload() -> None:
    """Re-read the YAML packs from disk (for authoring / hot-edit)."""
    global FRAGMENTS
    FRAGMENTS = _load()


def get(fragment_id: str) -> Fragment | None:
    return FRAGMENTS.get(fragment_id)


def all_fragments() -> list[Fragment]:
    return list(FRAGMENTS.values())


def cores() -> list[Fragment]:
    return [f for f in FRAGMENTS.values() if f.layer == CORE]


def by_layer(layer: str) -> list[Fragment]:
    return [f for f in FRAGMENTS.values() if f.layer == layer]


def provides_role(fragment: Fragment, required: str) -> bool:
    from . import capabilities as _caps
    return _caps.any_satisfies(fragment.provides, required)


def find_providers(required: str, *, layer: str | None = None) -> list[Fragment]:
    """Fragments that provide (satisfy) the `required` role, optionally restricted to a layer."""
    out = [f for f in FRAGMENTS.values() if provides_role(f, required)]
    if layer is not None:
        out = [f for f in out if f.layer == layer]
    return out
