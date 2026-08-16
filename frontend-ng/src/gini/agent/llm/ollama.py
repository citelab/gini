"""Ollama backend (self-hosted, local/campus).

Talks to Ollama's /api/chat with OpenAI-style tools. Uses only the stdlib (urllib) so
there's no extra dependency. The HTTP call is injectable (`transport=`) so the request
shaping and response parsing are unit-testable without a running server.

Config: url (e.g. http://gpu.cs.univ.edu:11434), model, timeout.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator

from .backend import Chunk, Message, ToolCall

# reasoning ("thinking") models wrap their chain-of-thought in tags — strip it so it
# never reaches the chat or the JSON tool-parser. Covers <think>…</think> and Gemma's
# <|think|>…<|/think|> styles, terminated or not.
_THINK_BLOCK = re.compile(r"<\|?\s*think\s*\|?>.*?<\|?\s*/\s*think\s*\|?>",
                          re.DOTALL | re.IGNORECASE)
_THINK_TAG = re.compile(r"<\|?\s*/?\s*think[^>]*?\|?>", re.IGNORECASE)


def strip_thinking(text: str) -> str:
    """Remove a model's reasoning, returning just the answer it intends to show."""
    if not text:
        return text
    for close in ("</think>", "<|/think|>", "<|end_think|>"):
        if close in text:                     # answer follows the last closing tag
            text = text.rsplit(close, 1)[1]
    text = _THINK_BLOCK.sub("", text)
    text = _THINK_TAG.sub("", text)           # drop any stray/open think markers
    return text.strip()


def _to_ollama_message(m: Message) -> dict:
    msg: dict = {"role": m.role, "content": m.content}
    if m.role == "tool" and m.name:
        msg["tool_name"] = m.name
    if m.tool_calls:
        msg["tool_calls"] = [
            {"function": {"name": tc.name, "arguments": tc.arguments}} for tc in m.tool_calls
        ]
    return msg


