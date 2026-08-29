"""First-run bootstrap: what the machine needs, and the one rule about architecture.

The rule worth defending: **an image reference must never name an architecture.** A multi-arch
manifest list means the REGISTRY resolves arm64 vs amd64 at pull time. Baking `-arm64` into a tag
would move that decision into the client, where it can be wrong — and the failure is silent, a
confident pull of binaries that will not run.

Everything here is Qt-free and network-free: `plan()` only looks at the machine, and `execute()`
takes its `run` injected.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from gini.services import bootstrap as B
from gini.setup import images, marker


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """A private ~/.gini, so a test never reads or writes the developer's real marker."""
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path / "home"))
    return tmp_path


class _Ran:
    returncode = 0


def _docker_ok(*a, **k):
    return _Ran()


@pytest.fixture
def with_docker(monkeypatch):
    """Pretend a runtime is present.

    Patched at `runtime.docker_available` rather than by injecting `run`, because that function
    checks `shutil.which("docker")` FIRST — on a machine with no Docker (CI, this sandbox) the
    injected `run` is never reached, and every test would silently exercise the no-runtime path
    while appearing to test the others.
    """
    monkeypatch.setattr(B.runtime, "docker_available", lambda run=None: True)


@pytest.fixture
def without_docker(monkeypatch):
    monkeypatch.setattr(B.runtime, "docker_available", lambda run=None: False)


# -- the architecture rule ----------------------------------------------------- #
def test_an_image_reference_never_names_an_architecture():
    """THE rule. The registry picks the right image from one tag; the client must not try."""
    for ref in images.image_refs("6.1.0"):
        low = ref.lower()
        for token in ("arm64", "aarch64", "amd64", "x86_64", "arm", "x86"):
            assert token not in low, f"{ref} names an architecture — that belongs to the registry"


def test_the_same_reference_is_produced_whatever_the_machine(monkeypatch):
    """A Mac and a PC must ask for the identical tag. If these ever diverge, students on one
    platform silently get different images from students on the other."""
    monkeypatch.setattr(B.platform, "machine", lambda: "arm64")
    on_mac = images.image_refs("6.1.0")
    monkeypatch.setattr(B.platform, "machine", lambda: "x86_64")
    assert images.image_refs("6.1.0") == on_mac


def test_arch_is_reported_for_diagnostics(monkeypatch):
    for reported, expected in (("arm64", "arm64"), ("aarch64", "arm64"),
                               ("x86_64", "amd64"), ("AMD64", "amd64")):
        monkeypatch.setattr(B.platform, "machine", lambda r=reported: r)
        assert B.arch() == expected


def test_an_unknown_architecture_is_named_rather_than_guessed(monkeypatch):
    """Reporting "unknown" is honest; defaulting to amd64 would produce a confident wrong pull."""
    monkeypatch.setattr(B.platform, "machine", lambda: "riscv64")
    assert B.arch() == "riscv64"


# -- surveying ----------------------------------------------------------------- #
def test_no_runtime_is_reported_without_pretending_we_can_fix_it(monkeypatch, without_docker):
    monkeypatch.setattr(images, "find_backend", lambda hint=None: None)
    p = B.plan("6.1.0", run=_docker_ok)
    assert p["state"] == B.NEEDS_RUNTIME
    assert "container runtime" in p["why"]
    assert "Run" in p["why"]                    # says what will not work, not just what is missing


def test_a_fresh_install_plans_a_pull(monkeypatch, with_docker):
    monkeypatch.setattr(images, "find_backend", lambda hint=None: None)
    p = B.plan("6.1.0", run=_docker_ok)
    assert p["state"] == B.PULL and p["refs"]


def test_a_source_checkout_plans_a_local_build(monkeypatch, tmp_path, with_docker):
    """The case that used to need `gini-setup --build` — a flag nobody discovers."""
    monkeypatch.setattr(images, "find_backend", lambda hint=None: tmp_path / "backend")
    p = B.plan("6.1.0", run=_docker_ok)
    assert p["state"] == B.BUILD
    assert p["source"].endswith("backend")


