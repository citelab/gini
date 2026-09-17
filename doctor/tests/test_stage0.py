"""Stage 0, run for real: the POSIX script under dash (or sh), the PowerShell script under
powershell/pwsh. Each test is skipped where its shell does not exist, so the same file runs on all
three platforms and CI exercises both scripts (Linux/macOS: sh; Windows: Windows PowerShell 5.1).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from conftest import STAGE0, find_posix_shell, find_powershell
from gini_doctor.stage1.report import Report

SH = find_posix_shell()
PS = find_powershell()

SHELLS = []
if SH and sys.platform != "win32":
    SHELLS.append(pytest.param([SH, str(STAGE0 / "stage0.sh")], id="sh"))
if PS:
    SHELLS.append(pytest.param([PS, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                                str(STAGE0 / "stage0.ps1")], id="powershell"))


def _env(**extra):
    env = dict(os.environ)
    for k in ("GINI_HEALTHCENTER", "GINI_DOCTOR_MIN_PYTHON", "GINI_DOCTOR_PYTHON",
              "GINI_DOCTOR_STAGE0", "PYTHONPATH"):
        env.pop(k, None)
    env["GINI_DOCTOR_PYTHON"] = sys.executable
    env.update({k: v for k, v in extra.items() if v is not None})
    return env


def _run(cmd, cwd, *args, **env):
    return subprocess.run(cmd + list(args), cwd=str(cwd), env=_env(**env), stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120,
                          universal_newlines=True, encoding="utf-8", errors="replace")


pytestmark = pytest.mark.skipif(not SHELLS, reason="no POSIX shell or PowerShell on this machine")


@pytest.mark.parametrize("cmd", SHELLS)
def test_hands_off_to_stage1_with_its_findings(cmd, tmp_path):
    out = tmp_path / "r.json"
    proc = _run(cmd, tmp_path, "run", "--only", "system", "--offline", "--quiet", "--out", str(out))
    assert proc.returncode == 0, proc.stderr + proc.stdout
    report = Report.load(str(out))
    assert report.doctor["engine"] == "python"
    assert report.groups[:2] == ["stage0", "system"]
    assert report.value("stage0.stage1") == "checkout"
    assert report.value("stage0.min_python.source") == "builtin"
    assert report.value("stage0.python.1.verdict") == "chosen"


@pytest.mark.parametrize("cmd", SHELLS)
def test_no_qualifying_python_is_a_report_not_a_crash(cmd, tmp_path):
    proc = _run(cmd, tmp_path, "--stdout", GINI_DOCTOR_MIN_PYTHON="99.0")
    assert proc.returncode == 3, proc.stderr
    report = Report.from_dict(json.loads(proc.stdout))
    assert report.doctor["engine"].startswith("stage0")
    assert report.groups == ["stage0"]
    assert "no usable Python" in report.value("stage0.verdict")
    assert report.value("stage0.min_python") == "99.0"
    assert "below floor 99.0" in report.value("stage0.python.1.verdict")


@pytest.mark.parametrize("cmd", SHELLS)
def test_without_stdout_it_saves_the_report_and_says_why(cmd, tmp_path):
    proc = _run(cmd, tmp_path, GINI_DOCTOR_MIN_PYTHON="99.0")
    assert proc.returncode == 3
    saved = list(tmp_path.glob("gini-doctor-*.json"))
    assert len(saved) == 1, proc.stdout + proc.stderr
    assert "no usable Python" in proc.stdout
    Report.load(str(saved[0]))


@pytest.mark.parametrize("cmd", SHELLS)
def test_the_floor_comes_from_the_health_center_when_it_answers(cmd, tmp_path, hc):
    server = hc({"version": "hc-1"})         # policy.txt says min_python=3.11
    proc = _run(cmd, tmp_path, "run", "--only", "system", "--stdout",
                GINI_HEALTHCENTER=server.url, GINI_DOCTOR_MIN_PYTHON=None)
    # Whether some interpreter on this machine meets 3.11 decides who writes the report (Stage 1,
    # exit 0, or Stage 0, exit 3); either way the floor it applied is recorded the same way.
    assert proc.returncode in (0, 3), proc.stderr
    facts = json.loads(proc.stdout)["facts"]
    assert facts["stage0.min_python"]["value"] == "3.11"
    assert facts["stage0.min_python.source"]["value"] == "live"


@pytest.mark.parametrize("cmd", SHELLS)
def test_an_unreachable_health_center_falls_back_to_the_builtin_floor(cmd, tmp_path):
    proc = _run(cmd, tmp_path, "run", "--only", "system", "--stdout",
                GINI_HEALTHCENTER="http://127.0.0.1:9")
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["facts"]["stage0.min_python.source"]["value"] == "builtin"
    assert "unavailable" in data["facts"]["stage0.policy.note"]["value"]


@pytest.mark.skipif(sys.platform == "win32" or not SH, reason="POSIX home layout")
def test_home_paths_are_redacted_in_stage0_facts(tmp_path):
    home = tmp_path / "home" / "someone"
    (home / "bin").mkdir(parents=True)
    link = home / "bin" / "python3"
    os.symlink(sys.executable, str(link))
    cmd = [SH, str(STAGE0 / "stage0.sh")]
    proc = _run(cmd, tmp_path, "--stdout", HOME=str(home), GINI_DOCTOR_PYTHON=str(link),
                GINI_DOCTOR_MIN_PYTHON="99.0")
    data = json.loads(proc.stdout)
    assert data["facts"]["stage0.python.1.path"]["value"] == "~/bin/python3"
    assert str(home) not in proc.stdout
