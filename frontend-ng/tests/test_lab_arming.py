"""Which assignment a machine is carrying, and what happens when that changes.

One A-Lab is armed per MACHINE. That is forced by the kernel rather than chosen: two syscall
assignments both edit `syscall.h`, `syscall.c`, `sysproc.c`, `user.h` and `usys.pl`, so two of
them wired into one tree collide in all five. Across machines there is no collision and these
tests pin that none is invented — M1 on A-Lab 01 while M2 is on A-Lab 02 is a supported thing a
student may do.

The rule this replaced was "the single shipped pack, if there is exactly one", which answered
None the moment a SECOND assignment shipped — and None makes the Machine Lab drop the User Code
face entirely. Adding an assignment made every assignment disappear, silently, with the YAML
sitting right there on disk. `test_a_second_assignment_does_not_hide_the_first` is that bug.
"""
from __future__ import annotations

import pytest

from gini.domain import lab_spec as L
from gini.services import xv6_lab as X

ONE = """
id: lab-one
title: A-Lab 01 — sysinfo
syscall_number: 23
files:
  - {name: syscall.h, tree: kernel/syscall.h}
  - {name: sysinfo.h, tree: kernel/sysinfo.h, seed: "// yours\\n"}
checks:
  - {id: c1, label: Give the call a number, file: syscall.h, match: "SYS_sysinfo", part: A}
parts: {A: Add the system call}
"""

TWO = """
id: lab-two
title: A-Lab 02 — trace
syscall_number: 24
files:
  - {name: syscall.h, tree: kernel/syscall.h}
  - {name: trace.c, tree: user/trace.c, seed: "// yours\\n", uprog: trace}
checks:
  - {id: c1, label: Give the call a number, file: syscall.h, match: "SYS_trace", part: A}
parts: {A: Add the system call}
"""


@pytest.fixture
def two_labs(tmp_path, monkeypatch):
    """Two assignments shipped, and a clean ~/.gini. Returns (specA, specB)."""
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path / "home"))
    monkeypatch.delenv("GINI_LAB", raising=False)
    labs = tmp_path / "labs"
    labs.mkdir()
    (labs / "one.yaml").write_text(ONE, encoding="utf-8")
    (labs / "two.yaml").write_text(TWO, encoding="utf-8")
    monkeypatch.setattr(L, "LABS_DIR", labs)
    return L.get("lab-one"), L.get("lab-two")


# --------------------------------------------------------------------------- #
# arming
# --------------------------------------------------------------------------- #

def test_a_second_assignment_does_not_hide_the_first(two_labs):
    """The bug this design exists for: dropping a second YAML beside the first used to make the
    User Code face vanish for BOTH, with no error anywhere."""
    assert len(L.catalog()) == 2
    a, _b = two_labs
    X.arm("M1", a.id)
    assert X.active_spec("M1").id == a.id


def test_nothing_is_armed_until_somebody_arms_it(two_labs):
    """No auto-pick. The hub is where the choice is made, and a machine that has never been
    given an assignment is honest about having none."""
    assert X.armed_id("M1") == ""
    assert X.active_spec("M1") is None


def test_arming_is_per_machine(two_labs):
    """M1 on A-Lab 01 and M2 on A-Lab 02, concurrently. Deliberately allowed: the machines have
    separate containers, separate kernel trees and separate folders, so nothing collides."""
    a, b = two_labs
    X.arm("M1", a.id)
    X.arm("M2", b.id)
    assert X.active_spec("M1").id == a.id
    assert X.active_spec("M2").id == b.id


