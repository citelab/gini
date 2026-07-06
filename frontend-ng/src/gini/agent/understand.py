"""Understanding front-door — raw NL (question / sentence / fragment) -> structured Intent.

Everything downstream (retrieval, routing, reasoning) keys off the Intent, so this is the
gate. It runs on the always-on context (element index + canvas + recent turns) because a
fragment ("why broke", "the db thing") only resolves against the canvas and history.

Two-tier, cheap-first:
  1. a deterministic parse handles the clear cases (element names, command verbs, concept
     keywords) at zero latency;
  2. an optional LLM refine (same model) only fires when the deterministic parse is
     low-confidence — a small, focused prompt, NOT the full tutor prompt.

The active mode biases the Intent (Coach -> diagnose, Wizard -> guide, Explain -> explain)
but doesn't fully override it. Pure logic + an injectable `llm` callable, so it's testable.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ..domain import concepts as _concepts
from ..domain import recipes as _recipes
from ..domain.devices import all_devices

TYPES = ("explain", "how_to", "build", "diagnose", "meta", "chitchat")
ANCHORS = ("canvas", "concept", "hybrid")
FORMS = ("tell", "show", "guide")


@dataclass
class Intent:
    type: str = "explain"
    anchor: str = "concept"
    output_form: str = "tell"
    topics: list[str] = field(default_factory=list)
    refs: list[str] = field(default_factory=list)
    depth: str = "shallow"
    confidence: float = 0.5
    query: str = ""

    def to_dict(self) -> dict:
        return {"type": self.type, "anchor": self.anchor, "output_form": self.output_form,
                "topics": self.topics, "refs": self.refs, "depth": self.depth,
                "confidence": round(self.confidence, 2)}


# -- keyword tables --------------------------------------------------------- #
_BUILD_CMD = ("add ", "connect ", "remove ", "delete ", "place ", "wire ", "attach ", "rename ")
_HOWTO = ("how to", "how do i", "how do you", "how can i", "how would i", "how should i",
          "how we", "how do we", "how can we", "show how", "show me how")
# verbs that mean "materialise it" (vs. "use / work" which stay explanatory)
_BUILD_WORDS = ("create", "build", "set up", "setup", "construct", "make", "deploy",
                "implement", "scaffold")
_SHOW = ("show me", "show an example", "give me an example", "construct", "build me",
         "build a", "build an", "set up", "setup", "lay out", "demonstrate", "create a",
         "create an", "make a", "make an")
_GUIDE = ("walk me through", "guide me", "step by step", "step-by-step", "help me build")
_EXPLAIN = ("explain", "what is", "what's a", "what are", "what does", "tell me about",
            "describe", "when do i", "when to use", "when should i")
_DIAGNOSE = ("what's wrong", "whats wrong", "is my", "are my", "check my", "not working",
             "unreachable", "why isn't", "why can't", "why won't", "broken", "fix ",
             "what is wrong", "isn't working", "doesn't work")
_META = ("what can you do", "which elements", "what elements", "list the", "what do you",
         "help me get started", "what can i build")
_GREET = ("hi", "hello", "hey", "thanks", "thank you", "yo", "sup")
_DEEP = ("why", "how does", "how do", "under the hood", "in detail", "explain how",
         "difference between", "compare", "what happens when", "trade-off", "tradeoff",
         "internally", "actually work")
_DEMONSTRATIVE = ("this ", "these ", " my ", " here", "the current", "my canvas", "my setup",
                  "my topology")


def _has(text: str, needles) -> bool:
    return any(n in text for n in needles)


def _extract_topics(text: str) -> list[str]:
    """Concept keywords, element keys, and recipe tags present in the text (dedup, ordered)."""
    topics: list[str] = []

    def add(t):
        if t and t not in topics:
            topics.append(t)

    for c in _concepts.CONCEPTS:
        for kw in c.keywords:
            if kw in text:
                add(kw)
    for d in all_devices():
        if re.search(rf"\b{re.escape(d.key)}\b", text) or d.label.lower() in text:
            add(d.key)
    for r in _recipes.RECIPES:
        for tag in r.intent:
            if tag in text:
                add(tag)
    return topics


def _extract_refs(text: str, canvas_names) -> list[str]:
    """Device names from the canvas that appear as whole words in the text."""
    if not canvas_names:
        return []
    low = text.lower()
    return [n for n in canvas_names if re.search(rf"\b{re.escape(n.lower())}\b", low)]


# -- the parse -------------------------------------------------------------- #
def understand(text: str, *, canvas_names=None, mode: str = "chat",
               llm=None, min_confidence: float = 0.45) -> Intent:
    """Interpret one user message. `canvas_names` is the list of placed device names;
    `mode` is the active panel mode; `llm` is an optional callable(prompt)->str used only
    when the deterministic parse is unsure."""
    raw = (text or "").strip()
    low = raw.lower()
    words = low.split()
    canvas_empty = not canvas_names

    it = Intent(query=raw)
    it.topics = _extract_topics(low)
    it.refs = _extract_refs(raw, canvas_names or [])

    # --- type ---
    typed = True
    if _has(low, _GREET) and len(words) <= 3:
        it.type = "chitchat"
    elif _has(low, _META):
        it.type = "meta"
    elif any(low.startswith(v) for v in _BUILD_CMD):
        it.type = "build"                      # an imperative command
    elif _has(low, _DIAGNOSE):
        it.type = "diagnose"
    elif _has(low, _HOWTO):
        it.type = "how_to"
    elif _has(low, _SHOW):
        it.type = "how_to"                     # "show me / build a X" is a how-to-build
    elif _has(low, _EXPLAIN):
        it.type = "explain"
    elif it.topics:
        it.type = "explain"                    # a bare topic -> explain it
        typed = False
    else:
        it.type = "explain"
        typed = False

    # --- output_form ---
    if _has(low, _GUIDE):
        it.output_form = "guide"
    elif _has(low, _SHOW) or (it.type in ("how_to", "build")
                              and _has(low, ("example",) + _BUILD_WORDS)):
        it.output_form = "show"                # "show/how to CREATE/BUILD X" -> materialise
    else:
        it.output_form = "tell"                # "how to USE X" -> explain + offer to build

    # --- anchor ---
    generative = it.type in ("how_to", "build") and (
        it.output_form in ("show", "guide") or _has(low, _BUILD_WORDS + ("add",)))
    if canvas_empty:
        it.anchor = "concept"
    elif it.refs or _has(low, _DEMONSTRATIVE):
        it.anchor = "hybrid" if generative else "canvas"
    elif generative:
        it.anchor = "hybrid"                   # build onto an existing canvas
    elif it.type == "diagnose":
        it.anchor = "canvas"
    else:
        it.anchor = "concept"

    # --- depth ---
    it.depth = "deep" if _has(low, _DEEP) else "shallow"

    # --- confidence ---
    conf = 0.5 + (0.3 if typed else 0.0) + (0.2 if it.topics else 0.0)
    if len(words) < 3 and not it.topics and not it.refs:
        conf -= 0.35
    it.confidence = max(0.0, min(1.0, conf))

    _apply_mode(it, mode, canvas_empty, low)

    # --- LLM refine only when unsure ---
    if it.confidence < min_confidence and llm is not None:
        _llm_refine(it, canvas_names, llm)
    return it


def _apply_mode(it: Intent, mode: str, canvas_empty: bool, low: str = "") -> None:
    """The active panel mode biases the Intent (doesn't fully override the parse)."""
    if mode == "coach":
        it.type = "diagnose"
        it.anchor = "concept" if canvas_empty else "canvas"
        it.output_form = "tell"
    elif mode == "wizard":
        # Wizard is for guided building: a bare goal ("a serverless api") is a build target
        # unless the student explicitly asked to be told about something.
        if not _has(low, _EXPLAIN) and not _has(low, _META):
            it.type = "build"
        it.output_form = "guide"
    elif mode == "explain" and it.type not in ("diagnose", "build"):
        it.type = "explain"


_REFINE_PROMPT = (
    "You interpret a student's message in the GINI network/cloud lab. Reply with ONLY a "
    "JSON object: {\"type\": explain|how_to|build|diagnose|meta|chitchat, \"anchor\": "
    "canvas|concept|hybrid, \"output_form\": tell|show|guide, \"topics\": [..], \"depth\": "
    "shallow|deep}. No prose.\n"
)


def _llm_refine(it: Intent, canvas_names, llm) -> None:
    """Ask the model to interpret an ambiguous fragment; merge if it returns valid JSON."""
    ctx = f"Canvas devices: {', '.join(canvas_names) if canvas_names else '(empty)'}.\n"
    try:
        out = llm(_REFINE_PROMPT + ctx + f"Message: {it.query!r}")
        obj = _first_json(out)
    except Exception:
        obj = None
    if not isinstance(obj, dict):
        return
    if obj.get("type") in TYPES:
        it.type = obj["type"]
    if obj.get("anchor") in ANCHORS:
        it.anchor = obj["anchor"]
    if obj.get("output_form") in FORMS:
        it.output_form = obj["output_form"]
    if obj.get("depth") in ("shallow", "deep"):
        it.depth = obj["depth"]
    if isinstance(obj.get("topics"), list):
        for t in obj["topics"]:
            if isinstance(t, str) and t.strip() and t not in it.topics:
                it.topics.append(t.strip())
    it.confidence = max(it.confidence, 0.6)    # the model resolved it


def _first_json(text: str):
    depth = 0
    start = None
    for i, ch in enumerate(text or ""):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    start = None
    return None
