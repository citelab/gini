"""The OS manual as a retrievable corpus — the one that was written for this and never read.

`docs/manual/README.md` says it outright:

    written to be indexable by GINI AI — every page carries YAML frontmatter and the same heading
    skeleton, so a retrieval system can answer "what is this number?", "where does it come from?",
    and "what are its limits?" from a single page.

Seventeen pages, 2,420 lines, `keywords` / `endpoints` / `kernel_files` on every one, and until now
nothing has ever opened them. This is the reader.

**Matching runs against the frontmatter, not the prose.** A page's body mentions everything it
touches, so scoring against the whole text makes every page match every question. The `keywords`
line is a human's deliberate answer to "what is this page for", and using it is cheaper, sharper,
and honest about where the judgement came from.

**`limits()` is why this module is more than search.** Every page carries a required *"Limits and
honesty"* section, and those bullets are the tree's own record of what must NOT be claimed — the
observer appearing in its own data, scause 14 not existing, an idle machine's feed being nearly all
timer. For the Reasoning Twin those are concerns with deterministic evidence behind them: an answer
asserting something a page lists as a limit is a silent miss that can be proved, not suspected.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from gini.domain.similarity import idf_table, query_terms, weighted_coverage

#: `reason/gini_reason/manual.py` -> the repo root -> docs/manual. The service runs from a checkout
#: (see reason/README.md), so the manual is simply there; `GINI_MANUAL` overrides it for a deploy
#: that puts the docs somewhere else.
def manual_root() -> Path:
    env = os.environ.get("GINI_MANUAL", "").strip()
    return Path(env) if env else Path(__file__).resolve().parents[2] / "docs" / "manual"


@dataclass(frozen=True)
class Page:
    id: str
    title: str
    name: str                      # the file, for citing
    subsystem: str
    keywords: tuple
    endpoints: tuple
    sections: dict                 # heading -> body text
    terms: str                     # the matchable surface: title + keywords + subsystem

    def section(self, heading: str) -> str:
        """One section by heading, matched loosely — two pages write 'Wire format — record
        reference' where the rest write 'Wire format', and a caller should not have to know."""
        want = heading.lower()
        for h, body in self.sections.items():
            if h.lower().startswith(want):
                return body
        return ""


_FM = re.compile(r"^---\n(.*?)\n---\n", re.S)
_LIST = re.compile(r"^\[(.*)\]$")


def _frontmatter(text: str) -> dict:
    """The YAML header, parsed by hand.

    Deliberately not `yaml.safe_load`: the frontmatter here is flat `key: value` with bracketed
    lists, PyYAML is a dependency this service does not otherwise need, and a parser that accepts
    less is a parser that cannot be surprised by a page.
    """
    m = _FM.match(text)
    out: dict = {}
    if not m:
        return out
    for line in m.group(1).splitlines():
        if ":" not in line or line.startswith(" "):
            continue
        k, _, v = line.partition(":")
        v = v.strip()
        lst = _LIST.match(v)
        out[k.strip()] = (tuple(x.strip() for x in lst.group(1).split(",") if x.strip())
                          if lst else v)
    return out


def _sections(text: str) -> dict:
    out, heading, buf = {}, "", []
    for line in text.splitlines():
        if line.startswith("## "):
            if heading:
                out[heading] = "\n".join(buf).strip()
            heading, buf = line[3:].strip(), []
        elif heading:
            buf.append(line)
    if heading:
        out[heading] = "\n".join(buf).strip()
    return out


def _page(path: Path) -> Page | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    fm = _frontmatter(text)
    if not fm.get("id"):
        return None                # README and anything else without a header is not a page
    kw = fm.get("keywords", ())
    kw = kw if isinstance(kw, tuple) else tuple(x.strip() for x in str(kw).split(",") if x.strip())
    ep = fm.get("endpoints", ())
    ep = ep if isinstance(ep, tuple) else (str(ep),) if ep else ()
    surface = " ".join([str(fm.get("title", "")), str(fm.get("subsystem", "")), " ".join(kw)])
    return Page(id=str(fm["id"]), title=str(fm.get("title", "")), name=path.name,
                subsystem=str(fm.get("subsystem", "")), keywords=kw, endpoints=ep,
                sections=_sections(text), terms=query_terms(surface))


@lru_cache(maxsize=1)
def pages(root: str = "") -> tuple:
    """Every indexable page. Cached: the manual does not change while a service is up, and a
    question should not cost seventeen file reads."""
    base = Path(root) if root else manual_root()
    if not base.is_dir():
        return ()
    found = [_page(p) for p in sorted(base.glob("*.md"))]
    return tuple(p for p in found if p is not None)


@lru_cache(maxsize=1)
def _weights(root: str = "") -> dict:
    """Term weights over the manual, computed once. Rebuilt only if the cache is cleared, which is
    correct: seventeen files do not change under a running service."""
    return idf_table(p.terms for p in pages(root))


def find(question: str, *, limit: int = 3, floor: float = 0.25, root: str = "") -> list:
    """Pages that speak to the question, best first, as `[(Page, score)]`.

    Scored by how much of the QUESTION's meaning the page's keywords address, weighted by how rare
    each term is across the manual. Unweighted coverage was the first version and it ties
    constantly on a corpus this small: "what does scause 13 mean" put `os-storage` level with
    `os-traps` at 0.33, because each matched one of three terms, and the tie broke alphabetically.
    Weighted, `scause` counts for several times `mean` and the right page wins.

    `floor` is low on purpose. This feeds retrieval, and the layer above decides what to do with a
    thin result; dropping a page here would hide it from that decision.
    """
    # `query_terms`, not `terms`: the latter refuses anything under three content words, which is
    # right for clustering and wrong for a search. "lock contention" is a perfectly good question.
    q = query_terms(question)
    if not q:
        return []
    idx = pages(root)
    table = _weights(root)
    hits = [(p, weighted_coverage(q, p.terms, table)) for p in idx]
    hits = [(p, s) for p, s in hits if s >= floor]
    # Ties broken by how much RARE meaning matched, not alphabetically. On a corpus this small two
    # pages routinely cover the same fraction of a short question, and "os-storage before os-traps"
    # is not a judgement — it is the sort order leaking into the answer.
    table = _weights(root)
    mass = lambda p: sum(table.get(t, 0.0) for t in q.split() if t in set(p.terms.split()))
    hits.sort(key=lambda h: (-h[1], -mass(h[0]), h[0].id))
    return hits[:limit]


def limits(page: Page) -> list:
    """The page's own statements of what must not be claimed, one per bullet.

    The Twin's evidence. Each is a sentence somebody wrote deliberately under a heading the manual
    requires every page to carry, which is what makes an objection built on one provable rather
    than merely plausible.
    """
    body = page.section("Limits and honesty")
    if not body:
        return []
    out, cur = [], []
    for line in body.splitlines():
        if line.strip().startswith(("- ", "* ")):
            if cur:
                out.append(" ".join(cur))
            cur = [line.strip()[2:].strip()]
        elif cur and line.strip():
            cur.append(line.strip())
        elif cur:
            out.append(" ".join(cur))
            cur = []
    if cur:
        out.append(" ".join(cur))
    return [re.sub(r"\s+", " ", x).strip() for x in out if x.strip()]