def test_switching_assignments_keeps_the_work_of_the_one_left(two_labs):
    """Non-destructive by construction: each assignment has its own folder, so coming back to
    A-Lab 01 next week finds it exactly as it was."""
    a, b = two_labs
    X.arm("M1", a.id)
    X.seed_host_files(a, "M1")
    (X.lab_dir("M1", a.id) / "syscall.h").write_text("#define SYS_sysinfo 23\n")

    X.arm("M1", b.id)
    X.seed_host_files(b, "M1")
    assert X.active_spec("M1").id == b.id
    assert (X.lab_dir("M1", a.id) / "syscall.h").read_text() == "#define SYS_sysinfo 23\n"

    X.arm("M1", a.id)
    assert X.read_file("M1", "syscall.h", a.id) == "#define SYS_sysinfo 23\n"


def test_the_env_override_still_wins(two_labs, monkeypatch):
    """A developer testing a pack that is not shipped yet should not have to arm it first."""
    a, b = two_labs
    X.arm("M1", a.id)
    monkeypatch.setenv("GINI_LAB", b.id)
    assert X.active_spec("M1").id == b.id


def test_arming_never_touches_the_gini_file_or_settings(two_labs):
    """It lives beside the work, in the machine's own directory. A topology a student downloads
    from a classmate must not re-arm their assignment."""
    a, _b = two_labs
    X.arm("M1", a.id)
    assert (X.machine_root("M1") / X.ARMED).is_file()
    assert (X.machine_root("M1") / X.ARMED).read_text().strip() == a.id


# --------------------------------------------------------------------------- #
# the mount, and the script that fills it
# --------------------------------------------------------------------------- #

def test_the_script_wires_the_armed_assignments_folder_and_not_the_mount_root(two_labs):
    a, b = two_labs
    assert f"{X.MOUNT}/{a.id}/syscall.h" in X.link_script(a)
    assert f"{X.MOUNT}/{b.id}/syscall.h" in X.link_script(b)


def test_the_link_is_remade_unconditionally_so_a_switch_takes_effect(two_labs):
    """A `[ -L ]` guard around the link would see the symlink left by the PREVIOUS assignment,
    call it done, and leave the machine building the lab the student just left."""
    _a, b = two_labs
    lines = X.link_script(b).splitlines()
    links = [ln for ln in lines if "ln -sf" in ln]
    assert links, "nothing is linked at all"
    for ln in links:
        assert "-L" not in ln, f"the link is guarded and a switch would not take: {ln.strip()}"


def test_the_original_is_captured_before_the_students_copy_is_seeded(two_labs):
    """Order is load-bearing. Seeding from the tree AFTER it is a symlink would copy the
    assignment the student just left into the one they just armed."""
    _a, b = two_labs
    script = X.link_script(b)
    orig = f"{X.MOUNT}/{X.PRISTINE_DIR}/syscall.h"
    mine = f"{X.MOUNT}/{b.id}/syscall.h"
    capture = script.index(f"cp kernel/syscall.h {orig}")
    seed = script.index(f"cp {orig} {mine}")
    link = script.index(f"ln -sf {mine} kernel/syscall.h")
    assert capture < seed < link, "capture the original, then seed from it, then link"


def test_one_pristine_per_machine_shared_by_every_assignment(two_labs):
    """The image's copy of syscall.h does not depend on what is armed — and per assignment it
    would not work at all, because after a switch the tree's file is a symlink and there is no
    original left to copy from."""
    assert X.pristine_mount() == f"{X.MOUNT}/{X.PRISTINE_DIR}"
    assert X.pristine_path("M1", "syscall.h").parent.parent == X.machine_root("M1")


# --------------------------------------------------------------------------- #
# submission
# --------------------------------------------------------------------------- #

def test_a_submission_carries_what_each_machine_was_carrying(two_labs):
    """A topology with M1 on one assignment and M2 on another hands in both, each attributed to
    the machine it came from."""
    a, b = two_labs
    X.arm("M1", a.id); X.seed_host_files(a, "M1")
    X.arm("M2", b.id); X.seed_host_files(b, "M2")
    got = X.collect(["M1", "M2"])
    assert "M1/sysinfo.h" in got
    assert "M2/trace.c" in got
    assert "M1/trace.c" not in got, "M1 was never on that assignment"


