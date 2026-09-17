"""One policy document, two renderings, two parsers — and no way for them to drift apart quietly.

Stage 0 is `sh` and PowerShell and reads `key=value`; Stage 1 is Python and reads JSON. A floor
raised for a course has to arrive at both, and the failure if it does not is silent on the side
that matters: the shell is the stage that actually chooses an interpreter.

So the cheap assertions here read the real regex out of `stage0.ps1` instead of restating it, and
the last test runs the real `stage0.sh` against a server serving both endpoints.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from gini_doctor.stage1 import policy as doctor_policy

from gini_healthcenter import policy


@pytest.fixture
def served():
    """A stand-in Health Center serving one document at both endpoints."""
    servers = []

    def make(doc):
        text, body = policy.as_text(doc).encode(), policy.as_json(doc).encode()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                payload = {"/doctor/policy.txt": text, "/doctor/policy.json": body}.get(self.path)
                self.send_response(200 if payload else 404)
                if payload:
                    self.send_header("Content-Type", "application/json" if self.path.endswith(
                        ".json") else "text/plain")
                self.end_headers()
                if payload:
                    self.wfile.write(payload)

            def log_message(self, *a):
                pass

        s = HTTPServer(("127.0.0.1", 0), Handler)
        servers.append(s)
        threading.Thread(target=s.serve_forever, daemon=True).start()
        return "http://127.0.0.1:%d" % s.server_address[1]

    yield make
    for s in servers:
        s.shutdown()
        s.server_close()


def test_an_unconfigured_health_center_changes_no_value_the_doctor_already_used():
    """Pointing a doctor at a fresh server must alter nothing but the recorded source. Otherwise
    "the Health Center only adds to a local run" is not true, and every report gathered against
    one becomes incomparable with every report gathered without one."""
    doc = policy.document()
    assert doc["version"] == policy.UNCONFIGURED
    assert {k: v for k, v in doc.items() if k != "version"} == \
           {k: v for k, v in doctor_policy.BUILTIN.items() if k != "version"}


def test_every_field_the_server_serves_is_one_the_doctor_keeps():
    """`validate` merges over its built-in and drops the rest, silently. A knob added here and not
    there would be served, accepted and ignored on thirty machines."""
    doc = policy.document("hc-3", min_python="3.10", probe_timeout_s=45,
                          groups_default=["system", "engine", "registry"])
    assert doctor_policy.validate(json.loads(policy.as_json(doc))) == doc

    with pytest.raises(ValueError) as e:
        policy.document("hc-4", probe_timeout_s=9999)      # outside the doctor's accepted range
    assert "would not honour probe_timeout_s" in str(e.value)


def test_a_patch_level_floor_is_refused_because_the_two_stages_would_read_it_differently():
    """Stage 0 captures major.minor; Stage 1 records all three. A floor of 3.8.1 would be enforced
    as 3.8 by the stage that picks the interpreter and reported as 3.8.1 by the stage that does
    not — the same machine described two ways."""
    with pytest.raises(ValueError) as e:
        policy.document("hc-5", min_python="3.10.4")
    assert "patch level" in str(e.value) and "against 3.10" in str(e.value)
    assert policy.document("hc-5", min_python="3.10")["min_python"] == "3.10"


def test_the_text_form_matches_the_regex_that_is_actually_in_stage0(stage0_dir):
    """Read out of the script, not restated here: a change to the pattern there fails this."""
    ps1 = (stage0_dir / "stage0.ps1").read_text(encoding="utf-8")
    m = re.search(r"\[regex\]::Match\(\$txt, '([^']+)'\)", ps1)
    assert m, "stage0.ps1 no longer matches its policy line with [regex]::Match"
    pattern = m.group(1)
    assert policy.STAGE0_KEY in pattern

    text = policy.as_text(policy.document("hc-6", min_python="3.9"))
    assert re.search(pattern, text).group(1) == "3.9"
    # Exactly one line may carry it: both stages take the FIRST match, so a second would win or
    # lose by accident.
    assert len(re.findall(pattern, text)) == 1
    floor = [ln for ln in text.splitlines() if ln.startswith(policy.STAGE0_KEY)]
    assert floor == ["min_python=3.9"]


def test_both_shells_look_for_the_key_the_server_renders(stage0_dir):
    for name in ("stage0.sh", "stage0.ps1"):
        assert policy.STAGE0_KEY in (stage0_dir / name).read_text(encoding="utf-8"), name
    assert policy.STAGE0_KEY in policy.TEXT_KEYS


@pytest.mark.skipif(sys.platform == "win32", reason="the POSIX bootstrap; CI runs the ps1 one")
@pytest.mark.skipif(not shutil.which("sh"), reason="no POSIX shell")
@pytest.mark.skipif(not (shutil.which("curl") or shutil.which("wget")),
                    reason="Stage 0 needs curl or wget to reach a Health Center")
def test_the_real_bootstrap_takes_its_floor_from_a_real_server(stage0_dir, served, tmp_path):
    """The whole contract in one run: `sh stage0.sh` fetches policy.txt and applies the floor,
    hands off to Stage 1, and Stage 1 fetches policy.json from the same server and records the
    version. One document, both endpoints, both parsers, no stubs."""
    url = served(policy.document("hc-e2e", min_python="3.9", probe_timeout_s=30))
    env = {k: v for k, v in __import__("os").environ.items()
           if k not in ("GINI_DOCTOR_MIN_PYTHON", "GINI_DOCTOR_STAGE0", "PYTHONPATH")}
    env.update(GINI_HEALTHCENTER=url, GINI_DOCTOR_PYTHON=sys.executable)
    proc = subprocess.run([shutil.which("sh"), str(stage0_dir / "stage0.sh"),
                           "run", "--only", "system", "--stdout"],
                          cwd=str(tmp_path), env=env, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180,
                          universal_newlines=True)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    report = json.loads(proc.stdout)
    facts = report["facts"]
    assert facts["stage0.min_python"]["value"] == "3.9"              # from policy.txt
    assert facts["stage0.min_python.source"]["value"] == "live"
    assert report["policy"] == {"source": "live", "version": "hc-e2e"}   # from policy.json
