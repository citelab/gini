"""Behavioral probes — the language of GINI-as-oracle win conditions.

A behavioral objective asserts a fact about the RUNNING system, which only GINI's runtime can
witness. This module parses the probe strings and evaluates them against a `Runner` — the thin
seam over the running topology (Docker exec of ping/curl, flow-table reads). GINI, never the
model, produces the verdict.

Probe grammar (Phase 2):
    reach(A -> B) == ok|fail          # L3 reachability (ICMP); with :port → TCP/L4
    reach(A -> B:port) == ok|fail
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

_REACH_RE = re.compile(
    r"^\s*(reach|ping|http)\(\s*(\w+)\s*->\s*(\w+)(?::(\d+))?\s*\)\s*==\s*(ok|fail)\s*$")
_BAL_RE = re.compile(r"^\s*balances\(\s*(\w+)\s*,\s*>=\s*(\d+)\s*\)\s*$")
_FLOW_RE = re.compile(r"^\s*flow_installed\(\s*(\w+)\s*,\s*(.+?)\s*\)\s*$")


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


class Runner(Protocol):
    """The running-topology seam. `available()` is False when there's no live runtime, so
    behavioral objectives stay `pending` rather than being wrongly failed."""
    def available(self) -> bool: ...
    def reach(self, src: str, dst: str, port: int | None = None) -> bool: ...
    def http(self, src: str, dst: str, port: int) -> bool: ...
    def backends(self, lb: str) -> int: ...
    def flow(self, ovs: str, match: str) -> bool: ...


def parse(probe: str) -> Probe:
    m = _REACH_RE.match(probe or "")
    if m:
        kind, src, dst, port, expect = m.groups()
        return Probe(kind=kind, src=src, dst=dst,
                     port=int(port) if port else None, expect_ok=(expect == "ok"))
    m = _BAL_RE.match(probe or "")
    if m:
        return Probe(kind=BALANCES, src=m.group(1), n=int(m.group(2)))
    m = _FLOW_RE.match(probe or "")
    if m:
        return Probe(kind=FLOW, src=m.group(1), match=m.group(2))
    raise ProbeError(f"cannot parse probe: {probe!r}")


def probe_ok(probe: str) -> bool:
    try:
        parse(probe)
        return True
    except ProbeError:
        return False


def evaluate(probe: str, runner: Runner) -> bool:
    """Evaluate a probe string against a live runner → the boolean fact."""
    p = parse(probe)
    if p.kind in (REACH, PING):
        got = runner.reach(p.src, p.dst, p.port)
        return got == p.expect_ok
    if p.kind == HTTP:
        got = runner.http(p.src, p.dst, p.port or 80)
        return got == p.expect_ok
    if p.kind == BALANCES:
        return runner.backends(p.src) >= p.n
    if p.kind == FLOW:
        return runner.flow(p.src, p.match)
    raise ProbeError(f"unknown probe kind {p.kind!r}")


# -- a fake runner for tests + offline authoring ---------------------------- #
class FakeRunner:
    """A scriptable runner backed by a facts dict — for unit tests and lesson playtests without
    Docker. Facts keys: ('reach', src, dst, port) -> bool, ('http', src, dst, port) -> bool,
    ('backends', lb) -> int, ('flow', ovs, match) -> bool."""

    def __init__(self, facts: dict | None = None, available: bool = True) -> None:
        self.facts = facts or {}
        self._available = available

    def available(self) -> bool:
        return self._available

    def reach(self, src: str, dst: str, port: int | None = None) -> bool:
        return bool(self.facts.get(("reach", src, dst, port), False))

    def http(self, src: str, dst: str, port: int) -> bool:
        return bool(self.facts.get(("http", src, dst, port), False))

    def backends(self, lb: str) -> int:
        return int(self.facts.get(("backends", lb), 0))

    def flow(self, ovs: str, match: str) -> bool:
        return bool(self.facts.get(("flow", ovs, match), False))
