"""Scripted backend for tests and offline demos.

Returns a pre-set list of Chunks per chat() call, so the agent loop can be exercised
deterministically without a model. Each entry in `script` is the list of Chunks the
n-th chat() call yields (e.g. a tool_call turn, then a text turn).
"""
from __future__ import annotations

from collections.abc import Iterator

from .backend import Chunk


class ScriptedBackend:
    def __init__(self, script: list[list[Chunk]]) -> None:
        self.script = list(script)
        self.calls: list[list] = []     # captured (messages, tools) per call

    def available(self) -> bool:
        return True

    def chat(self, messages, tools=None, stream=False) -> Iterator[Chunk]:
        self.calls.append((list(messages), tools))
        chunks = self.script.pop(0) if self.script else [Chunk(text="(done)")]
        yield from chunks
