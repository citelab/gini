"""When links fail and recover: the failure clocks (docs/design/link-properties.md, Phase 3).

Each link's failure model is two means, in seconds of running time (domain/link_props.py):
`fail_after` X (0 = never fails) and `repair_after` Y (0 = a failed link stays down until someone
restores it). Both times are EXPONENTIAL -- memoryless, the classic model of component failure --
so a link with both set alternates: up for an exponential time of mean X, down for one of mean Y,
up again. A flapping link, which is the best test a routing protocol can get.

Pure and deterministic: given the links and a seed, the whole schedule is fixed. That is what lets
a run be replayed, and a teacher pin the seed so a whole class sees the same failures. gBuilder's
main window owns the clock (one clock per link, in one place, so both ends of a link fail at the
same instant -- containers start seconds apart and could never agree on their own) and asks this
module what happens next.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from . import link_props as LP


@dataclass(order=True)
class Event:
    at: float                       # seconds of running time
    link_id: str = field(compare=False)
    up: bool = field(compare=False)  # False = the link fails, True = it is repaired


class Schedule:
    """The failure clocks of every link that can fail, from one seed.

    Each link gets its own random stream derived from the seed and its id, so adding or removing
    another link -- or changing its settings -- never shifts this one's schedule."""

    def __init__(self, topology, seed: int) -> None:
        self.seed = int(seed)
        self._links: dict[str, tuple[float, float]] = {}
        self._rng: dict[str, random.Random] = {}
        self._next: dict[str, Event] = {}
        self.down: set[str] = set()
        for l in topology.links.values():
            if not LP.can_fail(l):
                continue
            fail, repair = LP.get(l, LP.FAIL_AFTER), LP.get(l, LP.REPAIR_AFTER)
            if fail <= 0:
                continue                             # never fails: no clock at all
            self._links[l.id] = (fail, repair)
            self._rng[l.id] = random.Random(f"{self.seed}:{l.id}")
            self._next[l.id] = Event(self._draw(l.id, fail), l.id, False)

    def _draw(self, link_id: str, mean: float) -> float:
        return self._rng[link_id].expovariate(1.0 / mean)

    def next_event(self) -> Event | None:
        """The soonest pending failure or repair, or None if nothing will ever happen."""
        return min(self._next.values(), default=None)

    def due(self, now: float) -> list[Event]:
        """Every event at or before `now`, in order, advancing each link's clock past it. A link
        that fails with no repair time stays down: its clock stops until restore()."""
        out = []
        while True:
            ev = self.next_event()
            if ev is None or ev.at > now:
                return out
            out.append(ev)
            fail, repair = self._links[ev.link_id]
            if ev.up:
                self.down.discard(ev.link_id)
                self._next[ev.link_id] = Event(ev.at + self._draw(ev.link_id, fail),
                                               ev.link_id, False)
            else:
                self.down.add(ev.link_id)
                if repair > 0:
                    self._next[ev.link_id] = Event(ev.at + self._draw(ev.link_id, repair),
                                                   ev.link_id, True)
                else:
                    del self._next[ev.link_id]       # stays down until restored by hand

    def fail_now(self, link_id: str, now: float) -> None:
        """A manual failure (the link's Fail now). Its repair, if it has one, is drawn as usual."""
        self.down.add(link_id)
        fail, repair = self._links.get(link_id, (0.0, 0.0))
        if link_id in self._rng and repair > 0:
            self._next[link_id] = Event(now + self._draw(link_id, repair), link_id, True)
        else:
            self._next.pop(link_id, None)

    def restore(self, link_id: str, now: float) -> None:
        """A manual restore (the link's Restore): up again, and its next failure drawn afresh."""
        self.down.discard(link_id)
        fail, _repair = self._links.get(link_id, (0.0, 0.0))
        if link_id in self._rng and fail > 0:
            self._next[link_id] = Event(now + self._draw(link_id, fail), link_id, False)
        else:
            self._next.pop(link_id, None)


def endpoints(cfg, link_id: str) -> list[tuple[str, str, str]]:
    """Who has to act when `link_id` fails or recovers: [(kind, element, handle)].

    kind "router":  element is the router's (or OVS's) canvas name, handle its tunN.
    kind "fabric":  element is the switch's canvas name, handle the link id (`link <id> up|down`).
    kind "machine": element is the machine's canvas name, handle the link id.
    Canvas names, as everywhere else in gBuilder; the caller turns them into service names.
    Built from a compiled RuntimeConfig, which is the only place the link-to-interface mapping
    exists (it depends on the order links were drawn)."""
    out = []
    for r in cfg.routers:
        for i, itf in enumerate(r.ifaces, start=1):
            if getattr(itf, "link_id", "") == link_id:
                out.append(("router", r.name, f"tun{i}"))
    for o in getattr(cfg, "ovs_switches", []) or []:
        for i, ep in enumerate(getattr(o, "eps", []) or [], start=1):
            if getattr(ep, "link_id", "") == link_id:
                out.append(("router", o.name, f"tun{i}"))
    for s in getattr(cfg, "switches", []) or []:
        if any(getattr(ep, "link_id", "") == link_id for ep in s.eps):
            out.append(("fabric", s.name, link_id))
    for m in cfg.machines:
        if any(getattr(itf, "link_id", "") == link_id for itf in m.ifaces):
            out.append(("machine", m.name, link_id))
    return out


def commands(cfg, link_id: str, up: bool) -> list[tuple[str, str, str]]:
    """[(kind, element, console command)] that cut or restore `link_id` at every end."""
    word = "up" if up else "down"
    out = []
    for kind, element, handle in endpoints(cfg, link_id):
        if kind == "router":
            out.append((kind, element, f"ifconfig {word} {handle}"))
        else:
            out.append((kind, element, f"link {handle} {word}"))
    return out
