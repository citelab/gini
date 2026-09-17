"""Stage 1 must run on the oldest Python the built-in policy accepts.

Skipped unless an interpreter at the floor is available: set GINI_DOCTOR_TEST_FLOOR_PYTHON, or have
``python3.8`` on PATH. CI covers the floor directly by running the whole suite on it.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from conftest import SRC
from gini_doctor.stage1 import policy

FLOOR = policy.BUILTIN["min_python"]
PY = os.environ.get("GINI_DOCTOR_TEST_FLOOR_PYTHON") or shutil.which("python" + FLOOR)


@pytest.mark.skipif(not PY, reason="no Python %s interpreter available" % FLOOR)
def test_stage1_runs_on_the_floor_python(tmp_path):
    env = dict(os.environ, PYTHONPATH=str(SRC))
    env.pop("GINI_HEALTHCENTER", None)
    proc = subprocess.run([PY, "-m", "gini_doctor.stage1", "run", "--stdout", "--offline"],
                          cwd=str(tmp_path), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["facts"]["system.doctor.python.version"]["value"].startswith(FLOOR + ".")
    assert "system.probe_error" not in data["facts"]
