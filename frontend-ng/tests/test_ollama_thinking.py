"""Thinking/reasoning models work cleanly: reasoning is stripped, unsupported
options (tools/think) degrade gracefully instead of erroring the whole turn."""
from gini.agent.llm.backend import Message
from gini.agent.llm.ollama import OllamaBackend, strip_thinking


def test_strip_thinking_variants():
    assert strip_thinking("<think>reason here</think>The answer.") == "The answer."
    assert strip_thinking("planning...</think>Final answer") == "Final answer"
    assert strip_thinking("<|think|>step 1<|/think|>Hello") == "Hello"
    assert strip_thinking("just a plain answer") == "just a plain answer"


def test_reasoning_is_not_shown_to_the_student():
    def transport(path, payload):
        return {"message": {"content": "<think>R1 has two subnets, so…</think>"
                                       "R1 routes between 10.0.1.0/24 and 10.0.2.0/24."}}
    be = OllamaBackend(model="gemma4:e2b", think=True, transport=transport)
    out = "".join(c.text for c in be.chat([Message("user", "explain R1")]) if c.text)
    assert out == "R1 routes between 10.0.1.0/24 and 10.0.2.0/24."
    assert "think" not in out.lower()


def test_degrades_when_model_rejects_tools():
    seen = []

    def transport(path, payload):
        seen.append(payload)
        if "tools" in payload:
            raise RuntimeError("registry.go: model does not support tools")
        return {"message": {"content": "ok"}}

    be = OllamaBackend(model="gemma4:e2b", transport=transport)
    out = "".join(c.text for c in be.chat([Message("user", "hi")],
                                          tools=[{"type": "function"}]) if c.text)
    assert out == "ok"
    assert any("tools" in p for p in seen)          # it tried with tools first
    assert any("tools" not in p for p in seen)      # then retried without — succeeded


def test_think_flag_sent_when_enabled():
    seen = []

    def transport(path, payload):
        seen.append(payload)
        return {"message": {"content": "ok"}}

    OllamaBackend(model="x", think=True, transport=transport).chat(
        [Message("user", "hi")]).__next__()
    assert seen[0].get("think") is True


class _FakeResp:
    def __init__(self, lines): self._lines = lines
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def __iter__(self): return iter(self._lines)


def test_streaming_parses_ndjson_deltas(monkeypatch):
    """Real HTTP path streams Ollama's NDJSON: a Chunk per content delta + tool calls."""
    import json
    import urllib.request
    lines = [
        json.dumps({"message": {"content": "Hel"}}).encode(),
        json.dumps({"message": {"content": "lo"}}).encode(),
        json.dumps({"message": {"content": " R1.",
                                "tool_calls": [{"function": {"name": "trace_path",
                                                             "arguments": {"src": "M1"}}}]},
                    "done": True}).encode(),
    ]
    sent = {}

    def fake_urlopen(req, timeout=None):
        sent["body"] = json.loads(req.data.decode())
        return _FakeResp(lines)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    be = OllamaBackend(model="m", stream=True)          # default transport = real HTTP
    chunks = list(be.chat([Message("user", "explain R1")]))
    assert "".join(c.text for c in chunks if c.text) == "Hello R1."   # streamed deltas
    assert sent["body"]["stream"] is True
    assert any(c.tool_call and c.tool_call.name == "trace_path" for c in chunks)