def test_an_upgraded_app_plans_a_refresh(monkeypatch, with_docker):
    monkeypatch.setattr(images, "find_backend", lambda hint=None: None)
    marker.write_marker({"version": "6.0.0", "images": ["gini-xv6"]})
    p = B.plan("6.1.0", run=_docker_ok)
    assert p["state"] == B.UPDATE
    assert "6.0.0" in p["why"] and "6.1.0" in p["why"]


def test_a_settled_machine_needs_nothing(monkeypatch, with_docker):
    monkeypatch.setattr(images, "find_backend", lambda hint=None: None)
    marker.write_marker({"version": "6.1.0", "images": ["gini-xv6"]})
    assert B.plan("6.1.0", run=_docker_ok)["state"] == B.READY


def test_planning_touches_no_network(monkeypatch, with_docker):
    """Opening the app must not stall on a registry. Deciding is local; fetching is explicit."""
    monkeypatch.setattr(images, "find_backend", lambda hint=None: None)

    def explode(*a, **k):
        if a and "pull" in a[0]:
            raise AssertionError("plan() pulled something")
        return _docker_ok()

    B.plan("6.1.0", run=explode)


# -- doing it ------------------------------------------------------------------ #
def test_a_pull_records_only_what_actually_arrived(monkeypatch, with_docker):
    """A partial success remembered as a full one means the app never offers to finish the job."""
    monkeypatch.setattr(images, "find_backend", lambda hint=None: None)
    refs = images.image_refs("6.1.0")

    def half(cmd, **k):
        class R:
            returncode = 0 if cmd[-1] == refs[0] else 1
        return R()

    r = B.execute(B.plan("6.1.0", run=_docker_ok), run=half)
    assert not r["ok"]
    assert len(r["done"]) == 1 and len(r["failed"]) == len(refs) - 1
    assert marker.read_marker()["images"] == r["done"]


def test_a_clean_pull_says_so_plainly(monkeypatch, with_docker):
    monkeypatch.setattr(images, "find_backend", lambda hint=None: None)
    r = B.execute(B.plan("6.1.0", run=_docker_ok), run=_docker_ok)
    assert r["ok"] and "can run topologies now" in r["message"]
    assert marker.read_marker()["version"] == "6.1.0"


def test_a_total_failure_names_the_architecture_as_a_likely_cause(monkeypatch, with_docker):
    """The most probable reason a pull finds nothing is that this arch was never published — say
    it, rather than leaving someone to guess at a registry error."""
    monkeypatch.setattr(images, "find_backend", lambda hint=None: None)
    monkeypatch.setattr(B.platform, "machine", lambda: "arm64")

    def nope(*a, **k):
        class R:
            returncode = 1
        return R()

    r = B.execute(B.plan("6.1.0", run=_docker_ok), run=nope)
    assert not r["ok"] and "arm64" in r["message"]
    assert "keep building and reading topologies" in r["message"]


def test_progress_is_reported_per_image(monkeypatch, with_docker):
    """A five-minute silence looks identical to a hang."""
    monkeypatch.setattr(images, "find_backend", lambda hint=None: None)
    steps = []
    B.execute(B.plan("6.1.0", run=_docker_ok), on_step=steps.append, run=_docker_ok)
    assert len(steps) == len(images.IMAGES)
    assert all(s.startswith("Downloading") for s in steps)


def test_a_source_build_reports_building_not_downloading(monkeypatch, tmp_path, with_docker):
    monkeypatch.setattr(images, "find_backend", lambda hint=None: tmp_path)
    steps = []
    B.execute(B.plan("6.1.0", run=_docker_ok), on_step=steps.append, run=_docker_ok)
    assert steps and all(s.startswith("Building") for s in steps)


def test_nothing_is_attempted_when_there_is_no_runtime(monkeypatch, without_docker):
    monkeypatch.setattr(images, "find_backend", lambda hint=None: None)
    p = B.plan("6.1.0", run=_docker_ok)

    def explode(*a, **k):
        raise AssertionError("tried to pull with no runtime")

    r = B.execute(p, run=explode)
    assert not r["ok"] and r["done"] == []


def test_a_ready_machine_does_nothing(monkeypatch, with_docker):
    monkeypatch.setattr(images, "find_backend", lambda hint=None: None)
    marker.write_marker({"version": "6.1.0", "images": ["gini-xv6"]})
    p = B.plan("6.1.0", run=_docker_ok)

    def explode(*a, **k):
        raise AssertionError("re-pulled an already-ready machine")

    assert B.execute(p, run=explode)["ok"]


