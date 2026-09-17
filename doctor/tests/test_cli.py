import json

from gini_doctor.stage1 import cli, policy
from gini_doctor.stage1.redact import Redactor
from gini_doctor.stage1.report import Report


def test_run_to_stdout_is_a_loadable_report(capsys, monkeypatch):
    monkeypatch.delenv(policy.HEALTHCENTER_ENV, raising=False)
    monkeypatch.delenv(cli.STAGE0_ENV, raising=False)
    assert cli.main(["run", "--only", "system", "--stdout", "--offline"]) == 0
    data = json.loads(capsys.readouterr().out)
    report = Report.from_dict(data)
    assert report.groups == ["system"] and report.policy["source"] == "builtin"


def test_default_command_is_run_and_it_saves_a_file(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(policy.HEALTHCENTER_ENV, raising=False)
    assert cli.main(["--offline", "--quiet", "--only", "system"]) == 0
    saved = list(tmp_path.glob("gini-doctor-*.json"))
    assert len(saved) == 1 and Report.load(str(saved[0])).facts


def test_stage0_findings_become_facts_and_are_redacted():
    r = Report("linux")
    red = Redactor(homes=["/home/zed"], users=["zed"])
    cli.ingest_stage0(r, "python.1.path\t/home/zed/.local/bin/python3\nbad key\tx\nnoTab\n", red)
    assert r.value("stage0.python.1.path") == "~/.local/bin/python3"
    assert list(r.facts) == ["stage0.python.1.path"] and r.groups == ["stage0"]


def test_system_always_runs_and_unknown_groups_are_recorded():
    r = cli.collect(["nope"], policy.Policy(dict(policy.BUILTIN), "builtin"))
    assert r.groups[0] == "system"
    assert r.facts["doctor.unknown_group.nope"]["status"] == "error"


def test_compare_and_show_commands(tmp_path, capsys):
    a = Report("linux", host="good"); a.set("x.y", "ok", 1); a.save(str(tmp_path / "a.json"))
    b = Report("linux", host="bad"); b.set("x.y", "ok", 2); b.save(str(tmp_path / "b.json"))
    assert cli.main(["compare", str(tmp_path / "a.json"), str(tmp_path / "b.json")]) == 0
    out = capsys.readouterr().out
    assert "x.y" in out and "good" in out
    assert cli.main(["compare", str(tmp_path / "a.json")]) == 2
    assert cli.main(["compare", str(tmp_path / "a.json"), str(tmp_path / "missing.json")]) == 2
    assert cli.main(["show", str(tmp_path / "b.json")]) == 0
