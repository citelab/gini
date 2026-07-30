"""The engine's vocabulary — the discovery protocol (GINI_AUTHORING_DESIGN.md, "Discover").

A single, versioned manifest of everything a fragment can be built FROM on this engine: elements (+
their grammar), predicates, probes, and capability roles. gBuilder exports it; the Teaching Center
consumes it so it composes against a *declared version* rather than importing the live engine. This
is the forward-extensibility seam: when the engine gains a primitive, the exported vocabulary grows,
and authoring/composition see it automatically — no author-tool code change.
"""
from __future__ import annotations

from . import capabilities as _caps
from . import devices as _devices
from .content import ENGINE_VERSION, FRAGMENT_SCHEMA
from .objectives import _TYPE_ARG_FUNCS


def export() -> dict:
    """The full vocabulary manifest for this engine version."""
    elements = []
    for d in _devices.all_devices():
        cat = getattr(d, "category", None)
        elements.append({"key": d.key, "label": d.label,
                         "category": str(getattr(cat, "value", cat) or "")})
    return {
        "engine_version": ENGINE_VERSION,
        "schema_version": FRAGMENT_SCHEMA,
        "elements": sorted(elements, key=lambda e: e["key"]),
        # structural predicates that take element type_keys as args
        "predicates": sorted(_TYPE_ARG_FUNCS),
        # behavioral probes the runtime can witness (measure = a Source/Sink measurement assertion)
        "probes": ["reach", "ping", "http", "balances", "flow_installed", "measure"],
        # capability roles (the provides/requires vocabulary), as an is-a map
        "capabilities": dict(sorted(_caps.PARENTS.items())),
    }


def is_compatible(fragment_engine: str) -> tuple[bool, str]:
    """Coarse version gate: can content authored on `fragment_engine` be trusted here? Empty = a
    built-in (always fine). The FINE gate is validation against the real vocabulary (an unknown
    primitive fails); this is the friendly up-front check + message."""
    if not fragment_engine or fragment_engine == ENGINE_VERSION:
        return True, ""
    try:
        theirs = tuple(int(x) for x in fragment_engine.split("."))
        ours = tuple(int(x) for x in ENGINE_VERSION.split("."))
    except ValueError:
        return True, ""                          # unparseable → let validation be the judge
    if theirs > ours:
        return False, (f"authored on gBuilder {fragment_engine}, but this is {ENGINE_VERSION} — "
                       f"update the engine to use it")
    return True, ""                              # older content is fine (this engine is a superset)
