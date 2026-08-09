"""Behavioral probes — the language of GINI-as-oracle win conditions.

A behavioral objective asserts a fact about the RUNNING system, which only GINI's runtime can
witness. This module parses the probe strings and evaluates them against a `Runner` — the thin
seam over the running topology (Docker exec of ping/curl, flow-table reads). GINI, never the
model, produces the verdict.

Probe grammar (Phase 2):
    reach(A -> B) == ok|fail          # L3 reachability (ICMP); with :port → TCP/L4
    reach(A -> B:port) == ok|fail
    reach(A -> B, all) == ok          # EVERY A-B pair, not just some (see TypeRunner.reach_all)
    ping(A -> B) == ok|fail           # explicit ICMP
    http(A -> B:port) == ok|fail      # an HTTP 2xx from A to B:port
    balances(LB, >= n)                # LB fans out to at least n live backends
    flow_installed(OVS, <match>)      # an OpenFlow rule matching <match> is installed

Pure parsing + evaluation; the real Docker-backed runner lives in `services/probe_runner.py`,
and a `FakeRunner` (facts dict) makes everything unit-testable offline.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

# probe kinds
REACH, PING, HTTP, BALANCES, FLOW = "reach", "ping", "http", "balances", "flow_installed"
MEASURE = "measure"

_REACH_RE = re.compile(                          # `\w+` or a slot-scoped `\w+@\w+` (e.g. host@A)
    r"^\s*(reach|ping|http)\(\s*([\w@]+)\s*->\s*([\w@]+)(?::(\d+))?\s*(?:,\s*(all|any)\s*)?\)"
    r"\s*==\s*(ok|fail)\s*$")
_BAL_RE = re.compile(r"^\s*balances\(\s*(\w+)\s*,\s*>=\s*(\d+)\s*\)\s*$")
_FLOW_RE = re.compile(r"^\s*flow_installed\(\s*(\w+)\s*,\s*(.+?)\s*\)\s*$")
# measure(<rider_type>, <metric>) <op> <value> — an output assertion on a Sink/Source measurement
_MEASURE_RE = re.compile(
    r"^\s*measure\(\s*(\w+)\s*,\s*(\w+)\s*\)\s*(>=|<=|==|!=|>|<)\s*(-?\d+(?:\.\d+)?)\s*$")


class ProbeError(ValueError):
    pass


@dataclass(frozen=True)
class Probe:
    kind: str
    src: str = ""
    dst: str = ""
    port: int | None = None
    expect_ok: bool = True      # for reach/ping/http: True = expect success, False = expect failure
    n: int = 0                  # for balances
    match: str = ""             # for flow_installed
    quant: str = "any"          # "any" = SOME pair satisfies it; "all" = EVERY pair must
    metric: str = ""            # for measure: the rider metric (loss_pct, packets, mbps, …)
    op: str = ">="              # for measure: comparison operator
    value: float = 0.0          # for measure: the threshold


class Runner(Protocol):
    """The running-topology seam. `available()` is False when there's no live runtime, so
    behavioral objectives stay `pending` rather than being wrongly failed."""
    def available(self) -> bool: ...
    def reach(self, src: str, dst: str, port: int | None = None) -> bool: ...
    def http(self, src: str, dst: str, port: int) -> bool: ...
    def backends(self, lb: str) -> int: ...
    def flow(self, ovs: str, match: str) -> bool: ...
    def measure(self, rider_type: str, metric: str) -> float | None: ...


def parse(probe: str) -> Probe:
    m = _REACH_RE.match(probe or "")
    if m:
        kind, src, dst, port, quant, expect = m.groups()
        return Probe(kind=kind, src=src, dst=dst,
                     port=int(port) if port else None, expect_ok=(expect == "ok"),
                     quant=quant or "any")
    m = _BAL_RE.match(probe or "")
    if m:
        return Probe(kind=BALANCES, src=m.group(1), n=int(m.group(2)))
    m = _FLOW_RE.match(probe or "")
    if m:
        return Probe(kind=FLOW, src=m.group(1), match=m.group(2))
    m = _MEASURE_RE.match(probe or "")
    if m:
        return Probe(kind=MEASURE, src=m.group(1), metric=m.group(2),
                     op=m.group(3), value=float(m.group(4)))
    raise ProbeError(f"cannot parse probe: {probe!r}")


_OPS = {">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b,
        "<": lambda a, b: a < b, "==": lambda a, b: a == b, "!=": lambda a, b: a != b}


def probe_ok(probe: str) -> bool:
    try:
        parse(probe)
        return True
    except ProbeError:
        return False


def evaluate(probe: str, runner: Runner) -> bool:
    """Evaluate a probe string against a live runner → the boolean fact.

    The `all` quantifier only means something to a TYPE-resolving runner (one token can name many
    devices). A plain name-based runner has exactly one pair, so `all` and `any` coincide and it
    can simply not implement `*_all` — hence the getattr fallback rather than a protocol break."""
    p = parse(probe)
    if p.kind in (REACH, PING):
        fn = getattr(runner, "reach_all", None) if p.quant == "all" else None
        got = fn(p.src, p.dst, p.port) if fn else runner.reach(p.src, p.dst, p.port)
        return got == p.expect_ok
    if p.kind == HTTP:
        fn = getattr(runner, "http_all", None) if p.quant == "all" else None
        port = p.port or 80
        got = fn(p.src, p.dst, port) if fn else runner.http(p.src, p.dst, port)
        return got == p.expect_ok
    if p.kind == BALANCES:
        return runner.backends(p.src) >= p.n
    if p.kind == FLOW:
        return runner.flow(p.src, p.match)
    if p.kind == MEASURE:
        got = runner.measure(p.src, p.metric)
        if got is None:
            return False                    # no reading yet — treat as not-satisfied, not a crash
        return _OPS[p.op](got, p.value)
    raise ProbeError(f"unknown probe kind {p.kind!r}")


# -- type-aware resolution (name-agnostic behavioral probes) ---------------- #
class TypeRunner:
    """Wraps a name-based `Runner` so a probe's tokens are element TYPE keys, resolved existentially
    against the live topology — the behavioral analogue of our type-based structural predicates.
    `reach(web_app -> database)` becomes "SOME web_app device reaches SOME database device", so a
    behavioral objective matches whatever the student actually named things (exactly like `path`).

    `get_topology()` returns the live topology (anything with `.devices` whose values have
    `.name`/`.type_key`). Delegates availability to the wrapped runner."""

    def __init__(self, base: Runner, get_topology) -> None:
        self.base = base
        self._get_topology = get_topology

    def available(self) -> bool:
        return bool(self.base) and self.base.available()

    def _names(self, spec: str) -> list[str]:
        """Names of devices matching `type` or a slot-scoped `type@slot` (cross-slot reach). Slot
        scope is hierarchical: `host@pods0` includes hosts nested in `pods0_lans1` (see slot_match)."""
        from .objectives import slot_match
        type_key, _, slot = str(spec).partition("@")
        t = self._get_topology()
        return [d.name for d in getattr(t, "devices", {}).values()
                if getattr(d, "type_key", None) == type_key
                and slot_match(getattr(d, "slot", ""), slot)]

    def _pairs(self, src: str, dst: str):
        """Every (src, dst) name pair — EXCLUDING a device paired with itself. When both tokens are
        the same type (`reach(host -> host)`, the LAN-repair case) the self-pair would make the
        probe vacuously true: a host can always ping its own loopback. Proving a host talks to
        *itself* proves nothing about the network."""
        for s in self._names(src):
            for d in self._names(dst):
                if s != d:
                    yield s, d

    def reach(self, src: str, dst: str, port: int | None = None) -> bool:
        return any(self.base.reach(s, d, port) for s, d in self._pairs(src, dst))

    def http(self, src: str, dst: str, port: int) -> bool:
        return any(self.base.http(s, d, port) for s, d in self._pairs(src, dst))

    # -- universal ("all") resolution --------------------------------------- #
    # `reach(host -> host, all)` = EVERY host reaches every other. Needed for repair missions: with
    # the existential reading, one healthy pair would mask a broken third host. An empty pair set is
    # False, not vacuously True — "no hosts" must never look like "all hosts talk".
    def reach_all(self, src: str, dst: str, port: int | None = None) -> bool:
        pairs = list(self._pairs(src, dst))
        return bool(pairs) and all(self.base.reach(s, d, port) for s, d in pairs)

    def http_all(self, src: str, dst: str, port: int) -> bool:
        pairs = list(self._pairs(src, dst))
        return bool(pairs) and all(self.base.http(s, d, port) for s, d in pairs)

    def backends(self, lb: str) -> int:
        return max((self.base.backends(n) for n in self._names(lb)), default=0)

    def flow(self, ovs: str, match: str) -> bool:
        return any(self.base.flow(n, match) for n in self._names(ovs))

    def measure(self, rider_type: str, metric: str) -> float | None:
        # measure is inherently type-based (you assert on "the packet_view" of the fragment) —
        # delegate straight to the base, which finds a rider of that type and runs it.
        fn = getattr(self.base, "measure", None)
        return fn(rider_type, metric) if fn else None


# -- a fake runner for tests + offline authoring ---------------------------- #
class FakeRunner:
    """A scriptable runner backed by a facts dict — for unit tests and lesson playtests without
    Docker. Facts keys: ('reach', src, dst, port) -> bool, ('http', src, dst, port) -> bool,
    ('backends', lb) -> int, ('flow', ovs, match) -> bool."""

    def __init__(self, facts: dict | None = None, available: bool = True,
                 default: bool = False) -> None:
        self.facts = facts or {}
        self._available = available
        self._default = default          # verdict for unscripted reach/http facts

    def available(self) -> bool:
        return self._available

    def reach(self, src: str, dst: str, port: int | None = None) -> bool:
        return bool(self.facts.get(("reach", src, dst, port), self._default))

    def http(self, src: str, dst: str, port: int) -> bool:
        return bool(self.facts.get(("http", src, dst, port), self._default))

    def backends(self, lb: str) -> int:
        return int(self.facts.get(("backends", lb), 0))

    def measure(self, rider_type: str, metric: str) -> float | None:
        return self.facts.get(("measure", rider_type, metric))

    def flow(self, ovs: str, match: str) -> bool:
        return bool(self.facts.get(("flow", ovs, match), False))