def test_a_machine_with_nothing_armed_contributes_nothing(two_labs):
    """`collect` runs on the submission path, where a read that raises loses the whole proof."""
    assert X.collect(["never-armed"]) == {}


# --------------------------------------------------------------------------- #
# migrating a folder written before the hub existed
# --------------------------------------------------------------------------- #

def _pre_hub_folder(spec, machine="M1"):
    """What the old layout looked like: the student's files loose in the mount root."""
    root = X.machine_root(machine)
    (root / X.PRISTINE_DIR).mkdir(parents=True, exist_ok=True)
    (root / "syscall.h").write_text("#define SYS_sysinfo 23\n")
    (root / X.PRISTINE_DIR / "syscall.h").write_text("// as shipped\n")
    return root


def test_a_pre_hub_folder_moves_down_into_its_assignment(tmp_path, monkeypatch):
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path / "home"))
    monkeypatch.delenv("GINI_LAB", raising=False)
    labs = tmp_path / "labs"; labs.mkdir()
    (labs / "one.yaml").write_text(ONE, encoding="utf-8")
    monkeypatch.setattr(L, "LABS_DIR", labs)

    _pre_hub_folder(L.get("lab-one"))
    assert X.ensure_layout("M1") == "lab-one"
    assert X.read_file("M1", "syscall.h", "lab-one") == "#define SYS_sysinfo 23\n"
    # and it stays armed, because they were working on it — a migration should be invisible
    assert X.active_spec("M1").id == "lab-one"
    # the pristine copy stays where it is: it is per machine, not per assignment
    assert X.pristine_path("M1", "syscall.h").read_text() == "// as shipped\n"


def test_migrating_is_a_move_and_leaves_nothing_loose(tmp_path, monkeypatch):
    """Two copies that drift apart is worse than either alone, and the one the student can see
    would be the one they are not editing."""
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path / "home"))
    monkeypatch.delenv("GINI_LAB", raising=False)
    labs = tmp_path / "labs"; labs.mkdir()
    (labs / "one.yaml").write_text(ONE, encoding="utf-8")
    monkeypatch.setattr(L, "LABS_DIR", labs)

    root = _pre_hub_folder(L.get("lab-one"))
    X.ensure_layout("M1")
    loose = [p.name for p in root.iterdir() if p.is_file() and p.name != X.ARMED]
    assert loose == [], f"still loose in the mount root: {loose}"


def test_migration_refuses_to_guess_when_two_assignments_are_shipped(two_labs):
    """Loose files and more than one candidate: filing a student's work under the wrong
    assignment is not something they can undo, so it is left alone."""
    root = _pre_hub_folder(two_labs[0])
    assert X.ensure_layout("M1") == ""
    assert (root / "syscall.h").is_file(), "left exactly where it was"


def test_migration_is_a_no_op_on_a_folder_that_is_already_new(two_labs):
    a, _b = two_labs
    X.arm("M1", a.id)
    X.seed_host_files(a, "M1")
    assert X.ensure_layout("M1") == ""
    assert X.ensure_layout("M1") == "", "and stays a no-op on a second Run"


def test_migration_never_writes_over_an_assignment_that_has_work_in_it(tmp_path, monkeypatch):
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path / "home"))
    monkeypatch.delenv("GINI_LAB", raising=False)
    labs = tmp_path / "labs"; labs.mkdir()
    (labs / "one.yaml").write_text(ONE, encoding="utf-8")
    monkeypatch.setattr(L, "LABS_DIR", labs)

    _pre_hub_folder(L.get("lab-one"))
    d = X.lab_dir("M1", "lab-one")
    d.mkdir(parents=True, exist_ok=True)
    (d / "syscall.h").write_text("// newer work, do not clobber\n")
    assert X.ensure_layout("M1") == ""
    assert X.read_file("M1", "syscall.h", "lab-one") == "// newer work, do not clobber\n"
