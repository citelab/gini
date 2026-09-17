"""Run the doctor's tests against the source tree, not an installed copy.

The doctor has no dependencies, so its tests need no install step either: ``python -m pytest
doctor/tests`` from a bare checkout works on any of the three platforms.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

STAGE0 = SRC / "gini_doctor" / "stage0"


@pytest.fixture(autouse=True)
def _isolated_policy_cache(tmp_path_factory, monkeypatch):
    """No test reads or writes the real policy cache of the machine running the suite."""
    monkeypatch.setenv("GINI_DOCTOR_CACHE_DIR", str(tmp_path_factory.mktemp("policy-cache")))


@pytest.fixture
def src_path() -> Path:
    return SRC


@pytest.fixture
def stage0_dir() -> Path:
    return STAGE0


def find_posix_shell():
    for name in ("dash", "sh"):
        p = shutil.which(name)
        if p:
            return p
    return None


def find_powershell():
    for name in ("powershell", "pwsh"):
        p = shutil.which(name)
        if p:
            return p
    p = os.environ.get("GINI_DOCTOR_TEST_PWSH")
    return p if p and os.path.exists(p) else None


class _HC:
    """A stand-in Health Center serving /doctor/policy.json and /doctor/policy.txt."""

    def __init__(self, body):
        body_bytes = body if isinstance(body, bytes) else json.dumps(body).encode()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                if self.path == "/doctor/policy.json":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(body_bytes)
                elif self.path == "/doctor/policy.txt":
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"min_python=3.11\n")
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, *a):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def hc():
    servers = []

    def make(body):
        s = _HC(body)
        servers.append(s)
        return s

    yield make
    for s in servers:
        s.close()
