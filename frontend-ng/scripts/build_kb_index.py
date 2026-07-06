#!/usr/bin/env python3
"""Build the shipped semantic index for Ask GINI's L2 recall.

Embeds every KB document (`agent.embed.kb_documents()`) via an Ollama `/api/embed` call and
writes `src/gini/data/kb_index.json` (model tag + KB hash + id→vector). Run this whenever the
concept notes, recipes, or element catalog change — a test (`test_kb_index_sync`) fails if the
shipped index drifts from the live KB.

Requires a running Ollama with the embed model pulled, e.g.:

    ollama pull all-minilm
    GINI_LLM_URL=http://localhost:11434 python scripts/build_kb_index.py

Env: GINI_LLM_URL (default http://localhost:11434), GINI_EMBED_MODEL (default all-minilm).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from gini.agent.embed import _INDEX_PATH, kb_documents, kb_hash   # noqa: E402
from gini.agent.llm import OllamaBackend                          # noqa: E402


def main() -> int:
    url = os.environ.get("GINI_LLM_URL", "http://localhost:11434")
    model = os.environ.get("GINI_EMBED_MODEL", "all-minilm")
    backend = OllamaBackend(url, embed_model=model)
    if not backend.available():
        print(f"Ollama not reachable at {url} — start it and `ollama pull {model}`.")
        return 1

    docs = kb_documents()
    ids = [d for d, _ in docs]
    texts = [t for _, t in docs]
    print(f"Embedding {len(texts)} KB documents with '{model}' …")

    vectors: dict[str, list[float]] = {}
    for i in range(0, len(texts), 32):                 # batch to be gentle on the server
        chunk_ids = ids[i:i + 32]
        chunk_vecs = backend.embed(texts[i:i + 32])
        if len(chunk_vecs) != len(chunk_ids):
            print(f"  embed returned {len(chunk_vecs)} vectors for {len(chunk_ids)} docs — aborting.")
            return 2
        for did, vec in zip(chunk_ids, chunk_vecs):
            vectors[did] = [round(float(x), 6) for x in vec]

    dim = len(next(iter(vectors.values()))) if vectors else 0
    index = {"model": model, "dim": dim, "kb_hash": kb_hash(), "vectors": vectors}
    _INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    _INDEX_PATH.write_text(json.dumps(index))
    print(f"Wrote {_INDEX_PATH} — {len(vectors)} vectors, dim {dim}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