# -- the release contract ------------------------------------------------------ #
def test_the_image_script_builds_the_same_images_the_app_expects():
    """A release that ships an image the app never pulls, or misses one it does, is only visible
    when a student's Run button does nothing."""
    root = Path(__file__).resolve().parents[2]
    sh = (root / "scripts" / "images.sh").read_text()
    for name in images.IMAGES:
        assert name in sh, f"{name} is in IMAGES but the release script never builds it"


def test_the_image_script_merges_both_architectures_into_one_tag():
    """`imagetools create` is the step that makes one tag serve both machines. Without it you have
    two arch-suffixed tags and no way for a plain pull to find either."""
    root = Path(__file__).resolve().parents[2]
    sh = (root / "scripts" / "images.sh").read_text()
    assert "imagetools create" in sh
    assert "-arm64" in sh and "-amd64" in sh          # the per-arch tags it merges FROM
    merge = sh.split("merge)")[1]
    assert '"$REGISTRY/$img:$version"' in merge       # ...into an unsuffixed tag


def test_gini_setup_is_no_longer_a_command():
    """It was a second command a new user had to discover. gBuilder does it at launch now."""
    root = Path(__file__).resolve().parents[2]
    pyproject = (root / "frontend-ng" / "pyproject.toml").read_text()
    scripts = pyproject.split("[project.scripts]")[1].split("[tool")[0]
    assert "gbuilder =" in scripts
    assert "gini-setup =" not in scripts


def test_the_launch_preflight_cannot_block_or_raise(monkeypatch):
    """It runs before the window appears. A student with no Docker, no network and a corrupt
    marker must still get an app."""
    from gini import __main__ as M

    monkeypatch.setattr(B, "plan", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert M._setup_preflight() is None


def test_every_text_file_is_read_and_written_as_utf8():
    """Windows. `Path.write_text()` with no encoding uses the LOCALE encoding — cp1252 on most
    Windows machines — so a topology saved there with a non-ASCII device name is corrupt when a
    Mac opens it, and a proof written there may not round-trip at all.

    This project has been bitten by exactly that once already (a cp1252 write corrupted a generated
    Python file), which is why it is a test rather than a note.
    """
    import ast

    root = Path(__file__).resolve().parents[2]
    offenders = []
    for base in ("frontend-ng/src/gini", "core/src/gini", "teaching-center/src"):
        for f in (root / base).rglob("*.py"):
            if "__pycache__" in str(f):
                continue
            tree = ast.parse(f.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in ("read_text", "write_text")
                        and not any(k.arg == "encoding" for k in node.keywords)):
                    offenders.append(f"{f.relative_to(root)}:{node.lineno} {node.func.attr}")
    assert not offenders, "no explicit encoding:\n  " + "\n  ".join(offenders)


def test_the_scripts_that_exist_are_the_ones_the_readme_documents():
    """A README naming a script that was renamed sends someone to a command-not-found. Both are
    edited by hand, so the drift is silent until it wastes somebody's afternoon."""
    root = Path(__file__).resolve().parents[2]
    scripts = {p.name for p in (root / "scripts").glob("*.sh")}
    readme = (root / "scripts" / "README.md").read_text(encoding="utf-8")
    for s in scripts:
        assert s in readme, f"scripts/{s} exists but the README never mentions it"
    # And nothing documented has been deleted out from under the docs, except where the README
    # explicitly says it was removed.
    documented = set(re.findall(r"scripts/([a-z-]+\.sh)", readme))
    gone = documented - scripts
    history = readme.split("## What was here before")[-1]
    for g in gone:
        assert g in history, f"README points at scripts/{g}, which does not exist"


def test_release_refuses_a_dirty_tree_and_a_reused_version():
    """The two guards that have each already cost something: a dev-versioned package nobody can
    install, and a version number PyPI will never accept twice."""
    root = Path(__file__).resolve().parents[2]
    sh = (root / "scripts" / "release.sh").read_text(encoding="utf-8")
    assert "git status --porcelain" in sh          # dirty tree
    assert "already exists" in sh                  # tag reuse
    assert "pytest" in sh                          # tests before tagging
