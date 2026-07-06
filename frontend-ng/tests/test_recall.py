"""The hybrid recall layer: L0 lexical scoring + strength, and the gated L1 (LLM expansion)
and L2 (embedding) fallbacks firing only when the previous layer is thin."""
from gini.agent import recall


# -- L0 lexical + strength -------------------------------------------------- #
def test_sdn_acronym_and_phrase_both_hit_the_sdn_concept():
    # the reported bug: "explain SDN" must reach the SDN concept (it's an authored keyword)…
    c, s = recall.best_concept("explain SDN")
    assert c.key == "sdn" and s == "strong"
    # …and so must the spelled-out phrase, which has NO overlapping token with "sdn"
    c2, s2 = recall.best_concept("how does software defined networking work")
    assert c2.key == "sdn" and s2 == "strong"


def test_synonyms_reach_the_right_concept():
    assert recall.best_concept("set up a message bus")[0].key == "messaging-queue"
    assert recall.best_concept("spread traffic across replicas")[0].key == "load-balancing"


def test_off_topic_is_empty():
    for q in ("explain photosynthesis", "what is the weather", "tell me a joke"):
        c, s = recall.best_concept(q)
        assert c is None and s == "empty"


def test_recipes_are_ranked():
    hits = recall.search_recipes("a load balanced web app with a database")
    assert hits and hits[0].kind == "recipe"


# -- L1 expansion (gated) --------------------------------------------------- #
def test_expansion_fires_only_when_not_strong():
    calls = []

    def llm(prompt):
        calls.append(prompt)
        return '["load balancer", "round robin"]'

    # strong L0 → expansion must NOT be called
    recall._expand_cache.clear()
    recall.recall("explain SDN", [], llm=llm)
    assert not calls

    # empty L0 → expansion IS called, and if it yields real terms the result improves
    recall._expand_cache.clear()
    res = recall.recall("how do I balance my zorblaxes", [], llm=llm)
    assert calls                      # the model was consulted
    assert res.used_expansion


# -- L2 embedding (gated) --------------------------------------------------- #
class _FakeEmbedder:
    def __init__(self, hits):
        self._hits = hits

    def available(self):
        return True

    def query(self, text, k=4):
        return self._hits


def test_embedding_fires_only_when_lexical_empty():
    emb = _FakeEmbedder([("concept:sdn", 0.82)])

    # empty lexical → embedder consulted, concept recovered semantically
    res = recall.recall("xyzzy floop", [], embedder=emb)
    assert res.used_embedding
    assert any(c.key == "sdn" for c in res.concepts)
    assert res.strength == "thin"     # semantic-only match = partial grounding

    # strong lexical → embedder NOT needed
    res2 = recall.recall("explain SDN", [], embedder=emb)
    assert not res2.used_embedding


def test_null_embedder_is_a_noop():
    from gini.agent.embed import NullEmbeddings
    res = recall.recall("xyzzy floop", [], embedder=NullEmbeddings())
    assert not res.used_embedding and res.strength == "empty"
