"""Saying WHY there is no model.

Three different ways to end up without one — nothing configured, the import failing, the server not
answering — and a fourth that is worse than all of them: the server answering while the model it is
asked for does not exist. Every one of those returned a bare `model=no` in the first version, which
on a machine with a working tunnel reads as a broken connection and is not one.
"""
from __future__ import annotations

import io
import json

from gini_reason import service


class _Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _tags(names):
    return lambda *a, **k: _Reply(json.dumps({"models": [{"name": n} for n in names]}).encode())


def test_an_unset_url_falls_back_to_where_ollama_normally_is(monkeypatch):
    """The tunnel lands on the port Ollama uses locally, so the common setup needs no
    configuration — and an unset variable should not look like a missing model."""
    monkeypatch.delenv("GINI_LLM_URL", raising=False)
    monkeypatch.setattr(service, "_installed", lambda url: {"gemma3:4b"})
    monkeypatch.setenv("GINI_LLM_MODEL", "gemma3:4b")
    monkeypatch.setattr("gini.agent.llm.ollama.OllamaBackend.available", lambda self: True)
    be, why = service.llm_with_reason()
    assert be is not None
    assert service.DEFAULT_LLM in why and "from the default" in why


def test_a_server_that_does_not_answer_says_where_it_looked(monkeypatch):
    monkeypatch.setenv("GINI_LLM_URL", "http://127.0.0.1:59999")
    monkeypatch.setattr("gini.agent.llm.ollama.OllamaBackend.available", lambda self: False)
    be, why = service.llm_with_reason()
    assert be is None
    assert "127.0.0.1:59999" in why and "GINI_LLM_URL" in why


def test_a_server_that_is_up_without_the_model_lists_what_it_has(monkeypatch):
    """The one that cost a diagnosis. `available()` asks /api/tags and is satisfied by a reply, so
    a wrong GINI_LLM_MODEL passes it and then fails at the first question with a bare
    `HTTP Error 404: Not Found` — which names nothing and reads as the tunnel having broken."""
    monkeypatch.setenv("GINI_LLM_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("GINI_LLM_MODEL", "llama3.1")
    monkeypatch.setattr("gini.agent.llm.ollama.OllamaBackend.available", lambda self: True)
    monkeypatch.setattr(service, "_installed", lambda url: {"gemma3:4b", "deepseek-r1:8b"})

    be, why = service.llm_with_reason()
    assert be is None
    assert "no model called 'llama3.1'" in why
    assert "gemma3:4b" in why and "GINI_LLM_MODEL" in why


def test_a_tag_that_differs_only_by_its_version_suffix_still_matches(monkeypatch):
    monkeypatch.setenv("GINI_LLM_MODEL", "gemma3")
    monkeypatch.setattr("gini.agent.llm.ollama.OllamaBackend.available", lambda self: True)
    monkeypatch.setattr(service, "_installed", lambda url: {"gemma3:4b"})
    assert service.llm_with_reason()[0] is not None


def test_an_unreadable_model_list_is_never_an_accusation(monkeypatch):
    """If the list cannot be read, that is not evidence the model is missing — and refusing on it
    would turn a flaky read into "your model does not exist"."""
    monkeypatch.setattr("gini.agent.llm.ollama.OllamaBackend.available", lambda self: True)
    monkeypatch.setattr(service, "_installed", lambda url: set())
    assert service.llm_with_reason()[0] is not None


def test_the_ladder_reports_why_it_stopped_where_it_did(monkeypatch):
    """"model=no" ran two questions together — is there a model, and was it USED. A live tunnel
    with thin grounding answers yes and no, which reads as a broken connection."""
    from gini_reason.ladder import answer

    class _Ok:
        def chat(self, m, tools=None, stream=False):
            return iter([type("C", (), {"text": "ok"})()])

        def available(self):
            return True

    thin = answer("why is my process stuck in the scheduler", llm=_Ok())
    assert thin.rung == "L1" and not thin.used_model
    assert "below" in thin.why and "GINI_REASON_STRONG" in thin.why

    none = answer("why is my process stuck in the scheduler")
    assert none.why == "no model attached"

    empty = answer("what is the airspeed of a swallow")
    assert "nothing in the manual" in empty.why
