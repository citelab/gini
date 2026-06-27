"""A1 tests: shared registry, agent loop (native + JSON fallback), Ollama shaping."""
from gini.agent.api import GiniAPI
from gini.agent.llm.backend import Chunk, ToolCall
from gini.agent.llm.fake import ScriptedBackend
from gini.agent.llm.ollama import OllamaBackend
from gini.agent.loop import AgentLoop
from gini.agent.tools.registry import build_registry
from gini.app import AppContext


def make():
    ctx = AppContext()
    api = GiniAPI(ctx)
    return ctx, api, build_registry(api)


def test_registry_executes_and_builds():
    ctx, api, reg = make()
    assert "add_device" in reg.names()
    out = reg.execute("add_device", {"type_key": "router"})
    assert out["type"] == "router"
    assert reg.execute("summarize_topology")["devices"] == 1
    # unknown tool and bad args degrade to error dicts, not exceptions
    assert "error" in reg.execute("nope")
    assert "error" in reg.execute("add_device", {"type_key": "does-not-exist"})


def test_registry_openai_tool_schema():
    _, _, reg = make()
    tools = reg.openai_tools()
    add = next(t for t in tools if t["function"]["name"] == "add_device")
    assert add["type"] == "function"
    assert "type_key" in add["function"]["parameters"]["properties"]
    assert add["function"]["parameters"]["required"] == ["type_key"]


def test_agent_loop_native_tool_calls():
    ctx, api, reg = make()
    backend = ScriptedBackend([
        [Chunk(tool_call=ToolCall("add_device", {"type_key": "router", "name": "R1"}))],
        [Chunk(tool_call=ToolCall("add_device", {"type_key": "switch", "name": "S1"}))],
        [Chunk(tool_call=ToolCall("connect_devices", {"a": "R1", "b": "S1"}))],
        [Chunk(text="Built a router and switch and linked them.")],
    ])
    loop = AgentLoop(backend, reg)
    reply = loop.send("make a router and a switch and connect them")
    assert "Built" in reply
    assert ctx.topology.find_by_name("R1") is not None
    assert len(ctx.topology.links) == 1
    # the loop fed tool results back into history
    assert any(m.role == "tool" for m in loop.history)


def test_agent_loop_json_fallback():
    ctx, api, reg = make()
    backend = ScriptedBackend([
        [Chunk(text='Sure. {"tool": "add_device", "args": {"type_key": "vpc"}}')],
        [Chunk(text="Added a VPC.")],
    ])
    loop = AgentLoop(backend, reg)
    reply = loop.send("add a vpc")
    assert "Added a VPC" in reply
    assert ctx.topology.counts_by_category().get("Cloud Networking") == 1


def test_ollama_request_and_parse():
    _, _, reg = make()
    captured = {}

    def fake_transport(path, payload):
        captured["path"] = path
        captured["payload"] = payload
        # mimic an Ollama /api/chat tool-calling response
        return {"message": {"role": "assistant", "content": "ok",
                            "tool_calls": [{"function": {"name": "add_device",
                                                          "arguments": {"type_key": "host"}}}]}}

    be = OllamaBackend(url="http://campus-gpu:11434", model="llama3.1",
                       transport=fake_transport)
    chunks = list(be.chat([], tools=reg.openai_tools()))
    assert captured["path"] == "/api/chat"
    assert captured["payload"]["model"] == "llama3.1"
    assert captured["payload"]["tools"][0]["function"]["name"] == "list_device_types"
    texts = [c.text for c in chunks if c.text]
    calls = [c.tool_call for c in chunks if c.tool_call]
    assert texts == ["ok"]
    assert calls[0].name == "add_device" and calls[0].arguments == {"type_key": "host"}
