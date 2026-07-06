"""Hybrid recall — the layered retriever behind Ask GINI.

Given a student's question, find the GINI concept notes and recipes that ground the answer,
and report how CONFIDENT the match is (`strength`) so the reasoner can pick a grounding
stance. Three layers, cheap-first, each engaged only when the previous is thin:

  L0  lexical   — idf-weighted coverage over concepts+recipes, using `domain.lexicon` to
                  normalize student phrasing into GINI vocabulary (always runs, no deps);
  L1  expand    — if L0 is thin AND a model is available, ask it to rewrite the query into
                  GINI keywords, then re-run L0 (reuses the existing LLM; no embeddings);
  L2  embed     — if still empty AND an embedder is available, semantic recall over the
                  precomputed KB index (see `agent.embed`), which catches genuine synonyms
                  the finite lexicon can't anticipate ("software defined" ↔ SDN).

`strength ∈ {strong, thin, empty}` drives `ask.grounding_stance`. Pure/deterministic except
the injected `llm` (L1) and `embedder` (L2), so L0 is fully unit-testable offline.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field

from ..domain import concepts as _concepts
from ..domain import lexicon as _lex
from ..domain import recipes as _recipes

# strength thresholds on best idf-weighted coverage of the query's content terms
_STRONG = 0.45
_THIN = 0.15


@dataclass
class Hit:
    kind: str          # concept | recipe
    key: str
    obj: object
    score: float       # idf-weighted match mass (for ranking)
    coverage: float    # fraction of the query's idf mass that matched (for strength)


@dataclass
class RecallResult:
    concept_hits: list[Hit] = field(default_factory=list)
    recipe_hits: list[Hit] = field(default_factory=list)
    strength: str = "empty"
    used_expansion: bool = False
    used_embedding: bool = False

    @property
    def concepts(self) -> list:
        return [h.obj for h in self.concept_hits]

    @property
    def recipes(self) -> list:
        return [h.obj for h in self.recipe_hits]


# -- corpus + idf (built once) --------------------------------------------- #
def _concept_text(c) -> str:
    return " ".join((c.title, " ".join(c.keywords), " ".join(c.elements), c.body[:400]))


def _recipe_text(r) -> str:
    return " ".join((r.name, " ".join(r.intent), r.summary, r.teaches))


@dataclass
class _Doc:
    kind: str
    key: str
    obj: object
    tokens: frozenset


def _build_docs() -> list[_Doc]:
    docs: list[_Doc] = []
    for c in _concepts.CONCEPTS:
        docs.append(_Doc("concept", c.key, c, frozenset(_lex.normalize(_concept_text(c)))))
    for r in _recipes.RECIPES:
        docs.append(_Doc("recipe", r.id, r, frozenset(_lex.normalize(_recipe_text(r)))))
    return docs


_DOCS: list[_Doc] = _build_docs()
_N = len(_DOCS)


def _idf_table() -> dict[str, float]:
    df: dict[str, int] = {}
    for d in _DOCS:
        for t in d.tokens:
            df[t] = df.get(t, 0) + 1
    return {t: math.log((_N + 1) / (n + 0.5)) for t, n in df.items()}


_IDF = _idf_table()
_IDF_UNKNOWN = math.log((_N + 1) / 0.5)     # a query term absent from the KB is rare = informative


def _idf(token: str) -> float:
    return _IDF.get(token, _IDF_UNKNOWN)


def _query_tokens(text: str, terms) -> list[str]:
    toks = _lex.normalize(text, query=True)
    if terms:
        for extra in _lex.normalize(" ".join(str(t) for t in terms)):
            if extra not in toks:
                toks.append(extra)
    return toks


def _score(qtokens, doc: _Doc) -> tuple[float, float]:
    """(ranking score, coverage) of a query against one doc."""
    if not qtokens:
        return 0.0, 0.0
    matched = [t for t in qtokens if t in doc.tokens]
    score = sum(_idf(t) for t in matched)
    denom = sum(_idf(t) for t in qtokens)
    coverage = (score / denom) if denom else 0.0
    return score, coverage


def _rank(qtokens, kind: str) -> list[Hit]:
    hits: list[Hit] = []
    for d in _DOCS:
        if d.kind != kind:
            continue
        score, cov = _score(qtokens, d)
        if score > 0:
            hits.append(Hit(kind, d.key, d.obj, score, cov))
    hits.sort(key=lambda h: -h.score)
    return hits


def _strength(*hit_lists) -> str:
    best = max((h.coverage for hits in hit_lists for h in hits), default=0.0)
    if best >= _STRONG:
        return "strong"
    if best >= _THIN:
        return "thin"
    return "empty"


# -- public L0 API ---------------------------------------------------------- #
def search_concepts(text: str, terms=None) -> list[Hit]:
    return _rank(_query_tokens(text, terms or []), "concept")


def search_recipes(text: str, terms=None) -> list[Hit]:
    return _rank(_query_tokens(text, terms or []), "recipe")


def best_concept(text: str) -> tuple[object | None, str]:
    """Top concept + its strength — used by the offline 'explain <topic>' path."""
    hits = search_concepts(text)
    if not hits:
        return None, "empty"
    return hits[0].obj, _strength(hits[:1])


# -- L1 expansion ----------------------------------------------------------- #
_EXPAND_PROMPT = (
    "A student in a networks/cloud lab asked: {q!r}. Reply with ONLY a JSON array of up to 6 "
    "lowercase keywords naming the networking/cloud/OS topic (e.g. [\"load balancer\", "
    "\"round robin\"]). No prose."
)
_expand_cache: dict[str, list[str]] = {}


def _parse_array(text: str) -> list[str]:
    m = re.search(r"\[.*?\]", text or "", re.S)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    return [str(x).strip() for x in arr if isinstance(x, (str, int)) and str(x).strip()]


def expand_query(text: str, llm) -> list[str]:
    """Ask the model to name the topic in GINI-ish keywords (cached). Returns [] on any error
    so the caller degrades to L0."""
    key = (text or "").strip().lower()
    if key in _expand_cache:
        return _expand_cache[key]
    out: list[str] = []
    try:
        out = _parse_array(llm(_EXPAND_PROMPT.format(q=text)))
    except Exception:
        out = []
    _expand_cache[key] = out
    return out


# -- L2 embedding ----------------------------------------------------------- #
def _obj_for(doc_id: str):
    kind, _, key = doc_id.partition(":")
    if kind == "concept":
        return "concept", key, _concepts.by_key(key)
    if kind == "recipe":
        return "recipe", key, _recipes.get_recipe(key)
    return kind, key, None


# -- the orchestrator ------------------------------------------------------- #
def recall(text: str, terms=None, *, llm=None, embedder=None, deep: bool = False) -> RecallResult:
    """Run L0 → (L1 if thin) → (L2 if empty) and return ranked hits + strength."""
    terms = list(terms or [])
    chits = search_concepts(text, terms)
    rhits = search_recipes(text, terms)
    strength = _strength(chits, rhits)
    used_expansion = used_embedding = False

    if strength != "strong" and llm is not None:
        extra = expand_query(text, llm)
        if extra:
            terms = terms + extra
            chits = search_concepts(text, terms)
            rhits = search_recipes(text, terms)
            new_strength = _strength(chits, rhits)
            used_expansion = new_strength != strength or bool(extra)
            strength = new_strength

    if strength == "empty" and embedder is not None and getattr(embedder, "available", lambda: False)():
        seen_c = {h.key for h in chits}
        seen_r = {h.key for h in rhits}
        for doc_id, escore in embedder.query(text, k=4):
            kind, key, obj = _obj_for(doc_id)
            if obj is None:
                continue
            if kind == "concept" and key not in seen_c:
                chits.append(Hit("concept", key, obj, float(escore), float(escore)))
                seen_c.add(key)
                used_embedding = True
            elif kind == "recipe" and key not in seen_r:
                rhits.append(Hit("recipe", key, obj, float(escore), float(escore)))
                seen_r.add(key)
                used_embedding = True
        if used_embedding:
            chits.sort(key=lambda h: -h.score)
            rhits.sort(key=lambda h: -h.score)
            strength = "thin"          # semantic-only matches are treated as partial grounding

    return RecallResult(chits, rhits, strength, used_expansion, used_embedding)
