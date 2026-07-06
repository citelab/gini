"""Session accumulator — the agent's working *domain* memory.

Distinct from dialogue history (which is turns of conversation): this holds the GINI
knowledge CARDS retrieved so far this session, so the agent builds up understanding and can
reason over earlier facts on a later, deeper question. Cards are deduped by (kind, key) and
kept most-recent-last. When the block outgrows its budget the oldest cards are folded into a
running summary via a small-LLM pass (the chosen compaction) — with a bounded rule-based
fallback when no model is available.

Pure logic + an injectable `llm` callable(prompt)->str, so it's testable offline.
"""
from __future__ import annotations

from .kb import Card

_SUMMARY_PROMPT = (
    "Compress these GINI facts a student has already seen this session into a few compact "
    "sentences. Keep element names and the key 'how it works' points; drop repetition. "
    "Reply with only the summary.\n\n"
)


class SessionKnowledge:
    def __init__(self, max_chars: int = 6000, keep_recent: int = 8) -> None:
        self.cards: list[Card] = []          # most-recent last
        self.summary: str = ""
        self.max_chars = max_chars
        self.keep_recent = keep_recent

    # -- accumulate --------------------------------------------------------- #
    def add(self, cards, llm=None) -> None:
        """Fold newly-retrieved cards in (dedup by kind+key, move-to-end), then compact."""
        for c in cards or []:
            self.cards = [x for x in self.cards if (x.kind, x.key) != (c.kind, c.key)]
            self.cards.append(c)
        self.compact(llm)

    def _size(self) -> int:
        return len(self.summary) + sum(len(c.text) for c in self.cards)

    # -- compaction --------------------------------------------------------- #
    def compact(self, llm=None) -> None:
        """Keep the block under budget by summarising the oldest cards into `summary`."""
        guard = 0
        while self._size() > self.max_chars and len(self.cards) > 1 and guard < 20:
            guard += 1
            keep = min(self.keep_recent, max(1, len(self.cards) - 1))
            overflow = self.cards[:-keep]
            if not overflow:                 # recent cards alone exceed budget
                overflow = self.cards[:1]
            self.summary = self._summarize(self.summary,
                                           "\n".join(c.text for c in overflow), llm)
            self.cards = self.cards[len(overflow):]
        # last resort: even the summary is too big
        if len(self.summary) > self.max_chars:
            self.summary = self.summary[-self.max_chars:]

    def _summarize(self, prior: str, new: str, llm) -> str:
        if llm is not None:
            try:
                out = (llm(_SUMMARY_PROMPT + (prior + "\n" if prior else "") + new) or "").strip()
                if out:
                    return out
            except Exception:
                pass
        combined = (prior + " " + new).strip()
        return combined[-(self.max_chars // 2):]    # bounded rule-based fallback

    # -- render ------------------------------------------------------------- #
    def as_context(self) -> str:
        parts = []
        if self.summary:
            parts.append("Session knowledge so far: " + self.summary)
        if self.cards:
            parts.append("\n".join("- " + c.text for c in self.cards))
        return "\n".join(parts)

    def clear(self) -> None:
        self.cards, self.summary = [], ""
