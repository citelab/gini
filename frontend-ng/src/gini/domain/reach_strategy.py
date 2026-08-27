"""How "does every station reach every other" gets answered — completely, and cheaply.

The naive way is one `docker compose exec` per ordered pair: n(n-1) process spawns, ~0.19 s of
overhead each against a ~2 ms ping. Ten stations is 90 execs and half a minute; fifty is a quarter
of an hour. That is unaffordable at an observation cadence, so something has to give.

**What gives is the process count, not the coverage.** One exec per *host*, sweeping every other
host in parallel inside the container, measures the whole relation in n execs: `a -> b` comes from
a's sweep and `b -> a` from b's. Every ordered pair is measured. Nothing is inferred.

    per-pair probes   sweep per host   + 8-way concurrency
      9 hosts   22 s        10.7 s              1.3 s
     50 hosts  735 s        59.5 s              7.4 s

**Why not infer from transitivity?** Because it is unsound here, and that was measured rather than
guessed. In an unfiltered network reachability is an equivalence relation, so probing a spanning
star from one representative would prove the rest — n-1 probes instead of n(n-1). But a single
`iptables` rule inside one station breaks transitivity, and such a rule is invisible in the
topology graph, so no structural check can predict it. On a measured 3-LAN lab with one station
dropping one subnet, 5 of 9 possible representatives concluded "all reachable" when they were not.
A cross-check across several representatives narrows that but cannot close it: detecting an
arbitrary blocked pair requires probing that pair, which is information-theoretic rather than an
engineering gap. Measured detection for one blocked pair was 61% with one representative per
segment, against 100% for the sweep — at a cost difference that does not justify being wrong.

So: no inference, no representatives, no guard, no assumption about filtering. Pure and Qt-free;
the Docker half lives in `services/probe_runner.py`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# A destination whose result could not be read. Distinct from False on purpose: "the sweep produced
# nothing" and "the host is unreachable" are different facts, and collapsing them would let a
# broken measurement masquerade as a broken network.
UNKNOWN = None


def sweep_plan(hosts) -> list[tuple[str, tuple]]:
    """One (source, destinations) sweep per host — the whole measurement, ordered deterministically.

    Sorted throughout because the plan is part of a fairness-critical instrument: two students with
    the same topology must be probed identically, and iteration order over a dict or set is not a
    promise anybody should rely on for that.
    """
    ordered = sorted(hosts)
    return [(src, tuple(d for d in ordered if d != src)) for src in ordered]


def probe_count(hosts) -> tuple[int, int]:
    """(execs, ordered pairs measured) — for reporting what a run actually cost."""
    n = len(set(hosts))
    return n, n * (n - 1)


@dataclass(frozen=True)
class Relation:
    """The measured reachability relation over a set of hosts.

    `pairs` maps (src, dst) -> True | False | UNKNOWN. Complete: every ordered pair of distinct
    hosts appears, so nothing here is an inference.
    """
    hosts: tuple
    pairs: dict = field(default_factory=dict)

    # -- basic queries ---------------------------------------------------- #
    def get(self, a: str, b: str):
        return self.pairs.get((a, b), UNKNOWN)

    @property
    def unknown(self) -> list[tuple]:
        """Pairs whose result could not be read. A measurement fault, not a network fault."""
        return sorted(p for p, v in self.pairs.items() if v is UNKNOWN)

    @property
    def failures(self) -> list[tuple]:
        return sorted(p for p, v in self.pairs.items() if v is False)

    def all_reach(self) -> bool:
        """Every ordered pair measured reachable.

        An UNKNOWN is not a pass. A sweep that failed to report tells us nothing, and treating
        silence as success is how a broken probe becomes a student's undeserved tick.
        """
        return bool(self.pairs) and all(v is True for v in self.pairs.values())

    def any_reach(self) -> bool:
        return any(v is True for v in self.pairs.values())

    # -- structure -------------------------------------------------------- #
    def islands(self) -> list[list[str]]:
        """Hosts grouped by mutual reachability — the shape a report should show.

        "Three stations unreachable" is a result; "everything on 10.0.3.x is unreachable" points at
        a router. Union-find over pairs that succeeded BOTH ways.

        Note this is a transitive *closure* of what was measured, so on a non-transitive relation it
        will merge hosts that cannot actually reach each other. That is not a flaw to hide: compare
        against `counterexamples()`, and when those exist the islands are a summary rather than a
        partition. `all_reach()` never consults this.
        """
        parent = {h: h for h in self.hosts}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for (a, b), ok in sorted(self.pairs.items()):
            if ok is True and self.get(b, a) is True:
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[max(ra, rb)] = min(ra, rb)

        groups: dict = {}
        for h in self.hosts:
            groups.setdefault(find(h), []).append(h)
        return sorted((sorted(g) for g in groups.values()), key=lambda g: g[0])

    def asymmetric(self) -> list[tuple]:
        """Pairs where a reaches b but b does not reach a — usually a routing mistake in one
        direction, and worth naming because a symmetric summary would hide it."""
        return sorted((a, b) for (a, b), ok in self.pairs.items()
                      if ok is True and self.get(b, a) is False)

    def counterexamples(self, limit: int = 10) -> list[tuple]:
        """Triples where a->b and b->c hold but a->c does not.

        Not used to decide anything — the sweep measures every pair, so no verdict depends on
        transitivity. Kept because it names *why* a network is confusing: a non-transitive relation
        is the signature of filtering, and telling a teacher "M1 reaches M4, M4 reaches M7, but M1
        cannot reach M7" is a far better clue than a list of failed pairs.
        """
        out = []
        for a in self.hosts:
            for b in self.hosts:
                if b == a or self.get(a, b) is not True:
                    continue
                for c in self.hosts:
                    if c in (a, b) or self.get(b, c) is not True:
                        continue
                    if self.get(a, c) is False:
                        out.append((a, b, c))
                        if len(out) >= limit:
                            return out
        return out

    def summary(self) -> str:
        n_ok = sum(1 for v in self.pairs.values() if v is True)
        bits = [f"{n_ok}/{len(self.pairs)} ordered pairs reachable"]
        if self.unknown:
            bits.append(f"{len(self.unknown)} unreadable")
        groups = self.islands()
        if len(groups) > 1:
            bits.append(f"{len(groups)} islands: "
                        + " | ".join("{" + ", ".join(g) + "}" for g in groups))
        return "; ".join(bits)


def measure(hosts, sweep) -> Relation:
    """Run the plan and assemble the relation.

    `sweep(src, dsts) -> {dst: True|False|UNKNOWN}` does one host's worth of probing — in
    production a single batched exec, in tests a dict lookup. Keeping that the only injected
    behaviour is what makes this module testable with no Docker and no network.

    A sweep that omits a destination, or returns nothing at all, leaves UNKNOWN rather than False.
    """
    ordered = tuple(sorted(set(hosts)))
    pairs: dict = {}
    for src, dsts in sweep_plan(ordered):
        try:
            got = sweep(src, list(dsts)) or {}
        except Exception:                      # noqa: BLE001 — one bad host must not lose the rest
            got = {}
        for d in dsts:
            v = got.get(d, UNKNOWN)
            pairs[(src, d)] = v if v in (True, False) else UNKNOWN
    return Relation(hosts=ordered, pairs=pairs)
