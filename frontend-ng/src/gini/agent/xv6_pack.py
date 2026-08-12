"""Xv6Pack — the OS DomainPack.

Plugs xv6/OS onto the domain-neutral engine (composition, probes, grader, catalog, Teaching Center)
exactly the way NetworkingPack does for networking (see agent/domains.py: "Cloud and xv6/OS become
future packs implementing the same protocol"). Nothing here is a new engine — it is adapters:

  • fragments  — the OS assignments, authored as YAML in domain/missions/os/ (loaded like any pack).
  • behavioral oracle — the OS win-conditions are ordinary `measure(...)` probes; the runner that
    computes them from live kernel telemetry is `domain.xv6_runner.Xv6Runner` (built + tested). The
    grader evaluates a behavioral objective via `objectives.evaluate(obj, world, runner=Xv6Runner)`.
  • palette / vocabulary / knowledge — the xv6 element + reused capability roles + OS concept notes.
"""
from __future__ import annotations

from pathlib import Path

from .contracts import Observation


class Xv6Observer:
    """Senses the running xv6 (its Machine state / telemetry) as an Observation for the blackboard.
    Best-effort: whatever `world` the framework hands it (a MachineState or a serialised view)."""
    id = "xv6"

    def observe(self, world) -> list:
        data = world.to_dict() if hasattr(world, "to_dict") else world
        return [Observation(source="xv6", data=data)]


def _os_missions_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "domain" / "missions" / "os"


class Xv6Pack:
    """The OS domain pack. Same seven-member protocol as NetworkingPack."""
    name = "os"

    def verifiers(self, lesson) -> list:
        return []                                    # structural verifiers reused; OS grading is behavioral

    def observers(self) -> list:
        return [Xv6Observer()]

    def vocabulary(self):
        from ..domain import capabilities                # reuse the capability is-a hierarchy
        return capabilities

    def predicates(self) -> set:
        from ..domain.objectives import _PREDICATES      # structural predicates reused as-is
        return set(_PREDICATES)

    def fragments(self) -> list:
        """The OS assignments — YAML fragments in domain/missions/os/, loaded like any pack."""
        from ..domain import fragment_yaml as _fy
        loaded, _warn = _fy.load_dir(str(_os_missions_dir()), strict=False)
        return list(loaded.values())

    def palette(self) -> dict:
        from ..domain.devices import REGISTRY
        return {k: v for k, v in REGISTRY.items()
                if k in ("xv6", "terminal", "storage_volume")}

    def knowledge(self):
        from . import kb
        return kb

    # -- the behavioral oracle seam (not in the protocol, but how OS probes get graded) --------- #
    def runner(self, machine_state, window: int = 60):
        """An `Xv6Runner` over the live kernel telemetry — the runner the grader passes to
        `objectives.evaluate(...)` for this domain's behavioral (`measure(...)`) objectives."""
        from ..domain.xv6_runner import Xv6Runner
        shadows = machine_state.shadows() if hasattr(machine_state, "shadows") else {}
        return Xv6Runner(getattr(machine_state, "latest", None),
                         getattr(machine_state, "timeline", None), shadows, window)
