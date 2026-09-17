"""Scripted command output, so probe groups run the same on every platform with no Docker,
Podman, Qt or systemd on the machine running the suite."""
from __future__ import annotations

from gini_doctor.stage1 import runner


def ok(stdout="", stderr=""):
    return runner.Result(runner.OK, 0, stdout, stderr)


def failed(stderr="", stdout="", code=1):
    return runner.Result(runner.FAILED, code, stdout, stderr)


class FakeMachine:
    """table: command prefix (tuple) -> Result, longest prefix wins. installed: programs on PATH."""

    def __init__(self, monkeypatch, table=None, installed=()):
        self.table = dict(table or {})
        self.installed = set(installed)
        self.calls = []
        monkeypatch.setattr(runner, "run", self.run)
        monkeypatch.setattr(runner, "which", self.which)

    def which(self, program):
        return "/usr/bin/%s" % program if program in self.installed else None

    def run(self, argv, timeout=20.0, **kw):
        self.calls.append(list(argv))
        best = None
        for prefix, result in self.table.items():
            if tuple(argv[:len(prefix)]) == prefix and (best is None or len(prefix) > len(best[0])):
                best = (prefix, result)
        if best:
            return best[1]
        if argv and argv[0] not in self.installed and "/" not in argv[0]:
            return runner.Result(runner.ABSENT, None, "", "%s: not found on PATH" % argv[0])
        return failed("unscripted: %s" % " ".join(argv))