class OllamaBackend:
    def __init__(self, url: str = "http://localhost:11434", model: str = "llama3.1",
                 timeout: float = 120.0, think: bool = False, stream: bool = True,
                 num_ctx: int = 8192, embed_model: str = "all-minilm",
                 transport: Callable[[str, dict], dict] | None = None) -> None:
        self.url = url.rstrip("/")
        self.model = model
        self.embed_model = embed_model     # small embed model for L2 semantic recall (/api/embed)
        self.timeout = timeout
        # Ollama's DEFAULT context window is only 2048 tokens — far smaller than the model's
        # real window — and it SILENTLY TRUNCATES the prompt when exceeded. Set it explicitly
        # so the catalog/canvas/question always fit. (Ollama clamps to the model's real max.)
        self.num_ctx = num_ctx
        self.think = think          # ask reasoning models to think (Ollama `think` flag)
        self.stream = stream        # stream tokens for a live "typing" feel
        self._injected = transport is not None   # tests inject a transport (non-streaming)
        self._transport = transport or self._http_post

    # -- HTTP ---------------------------------------------------------------- #
    def _http_post(self, path: str, payload: dict) -> dict:
        req = urllib.request.Request(
            self.url + path,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode())

    def available(self) -> bool:
        try:
            req = urllib.request.Request(self.url + "/api/tags", method="GET")
            urllib.request.urlopen(req, timeout=3.0)
            return True
        except (urllib.error.URLError, OSError):
            return False

    # -- embeddings (L2 semantic recall) ------------------------------------- #
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed one or more texts via Ollama's `/api/embed` (same server as chat). Returns a
        list of vectors aligned with `texts`; [] on any failure so callers degrade to lexical."""
        if not texts:
            return []
        try:
            data = self._transport("/api/embed", {"model": self.embed_model, "input": texts})
        except Exception:
            return []
        return data.get("embeddings") or []

    # -- chat ---------------------------------------------------------------- #
    def chat(self, messages: list[Message], tools: list[dict] | None = None,
             stream: bool = False, schema: dict | None = None) -> Iterator[Chunk]:
        """`schema` (optional) is a JSON Schema passed as Ollama's `format` field — the server
        CONSTRAINS decoding so the reply is guaranteed to match (structured outputs). Used by the
        structured personas (coverage/classifier/critic JSON) so malformed output becomes a rare
        transport error instead of a routine small-model failure. Reasoning-2.0 §10 amendment."""
        base = {
            "model": self.model,
            "messages": [_to_ollama_message(m) for m in messages],
            "stream": False,            # tool turns are most reliable non-streamed
            "options": {"num_ctx": self.num_ctx},   # don't let Ollama truncate to 2048
        }
        # Try the richest request first, then degrade: a small model may not support
        # tools and/or thinking, and Ollama errors rather than ignoring them. Dropping
        # tools is safe — the agent loop's JSON-action fallback still drives tools.
        attempts: list[dict] = []
        full = dict(base)
        if self.think:
            full["think"] = True
        if tools:
            full["tools"] = tools
        attempts.append(full)
        if tools:                                   # without tools (keep think)
            p = dict(base)
            if self.think:
                p["think"] = True
            attempts.append(p)
        attempts.append(dict(base))                 # bare minimum
        if schema is not None:
            # constrain every attempt; keep ONE unconstrained fallback (an older server may
            # reject `format` — the callers' tolerant first_json parsing still applies then).
            attempts = [{**p, "format": schema} for p in attempts] + [dict(base)]

        # Stream the answer token-by-token over real HTTP (live "typing" feel). Tool
        # turns still come back whole in the final streamed object. Injected transports
        # (tests) keep the simple non-streaming path. Structured (schema) calls are
        # machine-consumed, never displayed — no reason to stream them.
        if self.stream and not self._injected and schema is None:
            last_err = None
            for payload in attempts:
                try:
                    yield from self._chat_stream(payload)
                    return
                except Exception as e:              # noqa: BLE001 — degrade on reject
                    last_err = e
            raise last_err

        data, last_err = None, None
        for payload in attempts:
            try:
                data = self._transport("/api/chat", payload)
                break
            except Exception as e:                  # noqa: BLE001 — degrade on any server reject
                last_err = e
        if data is None:
            raise last_err
        yield from self._parse(data)

    def _chat_stream(self, payload: dict) -> Iterator[Chunk]:
        """Stream Ollama's NDJSON response — one JSON object per line, each carrying a
        small content delta — yielding a Chunk per delta and tool calls at the end."""
        body = json.dumps({**payload, "stream": True}).encode()
        req = urllib.request.Request(self.url + "/api/chat", data=body,
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            for raw in resp:                        # iterate lines as they arrive
                line = raw.decode(errors="replace").strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = obj.get("message", {}) or {}
                delta = msg.get("content") or ""
                if delta:
                    yield Chunk(text=delta)         # not stripped: deltas are tiny
                for call in msg.get("tool_calls", []) or []:
                    fn = call.get("function", {})
                    args = fn.get("arguments", {})
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            args = {}
                    yield Chunk(tool_call=ToolCall(name=fn.get("name", ""), arguments=args or {}))
                if obj.get("done"):
                    break

    @staticmethod
    def _parse(data: dict) -> Iterator[Chunk]:
        msg = data.get("message", {}) or {}
        # Ollama returns reasoning either in a separate `thinking` field or inline in
        # `content`; in both cases we show only the final answer.
        text = strip_thinking(msg.get("content") or "")
        if text:
            yield Chunk(text=text)
        for call in msg.get("tool_calls", []) or []:
            fn = call.get("function", {})
            args = fn.get("arguments", {})
            if isinstance(args, str):           # some models return JSON-as-string
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            yield Chunk(tool_call=ToolCall(name=fn.get("name", ""), arguments=args or {}))
