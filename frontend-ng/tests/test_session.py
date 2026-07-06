"""Session accumulator: dedup, dedup-move-to-end, and small-LLM compaction under budget."""
from gini.agent.kb import Card
from gini.agent.session import SessionKnowledge


def _card(key, text="x"):
    return Card("concept", key, key, text)


def test_add_and_render():
    s = SessionKnowledge()
    s.add([_card("serverless", "functions are stateless"), _card("vpc", "isolated network")])
    ctx = s.as_context()
    assert "functions are stateless" in ctx and "isolated network" in ctx


def test_dedup_moves_to_end():
    s = SessionKnowledge()
    s.add([_card("a"), _card("b")])
    s.add([_card("a")])                       # re-touch 'a'
    keys = [c.key for c in s.cards]
    assert keys == ["b", "a"]                 # one 'a', now most-recent
    assert sum(c.key == "a" for c in s.cards) == 1


def test_compaction_summarises_oldest_via_llm():
    calls = []

    def stub(prompt):
        calls.append(prompt)
        return "COMPACT SUMMARY"

    s = SessionKnowledge(max_chars=120, keep_recent=2)
    s.add([_card(f"c{i}", "a fairly long fact about GINI " * 2) for i in range(6)], llm=stub)
    assert calls, "small-LLM summariser should be invoked over budget"
    assert s.summary == "COMPACT SUMMARY"
    assert len(s.cards) <= 2                   # trimmed to recent
    assert "Session knowledge so far: COMPACT SUMMARY" in s.as_context()


def test_compaction_without_llm_stays_bounded():
    s = SessionKnowledge(max_chars=100, keep_recent=1)
    s.add([_card(f"c{i}", "long fact " * 5) for i in range(8)])   # no llm
    assert s._size() <= s.max_chars + 5        # bounded by rule-based fallback


def test_clear():
    s = SessionKnowledge()
    s.add([_card("a")])
    s.clear()
    assert not s.cards and not s.summary
