"""Flow event log — accumulate the controller's flow-rule installs/expiries over time.

The live flow table is only a snapshot: a reactive controller installs exact-match rules for
learned conversations and they expire after an idle timeout, so at any instant you see only a
few. To show the FULL picture of what the controller programmed, `FlowLog` diffs successive
snapshots (the dashboard polls `openflow entry all` every ~2.5s): a rule present now but not
before is an "installed" event; one present before but not now is an "expired" event. This
captures every rule that lived at least one poll — no backend change needed.

Rules are identified by their match+action (the flow's identity), since the numeric slot
index gets reused. Pure/duck-typed (rows just need `match_summary()`/`action_summary()` and
`.packets`), so it's unit-tested without Qt or Docker.
"""
from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class FlowEvent:
    kind: str            # "installed" | "expired"
    match: str
    action: str
    packets: int | None  # at install: ~0; at expire: the final count the rule handled
    when: str            # HH:MM:SS


class FlowLog:
    def __init__(self, cap: int = 300) -> None:
        self._prev: dict[str, dict] = {}     # key -> {match, action, packets}
        self.events: list[FlowEvent] = []    # oldest first
        self.cap = cap

    def update(self, rows, now: str | None = None) -> list[FlowEvent]:
        """Fold a fresh snapshot in; return the events newly detected this poll."""
        ts = now or time.strftime("%H:%M:%S")
        cur: dict[str, dict] = {}
        for f in rows or []:
            match, action = f.match_summary(), f.action_summary()
            cur[f"{match} ⇒ {action}"] = {"match": match, "action": action,
                                          "packets": getattr(f, "packets", None)}
        fresh: list[FlowEvent] = []
        for key, v in cur.items():
            if key not in self._prev:
                fresh.append(FlowEvent("installed", v["match"], v["action"], v["packets"], ts))
        for key, v in self._prev.items():
            if key not in cur:                # gone this poll -> expired/removed
                fresh.append(FlowEvent("expired", v["match"], v["action"], v["packets"], ts))
        self._prev = cur
        self.events.extend(fresh)
        if len(self.events) > self.cap:
            self.events = self.events[-self.cap:]
        return fresh

    def recent(self, n: int = 200) -> list[FlowEvent]:
        """Most-recent events first (for display)."""
        return list(reversed(self.events[-n:]))

    def clear(self) -> None:
        self._prev, self.events = {}, []
