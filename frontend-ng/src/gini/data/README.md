# `gini/data`

Generated data assets shipped with GINI.

## `kb_index.json` — Ask GINI semantic index (L2 recall)

Precomputed embeddings of the knowledge base (concept notes + recipes + element
descriptions), used by `agent/embed.py` for semantic recall when lexical + LLM-expansion
retrieval comes back empty.

**This file is generated, not hand-edited.** Regenerate it whenever you change the concept
notes, recipes, or element catalog:

```bash
ollama pull all-minilm
GINI_LLM_URL=http://localhost:11434 python scripts/build_kb_index.py
```

If `kb_index.json` is absent, Ask GINI still works — L2 simply disables and retrieval falls
back to lexical (L0) + LLM keyword-expansion (L1). `tests/test_kb_index_sync.py` fails if the
shipped index drifts from the live KB, as a reminder to rebuild.
