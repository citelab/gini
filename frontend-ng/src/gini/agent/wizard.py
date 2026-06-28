"""Wizard brain — LLM-driven, goal-directed building.

The Wizard is "X-ray with a goal", and the goal-relevance is decided by the model, per
step, in the context of the canvas so far:

  1. **Pick a starter** — given the goal + the element catalog (with descriptions), the
     model names the single best first element. We place it.
  2. **Walk the build** — around the current element the grammar proposes the *valid*
     neighbours; the model filters them to the ones that serve the goal (one batched
     call) and gives a one-line reason each. The student taps one; it's added + connected;
     repeat from there.

The grammar guarantees validity (the model only ever sees valid candidates); the model
adds goal-relevance. This module is the *pure* part — building prompts and parsing
replies — so it's testable without a live model. The threading/UI lives in the assistant.
"""
from __future__ import annotations

from ..domain.devices import REGISTRY, by_category


def element_catalog() -> str:
    """A compact 'Label: one-line description' list of the placeable elements."""
    lines = []
    for items in by_category().values():
        for d in items:
            desc = d.description.split(". ")[0].rstrip(".")
            lines.append(f"- {d.label}: {desc}.")
    return "\n".join(lines)


def _label_key_map() -> dict[str, str]:
    m: dict[str, str] = {}
    for key, d in REGISTRY.items():
        if not d.hidden:
            m[d.label.lower()] = key
            m[key.lower()] = key
    return m


# ---------------------------------------------------------------- starter ---- #
def starter_prompt(goal: str, catalog: str) -> str:
    # NOTE: do NOT put a concrete element in the example — a small model will copy it and
    # answer the same element for every goal. Use a neutral placeholder, and make the model
    # state its final choice on an explicit PICK: line so we read its CONCLUSION, not the
    # first element it happens to mention while reasoning.
    return (f'A student wants to build this and nothing else: "{goal}".\n\n'
            f"Choose the single best FIRST element strictly from this list:\n{catalog}\n\n"
            "Pick the ONE element that is the foundation for THAT specific goal, matching its "
            "domain (e.g. a networking goal needs networking gear, not cloud/Kubernetes). "
            "You may reason briefly, but END with ONE final line in EXACTLY this form, "
            "copying a real element name from the list:\n"
            "    PICK: <element name> - <short reason it fits this goal>")


def starter_retry_prompt(goal: str, names: str) -> str:
    """A terse re-ask used when the model didn't return a clean, valid pick."""
    return (f'Goal: "{goal}".\nValid elements: {names}.\n'
            "Reply with ONLY this one line and nothing else:\n"
            "PICK: <one exact element name from the list above>")


def element_names() -> str:
    """Just the comma-separated element labels (for the terse retry prompt)."""
    from .. domain.devices import by_category
    return ", ".join(d.label for items in by_category().values() for d in items)


def _validate(name: str) -> str | None:
    """Strict: a candidate string is accepted ONLY if it is exactly one element label/key
    (after dropping a leading article). No fuzzy matching, no guessing among several."""
    n = (name or "").strip().strip(".\"'`").lower()
    for art in ("a ", "an ", "the "):
        if n.startswith(art):
            n = n[len(art):].strip()
    return _label_key_map().get(n)


def parse_starter(text: str) -> tuple[str | None, str]:
    """Return (type_key, reason) ONLY when the model gave a clean, validated pick:
    1) a 'PICK: <element> - <reason>' line whose element validates exactly; or
    2) a line that IS '<element> - <reason>' (its lead segment validates exactly).
    Anything ambiguous/rambling returns (None, '') — the caller re-asks rather than guess."""
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line[:5].lower() == "pick:":
            line = line[5:].strip()
        elif not line:
            continue
        head, _, reason = line.partition(" - ")
        if " — " in head and not _validate(head):     # tolerate an em-dash separator
            head, _, reason = line.partition(" — ")
        key = _validate(head)
        if key is not None:
            return key, reason.strip(" -:–—\t").strip()
    return None, ""


# ------------------------------------------------------------- neighbours ---- #
def filter_prompt(goal: str, current_label: str, candidates, canvas_summary: str) -> str:
    """`candidates` = list[(type_key, label)] — the grammar-valid neighbours of the
    current element. Ask the model which serve the goal."""
    opts = "\n".join(f"- {lbl}" for _k, lbl in candidates)
    canvas = f"\nWhat's built so far: {canvas_summary}\n" if canvas_summary else "\n"
    return (f'The student\'s goal is: "{goal}".\n'
            f"They just placed a {current_label}.{canvas}"
            f"These elements can validly connect to the {current_label}:\n{opts}\n\n"
            "List ONLY the ones that move the design toward the goal, best first, each on "
            "its own line as:  <Element name> - <short reason it helps the goal>. "
            "Omit ones that don't serve this goal.")


def parse_filter(text: str, candidates) -> list[tuple[str, str]]:
    """Parse the model's picks back to (type_key, reason), restricted to `candidates`."""
    by_label = {lbl.lower(): key for key, lbl in candidates}
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw in (text or "").splitlines():
        line = raw.strip(" -*•\t")
        if not line:
            continue
        low = line.lower()
        for lbl in sorted(by_label, key=len, reverse=True):     # longest label first
            if low.startswith(lbl) and by_label[lbl] not in seen:
                key = by_label[lbl]
                reason = line[len(lbl):].lstrip(" -:–—").strip()
                out.append((key, reason or "fits the goal"))
                seen.add(key)
                break
    if out:
        return out
    # fallback: scan the whole text for any candidate label mentioned, in candidate order
    low = (text or "").lower()
    for key, lbl in candidates:
        if lbl.lower() in low and key not in seen:
            out.append((key, "fits the goal"))
            seen.add(key)
    return out
