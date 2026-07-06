"""The Ask GINI topic cloud — an inviting, clickable set of things a student can explore or
build, generated from the live GINI knowledge base (concept notes, recipes, a few marquee
elements). Tapping a term SENDS the matching question/build to Ask GINI, so the cloud both
sparks ideas AND nudges the student toward vocabulary the (lexical) retriever lands on.

Framed as an invitation, not a cage — "GINI's great at these, or ask anything." Pure/data so
the cloud content is unit-tested; the UI renders these `CloudItem`s as a flow of pills.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import concepts as _concepts
from . import recipes as _recipes
from .devices import REGISTRY

# a colour rotation so the cloud looks lively (theme accent keys)
_ACCENTS = ["blue", "green", "purple", "teal", "indigo", "amber", "pink", "cyan", "orange"]
# recognisable elements worth a small pill (concepts + recipes carry the rest)
_MARQUEE = ["router", "switch", "firewall", "load_balancer", "function", "database",
            "vnf", "ovs", "container", "pod"]


@dataclass
class CloudItem:
    label: str          # what's shown on the pill
    kind: str           # concept | recipe | element
    query: str          # sent to Ask GINI when the pill is tapped
    weight: int         # 1..3 → font-size tier (concepts biggest)
    accent: str         # theme accent key for colour


def _short(c) -> str:
    """A punchy label for a concept — its canonical keyword, cased (acronyms upper)."""
    w = (c.keywords[0] if c.keywords else c.title)
    return w.upper() if len(w) <= 3 else w[:1].upper() + w[1:]


def _pill(text: str, cap: int = 22) -> str:
    """A tidy pill label: drop parentheticals and clip, so the cloud stays a cloud of
    short punchy words (the full name still goes in the query for retrieval)."""
    import re
    text = re.sub(r"\s*\([^)]*\)", "", text).strip()
    return text if len(text) <= cap else text[: cap - 1].rstrip() + "…"


def topic_cloud() -> list[CloudItem]:
    """Every explorable/buildable topic, as pills. Concepts → 'explain X' (big), recipes →
    'show me an X' build (medium), marquee elements → 'what is a X' (small)."""
    items: list[CloudItem] = []
    for i, c in enumerate(_concepts.CONCEPTS):
        lbl = _short(c)
        items.append(CloudItem(lbl, "concept", f"explain {lbl}", 3, _ACCENTS[i % len(_ACCENTS)]))
    for r in _recipes.RECIPES:
        items.append(CloudItem(_pill(r.name), "recipe",
                               f"show me a {r.name} example", 2, "green"))
    for key in _MARQUEE:
        dt = REGISTRY.get(key)
        if dt is not None:
            items.append(CloudItem(_pill(dt.label), "element", f"what is a {dt.label}?", 1,
                                   getattr(dt.accent, "value", "slate")))
    return items
