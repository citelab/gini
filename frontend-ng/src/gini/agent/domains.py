"""Domain packs — the seam that makes the mission engine domain-agnostic (GINI_MISSIONS_AGENT_
ARCHITECTURE.md §8). The framework (blackboard, notifier, personas, reasoning) is blind to the domain;
a DomainPack supplies the domain-specific pieces behind one interface: the tiny Verifiers, the Observers,
the capability Vocabulary, the objective PredicateSet, the KB, the palette, and the fragments.

Networking is the first pack — it just wraps the modules we already have, so nothing is duplicated.
Cloud and xv6/OS become future packs implementing the same protocol; the reasoner discovers a pack's
tools by enumerating its verifiers/observers, which is the proof the reasoning is domain-neutral.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from .contracts import Observation


@runtime_checkable
class DomainPack(Protocol):
    name: str

    def verifiers(self, lesson) -> list: ...       # tiny verifiers for this lesson
    def observers(self) -> list: ...               # world sensors
    def vocabulary(self): ...                       # capability roles module (is-a hierarchy)
    def predicates(self) -> set: ...                # objective predicate names
    def fragments(self) -> list: ...                # composable content
    def palette(self) -> dict: ...                  # buildable/observable elements
    def knowledge(self): ...                        # KB module (retrieval)


class TopologyObserver:
    """Senses the networking world (the drawn topology) as a snapshot Observation."""
    id = "topology"

    def observe(self, world) -> list[Observation]:
        data = world.to_dict() if hasattr(world, "to_dict") else world
        return [Observation(source="topology", data=data)]


class NetworkingPack:
    """The first domain pack — a thin adapter over the existing networking modules."""
    name = "networking"

    def verifiers(self, lesson) -> list:
        from . import verifiers as _v
        return _v.for_lesson(lesson)

    def observers(self) -> list:
        return [TopologyObserver()]

    def vocabulary(self):
        from ..domain import capabilities
        return capabilities

    def predicates(self) -> set:
        from ..domain.objectives import _PREDICATES
        return set(_PREDICATES)

    def fragments(self) -> list:
        from ..domain import fragments
        return fragments.all_fragments()

    def palette(self) -> dict:
        from ..domain.devices import REGISTRY
        return dict(REGISTRY)

    def knowledge(self):
        from . import kb
        return kb


# -- registry --------------------------------------------------------------- #
_PACKS: dict[str, DomainPack] = {}


def register(pack: DomainPack) -> None:
    _PACKS[pack.name] = pack


def get(name: str) -> DomainPack | None:
    return _PACKS.get(name)


def names() -> list[str]:
    return list(_PACKS)


register(NetworkingPack())

# the OS pack — same protocol, drops xv6/OS onto the domain-neutral engine.
from .xv6_pack import Xv6Pack   # noqa: E402  (import here to avoid a cycle at module top)
register(Xv6Pack())
