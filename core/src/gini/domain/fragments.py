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

from dataclasses import dataclass, field

from .content import content_dirs
from .objectives import Objective

CORE, EXERCISE, OBSERVE = "core", "exercise", "observe"

# fork kinds — see GINI_AUTHORING_DESIGN.md "FORKS — the difficulty knob"
CONVERGE, DIVERGE = "converge", "diverge"


@dataclass(frozen=True)
class ObjectiveTemplate:
    id: str
    say: str
    kind: str = "structural"     # structural | behavioral
    check: str = ""              # structural predicate (may contain {ref} placeholders)
    probe: str = ""              # behavioral probe (may contain {ref} placeholders)
    level: int | None = None     # explicit ladder tier (1 place · 2 connect · 3 group · 4 live)
    stars: int = 0               # difficulty PASS: 0 = base, 1+ = harder progressive passes


@dataclass(frozen=True)
class Slot:
    """A named, typed dependency socket — the composition non-terminal (see the graph-grammar model).
    `role` is the capability it needs (is-a matched, so a `network` slot accepts a LAN or a routed
    network). Cardinality `min..max` (max 0 = unbounded) makes it a fixed leg or a variable group
    (a router's ≥2 legs, a load balancer's N backends). `distinct` = must bind to a different
    provider than the fragment's other slots. Objectives reference a slot as `type@name`."""
    name: str
    role: str
    min: int = 1
    max: int = 1
    distinct: bool = True


@dataclass(frozen=True)
class Peering:
    """A LATERAL composition group — N sibling members of one role that interconnect as PEERS (a
    graph, possibly with cycles), unlike a Slot which links the delta to each member. `topology`
    shapes the graph: `mesh` (every pair), `ring`, `line`, or `star` (member 0 is the hub). This is
    the axis slots can't express — meshes/graphs of routers. Objectives reference it as `type@name`
    (all-pairs), and the assembler wires the members' connection points per topology."""
    name: str
    role: str
    min: int = 2
    max: int = 0                            # 0 = unbounded
    topology: str = "mesh"                  # mesh | ring | line | star


@dataclass(frozen=True)
class Fork:
    """A difficulty branch off the core. `converge` = a harder *way to the same goal* (rejoins the
    main line); `diverge` = a *different path* (may not rejoin). Completing a fork lifts the band
    toward gold — difficulty is how deep into the forks you go."""
    id: str
    label: str = ""
    difficulty: int = 1                     # 1 = the golden path; higher = harder
    kind: str = CONVERGE                    # converge | diverge
    objectives: tuple[ObjectiveTemplate, ...] = ()

    def instantiate(self) -> list[Objective]:
        return [Objective(id=t.id, say=t.say, kind=t.kind, check=t.check, probe=t.probe,
                          level=t.level, stars=getattr(t, "stars", 0)) for t in self.objectives]


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
    slots: tuple[Slot, ...] = ()           # named dependency sockets (the composition non-terminals)
    peerings: tuple[Peering, ...] = ()     # lateral peer groups (meshes/graphs — the Phase 5 axis)
    forks: tuple[Fork, ...] = ()            # optional difficulty branches (the golden-path model)
    # provenance / compatibility — stamped when a fragment is authored & blessed. Empty on built-ins
    # (they ARE the engine). Lets the TC / a student client refuse-with-reason on a version gap.
    engine_version: str = ""
    schema_version: int = 1
    author: str = ""
    certified: bool = False                 # runtime-playtested at the client (winnable + live)

    def instantiate(self) -> list[Objective]:
        return [Objective(id=t.id, say=t.say, kind=t.kind, check=t.check, probe=t.probe,
                          level=t.level, stars=getattr(t, "stars", 0)) for t in self.objectives]


# -- registry: fragments loaded from the SYSTEM layer + the USER layer -------- #
# Content is data (YAML), behaviour is code. The system layer (bundled built-ins) is authoritative and
# must be valid — a broken built-in is a bug and raises. The user layer (~/.gini/content/fragments:
# authored + OTA-pulled) is best-effort — a broken pack there is DECLINED, not fatal, so a bad OTA
# fragment can never brick a student's client. User fragments overlay system ones by id.
LOAD_WARNINGS: list[str] = []


def _load() -> dict[str, Fragment]:
    from . import content as _content
    from . import fragment_yaml as _fy          # deferred: fragment_yaml imports names from here
    LOAD_WARNINGS.clear()
    roots = _content.content_dirs()
    out: dict[str, Fragment] = {}
    for i, root in enumerate(roots):
        strict = (i == 0)                        # system layer strict; user layer best-effort
        loaded, warnings = _fy.load_dir(str(root), strict=strict)
        out.update(loaded)                       # later roots (user) overlay earlier (system)
        LOAD_WARNINGS.extend(warnings)
    return out


FRAGMENTS: dict[str, Fragment] = _load()


def reload() -> None:
    """Re-read the YAML packs from disk (for authoring / hot-edit / after an OTA pull)."""
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
