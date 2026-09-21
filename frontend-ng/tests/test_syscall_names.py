"""A student's own system call, called by its name.

Stock xv6 ends at `SYS_sync = 22`. An A-Lab adds one past that, and the Syscall Lab's name table
stopped at the stock list — so the call a student had just built appeared as `sys23`, in the very
panel the handout sends them to in order to watch it work. "Your call isn't there" is what that
reads as, and it is wrong: the call is there, the label was not.

`SyscallLab` has always taken a `name_extra` override. Nothing ever passed one.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from gini.domain import lab_spec as L
from gini.domain.xv6 import SYSCALL_NAMES, parse_syscall_defines, syscall_name
from gini.services import xv6_lab as X

SPEC = """
id: lab-one
title: A-Lab 01 — sysinfo
summary: Report free memory.
syscall_number: 23
files:
  - {name: syscall.h, tree: kernel/syscall.h}
  - {name: sysinfo.h, tree: kernel/sysinfo.h, seed: "// yours\\n"}
checks:
  - {id: c1, label: number it, file: syscall.h, match: "SYS_sysinfo", part: A}
parts: {A: Add the system call}
"""


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def armed(tmp_path, monkeypatch):
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path / "home"))
    monkeypatch.delenv("GINI_LAB", raising=False)
    labs = tmp_path / "labs"
    labs.mkdir()
    (labs / "one.yaml").write_text(SPEC, encoding="utf-8")
    monkeypatch.setattr(L, "LABS_DIR", labs)
    spec = L.get("lab-one")
    X.arm("M1", spec.id)
    X.seed_host_files(spec, "M1")
    return spec


# --------------------------------------------------------------------------- #
# reading the defines
# --------------------------------------------------------------------------- #

def test_a_define_becomes_a_name():
    assert parse_syscall_defines("#define SYS_sysinfo 23") == {23: "sysinfo"}


def test_whitespace_the_way_people_actually_write_it():
    got = parse_syscall_defines("  #  define   SYS_trace\t24\n\t#define SYS_mmap 25\n")
    assert got == {24: "trace", 25: "mmap"}


def test_a_commented_out_define_is_not_a_system_call():
    """Students comment the line out while bisecting a build failure. A call that is not in the
    kernel must not be labelled as though it were."""
    assert parse_syscall_defines("// #define SYS_ghost 99") == {}
    assert parse_syscall_defines("   //#define SYS_ghost 99") == {}


def test_nothing_sensible_in_gives_nothing_out():
    for bad in ("", None, "#define SYS_broken\n", "#define NOT_A_SYSCALL 7\n"):
        assert parse_syscall_defines(bad) == {}


def test_the_stock_table_is_unchanged_by_any_of_this():
    """The override only ever ADDS. A kernel with no assignment on it reads exactly as before."""
    assert syscall_name(14) == "uptime"
    assert syscall_name(23) == "sys23", "nothing armed: still the honest fallback"
    assert max(SYSCALL_NAMES) == 22


# --------------------------------------------------------------------------- #
# reading them off the machine
# --------------------------------------------------------------------------- #

def test_the_name_comes_from_the_students_file(armed):
    (X.lab_dir("M1", armed.id) / "syscall.h").write_text(
        "#define SYS_sync 22\n#define SYS_sysinfo 23\n")
    names = X.syscall_names(armed, "M1")
    assert names[23] == "sysinfo"
    assert syscall_name(23, names) == "sysinfo"


def test_the_number_they_actually_used_wins_over_the_one_the_handout_said(armed):
    """The spec says 23. A student who used 24 has a working call, and a trace that labels it
    `sys24` is telling them it does not exist."""
    assert armed.syscall_number == 23
    (X.lab_dir("M1", armed.id) / "syscall.h").write_text("#define SYS_sysinfo 24\n")
    names = X.syscall_names(armed, "M1")
    assert names == {24: "sysinfo"}
    assert syscall_name(24, names) == "sysinfo"
    assert syscall_name(23, names) == "sys23", "23 is not wired, so it is not named"


def test_before_they_have_started_there_is_nothing_to_name(armed):
    """The seeded tree has no syscall.h until the link script copies one out of the image."""
    assert X.syscall_names(armed, "M1") == {}


def test_no_assignment_armed_is_not_a_crash(armed):
    assert X.syscall_names(None, "M1") == {}
    assert X.syscall_names(armed, "never-heard-of-it") == {}


def test_it_follows_the_machine_not_the_installation(armed):
    """Arming is per machine, so M2's kernel has its own answer."""
    (X.lab_dir("M1", armed.id) / "syscall.h").write_text("#define SYS_sysinfo 23\n")
    assert X.syscall_names(armed, "M1") == {23: "sysinfo"}
    assert X.syscall_names(armed, "M2") == {}


# --------------------------------------------------------------------------- #
# and that the panel is actually given them
# --------------------------------------------------------------------------- #

def test_the_syscall_lab_is_handed_the_names(armed, app):
    """The regression: `name_extra` existed, was used in both render paths, and nothing ever
    passed one."""
    from gini.ui.machine_lab import MachineLab
    from gini.ui.theme import ThemeManager

    (X.lab_dir("M1", armed.id) / "syscall.h").write_text("#define SYS_sysinfo 23\n")

    class _Dev:
        type_key, name, properties = "xv6", "M1", {"Timeslice": "1"}

    lab = MachineLab(None, ThemeManager(app), _Dev(), state=None)
    lab._open_syscall_lab()
    assert lab._sclab._name_extra.get(23) == "sysinfo"
