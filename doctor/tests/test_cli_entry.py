"""`gini-doctor` runs the Python doctor; the legacy engine stays reachable, and its options still work."""
from __future__ import annotations

import json

import gini_doctor
from gini_doctor import cli


def test_the_command_runs_stage1(capsys, monkeypatch):
    monkeypatch.delenv("GINI_HEALTHCENTER", raising=False)
    assert cli.main(["run", "--only", "system", "--stdout", "--offline"]) == 0
    assert json.loads(capsys.readouterr().out)["doctor"]["engine"] == "python"


def test_legacy_options_and_the_legacy_subcommand_reach_the_shell_engine(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli, "legacy", lambda args: calls.append(list(args)) or 0)
    assert cli.main(["legacy", "--list"]) == 0
    assert cli.main(["--fanout", "hosts.txt", "reports/"]) == 0
    assert cli.main(["--compare", "a.txt", "b.txt"]) == 0
    assert calls == [["--list"], ["--fanout", "hosts.txt", "reports/"], ["--compare", "a.txt", "b.txt"]]
    assert "legacy engine" in capsys.readouterr().err


def test_list_maps_to_the_stage1_groups(capsys):
    assert cli.main(["--list"]) == 0
    out = capsys.readouterr().out
    assert "registry" in out and "xv6" in out


def test_the_package_points_at_every_file_it_ships():
    for path in (gini_doctor.SCRIPT, gini_doctor.STAGE0_SH, gini_doctor.STAGE0_PS1, gini_doctor.BUNDLE):
        assert path.exists(), path
