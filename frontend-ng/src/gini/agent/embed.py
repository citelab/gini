"""L2 semantic recall — embeddings over the (small, fixed) GINI knowledge base.

The KB is ~75 short documents (concept notes + recipes + element descriptions), so semantic
search is a cosine over a precomputed matrix, not a vector-database problem:

  • POPULATE (offline, shipped):  `scripts/build_kb_index.py` embeds every `kb_documents()`
    entry once and writes `data/kb_index.json` (model tag + KB hash + id→vector). A test
    asserts the hash matches the live KB, so an edited note without a rebuild fails CI.
  • QUERY (online, reused infra):  `OllamaEmbeddings` embeds the student's query via the SAME
    Ollama server GINI already uses for the LLM (`/api/embed`) — no new Python dependency —
    then cosines against the shipped vectors.

`NullEmbeddings` is the graceful fallback when no index/embed-model is available, so GINI
still runs offline (it simply drops to L0+L1). Callers use the `EmbeddingIndex` protocol and
never care which is active.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Protocol

from ..domain import concepts as _concepts
from ..domain import recipes as _recipes
from ..domain.devices import all_devices

_INDEX_PATH = Path(__file__).resolve().parent.parent / "data" / "kb_index.json"


# -- the shared document set (builder + validation) ------------------------- #
def kb_documents() -> list[tuple[str, str]]:
    """(doc_id, text) for every embeddable KB card. Stable IDs `concept:<key>` /
    `recipe:<id>` / `element:<key>` so recall can map a vector back to its object."""
    docs: list[tuple[str, str]] = []
    for c in _concepts.CONCEPTS:
        docs.append((f"concept:{c.key}",
                     f"{c.title}. {' '.join(c.keywords)}. {c.body}"))
    for r in _recipes.RECIPES:
        docs.append((f"recipe:{r.id}",
                     f"{r.name}. {r.summary} {r.teaches} {' '.join(r.intent)}"))
    for d in all_devices():
        docs.append((f"element:{d.key}", f"{d.label}: {d.description}"))
    return docs


def kb_hash() -> str:
    """A stable hash of the KB text — the index records it so we can detect drift."""
    h = hashlib.sha256()
    for doc_id, text in kb_documents():
        h.update(doc_id.encode()); h.update(b"\x00"); h.update(text.encode()); h.update(b"\x01")
    return h.hexdigest()


# -- cosine helpers --------------------------------------------------------- #
def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


# -- the protocol + implementations ----------------------------------------- #
class EmbeddingIndex(Protocol):
    def available(self) -> bool: ...
    def query(self, text: str, k: int = 4) -> list[tuple[str, float]]: ...


class NullEmbeddings:
    """The offline fallback — never matches, so recall degrades to L0+L1."""

    def available(self) -> bool:
        return False

    def query(self, text: str, k: int = 4) -> list[tuple[str, float]]:
        return []


def load_index(path: Path = _INDEX_PATH) -> dict | None:
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return None


class OllamaEmbeddings:
    """Semantic recall backed by the precomputed index + an Ollama `/api/embed` query call.

    `backend` is any object exposing `embed(texts) -> list[vector]` (the OllamaBackend). The
    index is loaded once; if it's missing or the KB has drifted, `available()` is False and we
    fall back cleanly. `min_score` gates weak cosine matches out."""

    def __init__(self, backend, *, index: dict | None = None, index_path: Path = _INDEX_PATH,
                 min_score: float = 0.55, check_hash: bool = True) -> None:
        self.backend = backend
        self.min_score = min_score
        self._index = index if index is not None else load_index(index_path)
        self._ok = bool(self._index) and bool(self._index.get("vectors"))
        if self._ok and check_hash:
            # a mismatched hash means the notes changed without a rebuild — don't trust it
            if self._index.get("kb_hash") not in (None, kb_hash()):
                self._ok = False

    def available(self) -> bool:
        return self._ok and self.backend is not None

    def query(self, text: str, k: int = 4) -> list[tuple[str, float]]:
        if not self.available():
            return []
        try:
            vecs = self.backend.embed([text])
            qv = vecs[0] if vecs else None
        except Exception:
            return []
        if not qv:
            return []
        scored = [(doc_id, _cosine(qv, vec)) for doc_id, vec in self._index["vectors"].items()]
        scored = [(d, s) for d, s in scored if s >= self.min_score]
        scored.sort(key=lambda p: -p[1])
        return scored[:k]
