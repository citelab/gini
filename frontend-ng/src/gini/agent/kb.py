"""GINI knowledge base — retrieval over the four assets that ground the agent.

Assets: the element catalog (`devices`), the connection grammar (`connection_rules`), the
concept notes (`concepts`), and the recipes (`recipes`). Two tiers:

  • `always_on_context()` — the compact element index + connection groups injected EVERY
    turn (~1.3K tokens, flat as the palette grows). Keeps the model on GINI's vocabulary.
  • `retrieve(intent, topology)` — the question-specific detail: concept notes for the
    topics (depth-gated), per-element cards with grammar-valid partners, and the recipe to
    describe/build. Pulled on demand and folded into the session accumulator by the caller.

Pure logic (no Qt); `topology` is duck-typed (needs `.devices` values with `.name`/`.type`).
`intent` is duck-typed too (`.topics`, `.anchor`, `.output_form`, `.refs`, `.depth`) so this
module doesn't depend on `understand` and stays independently testable.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..domain import connection_rules as _cr
from ..domain.devices import REGISTRY, all_devices

_HIDDEN = {"k8s_node"}          # not on the palette; never surface it


@dataclass
class Card:
    """A titled knowledge snippet destined for the reasoning prompt."""
    kind: str                   # element | grammar | concept | recipe
    key: str
    title: str
    text: str


@dataclass
class Retrieval:
    cards: list[Card] = field(default_factory=list)
    recipe: object | None = None      # recipes.Recipe when a build/show is implied
    strength: str = "empty"           # recall confidence (strong|thin|empty) → grounding stance

    def as_context(self) -> str:
        """Flatten to a context block for the model (empty string if nothing retrieved)."""
        if not self.cards:
            return ""
        return "Relevant GINI knowledge:\n" + "\n".join(f"- {c.text}" for c in self.cards)


# -- always-on tier --------------------------------------------------------- #
def _palette():
    return [d for d in all_devices() if d.key not in _HIDDEN]


def element_index() -> str:
    """Compact one-line-per-element catalog, grouped by category."""
    by_cat: dict[str, list[str]] = {}
    for d in _palette():
        by_cat.setdefault(d.category.value, []).append(f"{d.key} — {d.description}")
    lines = [f"[{cat}] " + "; ".join(items) for cat, items in by_cat.items()]
    return "GINI elements (the ONLY elements available):\n" + "\n".join(lines)


def group_defs() -> str:
    """The connection grammar's reusable groups — the compressed shape of the matrix."""
    parts = [f"{g}={{{', '.join(v)}}}" for g, v in _cr.GROUPS.items()]
    return "Connection groups: " + "; ".join(parts) + "."


def always_on_context() -> str:
    return element_index() + "\n" + group_defs()


# -- per-question tier ------------------------------------------------------ #
def _partners_line(type_key: str, cap: int = 10) -> str:
    ps = _cr.partners_for(type_key)
    if not ps:
        return "no standard connections"
    bits = [f"{p.type_key}{'*' if p.required else ''}" for p in ps[:cap]]
    more = "" if len(ps) <= cap else f", +{len(ps) - cap} more"
    return "connects to " + ", ".join(bits) + more + " (*=required)"


def element_card(type_key: str) -> Card | None:
    dt = REGISTRY.get(type_key)
    if dt is None or type_key in _HIDDEN:
        return None
    text = f"{dt.key} ({dt.label}): {dt.description} — {_partners_line(type_key)}"
    return Card("element", type_key, dt.label, text)


def concept_card(c) -> Card:
    return Card("concept", c.key, c.title, f"{c.title}: {c.body}")


def retrieve(intent, topology=None, *, llm=None, embedder=None) -> Retrieval:
    """Question-specific knowledge for an Intent. See module docstring for the contract.

    Retrieval runs through the hybrid `recall` layer (lexical → LLM-expansion → embeddings);
    pass `llm`/`embedder` to enable the L1/L2 fallbacks (they do I/O, so the caller only wires
    them on the worker thread). `out.strength` reports match confidence for the grounding
    stance."""
    from . import recall as _recall

    anchor = getattr(intent, "anchor", "concept")
    output_form = getattr(intent, "output_form", "tell")
    depth = getattr(intent, "depth", "shallow")
    topics = list(getattr(intent, "topics", []) or [])
    refs = list(getattr(intent, "refs", []) or [])
    query = getattr(intent, "query", "") or " ".join(topics)

    out = Retrieval()
    elem_keys: list[str] = []

    res = _recall.recall(query, topics, llm=llm, embedder=embedder, deep=(depth == "deep"))
    out.strength = res.strength

    # concept notes (depth-gated): deep questions get more of the "how it works" layer
    for h in res.concept_hits[: (2 if depth == "deep" else 1)]:
        c = h.obj
        out.cards.append(concept_card(c))
        elem_keys.extend(c.elements)

    # recipe pick — for generative/build asks, choose the pattern to describe or build
    if anchor in ("concept", "hybrid") and res.recipe_hits:
        out.recipe = res.recipe_hits[0].obj
        elem_keys.extend(el.type_key for el in out.recipe.elements)
        uniq = list(dict.fromkeys(el.type_key for el in out.recipe.elements))
        verb = "Example to build" if output_form in ("show", "guide") else "Canonical pattern"
        out.cards.append(Card("recipe", out.recipe.id, out.recipe.name,
                              f"{verb} — {out.recipe.name}: {out.recipe.summary} "
                              f"(uses ONLY these GINI elements: {', '.join(uniq)})"))

    # canvas anchor: pull the referenced devices' element cards (map name -> type)
    if anchor in ("canvas", "hybrid") and refs and topology is not None:
        by_name = {d.name: d for d in topology.devices.values()}
        for r in refs:
            d = by_name.get(r)
            if d is not None:
                elem_keys.append(d.type.key if hasattr(d, "type") else d.type_key)

    # element cards (dedup, order-preserving)
    seen: set[str] = set()
    for k in elem_keys:
        if k and k not in seen and k not in _HIDDEN:
            seen.add(k)
            card = element_card(k)
            if card is not None:
                out.cards.append(card)
    return out
