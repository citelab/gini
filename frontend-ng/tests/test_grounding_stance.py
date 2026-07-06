"""The tiered grounding stance: strength → directive, with the 'never invent a GINI element'
rule holding in EVERY tier (off-topic relaxation must not reopen element invention)."""
from gini.agent import ask
from gini.agent.kb import Retrieval


def _stance(strength):
    return ask.grounding_stance(Retrieval(strength=strength))


def test_strength_maps_to_distinct_stances():
    strong, thin, empty = _stance("strong"), _stance("thin"), _stance("empty")
    assert "CLOSED WORLD" in strong
    assert "MOSTLY CLOSED" in thin
    assert "OPEN BUT FENCED" in empty
    assert strong != thin != empty


def test_element_invention_is_banned_in_every_tier():
    # the open tier may allow general knowledge, but must still forbid claiming fake elements
    empty = _stance("empty").lower()
    assert "not claim any gini element" in empty
    # the thin tier discourages invention explicitly
    assert "rather than inventing" in _stance("thin").lower()
    # the strong tier constrains named elements to the list
    assert "must appear in the elements list" in _stance("strong").lower()


def test_grounded_context_prepends_the_stance():
    ctx = ask.grounded_context("ALWAYS", "", None, "", intent=None,
                               stance=_stance("empty"))
    assert ctx.startswith(_stance("empty"))
    assert "ALWAYS" in ctx


def test_missing_retrieval_defaults_closed():
    assert "CLOSED WORLD" in ask.grounding_stance(None)
