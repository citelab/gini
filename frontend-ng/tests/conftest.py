"""Shared test configuration.

Keep the Ask GINI panel OFFLINE (no model attached) by default. Many UI tests assert the
panel's offline/deterministic behaviour — model-gated buttons disabled (Wizard / Coach /
Missions), deterministic replies, no async LLM path. On a developer machine with a configured
*and running* Ollama, `MainWindow` auto-connects a model on construction (`_wire_llm`), which
breaks those assumptions and makes the suite pass or fail depending on whether Ollama happens to
be up. CI has no model, so the tests were written for the offline state; this fixture forces that
state everywhere so results are environment-independent. Tests that need a model attach one
explicitly (e.g. `assistant.set_loop(...)` or a fake backend).
"""
import pytest


@pytest.fixture(autouse=True)
def _ask_gini_offline(monkeypatch):
    try:
        from gini.ui.main_window import MainWindow
    except Exception:
        return
    # neutralise auto-connect: a freshly built MainWindow starts with no model attached
    monkeypatch.setattr(MainWindow, "_wire_llm",
                        lambda self: self.assistant.set_loop(None), raising=False)
