"""One small model, many narrow personas (GINI_MISSIONS_AGENT_ARCHITECTURE.md §5). A Persona is a
first-class object — a "you do ONLY this" system prompt, decoding params, and a stateful flag. The
PersonaRunner is the single, clean-isolation call site: it builds a prompt from exactly
`system + context + task` and carries NOTHING else across a call (the defence against persona-leak).

State lives on the blackboard (the shared curated memory), never inside the runner — so the runner is
stateless and any persona's "history" is reconstructed per call from the memory slice it's handed.
"""
from __future__ import annotations

import inspect
import json
from dataclasses import dataclass


def first_json(text: str):
    """The first balanced {...} object in a model reply, parsed — or None. Small models wrap JSON in
    prose; this is the tolerant extractor every persona uses to read structured output."""
    depth, start = 0, None
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


@dataclass(frozen=True, eq=False)
class Persona:
    name: str
    system: str                 # "you do ONLY this" — no general-assistant framing
    temperature: float = 0.3
    stateful: bool = False       # only the Reasoning persona is stateful (reads shared memory)
    schema: dict | None = None   # optional JSON Schema → decoder-constrained output (Reasoning 2.0)


class PersonaRunner:
    """Invokes personas on one injected model. `llm` is `llm(prompt) -> str`, optionally accepting
    `temperature=` and/or `schema=` keyword args (the runner passes each only when the callable
    accepts it — so simple lambdas in tests and older backends keep working unchanged)."""

    def __init__(self, llm) -> None:
        self._llm = llm
        self._accepts_temp = False
        self._accepts_schema = False
        try:
            params = inspect.signature(llm).parameters
            self._accepts_temp = "temperature" in params
            self._accepts_schema = "schema" in params
        except (TypeError, ValueError):
            pass
        self.last_prompt = ""    # for tests / debugging — the exact text last sent

    def call(self, persona: Persona, *, context: str = "", task: str = "") -> str:
        parts = [persona.system]
        if context:
            parts.append(context)
        if task:
            parts.append(task)
        prompt = "\n\n".join(p for p in parts if p).strip()
        self.last_prompt = prompt
        if self._llm is None:
            return ""
        kwargs = {}
        if self._accepts_temp:
            kwargs["temperature"] = persona.temperature
        if self._accepts_schema and persona.schema is not None:
            kwargs["schema"] = persona.schema
        try:
            return (self._llm(prompt, **kwargs) or "").strip()
        except Exception:
            return ""
