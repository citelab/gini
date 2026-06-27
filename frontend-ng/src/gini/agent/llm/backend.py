"""LLM backend abstraction.

One thin contract so the model is a config choice: Ollama (local/campus) by default,
any OpenAI-compatible server, or cloud. `chat()` yields a stream of Chunks (text deltas
and/or tool calls). The agent loop drives it; the deterministic core means the app
still works if no backend is reachable.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class Message:
    role: str                       # system | user | assistant | tool
    content: str = ""
    name: str | None = None         # tool name (role == "tool")
    tool_calls: list["ToolCall"] = field(default_factory=list)


@dataclass
class ToolCall:
    name: str
    arguments: dict
    id: str = ""


@dataclass
class Chunk:
    text: str = ""
    tool_call: ToolCall | None = None


@runtime_checkable
class LLMBackend(Protocol):
    def chat(self, messages: list[Message], tools: list[dict] | None = None,
             stream: bool = False) -> Iterator[Chunk]:
        ...

    def available(self) -> bool:
        ...
