"""Ask GINI orchestration (pure) — turn an Intent + retrieved knowledge into a Plan, and
assemble the grounded context for the reasoning call.

This is the routing matrix from the design, kept Qt-free so it's testable. The assistant
(Qt side) calls `plan()` to decide what to do and `grounded_context()` to build the block
the reasoning LLM sees; the actual building (apply_recipe), tool execution, and chat posting
happen in the assistant.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Plan:
    action: str                 # reason | build_recipe | execute | diagnose | clarify | chitchat | meta
    recipe_id: str = ""         # for build_recipe, or the recipe to offer on a reason
    offer_build: bool = False   # reason path: end by offering to build recipe_id
    clarify: str = ""           # for clarify


_CLARIFY = ("Could you say a bit more? For example, name an element you want explained, "
            "or tell me what you'd like to build.")


def plan(intent, retrieval, *, min_confidence: float = 0.45) -> Plan:
    """Dispatch by (type x output_form x anchor). See the design's routing matrix."""
    it = intent
    recipe = getattr(retrieval, "recipe", None)
    rid = recipe.id if recipe is not None else ""

    if it.type == "chitchat":
        return Plan("chitchat")
    if it.type == "meta":
        return Plan("meta")

    # genuinely ambiguous fragment -> ask one question rather than guess
    if it.confidence < min_confidence and not it.topics and not it.refs:
        return Plan("clarify", clarify=_CLARIFY)

    # imperative command ("add a router", "connect R1 and S1") -> tool execution
    if it.type == "build" and it.output_form not in ("show", "guide"):
        return Plan("execute")

    if it.type == "diagnose":
        return Plan("diagnose")

    # generative "show me / construct / build" with a known pattern
    if it.output_form in ("show", "guide") and recipe is not None:
        if it.anchor == "concept":
            return Plan("build_recipe", recipe_id=rid)         # empty canvas -> auto-build
        return Plan("reason", offer_build=True, recipe_id=rid)  # populated -> offer first

    # explain / how_to / tell -> reason, offering the example if we have one
    return Plan("reason", offer_build=recipe is not None, recipe_id=rid)


_STANCE = {
    "strong": (
        "GROUNDING STANCE — CLOSED WORLD: the GINI knowledge and canvas below fully cover this "
        "question. Answer only from them; every element you name must appear in the elements "
        "list."
    ),
    "thin": (
        "GROUNDING STANCE — MOSTLY CLOSED: the KB match here is only partial. Lean on what's "
        "provided; if a detail isn't covered, say so rather than inventing a GINI element."
    ),
    "empty": (
        "GROUNDING STANCE — OPEN BUT FENCED: this question falls outside GINI's built-in "
        "topics. You may answer from general knowledge, but do NOT claim any GINI element or "
        "feature exists unless it's in the elements list; if GINI has no element for it, say so "
        "plainly and point to the nearest thing GINI does have."
    ),
}


def grounding_stance(retrieval, intent=None) -> str:
    """One directive line telling the reasoner how tightly to stay on the GINI KB this turn,
    derived from retrieval strength. The 'never invent a GINI element' rule holds in EVERY
    tier — only the freedom to reason from general knowledge widens when the KB is thin."""
    # no retrieval at all → default to the closed-world floor (keep the student on GINI); the
    # open stance is only chosen when retrieval explicitly reports an empty/off-topic match.
    strength = getattr(retrieval, "strength", "strong") if retrieval is not None else "strong"
    return _STANCE.get(strength, _STANCE["strong"])


def grounded_context(always_on: str, accumulator: str, retrieval, canvas_digest: str,
                     intent, machine_card: str = "", stance: str = "") -> str:
    """Assemble the context block the reasoning LLM sees: grounding stance + always-on index +
    canvas + live machine (xv6) state + accumulated session knowledge + this turn's cards.

    `machine_card` is the xv6 kernel state card (from domain.machine_state) when the focus is a
    running xv6 Machine — a second 'ground truth' source alongside the canvas, so OS help is
    grounded in this student's actual kernel run. `stance` (from `grounding_stance`) is prepended
    so the model knows how closed-world to be for THIS question."""
    parts = []
    if stance:
        parts.append(stance)
    parts.append(always_on)
    if canvas_digest:
        parts.append("Current canvas (ground truth):\n" + canvas_digest)
    if machine_card:
        parts.append(machine_card)
    if accumulator:
        parts.append(accumulator)
    rc = retrieval.as_context() if retrieval is not None else ""
    if rc:
        parts.append(rc)
    return "\n\n".join(p for p in parts if p)


def machine_card_level(text: str) -> int:
    """How deep an xv6 state card the question warrants (progressive; keeps the small-LLM
    budget lean). 0 = scheduling picture; 1 = + registers/stack; 2 = + memory/FS."""
    q = (text or "").lower()
    if any(w in q for w in ("page table", "pagetable", "satp", "virtual memory", "paging",
                            "address space", "inode", "file system", "filesystem", "journal",
                            "buffer cache", "disk", "block")):
        return 2
    if any(w in q for w in ("register", "pc", "stack", "backtrace", "trap", "trapframe",
                            "context", "swtch", "ra ", "sp ", "program counter")):
        return 1
    return 0
