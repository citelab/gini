"""The Teaching Center's model client — streaming, and the two limits it exists to separate.

The point of streaming here is not speed. It is that a single wall-clock timeout cannot tell a
model that is **working slowly** from one that is **dead**, so the only tuning available is to raise
one number until the worst case fits — after which a genuinely hung request also takes that long to
notice. Streaming splits that into an idle limit (silence) and a total budget (still producing),
each of which means one thing and fails with a different message.

Everything here runs against a fake socket. No Ollama, no network.
"""
from __future__ import annotations

import io
import json
import sys
import time
import urllib.error
from pathlib import Path

import pytest

_TC = Path(__file__).resolve().parents[2] / "teaching-center"
pytestmark = pytest.mark.skipif(not _TC.exists(), reason="teaching-center not checked out")
if str(_TC) not in sys.path:
    sys.path.insert(0, str(_TC))

import ai as AI                                                     # noqa: E402


class FakeStream(io.BytesIO):
    """An Ollama NDJSON response. `__enter__`/`__exit__` so it works as a context manager."""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def ndjson(*chunks, done=True, error=None, thinking=False):
    lines = []
    for c in chunks:
        msg = {"content": c}
        if thinking:
            msg["thinking"] = "musing"
        lines.append(json.dumps({"message": msg, "done": False}))
    if error:
        lines.append(json.dumps({"error": error}))
    if done:
        lines.append(json.dumps({"message": {"content": ""}, "done": True}))
    return FakeStream(("\n".join(lines) + "\n").encode())


@pytest.fixture
def sent(monkeypatch):
    """Capture request bodies and serve a scripted stream."""
    box = {"bodies": [], "reply": lambda: ndjson("hello")}

    def fake_urlopen(req, timeout=None):
        box["bodies"].append(json.loads(req.data.decode()))
        box["timeout"] = timeout
        r = box["reply"]
        return r() if callable(r) else r

    monkeypatch.setattr(AI.urllib.request, "urlopen", fake_urlopen)
    return box


def llm(**kw):
    return AI.Ollama("http://x", "granite4.2:8b", **kw)


# -- the request shape -------------------------------------------------------- #
def test_it_streams(sent):
    llm().chat("", "hi")
    assert sent["bodies"][0]["stream"] is True


def test_json_mode_constrains_the_output(sent):
    """`format: json` is what stops a reasoning model emitting a prose preamble at all."""
    llm().chat("", "hi", json_mode=True)
    assert sent["bodies"][0]["format"] == "json"


def test_prose_calls_are_not_constrained(sent):
    """The back-translation is for the teacher to read; JSON mode would quote it."""
    llm().chat("", "hi")
    assert "format" not in sent["bodies"][0]


def test_thinking_is_disabled_and_output_capped(sent):
    llm().chat("", "hi", num_predict=800)
    b = sent["bodies"][0]
    assert b["think"] is False
    assert b["options"]["num_predict"] == 800


def test_the_socket_timeout_is_the_IDLE_limit_not_the_total(sent):
    """This is what makes the idle limit free: urlopen's timeout applies to each read, so while
    chunks keep arriving the clock keeps resetting."""
    llm(timeout=300, idle_s=45).chat("", "hi")
    assert sent["timeout"] == 45


# -- assembling the answer ---------------------------------------------------- #
def test_chunks_are_joined(sent):
    sent["reply"] = lambda: ndjson("Hel", "lo ", "world")
    assert llm().chat("", "hi") == "Hello world"


def test_a_reasoning_trace_is_stripped(sent):
    sent["reply"] = lambda: ndjson("<think>", "let me see", "</think>", '{"ok":1}')
    assert llm().chat("", "hi") == '{"ok":1}'


def test_a_partial_line_does_not_crash_the_read(sent):
    sent["reply"] = lambda: FakeStream(
        b'{"message":{"content":"a"},"done":false}\n'
        b'{"message": broken json here\n'
        b'{"message":{"content":"b"},"done":true}\n')
    assert llm().chat("", "hi") == "ab"


def test_progress_is_reported_chunk_by_chunk(sent):
    seen = []
    sent["reply"] = lambda: ndjson("one", "two")
    llm().chat("", "hi", on_chunk=seen.append)
    assert seen == ["one", "two"]


def test_an_error_object_in_the_stream_is_raised(sent):
    sent["reply"] = lambda: ndjson("partial", error="model not found", done=False)
    with pytest.raises(AI.ModelUnavailable, match="model not found"):
        llm().chat("", "hi")


# -- the two failures, told apart --------------------------------------------- #
def test_silence_fails_as_a_timeout_not_as_slowness(sent):
    """A dead model produces nothing, so the socket read times out. That must NOT be reported as
    'too slow' — the fix is completely different."""
    def dead(req, timeout=None):
        raise TimeoutError("timed out")

    sent  # fixture installed the patch; replace it
    AI.urllib.request.urlopen = dead
    with pytest.raises(TimeoutError):
        llm().chat("", "hi")


def test_a_model_that_never_stops_hits_the_total_budget(sent):
    """It IS producing — the idle clock never fires — so only a total budget can end it."""
    class Endless:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def __iter__(self):
            while True:
                time.sleep(0.01)
                yield b'{"message":{"content":"and"},"done":false}\n'

    sent["reply"] = Endless
    with pytest.raises(AI.ModelTooSlow) as e:
        llm(timeout=0.2, idle_s=45).chat("", "hi")
    assert "still producing" in str(e.value)


def test_the_too_slow_message_says_what_to_do(sent):
    class Endless:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def __iter__(self):
            while True:
                time.sleep(0.01)
                yield b'{"message":{"content":"x","thinking":"yes"},"done":false}\n'

    sent["reply"] = Endless
    with pytest.raises(AI.ModelTooSlow) as e:
        llm(timeout=0.2).chat("", "hi")
    msg = str(e.value)
    assert "AI_TIMEOUT" in msg and "reasoning" in msg      # names the knob AND the likely cause


# -- version tolerance -------------------------------------------------------- #
def test_an_old_ollama_that_rejects_the_new_fields_still_works(sent):
    """`think` and `format` are not universal. A 400 must fall back rather than fail, or upgrading
    GINI would break every deployment running an older model host."""
    calls = {"n": 0}

    def picky(req, timeout=None):
        calls["n"] += 1
        body = json.loads(req.data.decode())
        if "think" in body:
            raise urllib.error.HTTPError(req.full_url, 400, "bad field", {}, None)
        return ndjson("fallback worked")

    AI.urllib.request.urlopen = picky
    assert llm().chat("", "hi") == "fallback worked"
    assert calls["n"] == 2


def test_a_real_http_error_is_not_swallowed_by_the_fallback(sent):
    def broken(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 500, "boom", {}, None)

    AI.urllib.request.urlopen = broken
    with pytest.raises(urllib.error.HTTPError):
        llm().chat("", "hi")


# -- the limits are configurable ---------------------------------------------- #
def test_both_limits_come_from_the_environment():
    assert AI.TIMEOUT_S >= AI.IDLE_S, "the total budget must exceed the idle limit"
    assert AI.IDLE_S > 0
