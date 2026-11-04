"""Parse and track the multicast state the Multicast HUD shows.

Every router running ``mcast_tree.lua`` publishes a snapshot that ``gpipe cp status``
returns:

    MCAST v1 2
    G 239.1.1.1 IF 1,2 CP 1:120,2:118
    G 239.7.7.7 IF 2 CP 2:9

``parse_cp_status`` turns one router's snapshot into ``GroupState`` rows.
``McastTracker`` ingests rows from every router each poll and derives what the HUD
draws: the union of groups, each router's member interfaces, per-interface copy
*rates*, and a join/leave event log.

**A rate is measured between CHANGES of a counter, not between polls.** The router
republishes its snapshot once per control-plane tick, and the HUD polls every 1.2 s.
Differencing poll against poll is only right when every poll sees a new snapshot — and
`gpipe cp add lua mcast_tree.lua` with no tick argument runs at 5 s, so four polls in
five saw the same counters. Measured on a real gRouter with a steady 1 datagram/s
sender: the counter read 0,0,0,0,5,5,5,10,… and the HUD's rate went 0, 0, 0, 4/s, 0, 0 —
every member badge flashing teal for one poll and grey for three, while forwarding never
missed a datagram. That is what "the HUD shows multicast working sometimes" was. An
unchanged counter now means "no new snapshot yet": the last rate is held until the
counter moves again, or until it has been still for much longer than it usually takes
to move (`STALE_FACTOR`, never less than `STALE_MIN_S`) — which is a sender that stopped.
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


#: A counter that has not moved for this many of its usual intervals has stopped.
STALE_FACTOR = 2.5
#: …but never call it stopped sooner than this: two polls' worth, with room for a slow one.
STALE_MIN_S = 3.0
#: A rate is averaged over the counter's moves in about this long. A 1 s tick polled every
#: 1.2 s sees one copy, then two, then one — 0.8/s, 1.7/s, 0.8/s from the last move alone.
WINDOW_S = 6.0


@dataclass
class McastTracker:
    """Accumulates polls; derives rates and join/leave events."""
    state: dict[tuple[str, str], GroupState] = field(default_factory=dict)
    rates: dict[tuple[str, str, int], float] = field(default_factory=dict)
    events: list[McastEvent] = field(default_factory=list)
    max_events: int = 40
    # per (router, group, iface): the (count, poll time) of each time the counter moved, oldest
    # first, kept back to about WINDOW_S
    _moves: dict[tuple[str, str, int], list[tuple[int, float]]] = field(default_factory=dict)

    def _count(self, k: tuple[str, str, int], c: int, tnow: float) -> None:
        h = self._moves.get(k)
        if not h or c < h[-1][0]:                # first sight, or the module was reloaded
            if h:
                self.rates[k] = 0.0
            self._moves[k] = [(c, tnow)]
            return
        if c > h[-1][0]:
            h.append((c, tnow))
            while len(h) > 2 and tnow - h[1][1] >= WINDOW_S:
                h.pop(0)
            (c0, t0), (c1, t1) = h[0], h[-1]
            if t1 > t0:
                self.rates[k] = (c1 - c0) / (t1 - t0)
            return
        usual = (h[-1][1] - h[-2][1]) if len(h) >= 2 else 0.0
        if tnow - h[-1][1] > max(STALE_MIN_S, STALE_FACTOR * usual):
            self.rates[k] = 0.0                  # still for far longer than usual: stopped

    def _forget(self, k: tuple[str, str, int]) -> None:
        self.rates.pop(k, None)
        self._moves.pop(k, None)

    def ingest(self, rows: list[GroupState], tnow: float | None = None,
               polled: set[str] | None = None) -> None:
        """`polled` names every router that answered this poll (so a router whose
        snapshot went empty generates leaves, while an unpolled router is left
        alone). Defaults to the routers present in `rows`.

        "Answered" is the caller's to get right, and it was the HUD's second flicker: a
        query that FAILED (a docker exec that timed out, a busy engine) came back as text
        with no group rows, was counted as an answer, and every group on that router was
        logged as a leave — then joined again on the next poll that got through."""
        tnow = time.monotonic() if tnow is None else tnow
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
                self._count((r.router, r.group, i), c, tnow)
            self.state[key] = r

        # a group that vanished from a router we DID poll = every iface left
        for key in [k for k in self.state if k not in seen and k[0] in routers_polled]:
            old = self.state.pop(key)
            for i in old.ifaces:
                self.events.append(McastEvent(tnow, old.router, old.group, i, "leave"))
            for i in list(old.copies):
                self._forget((old.router, old.group, i))

        del self.events[:-self.max_events]

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
