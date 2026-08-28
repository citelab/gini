"""Shared tool registry — the single contract for build / inspect / explain / present.

One registry is consumed by BOTH the in-app agent loop (local Ollama) and the MCP
server (external agents). Register a tool once and it's available everywhere. Each
tool carries a JSON-Schema parameter spec, so it can be handed to an LLM as an
OpenAI/Ollama-style function and executed by name.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..api import GiniAPI


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict          # JSON Schema (object)
    handler: Callable[..., Any]
    group: str = "general"    # build | inspect | explain | present

    def to_openai(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }}


class ToolRegistry:
    """The tools the model may call.

    `dispatch` decides WHERE a handler runs. It is None here on purpose: this package
    stays free of Qt, and headless callers (tests, the MCP server) want the handler run
    inline. The GUI injects a marshaller that hops to the GUI thread, because the tools
    mutate the topology and the LLM turn runs on a worker -- see the note in `execute`.
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self.dispatch = None            # callable(fn) -> fn's return value; None = inline

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def specs(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def openai_tools(self) -> list[dict]:
        return [t.to_openai() for t in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools)

    def execute(self, name: str, args: dict | None = None) -> Any:
        """Run one tool call and return its result.

        Handlers run through `dispatch` when the host supplied one. In gBuilder that hops
        to the GUI thread, and it matters: an LLM turn runs on a worker thread, and these
        handlers insert into `topology.devices` / `topology.links`. The GUI thread iterates
        those same dicts on every canvas paint, so a build that lands mid-paint raises
        `RuntimeError: dictionary changed size during iteration` -- reachable just by moving
        the mouse while GINI assembles a recipe. Reads are marshalled too: a read racing a
        UI-thread edit is no safer than a write.

        Errors are returned to the model rather than raised, so a bad call is something it
        can see and correct instead of a dead turn.
        """
        args = args or {}
        spec = self._tools.get(name)
        if spec is None:
            return {"error": f"unknown tool {name!r}"}
        run = self.dispatch or (lambda fn: fn())
        try:
            return run(lambda: spec.handler(**args))
        except TypeError as e:
            return {"error": f"bad arguments for {name}: {e}"}
        except Exception as e:  # surface to the agent rather than crashing
            return {"error": str(e)}


# JSON-schema fragments
_STR = {"type": "string"}
_NUM = {"type": "number"}


def build_registry(api: GiniAPI) -> ToolRegistry:
    """Register the GiniAPI surface as tools. `present` tools are added in phase A3."""
    r = ToolRegistry()

    r.register(ToolSpec(
        "list_device_types", "List every device/element type GINI can place (networking + cloud).",
        {"type": "object", "properties": {}},
        lambda: api.list_device_types(), group="inspect"))

    r.register(ToolSpec(
        "add_device", "Add a device. type_key e.g. 'router','switch','vpc','container','instance'.",
        {"type": "object",
         "properties": {"type_key": _STR, "name": _STR, "x": _NUM, "y": _NUM},
         "required": ["type_key"]},
        lambda type_key, name="", x=0.0, y=0.0:
            api.add_device(type_key, name=name or None, x=x, y=y), group="build"))

    r.register(ToolSpec(
        "connect_devices", "Create a link between two devices (by name or id).",
        {"type": "object",
         "properties": {"a": _STR, "b": _STR, "label": _STR}, "required": ["a", "b"]},
        lambda a, b, label="": api.connect(a, b, label), group="build"))

    r.register(ToolSpec(
        "set_property", "Set a property on a device (by name or id).",
        {"type": "object",
         "properties": {"device": _STR, "key": _STR, "value": _STR},
         "required": ["device", "key", "value"]},
        lambda device, key, value: api.set_property(device, key, value), group="build"))

    r.register(ToolSpec(
        "remove_device", "Remove a device (by name or id).",
        {"type": "object", "properties": {"device": _STR}, "required": ["device"]},
        lambda device: (api.remove_device(device), {"removed": device})[1], group="build"))

    r.register(ToolSpec(
        "inspect_device", "Inspect a device's type, properties, neighbors, and degree.",
        {"type": "object", "properties": {"device": _STR}, "required": ["device"]},
        lambda device: api.inspect(device), group="inspect"))

    r.register(ToolSpec(
        "get_topology", "Return the full topology as JSON (devices + links).",
        {"type": "object", "properties": {}},
        lambda: api.get_topology(), group="inspect"))

    r.register(ToolSpec(
        "summarize_topology", "Counts of devices, links, and categories.",
        {"type": "object", "properties": {}},
        lambda: api.summary(), group="inspect"))

    r.register(ToolSpec(
        "explain_topology", "Explain the whole topology in plain language for a student.",
        {"type": "object", "properties": {}},
        lambda: api.explain_topology(), group="explain"))

    r.register(ToolSpec(
        "explain_device", "Explain a single device in plain language for a student.",
        {"type": "object", "properties": {"device": _STR}, "required": ["device"]},
        lambda device: api.explain_device(device), group="explain"))

    r.register(ToolSpec(
        "explain_element", "Explain a palette element TYPE (e.g. 'router', 'switch', "
        "'hub', 'load_balancer') — what it is and when to use it vs. similar elements. "
        "Use when the student asks about an element from the palette, not a placed device.",
        {"type": "object", "properties": {"type_key": _STR}, "required": ["type_key"]},
        lambda type_key: api.explain_element_type(type_key), group="explain"))

    r.register(ToolSpec(
        "trace_path", "Get the hop-by-hop device path a packet takes from one device to "
        "another (names). Feed the result to animate_packet to show it on the canvas.",
        {"type": "object", "properties": {"src": _STR, "dst": _STR},
         "required": ["src", "dst"]},
        lambda src, dst: {"path": api.trace_path(src, dst)}, group="inspect"))

    _register_present(r, api)
    return r


def _register_present(r: ToolRegistry, api: GiniAPI) -> None:
    """The `present` verb — the AI tutor's stage. Handlers emit on the event bus;
    the canvas overlay renders them. Available to the in-app loop and external agents."""
    bus = api.ctx.bus

    def ids(names: list[str]) -> list[str]:
        out = []
        for n in names or []:
            try:
                out.append(api._resolve(n).id)
            except KeyError:
                pass
        return out

    _ARR = {"type": "array", "items": _STR}

    r.register(ToolSpec(
        "spotlight", "Spotlight device(s) by name and dim the rest, to focus attention.",
        {"type": "object", "properties": {"targets": _ARR}, "required": ["targets"]},
        lambda targets: (bus.present_spotlight.emit(ids(targets)), {"spotlight": targets})[1],
        group="present"))

    r.register(ToolSpec(
        "highlight", "Outline/ring device(s) by name without dimming others.",
        {"type": "object", "properties": {"targets": _ARR}, "required": ["targets"]},
        lambda targets: (bus.present_highlight.emit(ids(targets)), {"highlight": targets})[1],
        group="present"))

    r.register(ToolSpec(
        "callout", "Show an anchored speech bubble on a device with explanatory text.",
        {"type": "object", "properties": {"device": _STR, "text": _STR},
         "required": ["device", "text"]},
        lambda device, text: (bus.present_callout.emit(ids([device])[0] if ids([device]) else "", text),
                              {"callout": device})[1], group="present"))

    r.register(ToolSpec(
        "narrate", "Speak a line of narration to the student (shown on the canvas).",
        {"type": "object", "properties": {"text": _STR}, "required": ["text"]},
        lambda text: (bus.present_narrate.emit(text), {"narrated": True})[1], group="present"))

    r.register(ToolSpec(
        "animate_packet", "Animate a packet travelling along a path of device names.",
        {"type": "object", "properties": {"path": _ARR}, "required": ["path"]},
        lambda path: (bus.present_packet.emit(ids(path)), {"animated": path})[1], group="present"))

    r.register(ToolSpec(
        "clear_stage", "Clear all tutor overlays (spotlights, callouts, highlights).",
        {"type": "object", "properties": {}},
        lambda: (bus.present_clear.emit(), {"cleared": True})[1], group="present"))
