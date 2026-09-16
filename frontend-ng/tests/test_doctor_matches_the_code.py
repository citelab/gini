"""scripts/gini-doctor.sh describes THIS codebase, so it has to be checked against it.

The doctor is a mirror of GINI's runtime contract: the four image names, the three distributions,
the `gbuilder` launcher, `GINI_ENGINE`, `~/.gini`, and the compose labels container lookups filter
on. Every one of those is defined somewhere in the packages, and a diagnostic that reports on a
name the code stopped using is worse than no diagnostic — it says "absent" about something that
is present and sends the reader somewhere else entirely.

This is also the answer to whether the doctor should live in its own repository. It could, and it
would then drift, because nothing would be able to run these assertions. `services/xv6_shadows.py`
makes the same argument about a path: "two copies of a path rule is one copy too many: if they
ever disagreed, GINI would submit an empty directory while the student's real work sat somewhere
else, and nothing would look wrong."
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
# The script lives in the gini-doctor distribution, which ships it inside its wheel;
# scripts/gini-doctor.sh is a wrapper so a checkout can still run it by the documented path.
DOCTOR_PATH = ROOT / "doctor" / "src" / "gini_doctor" / "gini-doctor.sh"


@pytest.fixture(scope="module")
def doctor() -> str:
    if not DOCTOR_PATH.exists():
        pytest.skip("scripts/gini-doctor.sh not present")
    return DOCTOR_PATH.read_text(encoding="utf-8")


def test_it_checks_for_every_image_gini_actually_builds(doctor):
    """A machine missing an image is the failure this found in the lab; missing one from the
    list would have reported that machine as fine."""
    from gini.setup.images import BUILD_SPECS
    for name in BUILD_SPECS:
        assert name in doctor, f"{name} is built by GINI but the doctor never looks for it"


def test_it_names_the_launcher_the_package_installs(doctor):
    """`gbuilder` is how the doctor finds the interpreter that actually runs the app — reading
    the shebang of whatever `command -v gbuilder` resolves to. Rename the entry point and that
    probe silently falls back to /usr/bin/python3 and reports Qt missing on every machine."""
    pyproject = (ROOT / "frontend-ng" / "pyproject.toml").read_text(encoding="utf-8")
    scripts = re.search(r"\[project\.scripts\](.*?)(?:\n\[|\Z)", pyproject, re.S)
    assert scripts, "frontend-ng declares no console scripts"
    names = re.findall(r"^\s*([A-Za-z0-9_-]+)\s*=", scripts.group(1), re.M)
    assert "gbuilder" in names, "the app's entry point is no longer called gbuilder"
    assert "gbuilder" in doctor


def test_it_reports_on_all_three_distributions(doctor):
    for pkg in ("core", "frontend-ng", "teaching-center"):
        text = (ROOT / pkg / "pyproject.toml").read_text(encoding="utf-8")
        name = re.search(r'^name\s*=\s*"([^"]+)"', text, re.M)
        assert name, f"{pkg}/pyproject.toml has no name"
        assert name.group(1) in doctor, f"{name.group(1)} is shipped but the doctor ignores it"


def test_it_filters_on_the_labels_container_lookups_use(doctor):
    """`container_id` and `_status_by_label` find containers by these two labels. The doctor's
    live round trip proves that mechanism works on a machine, so it must use the same two."""
    orch = (ROOT / "frontend-ng" / "src" / "gini" / "services"
            / "orchestrator.py").read_text(encoding="utf-8")
    for label in ("com.docker.compose.project", "com.docker.compose.service"):
        assert label in orch, f"{label} is no longer how the orchestrator finds containers"
        assert label in doctor, f"the doctor does not filter on {label}"


def test_it_honours_the_environment_variable_that_forces_an_engine(doctor):
    """The doctor reports which engine gBuilder WILL use, not merely which are installed, and
    GINI_ENGINE outranks everything else in that decision."""
    runtime = (ROOT / "frontend-ng" / "src" / "gini" / "setup"
               / "runtime.py").read_text(encoding="utf-8")
    assert "GINI_ENGINE" in runtime
    assert "GINI_ENGINE" in doctor


def test_it_looks_in_the_directory_gini_actually_uses(doctor):
    """Read from the source, not by calling gini_home(): conftest redirects GINI_HOME_DIR at
    import so the suite never touches a real one, and the default is what the doctor must match."""
    paths = (ROOT / "frontend-ng" / "src" / "gini" / "app" / "paths.py").read_text(encoding="utf-8")
    default = re.search(r'Path\.home\(\)\s*/\s*"([^"]+)"', paths)
    assert default, "app/paths.py no longer derives the GINI home from the user's home directory"
    assert default.group(1) in doctor, "the doctor looks for a GINI home that is not GINI's"


def test_the_readme_documents_the_probe_groups_it_offers(doctor):
    """`--list` is the discoverable surface; scripts/README.md is where somebody looks first."""
    readme = (ROOT / "scripts" / "README.md").read_text(encoding="utf-8")
    groups = re.search(r'GROUPS_DEFAULT="([^"]+)"', doctor)
    assert groups, "the doctor no longer declares a default group set"
    assert "--list" in readme, "the README never mentions how to see the groups"


def test_the_checkout_wrapper_points_at_the_packaged_script():
    """`sh scripts/gini-doctor.sh` is what every instruction says to type, and it now forwards to
    the copy inside the distribution. One file, two ways in."""
    wrapper = (ROOT / "scripts" / "gini-doctor.sh").read_text(encoding="utf-8")
    assert "doctor/src/gini_doctor/gini-doctor.sh" in wrapper
    assert "exec sh" in wrapper, "it must exec, so the exit status and the terminal survive"


def test_the_distribution_carries_the_script_and_nothing_else():
    """gini-doctor depends on nothing on purpose — see doctor/pyproject.toml. A dependency here
    is a way for the diagnostic to be unavailable for the same reason as its patient."""
    pyproject = (ROOT / "doctor" / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r"^dependencies\s*=\s*\[\s*\]", pyproject, re.M), \
        "gini-doctor has grown a dependency"
    assert 'gini_doctor = ["*.sh"]' in pyproject, "the wheel would not carry the script"
    assert "gini-doctor = \"gini_doctor.cli:main\"" in pyproject
