"""DOs / DON'Ts for a described mission — the student's explicit *negative* intent.

The composer must honour "…but no metrics or dashboards": otherwise the auto-filled enrichment layers
put things on the board the student explicitly refused. We extract an `Excludes` set (roles / element
types / layers to leave OUT) two ways and merge them:

  • a deterministic **negation scan** of the intent text (reliable, model-free) — catches "no X",
    "without X", "don't add X", "skip X", across an "and" list;
  • the reasoning model's own `exclude:` list (belt-and-suspenders, mapped through the same table).

Only *optional* things are excludable (the enrichment layers + their elements) — core network pieces
(firewall, router, switch, host, internet, LAN) are deliberately NOT here, so "hide it from the
Internet" can never be misread as "remove the Internet". Assembly then suppresses excluded companions,
and `compose` reports an **infeasibility** when a thing the student asked FOR inherently needs a thing
they asked to leave out. Pure data; prune/extend the table as we learn.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .capabilities import ancestors
from .objectives import element_types_in_check

# keyword (substring, lower) -> (roles, element type_keys, assembly layers) it refers to
_EXCLUDE_TABLE: dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = {
    "metric":        (("metrics-source", "metrics-collector"), ("metrics",), ("observe",)),
    "dashboard":     (("dashboard-view", "visualizer"), ("dashboard",), ("observe",)),
    "grafana":       (("dashboard-view", "visualizer"), ("dashboard",), ("observe",)),
    "monitor":       (("visualizer", "metrics-source"), ("metrics", "dashboard"), ("observe",)),
    "observab":      (("visualizer", "metrics-source"), ("metrics", "dashboard"), ("observe",)),
    "visuali":       (("visualizer",), (), ("observe",)),
    "telemetry":     (("metrics-source", "metrics-collector"), ("metrics",), ("observe",)),
    "load generat":  (("load-generator",), (), ("exercise",)),
    "load-generat":  (("load-generator",), (), ("exercise",)),
    "load gen":      (("load-generator",), (), ("exercise",)),
    "load test":     (("load-generator",), (), ("exercise",)),
    "load-test":     (("load-generator",), (), ("exercise",)),
    "traffic gen":   (("traffic-source",), (), ("exercise",)),
    # "nothing else / no extras / just the network" — leave out ALL enrichment
    "nothing else":  ((), (), ("exercise", "observe")),
    "no extras":     ((), (), ("exercise", "observe")),
    "no extra":      ((), (), ("exercise", "observe")),
}

# negation cues; the excluded span runs from the cue to the next clause boundary (so "no metrics and
# dashboards" captures both, but "no metrics but add a router" stops the exclusion at "but").
_NEG = re.compile(r"\b(no|without|not|don'?t|do not|avoid|skip|exclude|omit|leave out|minus|"
                  r"except|sans)\b", re.I)
_STOP = re.compile(r"[.;,]|\b(but|however|while|yet|although|though|and then)\b", re.I)


@dataclass
class Excludes:
    roles: set[str] = field(default_factory=set)
    types: set[str] = field(default_factory=set)
    layers: set[str] = field(default_factory=set)

    def __bool__(self) -> bool:
        return bool(self.roles or self.types or self.layers)

    def label(self) -> str:
        """A short human phrase for what was excluded (for the infeasibility / suppression note)."""
        bits = sorted(self.types) or sorted(self.layers) or sorted(self.roles)
        return ", ".join(bits)


def _add_keyword(ex: Excludes, kw: str) -> None:
    roles, types, layers = _EXCLUDE_TABLE[kw]
    ex.roles.update(roles)
    ex.types.update(types)
    ex.layers.update(layers)


def _negated_text(text: str) -> str:
    """The concatenation of every span under a negation cue."""
    spans = []
    for m in _NEG.finditer(text or ""):
        rest = text[m.end():]
        stop = _STOP.search(rest)
        spans.append(rest[:stop.start()] if stop else rest)
    return " ".join(spans).lower()


def positive_text(text: str) -> str:
    """The intent with every negated span removed — so a coverage/keyword match can't be triggered by
    a thing the student said they DON'T want ("no dashboards" must not pull in a dashboard)."""
    out, idx = [], 0
    for m in _NEG.finditer(text or ""):
        out.append(text[idx:m.start()])
        rest = text[m.end():]
        stop = _STOP.search(rest)
        idx = m.end() + (stop.start() if stop else len(rest))
    out.append(text[idx:])
    return " ".join(" ".join(out).split())


def from_text(text: str) -> Excludes:
    """Extract DON'Ts from free-form intent by scanning only the NEGATED spans (model-free)."""
    ex = Excludes()
    neg = _negated_text(text)
    for kw in _EXCLUDE_TABLE:
        if kw in neg:
            _add_keyword(ex, kw)
    return ex


def from_terms(terms) -> Excludes:
    """Map an explicit exclude list (e.g. from the model) through the same table — here the terms are
    already known to be exclusions, so no negation scan is needed."""
    ex = Excludes()
    for t in terms or []:
        low = str(t).lower()
        for kw in _EXCLUDE_TABLE:
            if kw in low:
                _add_keyword(ex, kw)
    return ex


def merge(*exes: Excludes) -> Excludes:
    out = Excludes()
    for e in exes:
        out.roles |= e.roles
        out.types |= e.types
        out.layers |= e.layers
    return out


def fragment_excluded(fragment, ex: Excludes) -> bool:
    """Should this fragment be kept OUT under `ex`? True if its layer is excluded, it provides an
    excluded capability (matched up the is-a hierarchy), or its objectives place an excluded element."""
    if not ex:
        return False
    if fragment.layer in ex.layers:
        return True
    for p in fragment.provides:
        if ancestors(p) & ex.roles:
            return True
    for t in fragment.objectives:
        if any(et in ex.types for et in element_types_in_check(getattr(t, "check", "") or "")):
            return True
    return False


def objective_conflicts(objectives, ex: Excludes) -> list[str]:
    """Excluded element types that STILL appear in the assembled objectives — i.e. a requested core
    inherently needs something the student asked to leave out (an infeasibility)."""
    bad: list[str] = []
    for o in objectives:
        for et in element_types_in_check(getattr(o, "check", "") or ""):
            if et in ex.types and et not in bad:
                bad.append(et)
    return bad
