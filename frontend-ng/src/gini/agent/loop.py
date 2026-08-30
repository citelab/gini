"""The agent loop: backend + tool registry -> a working assistant/tutor brain.

Runs the standard cycle — model proposes tool calls, we execute them against the shared
registry, feed results back, repeat until the model answers in prose. Supports native
tool calling and a JSON-action fallback for models that can't call tools, so GINI isn't
hostage to one model's tool-use quality.
"""
from __future__ import annotations

import json
import re
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
    "Prefer showing on the canvas over long text. Use build tools only when asked to "
    "change the topology: `add_device` (args: type_key e.g. 'router'/'switch'/'host', and "
    "optional name), `connect_devices` (args: a, b), `set_property`, `remove_device`. "
    "`callout` needs a device and text. When no tool is needed, answer in plain language.\n"
    "If you cannot call tools natively, emit JSON objects of the form "
    '{\"tool\": \"<name>\", \"args\": { ... }} — and write any explanation as plain prose, '
    "NOT inside the JSON. Never write tool calls as prose (e.g. `add_device type_key=...`).\n"
    "GROUNDING: GINI has a specific, fixed set of elements — the ones listed in the context "
    "below. Answer USING those elements and the GINI knowledge provided; treat that "
    "knowledge and the canvas as the only source of truth. EVERY element you name must "
    "appear verbatim in that GINI elements list; if something is not listed, it does NOT "
    "exist in GINI — never mention or invent it, even if it is common in the real world "
    "(e.g. there is no 'SDN dashboard'). If GINI has no element for something, say so "
    "plainly. Do NOT reach for generic infrastructure or other products from your training. "
    "When a live xv6 Machine state card is provided, the SAME rule applies to the kernel: "
    "reason only about the processes, pids, states, registers and stack frames shown in that "
    "card — it is this student's actual running kernel. Do not invent pids or values that "
    "aren't there; if a detail isn't in the card, say it isn't shown rather than guessing.\n"
    "Format answers in Markdown."
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


_TOOL_TAG_RE = re.compile(r"<\s*(tool_call|tool|json|function_call)\s*>.*?</\s*\1\s*>",
                          re.S | re.I)

# Some small models write tool calls as PROSE (`add_device type_key='host' name='F1'`),
# which is neither native nor JSON, so it slips past the tag/JSON strippers and leaks into
# chat. Strip those pseudo-calls: a full build/present tool name, one optional device-ref
# arg (R1/F1/…), then key=value pairs. We DON'T match bare words as args (so following prose
# like "… Now that …" is never eaten) and we leave callout/narrate alone (their text is the
# model teaching). Bare verb aliases (add/connect) are excluded — too common in real prose.
_PSEUDO_CALL_RE = re.compile(
    r"\b(?:add_device|connect_devices|set_property|remove_device|spotlight|highlight|"
    r"animate_packet|trace_path|clear_stage|inspect_device|get_topology|summarize_topology|"
    r"explain_topology|explain_device|explain_element|list_device_types)\b"
    r"(?:"
    r"[ \t]+[A-Za-z]{1,4}\d+(?:[ \t]+\w+=(?:'[^']*'|\"[^\"]*\"|[^\s]+))*"   # ref [+ key=val…]
    r"|(?:[ \t]+\w+=(?:'[^']*'|\"[^\"]*\"|[^\s]+))+"                        # or one+ key=val
    r")")     # require args, so a bare mention of a tool name in prose is NOT stripped


def _strip_tool_objects(text: str) -> str:
    """Remove top-level {...} JSON objects that are tool actions (have a 'tool' key)."""
    spans: list[tuple[int, int]] = []
    depth = 0
    start: int | None = None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    obj = json.loads(text[start:i + 1])
                    if isinstance(obj, dict) and "tool" in obj:
                        spans.append((start, i + 1))
                except json.JSONDecodeError:
                    pass
                start = None
    if not spans:
        return text
    out, prev = [], 0
    for a, b in spans:
        out.append(text[prev:a])
        prev = b
    out.append(text[prev:])
    return "".join(out)


def visible_text(raw: str) -> str:
    """The user-facing prose from a model reply: keep the natural language and the *text*
    of any callout/narrate calls (that's the model talking), but drop all tool-call syntax
    — `<tool_call>`/`<json>` tag blocks and bare {"tool": …} action objects. So the chat
    shows what the model SAID, while the loop still executes what it DID."""
    raw = raw or ""
    # A reasoning model inlines its chain of thought in `content`, and `ollama.strip_thinking`
    # runs per streamed CHUNK — so a block split across two deltas survives it and lands here.
    # The final text is the authority for what gets persisted and for anything the streamed copy
    # missed, so it has to be clean too, or a student reads the model's private reasoning at the
    # bottom of every answer.
    from .llm.ollama import strip_thinking
    raw = strip_thinking(raw)
    spoken: list[str] = []
    for obj in _extract_json_objects(raw):
        if obj.get("tool") in ("callout", "narrate"):
            args = obj.get("args") or {}
            t = args.get("text") or args.get("line") or args.get("note")
            if t:
                spoken.append(str(t).strip())
    prose = _strip_tool_objects(_TOOL_TAG_RE.sub(" ", raw))
    prose = _PSEUDO_CALL_RE.sub(" ", prose)                  # prose-style tool calls
    prose = re.sub(r"`{3}.*?`{3}", " ", prose, flags=re.S)   # stray code fences
    prose = re.sub(r"[ \t]*\n[ \t]*", "\n", prose)
    prose = re.sub(r"[ \t]{2,}", " ", prose).strip()
    parts, seen = [], set()
    for p in ([prose] if prose else []) + spoken:
        if p and p not in seen:
            parts.append(p)
            seen.add(p)
    return "\n".join(parts)


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
        # a per-project 'brief' — the teacher's framing for this lab. Always injected as a
        # system message so every answer is shaped by it (set by the Ask GINI panel on
        # project load). Empty by default.
        self.brief: str = ""
        # the Ask GINI pipeline sets this each turn to the fully-assembled grounded context
        # (always-on index + canvas + session knowledge + retrieved cards). When set it
        # REPLACES the bare canvas snapshot, so grounding all flows through one block.
        self.extra_context: str = ""

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
        if self.brief:
            msgs.append(Message("system",
                                "Project brief — the teacher's framing for this lab. Keep your "
                                "guidance consistent with it:\n" + self.brief))
        if self.extra_context:
            msgs.append(Message("system", self.extra_context))     # pipeline-assembled
        elif self.context_provider is not None:
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

    # forgiving aliases — local models follow the prose prompt and use short verb names /
    # 'type' instead of the registry's exact 'add_device' / 'type_key', so normalize them
    # (otherwise the call silently doesn't match and nothing happens).
    _TOOL_ALIASES = {
        "add": "add_device", "create": "add_device", "place": "add_device",
        "connect": "connect_devices", "link": "connect_devices", "wire": "connect_devices",
        "remove": "remove_device", "delete": "remove_device",
        "rename": "set_property", "set": "set_property",
    }
    _ARG_ALIASES = {"type": "type_key", "kind": "type_key", "device_type": "type_key"}

    def _parse_json_actions(self, text: str) -> list[ToolCall]:
        calls: list[ToolCall] = []
        names = self.registry.names()
        for obj in _extract_json_objects(text):
            raw = obj.get("tool")
            if not raw:
                continue
            name = self._TOOL_ALIASES.get(raw, raw)
            if name not in names:
                continue
            args = {self._ARG_ALIASES.get(k, k): v for k, v in (obj.get("args") or {}).items()}
            calls.append(ToolCall(name=name, arguments=args))
        return calls
