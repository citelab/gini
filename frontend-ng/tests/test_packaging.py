"""The two distributions build, install cleanly, and stay apart.

The Teaching Center runs on a headless server VM. The trap this guards is that `gini-toolkit`
hard-depends on PySide6-Essentials + PySide6-Addons — Qt and a Chromium build — so a Teaching
Center that depended on the toolkit would drag several hundred MB onto a machine that never opens
a window, and would fail outright where PySide6 has no wheel.

`gini` is an implicit NAMESPACE package split across gini-core and gini-toolkit. Neither may ship
`gini/__init__.py`, or one distribution silently shadows the other's half of the tree.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
CORE = _ROOT / "core" / "pyproject.toml"
TOOLKIT = _ROOT / "frontend-ng" / "pyproject.toml"
TC = _ROOT / "teaching-center" / "pyproject.toml"

pytestmark = pytest.mark.skipif(not CORE.exists(), reason="core/ not checked out")


# Read as TEXT rather than parsed TOML: `tomllib` is 3.11+, and this project supports 3.10 — a
# guard that silently skips on the interpreter half the machines are running is not a guard.
def _deps(p: Path) -> str:
    """The `dependencies = [...]` block of a pyproject, lowercased."""
    txt = p.read_text()
    # Both spellings: a one-line list and a multi-line one.
    m = re.search(r"^dependencies\s*=\s*\[(.*?)\]\s*$", txt, re.S | re.M)
    assert m, f"no dependencies block in {p}"
    return m.group(1).lower()


def test_the_teaching_center_never_depends_on_the_gui_toolkit():
    """THE property. One line of drift here puts Qt on the server."""
    deps = _deps(TC)
    assert "gini-core" in deps
    assert "gini-toolkit" not in deps
    assert "pyside" not in deps


def test_gini_core_stays_tiny_and_pure_python():
    """It has to install on whatever Python the VM happens to have. PyYAML is allowed — two domain
    modules genuinely need it — but nothing that needs a compiler or a display."""
    deps = _deps(CORE)
    assert "pyside" not in deps and "gini-toolkit" not in deps
    for banned in ("numpy", "matplotlib", "pyqt"):
        assert banned not in deps, f"{banned} does not belong in the shared core"


def test_the_toolkit_depends_on_core_rather_than_carrying_its_own_copy():
    """Two copies of the proof format is the failure that cannot be seen: receipts would stop
    matching between a student's gBuilder and the server, silently."""
    assert "gini-core" in _deps(TOOLKIT)


def test_neither_distribution_ships_a_gini_init():
    """A namespace package cannot have one. If either grows an `__init__.py`, the other's
    subpackages become unimportable — and only after both are installed, which is exactly the
    situation nobody tests locally."""
    assert not (_ROOT / "core" / "src" / "gini" / "__init__.py").exists()
    assert not (_ROOT / "frontend-ng" / "src" / "gini" / "__init__.py").exists()


def test_the_two_halves_of_gini_do_not_overlap():
    """The same module shipped by both distributions means last-installed wins."""
    core = {p.name for p in (_ROOT / "core" / "src" / "gini").iterdir()}
    toolkit = {p.name for p in (_ROOT / "frontend-ng" / "src" / "gini").iterdir()
               if p.name != "__pycache__"}
    overlap = core & toolkit
    assert overlap <= {"_version.py"}, f"shipped by both: {overlap}"


def test_the_toolkit_excludes_the_domain_it_no_longer_owns():
    assert 'exclude = ["gini.domain*"]' in TOOLKIT.read_text()


def test_the_console_pages_are_declared_as_package_data():
    """They are served straight off disk. Left out of the wheel, the server installs happily and
    then 500s on its own front page."""
    txt = TC.read_text()
    assert '"gini_teaching_center" = ["static/*.html"]' in txt


def test_there_is_a_console_script_to_hand_a_service_manager():
    txt = TC.read_text()
    assert "gini-teaching-center = " in txt and "gini-tc = " in txt


def test_the_pm2_config_keeps_one_process_over_one_sqlite_file():
    """pm2's cluster mode would start several writers over a single database."""
    js = (_ROOT / "teaching-center" / "deploy" / "ecosystem.config.js").read_text()
    assert "instances: 1" in js
    assert '"fork"' in js and "cluster" not in js.split("// never")[0]


def test_the_server_binds_localhost_by_default():
    """Staff sign in with a password over plain HTTP. Binding 0.0.0.0 by default would put that
    on the campus network in clear text."""
    cli = (_ROOT / "teaching-center" / "src" / "gini_teaching_center" / "cli.py").read_text()
    assert '"HOST", "127.0.0.1"' in cli
