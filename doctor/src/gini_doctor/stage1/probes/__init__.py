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
                 redactor: Redactor, timeout: float = 20.0):
        self.report = report
        self.group = group
        self.platform = platform
        self.redactor = redactor
        self.timeout = timeout

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


_REGISTRY: Dict[str, List[Probe]] = {}
_DESCRIPTIONS: Dict[str, str] = {}


def probe(group: str, platforms: Iterable[str] = _platforms.ALL, describe: str = "",
          na_keys: Sequence[str] = ()):
    plats = frozenset(platforms)
    unknown = plats - _platforms.ALL
    if unknown:
        raise ValueError("unknown platform(s) %s" % sorted(unknown))

    def register(func: Callable[[Context], None]) -> Callable[[Context], None]:
        _REGISTRY.setdefault(group, []).append(Probe(group, func, plats, describe, tuple(na_keys)))
        if describe and group not in _DESCRIPTIONS:
            _DESCRIPTIONS[group] = describe
        return func

    return register


def load_builtin() -> None:
    """Import the modules that register the built-in probes."""
    from . import system  # noqa: F401


def groups() -> List[str]:
    load_builtin()
    return list(_REGISTRY)


def describe(group: str) -> str:
    load_builtin()
    return _DESCRIPTIONS.get(group, "")


def run_group(group: str, report: "_report.Report", platform: str, redactor: Redactor,
              timeout: float = 20.0) -> bool:
    load_builtin()
    probes = _REGISTRY.get(group)
    if not probes:
        return False
    for p in probes:
        ctx = Context(report, group, platform, redactor, timeout)
        if platform not in p.platforms:
            for k in p.na_keys:
                ctx.na(k)
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
