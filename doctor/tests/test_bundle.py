"""The single-file Stage 1, and Stage 0 fetching it on a machine where nothing is installed."""
from __future__ import annotations

import functools
import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

import pytest

from conftest import STAGE0, find_posix_shell, find_powershell
from gini_doctor.stage1.report import Report

DOCTOR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DOCTOR / "tools"))
import bundle  # noqa: E402

BUNDLE = STAGE0 / "stage1-bundle.py"


def _clean_env(**extra):
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", "GINI_HEALTHCENTER", "GINI_DOCTOR_STAGE0", "GINI_DOCTOR_MIN_PYTHON")}
    env.update(extra)
    return env


def test_the_committed_bundle_is_built_from_the_current_sources():
    assert BUNDLE.read_text(encoding="utf-8") == bundle.build(), \
        "stage1-bundle.py is stale: run python doctor/tools/bundle.py"


def test_the_bundle_runs_alone_outside_the_repository(tmp_path):
    copy = tmp_path / "anywhere.py"
    shutil.copy(str(BUNDLE), str(copy))
    proc = subprocess.run([sys.executable, str(copy), "run", "--only", "system", "--stdout", "--offline"],
                          cwd=str(tmp_path), env=_clean_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    report = Report.from_dict(json.loads(proc.stdout))
    assert report.value("system.os.family")
    # the remedy table travels inside the bundle too
    (tmp_path / "r.json").write_text(json.dumps({"schema": "gini-doctor/1", "platform": "linux", "facts": {
        "perf.cpu.throttle.count": {"status": "ok", "value": 3}}}))
    proc = subprocess.run([sys.executable, str(copy), "diagnose", str(tmp_path / "r.json")], cwd=str(tmp_path),
                          env=_clean_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, timeout=60)
    assert proc.returncode == 0 and "thermally throttled" in proc.stdout, proc.stderr


@pytest.fixture
def served(tmp_path_factory):
    """A static web server over a directory, standing in for raw.githubusercontent.com."""
    root = tmp_path_factory.mktemp("web")
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *a: None
    server = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield root, "http://127.0.0.1:%d" % server.server_address[1]
    server.shutdown()
    server.server_close()


def _shells():
    out = []
    if find_posix_shell() and sys.platform != "win32":
        out.append("sh")
    if find_powershell():
        out.append("powershell")
    return out


def _cmd(kind, tmp_path):
    lone = tmp_path / "lone"
    lone.mkdir(exist_ok=True)
    if kind == "sh":
        shutil.copy(str(STAGE0 / "stage0.sh"), str(lone / "stage0.sh"))
        return [find_posix_shell(), str(lone / "stage0.sh")]
    shutil.copy(str(STAGE0 / "stage0.ps1"), str(lone / "stage0.ps1"))
    return [find_powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(lone / "stage0.ps1")]


@pytest.mark.skipif(not _shells(), reason="no POSIX shell or PowerShell")
@pytest.mark.parametrize("kind", _shells())
def test_stage0_fetches_and_runs_stage1_when_nothing_is_local(kind, tmp_path, served):
    root, url = served
    shutil.copy(str(BUNDLE), str(root / "stage1-bundle.py"))
    proc = subprocess.run(_cmd(kind, tmp_path) + ["run", "--only", "system", "--stdout", "--offline"],
                          cwd=str(tmp_path), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, timeout=180,
                          env=_clean_env(GINI_DOCTOR_PYTHON=sys.executable,
                                         GINI_DOCTOR_BUNDLE_URL=url + "/stage1-bundle.py"))
    assert proc.returncode == 0, proc.stderr + proc.stdout
    report = Report.from_dict(json.loads(proc.stdout))
    assert report.doctor["engine"] == "python"
    assert report.value("stage0.stage1") == "fetched"
    assert report.value("stage0.stage1.source").endswith("/stage1-bundle.py")


@pytest.mark.skipif(not _shells(), reason="no POSIX shell or PowerShell")
@pytest.mark.parametrize("kind", _shells())
def test_something_that_is_not_the_bundle_is_never_run(kind, tmp_path, served):
    root, url = served
    (root / "stage1-bundle.py").write_text("print('captive portal login page')\n")
    proc = subprocess.run(_cmd(kind, tmp_path) + ["--stdout"], cwd=str(tmp_path), stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=180,
                          env=_clean_env(GINI_DOCTOR_PYTHON=sys.executable,
                                         GINI_DOCTOR_BUNDLE_URL=url + "/stage1-bundle.py"))
    assert proc.returncode == 3, proc.stderr
    facts = json.loads(proc.stdout)["facts"]
    assert "could not be fetched" in facts["stage0.verdict"]["value"]
    assert "could not fetch" in facts["stage0.stage1.fetch"]["value"]
    assert "captive portal" not in proc.stdout


def test_the_bundle_is_pure_ascii_so_no_machine_has_to_guess_its_encoding():
    """macOS + Python 3.8 refused a UTF-8 bundle with

        SyntaxError: Non-UTF-8 code starting with '\\xe2' ... but no encoding declared

    about a file that decodes as UTF-8 perfectly well. `BUFSIZ` is 1024 there against 8192 on
    glibc, and the pre-3.9 tokenizer decoded a twenty-thousand-byte line in `BUFSIZ` pieces, so an
    em-dash straddling the boundary came apart. Linux, Windows and 3.12 were all fine, which is how
    this passed CI on one commit and failed on the next, when a new module moved the em-dashes.

    `ascii()` removes the class rather than the character: the file now reads the same under any
    ASCII superset, whatever curl, wget or Invoke-WebRequest wrote it down as.
    """
    raw = BUNDLE.read_bytes()
    assert [b for b in raw if b > 127] == [], "the bundle has non-ASCII bytes again"
    # The escapes must still reconstruct every module byte for byte, or the doctor inside the
    # bundle is not the doctor that was tested.
    ns = {"__name__": "_bundle_under_test"}
    exec(compile(raw.decode("ascii"), str(BUNDLE), "exec"), ns)      # noqa: S102
    assert ns["FILES"] == dict(bundle.sources())
