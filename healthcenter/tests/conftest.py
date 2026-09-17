"""Run the Health Center's tests against the source tree, not an installed copy.

Two roots go on the path: this package's, and the doctor's — the Health Center reads and writes the
doctor's formats by importing them, so a test of the case model is also a test that the two agree.
No install step and no dependencies beyond pytest, the same bargain `doctor/tests` makes.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "healthcenter" / "src"
DOCTOR_SRC = REPO / "doctor" / "src"
STAGE0 = DOCTOR_SRC / "gini_doctor" / "stage0"

for root in (SRC, DOCTOR_SRC):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


@pytest.fixture(autouse=True)
def _isolated_policy_cache(tmp_path_factory, monkeypatch):
    """A test that fetches a policy must not leave it in the cache of the machine running the
    suite, where the next real run would prefer it over the built-in one."""
    monkeypatch.setenv("GINI_DOCTOR_CACHE_DIR", str(tmp_path_factory.mktemp("policy-cache")))


@pytest.fixture
def stage0_dir() -> Path:
    return STAGE0


@pytest.fixture
def repo() -> Path:
    return REPO
