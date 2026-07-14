"""L2 embedding layer: the Ollama /api/embed request shape, cosine top-k over a fixture
index, the NullEmbeddings no-op, and the KB-hash drift guard."""
from gini.agent import embed
from gini.agent.embed import NullEmbeddings, OllamaEmbeddings
from gini.agent.llm import OllamaBackend


# -- OllamaBackend.embed request/response shape ----------------------------- #
def test_backend_embed_calls_api_embed():
    seen = {}

    def transport(path, payload):
        seen["path"] = path
        seen["payload"] = payload
        return {"embeddings": [[0.1, 0.2, 0.3]]}

    be = OllamaBackend(embed_model="all-minilm", transport=transport)
    vecs = be.embed(["hello"])
    assert seen["path"] == "/api/embed"
    assert seen["payload"] == {"model": "all-minilm", "input": ["hello"]}
    assert vecs == [[0.1, 0.2, 0.3]]


def test_backend_embed_degrades_to_empty_on_error():
    def boom(path, payload):
        raise RuntimeError("down")

    assert OllamaBackend(transport=boom).embed(["x"]) == []


# -- OllamaEmbeddings cosine recall over a fixture index -------------------- #
class _Backend:
    """Returns a query vector aligned with whichever fixture doc it should match."""

    def __init__(self, vec):
        self.vec = vec

    def embed(self, texts):
        return [self.vec]


_FIXTURE = {
    "model": "test", "dim": 2,
    "kb_hash": None,          # None disables the drift check
    "vectors": {"concept:sdn": [1.0, 0.0], "concept:cost-model": [0.0, 1.0]},
}


def test_cosine_returns_the_nearest_doc():
    emb = OllamaEmbeddings(_Backend([0.9, 0.1]), index=_FIXTURE, min_score=0.5, check_hash=False)
    assert emb.available()
    hits = emb.query("anything", k=1)
    assert hits and hits[0][0] == "concept:sdn"


def test_min_score_filters_weak_matches():
    emb = OllamaEmbeddings(_Backend([0.71, 0.70]), index=_FIXTURE, min_score=0.99, check_hash=False)
    assert emb.query("anything") == []          # nothing clears the high bar


def test_null_embeddings_is_unavailable():
    n = NullEmbeddings()
    assert not n.available() and n.query("x") == []


def test_hash_drift_disables_the_index():
    bad = dict(_FIXTURE, kb_hash="deadbeef")     # a stale hash
    emb = OllamaEmbeddings(_Backend([1.0, 0.0]), index=bad, check_hash=True)
    assert not emb.available()


# -- documents + hash ------------------------------------------------------- #
def test_kb_documents_cover_all_three_kinds():
    kinds = {d.split(":", 1)[0] for d, _ in embed.kb_documents()}
    assert {"concept", "recipe", "element"} <= kinds
    assert len(embed.kb_hash()) == 64
