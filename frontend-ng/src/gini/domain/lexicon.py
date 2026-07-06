"""Lexical normalization for GINI retrieval — the dependency-free layer that closes most of
the recall gap before we ever reach embeddings.

Three cheap transforms turn a student's phrasing into the GINI vocabulary the (lexical)
retriever indexes on:

  1. tokenize   — split to lowercase word tokens, drop query-noise ("explain", "what", …);
  2. stem       — a tiny suffix stemmer so "routing"/"routes" collapse to one form;
  3. synonyms   — a small, HAND-OWNED map from student words / real-world product names to
                  GINI's canonical terms (e.g. "vm" -> instance, "software defined" -> sdn).

Pure data + functions, no dependencies. The synonym map is deliberately tiny and lives here
beside the concepts it feeds; a test asserts every mapped target is a real concept keyword,
so the map can't rot silently.
"""
from __future__ import annotations

import re

# words that carry no topic signal in a question — stripped from the QUERY side so a short
# query like "explain SDN" reduces to its content term {sdn}.
_QUERY_STOP = frozenset((
    "a", "an", "the", "is", "are", "am", "of", "to", "in", "on", "for", "and", "or", "with",
    "how", "what", "whats", "why", "when", "where", "which", "who", "do", "does", "did", "can",
    "could", "would", "should", "i", "we", "you", "my", "me", "us", "explain", "tell", "about",
    "show", "give", "describe", "please", "help", "want", "need", "use", "using", "work",
    "works", "working", "it", "this", "that", "these", "those", "be", "get", "make", "set",
    "up", "into", "from", "at", "as", "if", "so", "then", "than", "vs", "versus", "between",
))

# single-token synonyms → one or more canonical GINI terms (already in the KB's vocabulary).
_SYNONYMS: dict[str, tuple[str, ...]] = {
    "vm": ("instance",), "vms": ("instance",), "virtualmachine": ("instance",),
    "lambda": ("function", "serverless"), "faas": ("function", "serverless"),
    "serverlessfunction": ("function",),
    "bucket": ("object", "storage"), "buckets": ("object", "storage"), "s3": ("object", "storage"),
    "blob": ("object", "storage"),
    "bus": ("queue", "messaging"), "broker": ("queue", "messaging"),
    "kafka": ("stream",), "rabbitmq": ("queue",), "nats": ("messaging",),
    "waf": ("firewall",), "iptables": ("firewall",),
    "segmentation": ("security", "group", "isolation"),
    "microsegmentation": ("security", "group", "isolation"),
    "k8s": ("kubernetes",), "k3s": ("kubernetes",), "hpa": ("autoscaling", "pod"),
    "db": ("database",), "rdbms": ("database",), "postgres": ("database",), "postgresql": ("database",),
    "mongo": ("nosql",), "mongodb": ("nosql",), "redis": ("cache",), "memcached": ("cache",),
    "lb": ("load", "balancer"), "nginx": ("load", "balancer"), "haproxy": ("load", "balancer"),
    "traefik": ("proxy", "gateway"), "envoy": ("proxy",),
    "prometheus": ("metrics",), "grafana": ("dashboard",), "jaeger": ("tracing",),
    "microvm": ("kata", "isolation"), "hypervisor": ("kata", "isolation"),
    "os": ("kernel", "process"), "syscall": ("system", "call"),
    "vpn": ("gateway", "network"), "nat": ("internet", "gateway"),
    "middlebox": ("vnf", "nfv"), "appliance": ("vnf", "nfv"),
    "l2": ("switch", "layer"), "l3": ("router", "layer"),
    "wifi": ("wap", "wireless"), "wlan": ("wap", "wireless"),
}

# multi-word phrases → canonical terms, matched on the raw (lowercased) query before tokenizing.
# This is where "software defined" -> sdn lives (the exact case that exposed lexical brittleness).
_PHRASES: dict[str, tuple[str, ...]] = {
    "software defined": ("sdn", "openflow"),
    "software-defined": ("sdn", "openflow"),
    "control plane": ("sdn", "control"),
    "data plane": ("sdn", "data"),
    "message bus": ("queue", "messaging"),
    "message queue": ("queue",),
    "event log": ("stream",),
    "object storage": ("object", "storage"),
    "block storage": ("block", "volume"),
    "virtual machine": ("instance", "vm"),
    "load balancer": ("load", "balancer"),
    "load balancing": ("load", "balancer"),
    "reverse proxy": ("proxy",),
    "service mesh": ("proxy", "sidecar"),
    "security group": ("security", "group"),
    "network function": ("vnf", "nfv"),
    "service chain": ("sfc", "chain"),
    "service function chain": ("sfc", "chain"),
    "access point": ("wap", "wireless"),
    "context switch": ("scheduler", "context"),
    "page table": ("paging", "memory"),
    "virtual memory": ("paging", "memory"),
    "file system": ("filesystem", "inode"),
    "operating system": ("kernel", "process"),
    "system call": ("syscall", "kernel"),
}

_WORD = re.compile(r"[a-z0-9][a-z0-9+]*")


def _stem(word: str) -> str:
    """A deliberately tiny suffix stemmer — enough to fold common inflections without a
    dictionary. Order matters (longest suffix first); never stem below 3 chars."""
    w = word
    for suf in ("ization", "isation", "ing", "ies", "ied", "ion", "ers", "er", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            base = w[: -len(suf)]
            if suf == "ies":
                base += "y"
            return base
    return w


def tokenize(text: str, *, drop_stop: bool = False) -> list[str]:
    """Lowercase word tokens. With `drop_stop`, remove query-noise words (used on the QUERY
    side; documents keep all their words)."""
    toks = _WORD.findall((text or "").lower())
    if drop_stop:
        toks = [t for t in toks if t not in _QUERY_STOP]
    return toks


def _expand_phrases(low: str) -> list[str]:
    extra: list[str] = []
    for phrase, terms in _PHRASES.items():
        if phrase in low:
            extra.extend(terms)
    return extra


def normalize(text: str, *, query: bool = False) -> list[str]:
    """Normalized, de-duplicated token list for scoring: phrase expansion + tokenization +
    stemming + single-token synonym expansion. On the query side, noise words are dropped."""
    low = (text or "").lower()
    raw = tokenize(low, drop_stop=query)
    out: list[str] = []
    seen: set[str] = set()

    def add(tok: str) -> None:
        s = _stem(tok)
        if s and s not in seen:
            seen.add(s)
            out.append(s)

    for tok in _expand_phrases(low):     # multi-word phrase hits first
        add(tok)
    for tok in raw:
        add(tok)
        for syn in _SYNONYMS.get(tok, ()):    # single-token synonyms
            add(syn)
    return out


def synonym_targets() -> set[str]:
    """Every canonical term the synonym/phrase maps point at (for the sync test)."""
    out: set[str] = set()
    for terms in _SYNONYMS.values():
        out.update(terms)
    for terms in _PHRASES.values():
        out.update(terms)
    return out
