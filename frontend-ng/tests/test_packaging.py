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


# -- the READMEs, which are also the PyPI landing pages ------------------------ #
#
# These went stale once already and it was invisible: the root README told everyone to run
# `gini-setup` (deleted) and `pip install -e .` at the root (no pyproject there), and core/README.md
# was a byte-for-byte copy of the app README — so the gini-core PyPI page advertised a Qt desktop
# app. Nothing fails when a README is wrong; a person just follows it and gets stuck.

ROOT = Path(__file__).resolve().parents[2]


def _readme(rel: str) -> str:
    f = ROOT / rel
    assert f.exists(), f"{rel} is missing — it is a package's PyPI description"
    return f.read_text(encoding="utf-8")


def test_no_readme_still_tells_people_to_run_gini_setup():
    """The command no longer exists; gBuilder does this itself at launch. A reader who types it
    gets `command not found` and concludes the install failed."""
    for rel in ("README.md", "core/README.md", "frontend-ng/README.md",
                "teaching-center/README.md", "scripts/README.md"):
        assert "gini-setup" not in _readme(rel), f"{rel} still references the deleted gini-setup"


def test_the_package_readmes_are_not_copies_of_each_other():
    """Each is a different PyPI page for a different audience: a domain library, a desktop app, a
    course server."""
    a, b, c = (_readme("core/README.md"), _readme("frontend-ng/README.md"),
               _readme("teaching-center/README.md"))
    assert a != b and b != c and a != c
    assert "gini-core" in a.split("\n")[0]


def test_the_root_readme_does_not_promise_a_root_level_editable_install():
    """There is no pyproject.toml at the root — the packages live in core/, frontend-ng/ and
    teaching-center/. `pip install -e .` here fails with a message about the missing file."""
    assert not (ROOT / "pyproject.toml").exists(), \
        "a root pyproject.toml appeared — this test's premise, and the README, need revisiting"
    assert "pip install -e .\n" not in _readme("README.md")


def test_the_source_install_route_points_at_a_script_that_exists():
    readme = _readme("README.md")
    assert "./scripts/dev.sh install" in readme
    for name in ("dev.sh", "release.sh", "images.sh"):
        if f"scripts/{name}" in readme:
            assert (ROOT / "scripts" / name).exists(), f"README names scripts/{name}, which is gone"


def test_release_and_version_bumps_are_documented_somewhere_findable():
    """Versions come from git tags via setuptools-scm and nobody types one. That is only useful if
    a contributor can find out — the root README has to point at where it is written down."""
    assert "scripts/README.md" in _readme("README.md")
    scripts = _readme("scripts/README.md")
    assert "setuptools-scm" in scripts
    for level in ("patch", "minor", "major"):
        assert f"release.sh {level}" in scripts or level in scripts


# -- every distribution must have a way to be published ------------------------ #
#
# This is the test that would have caught it: `release-pypi.sh` was the only thing that ever
# published gini-toolkit, and deleting it left the package with no publisher at all. A tag would
# have shipped a new gini-core while the app students actually install stayed behind — and nothing
# would have failed. The release would simply have been half a release.

def _distributions() -> list[str]:
    return sorted(d.name for d in ROOT.iterdir()
                  if (d / "pyproject.toml").exists() and d.name != "legacy")


def test_every_distribution_has_a_publish_workflow():
    flows = (ROOT / ".github" / "workflows")
    published = "\n".join(f.read_text(encoding="utf-8") for f in flows.glob("*.yml"))
    for d in _distributions():
        name = (ROOT / d / "pyproject.toml").read_text(encoding="utf-8")
        dist = next(l.split('"')[1] for l in name.splitlines() if l.startswith("name ="))
        assert f"outdir dist/ {d}/" in published, (
            f"{dist} (in {d}/) is built by no workflow — a tag would publish the others and "
            f"silently leave this one behind")


def test_each_project_gets_its_own_workflow_file():
    """PyPI's trusted publishing binds one workflow file per project, and a shared file would let
    one distribution's failure block the rest."""
    flows = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
    assert len(flows) >= len(_distributions())
    for f in flows:
        built = [l for l in f.read_text(encoding="utf-8").splitlines() if "outdir dist/" in l]
        assert len(built) == 1, f"{f.name} builds {len(built)} distributions; expected exactly 1"


def test_the_release_script_names_every_workflow_it_will_trigger():
    """The script prints what the tag is about to publish, and you confirm from that list. If it
    under-reports, you approve a release believing something shipped that did not."""
    script = (ROOT / "scripts" / "release.sh").read_text(encoding="utf-8")
    for f in (ROOT / ".github" / "workflows").glob("*.yml"):
        assert f.name in script, f"release.sh does not mention {f.name} in its confirmation"
