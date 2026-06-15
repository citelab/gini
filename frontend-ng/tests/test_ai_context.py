"""The assistant knows the live topology — context is injected into every model turn."""
from gini.agent.api import GiniAPI
from gini.agent.llm.backend import Chunk
from gini.agent.llm.fake import ScriptedBackend
from gini.agent.loop import AgentLoop
from gini.agent.tools.registry import build_registry
from gini.app import AppContext


def _ctx_with_topology() -> AppContext:
    ctx = AppContext()
    api = GiniAPI(ctx)
    r = api.add_device("router")["id"]
    h1 = api.add_device("host")["id"]
    h2 = api.add_device("host")["id"]
    api.connect(r, h1)
    api.connect(r, h2)
    return ctx, api


def test_context_digest_describes_devices_ips_and_links():
    ctx, api = _ctx_with_topology()
    digest = api.context_digest()
    assert "R1" in digest and "M1" in digest
    assert "10.0." in digest                      # addressing is included
    assert "connected to" in digest               # connectivity is included
    assert "Subnets:" in digest


def test_loop_injects_live_context_each_turn():
    ctx, api = _ctx_with_topology()
    registry = build_registry(api)
    backend = ScriptedBackend([[Chunk(text="There are two hosts on R1.")]])
    loop = AgentLoop(backend, registry, context_provider=api.context_digest)

    loop.send("what is connected to R1?")

    # the backend must have received a system message carrying the canvas state
    messages, _tools = backend.calls[0]
    ctx_msgs = [m for m in messages if m.role == "system" and "Current canvas" in m.content]
    assert ctx_msgs, "no canvas-context message was injected"
    assert "R1" in ctx_msgs[0].content and "M1" in ctx_msgs[0].content


def test_context_is_refreshed_not_stale():
    ctx, api = _ctx_with_topology()
    registry = build_registry(api)
    backend = ScriptedBackend([[Chunk(text="ok")], [Chunk(text="ok2")]])
    loop = AgentLoop(backend, registry, context_provider=api.context_digest)

    loop.send("hi")
    api.add_device("switch")                       # canvas changes between turns
    loop.send("what changed?")

    latest = [m for m in backend.calls[-1][0] if m.role == "system" and "Current canvas" in m.content]
    assert latest and "S1" in latest[0].content    # the new switch is reflected


def test_empty_canvas_digest():
    ctx = AppContext()
    assert "empty" in GiniAPI(ctx).context_digest().lower()
