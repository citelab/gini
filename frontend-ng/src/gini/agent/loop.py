"""The agent loop: backend + tool registry -> a working assistant/tutor brain.

Runs the standard cycle — model proposes tool calls, we execute them against the shared
registry, feed results back, repeat until the model answers in prose. Supports native
tool calling and a JSON-action fallback for models that can't call tools, so GINI isn't
hostage to one model's tool-use quality.
"""
from __future__ import annotations

import json
from collections.abc import Callable

from .llm.backend import LLMBackend, Message, ToolCall
from .tools.registry import ToolRegistry

ASSISTANT_NAME = "GINI"

SYSTEM_PROMPT = (
    f"You are {ASSISTANT_NAME}, a friendly teaching assistant living inside the GINI "
    "gBuilder lab, where students build and run computer-network and cloud topologies. "
    "Before each question you are given the LIVE state of the student's canvas — every "
    "device, its IP addresses and subnets, and how things connect. Treat that state as "
    "ground truth and answer from it directly; you do NOT need a tool to see the canvas. "
    "Refer to devices by their names (e.g. R1, M1). Help students build, inspect, and "
    "understand their topology, and explain networking/cloud concepts clearly and "
    "concisely.\n"
    "You can TEACH ON THE CANVAS — when explaining how something works, use the present "
    "tools to direct the student's eye: `spotlight` a device to focus on it, `highlight` "
    "a set, `callout` to anchor a short note on a device, `narrate` a line of teaching, "
    "and `trace_path` + `animate_packet` to show how a packet flows between two hosts. "
    "Prefer showing on the canvas over long text. Use build tools (add/connect/remove/"
    "rename) only when asked to change the topology. When no tool is needed, answer in "
    "plain language.\n"
    "If you cannot call tools natively, emit a single JSON object on its own line: "
    '{\"tool\": \"<name>\", \"args\": { ... }} and nothing else.'
)


def _extract_json_objects(text: str) -> list[dict]:
    """Pull balanced-brace JSON objects out of free text (handles nested args)."""
    objs: list[dict] = []
    depth = 0
    start: int | None = None
    for i, ch in enumerate(text or ""):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    objs.append(json.loads(text[start:i + 1]))
                except json.JSONDecodeError:
                    pass
                start = None
    return objs


class AgentLoop:
    def __init__(self, backend: LLMBackend, registry: ToolRegistry,
                 system_prompt: str = SYSTEM_PROMPT, max_steps: int = 6,
                 context_provider: Callable[[], str] | None = None,
                 max_history: int = 30) -> None:
        self.backend = backend
        self.registry = registry
        self.max_steps = max_steps
        # returns a fresh snapshot of the live canvas; injected every turn
        self.context_provider = context_provider
        # keep the recent conversation (so the tutor remembers what was discussed) but
        # bound it so it never overflows the model's context window — a sliding window
        # over whole turns. The live canvas context is re-injected each turn regardless.
        self.max_history = max_history
        self.history: list[Message] = [Message("system", system_prompt)]

    def _trim(self) -> None:
        if len(self.history) <= self.max_history:
            return
        cut = len(self.history) - self.max_history
        # advance to the next 'user' message so we don't orphan assistant/tool turns
        while cut < len(self.history) and self.history[cut].role != "user":
            cut += 1
        if cut < len(self.history):
            self.history = [self.history[0]] + self.history[cut:]

    def _messages(self) -> list[Message]:
        """Base system prompt + a FRESH canvas-state message + the conversation.
        The context is regenerated each call (never stored), so the model always sees
        the current topology and the history doesn't fill up with stale snapshots."""
        msgs = [self.history[0]]
        if self.context_provider is not None:
            try:
                ctx = self.context_provider()
            except Exception:
                ctx = ""
            if ctx:
                msgs.append(Message("system", "Current canvas (ground truth):\n" + ctx))
        return msgs + self.history[1:]

    def send(self, user_text: str, on_text: Callable[[str], None] | None = None) -> str:
        """Run a full turn (possibly several tool round-trips) and return final prose."""
        self.history.append(Message("user", user_text))
        final = ""
        for _ in range(self.max_steps):
            text = ""
            calls: list[ToolCall] = []
            for chunk in self.backend.chat(self._messages(), tools=self.registry.openai_tools()):
                if chunk.text:
                    text += chunk.text
                    if on_text:
                        on_text(chunk.text)
                if chunk.tool_call:
                    calls.append(chunk.tool_call)

            # fallback: a model with no native tools may emit a JSON action in text
            if not calls:
                calls = self._parse_json_actions(text)

            self.history.append(Message("assistant", text, tool_calls=calls))

            if not calls:
                final = text
                break

            for tc in calls:
                result = self.registry.execute(tc.name, tc.arguments)
                self.history.append(Message("tool", json.dumps(result, default=str), name=tc.name))
        self._trim()                 # keep recent context, bound the window
        return final

    def _parse_json_actions(self, text: str) -> list[ToolCall]:
        calls: list[ToolCall] = []
        for obj in _extract_json_objects(text):
            name = obj.get("tool")
            if name in self.registry.names():
                calls.append(ToolCall(name=name, arguments=obj.get("args", {}) or {}))
        return calls
