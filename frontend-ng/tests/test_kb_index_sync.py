"""If a semantic index is shipped, it must match the live KB — otherwise semantic recall
returns stale results. Skips when no index has been built (e.g. in CI without Ollama)."""
import pytest

from gini.agent import embed


def test_shipped_index_matches_the_kb():
    index = embed.load_index()
    if index is None:
        pytest.skip("no kb_index.json shipped — run scripts/build_kb_index.py to generate it")
    assert index.get("kb_hash") == embed.kb_hash(), (
        "kb_index.json is out of date — a concept/recipe/element changed without a rebuild. "
        "Run: python scripts/build_kb_index.py"
    )
    # every indexed id must still exist in the KB
    live_ids = {d for d, _ in embed.kb_documents()}
    assert set(index.get("vectors", {})) <= live_ids
