"""Probe registry.

A probe is a function over a :class:`Context`, registered for one group and a set of platforms::

    @probe("system", platforms=platforms.ALL, describe="host identity: OS, CPU, memory, disk")
    def system(ctx):
        ctx.ok("os.family", ctx.platform)

On a platform a probe does not declare, the doctor does not call it; it records the group's
declared facts as ``n/a`` instead (see ``na_keys``), so a comparison across platforms shows
"not applicable here" rather than "missing".

A probe that raises is caught: the group records ``<group>.probe_error`` with the exception and
the run continues. A doctor that dies on the first surprise is useless on exactly the machine
that needs it.

A probe that goes beyond looking — starts a container, boots a kernel — declares ``consent``: a
plain statement of what it will run and why. The doctor shows that text and runs the probe only
when the person says yes (Y at the prompt, or ``--yes``). Declined or unanswerable (no terminal),
the group records ``<group>.skipped`` and moves on. Everything else is read-only by rule.
"""
from __future__ import annotations

import traceback
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, NamedTuple, Optional, Sequence

from .. import platforms as _platforms
from .. import report as _report
from .. import runner as _runner
from ..redact import Redactor


class Context:
    def __init__(self, report: "_report.Report", group: str, platform: str,
                 redactor: Redactor, timeout: float = 20.0, shared: Optional[Dict[str, Any]] = None):
        self.report = report
        self.group = group
        self.platform = platform
        self.redactor = redactor
        self.timeout = timeout
        # One dict per run, shared by every group: answers that are expensive to get (which
        # engine is answering) are asked once, not once per group.
        self.shared = shared if shared is not None else {}

    def key(self, name: str) -> str:
        return "%s.%s" % (self.group, name)

    # -- recording -------------------------------------------------------- #
    def ok(self, name: str, value: Any) -> None:
        self.report.set(self.key(name), _report.OK, self.redactor.value(value))

    def absent(self, name: str, detail: Optional[str] = None) -> None:
        self.report.set(self.key(name), _report.ABSENT,
                        detail=self.redactor.text(detail) if detail else None)

    def error(self, name: str, detail: str) -> None:
        self.report.set(self.key(name), _report.ERROR, detail=self.redactor.text(detail))

    def na(self, name: str) -> None:
        self.report.set(self.key(name), _report.NA)

    def from_result(self, name: str, result: "_runner.Result",
                    value: Callable[["_runner.Result"], Any] = None) -> None:
        """Record a command's outcome as a fact: its value when it worked, ``absent`` when the
        program is not installed, ``error`` with the command's own words otherwise."""
        if result.status == _runner.OK:
            self.ok(name, value(result) if value else result.text())
        elif result.status == _runner.ABSENT:
            self.absent(name, result.evidence())
        else:
            self.error(name, "%s: %s" % (result.status, result.evidence()))

    # -- running ---------------------------------------------------------- #
    def run(self, argv: Sequence[str], timeout: Optional[float] = None, **kw) -> "_runner.Result":
        return _runner.run(argv, timeout=timeout if timeout is not None else self.timeout, **kw)

    def which(self, program: str) -> Optional[str]:
        return _runner.which(program)


class Probe(NamedTuple):
    group: str
    func: Callable[[Context], None]
    platforms: FrozenSet[str]
    describe: str
    na_keys: Sequence[str]
    consent: str


# approve(group, consent_text) -> bool. The default refuses: nothing that goes beyond looking
# runs unless someone said so.
Approver = Callable[[str, str], bool]


def refuse(group: str, text: str) -> bool:
    return False


_REGISTRY: Dict[str, List[Probe]] = {}
_DESCRIPTIONS: Dict[str, str] = {}


def probe(group: str, platforms: Iterable[str] = _platforms.ALL, describe: str = "",
          na_keys: Sequence[str] = (), consent: str = ""):
    plats = frozenset(platforms)
    unknown = plats - _platforms.ALL
    if unknown:
        raise ValueError("unknown platform(s) %s" % sorted(unknown))

    def register(func: Callable[[Context], None]) -> Callable[[Context], None]:
        _REGISTRY.setdefault(group, []).append(
            Probe(group, func, plats, describe, tuple(na_keys), consent.strip()))
        if describe and group not in _DESCRIPTIONS:
            _DESCRIPTIONS[group] = describe
        return func

    return register


def load_builtin() -> None:
    """Import the modules that register the built-in probes."""
    from . import compose, engine, gini, live, perf, qt, registry, rootless, system, xv6  # noqa: F401


def groups() -> List[str]:
    load_builtin()
    return list(_REGISTRY)


def describe(group: str) -> str:
    load_builtin()
    return _DESCRIPTIONS.get(group, "")


def consent_text(group: str) -> str:
    load_builtin()
    return "\n".join(p.consent for p in _REGISTRY.get(group, []) if p.consent)


def run_group(group: str, report: "_report.Report", platform: str, redactor: Redactor,
              timeout: float = 20.0, approve: Approver = refuse,
              shared: Optional[Dict[str, Any]] = None) -> bool:
    load_builtin()
    probes = _REGISTRY.get(group)
    if not probes:
        return False
    shared = shared if shared is not None else {}
    for p in probes:
        ctx = Context(report, group, platform, redactor, timeout, shared)
        if platform not in p.platforms:
            for k in p.na_keys:
                ctx.na(k)
            continue
        if p.consent and not approve(group, p.consent):
            ctx.ok("skipped", "not run: it needs your consent (answer Y, or run with --yes)")
            continue
        try:
            p.func(ctx)
        except Exception as e:  # a probe must never take the run down
            tb = traceback.extract_tb(e.__traceback__)
            where = "%s:%d" % (tb[-1].filename.rsplit("/", 1)[-1], tb[-1].lineno) if tb else "?"
            ctx.error("probe_error", "%s in %s: %s" % (type(e).__name__, where, e))
    if group not in report.groups:
        report.groups.append(group)
    return True
