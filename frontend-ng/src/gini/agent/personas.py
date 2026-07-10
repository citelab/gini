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


@dataclass(frozen=True)
class Persona:
    name: str
    system: str                 # "you do ONLY this" — no general-assistant framing
    temperature: float = 0.3
    stateful: bool = False       # only the Reasoning persona is stateful (reads shared memory)


class PersonaRunner:
    """Invokes personas on one injected model. `llm` is `llm(prompt) -> str`, or
    `llm(prompt, temperature=...) -> str` (the runner passes temperature when the callable accepts it)."""

    def __init__(self, llm) -> None:
        self._llm = llm
        self._accepts_temp = False
        try:
            self._accepts_temp = "temperature" in inspect.signature(llm).parameters
        except (TypeError, ValueError):
            self._accepts_temp = False
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
        try:
            if self._accepts_temp:
                return (self._llm(prompt, temperature=persona.temperature) or "").strip()
            return (self._llm(prompt) or "").strip()
        except Exception:
            return ""
