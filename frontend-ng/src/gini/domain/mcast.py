"""Parse and track the multicast state the Multicast HUD shows.

Every router running ``mcast_tree.lua`` publishes a snapshot that ``gpipe cp status``
returns:

    MCAST v1 2
    G 239.1.1.1 IF 1,2 CP 1:120,2:118
    G 239.7.7.7 IF 2 CP 2:9

``parse_cp_status`` turns one router's snapshot into ``GroupState`` rows.
``McastTracker`` ingests rows from every router each poll and derives what the HUD
draws: the union of groups, each router's member interfaces, per-interface copy
*rates* (counters differenced between polls), and a join/leave event log.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field


@dataclass
class GroupState:
    """One router's view of one group at one poll."""
    router: str
    group: str
    ifaces: list[int]
    copies: dict[int, int]          # iface -> cumulative copy counter


_LINE = re.compile(r"^G\s+(\S+)\s+IF\s+(\S+)(?:\s+CP\s+(\S+))?\s*$")


def parse_cp_status(text: str, router: str) -> list[GroupState]:
    """Parse one router's `gpipe cp status` output. Tolerates non-MCAST lines (other
    modules publish too) and garbled input."""
    rows: list[GroupState] = []
    for raw in (text or "").splitlines():
        m = _LINE.match(raw.strip())
        if not m:
            continue
        group, ifs, cps = m.group(1), m.group(2), m.group(3) or ""
        try:
            ifaces = [int(x) for x in ifs.split(",") if x != ""]
        except ValueError:
            continue
        copies: dict[int, int] = {}
        for part in cps.split(","):
            if ":" in part:
                a, b = part.split(":", 1)
                try:
                    copies[int(a)] = int(b)
                except ValueError:
                    pass
        rows.append(GroupState(router=router, group=group, ifaces=ifaces, copies=copies))
    return rows


@dataclass
class McastEvent:
    t: float                        # time.monotonic() of the poll that saw it
    router: str
    group: str
    iface: int
    kind: str                       # "join" | "leave"

    def label(self) -> str:
        return f"{self.router} if{self.iface} {self.kind} {self.group}"


@dataclass
class McastTracker:
    """Accumulates polls; derives rates and join/leave events."""
    state: dict[tuple[str, str], GroupState] = field(default_factory=dict)
    rates: dict[tuple[str, str, int], float] = field(default_factory=dict)
    events: list[McastEvent] = field(default_factory=list)
    _last_t: float = 0.0
    max_events: int = 40

    def ingest(self, rows: list[GroupState], tnow: float | None = None,
               polled: set[str] | None = None) -> None:
        """`polled` names every router that answered this poll (so a router whose
        snapshot went empty generates leaves, while an unpolled router is left
        alone). Defaults to the routers present in `rows`."""
        tnow = time.monotonic() if tnow is None else tnow
        dt = (tnow - self._last_t) if self._last_t else 0.0
        seen: set[tuple[str, str]] = set()
        routers_polled = set(polled) if polled is not None else {r.router for r in rows}

        for r in rows:
            key = (r.router, r.group)
            seen.add(key)
            prev = self.state.get(key)
            prev_ifs = set(prev.ifaces) if prev else set()
            for i in sorted(set(r.ifaces) - prev_ifs):
                self.events.append(McastEvent(tnow, r.router, r.group, i, "join"))
            for i in sorted(prev_ifs - set(r.ifaces)):
                self.events.append(McastEvent(tnow, r.router, r.group, i, "leave"))
            for i, c in r.copies.items():
                if prev and dt > 0:
                    dc = c - prev.copies.get(i, 0)
                    self.rates[(r.router, r.group, i)] = max(0.0, dc / dt)
            self.state[key] = r

        # a group that vanished from a router we DID poll = every iface left
        for key in [k for k in self.state if k not in seen and k[0] in routers_polled]:
            old = self.state.pop(key)
            for i in old.ifaces:
                self.events.append(McastEvent(tnow, old.router, old.group, i, "leave"))
            for i in list(old.copies):
                self.rates.pop((old.router, old.group, i), None)

        del self.events[:-self.max_events]
        self._last_t = tnow

    # ---- accessors the HUD paints from ---------------------------------- #
    def groups(self) -> list[str]:
        return sorted({g for (_, g) in self.state})

    def routers_for(self, group: str) -> list[GroupState]:
        return sorted((s for (r, g), s in self.state.items() if g == group),
                      key=lambda s: s.router)

    def rate(self, router: str, group: str, iface: int) -> float:
        return self.rates.get((router, group, iface), 0.0)

    def recent_events(self, n: int = 6) -> list[McastEvent]:
        return self.events[-n:]
