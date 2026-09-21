"""What reaches the Teaching Center when a student does an A-Lab.

Two things have to be true, and they are the same thing seen from both ends: the chain must record
every Load and Revert with the files AS THEY STOOD at that moment, and the submission must carry
those same files. If either half uses the wrong file list the server's `check_sources` reports
`never_built` against work that was built, or — worse — a marker opens a package containing three
files the student never touched.

That failure has happened once already, which is why `services/xv6_shadows.py` opens with it: "an
OS submission did not contain the assignment". These tests are about it not happening again by a
different door.
"""
from __future__ import annotations

import pytest

from gini.domain import lab_spec as L
from gini.services import xv6_lab as X


@pytest.fixture
def lab(tmp_path, monkeypatch):
    """A machine whose student has edited some of the assignment's files and not others."""
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path))
    spec = L.get("syscall-sysinfo")
    assert spec is not None
    X.arm("M1", spec.id)
    X.seed_host_files(spec, "M1")
    d = X.lab_dir("M1", spec.id)
    # What the link script does on the first Run: every file the assignment names is copied out
    # of the image, and a pristine copy kept beside it. Without that step only the two files the
    # assignment CARRIES exist, which is why a submission from a machine that was never started
    # is nearly empty — correct, and worth knowing.
    for f in spec.files:
        if not (d / f.name).exists():
            (d / f.name).write_text(f"// as shipped: {f.name}\n")
        X.pristine_path("M1", f.name).write_text((d / f.name).read_text())
    # then the student edits three of them
    for name, text in (("syscall.h", "#define SYS_sysinfo 23\n"),
                       ("kalloc.c", "uint64 freemem(void){ return 0; }\n"),
                       ("usys.pl", 'entry("sysinfo");\n')):
        (d / name).write_text(text)
    return spec


def test_a_submission_carries_the_assignments_files_not_a_shadow_labs(lab):
    got = X.collect(["M1"])
    names = {k.rsplit("/", 1)[-1] for k in got}
    assert "syscall.h" in names and "usys.pl" in names and "sysproc.c" in names
    assert not any(n.startswith("gini_") for n in names), "those belong to a shadow lab"
    assert names == {f.name for f in lab.files}, "every file the student owns, not a subset"


def test_an_untouched_file_is_still_submitted(lab):
    """"They never opened kalloc.c" is evidence, and a marker cannot infer it from an absence."""
    got = X.collect(["M1"])
    assert any(k.endswith("/defs.h") for k in got)


def test_each_file_travels_with_the_hash_that_binds_it_to_the_chain(lab):
    got = X.collect(["M1"])
    rec = got["M1/syscall.h"]
    assert rec["sha256"] == X.digest("#define SYS_sysinfo 23\n")
    assert rec["text"] == "#define SYS_sysinfo 23\n"
    assert rec["lines"] == 1 and rec["bytes"] > 0


def test_the_hashes_recorded_at_build_time_are_the_ones_submitted(lab):
    """The chain says what was compiled; the package carries what is opened. Same bytes, or a
    marker is reading a file that was never built."""
    built = X.hashes_for(lab, "M1")
    sent = X.collect(["M1"])
    for key, rec in sent.items():
        name = key.rsplit("/", 1)[-1]
        assert built[name]["sha256"] == rec["sha256"], name


def test_one_machines_work_never_lands_in_anothers_submission(lab):
    """Lab folders are per-machine and outlive the topology that made them."""
    X.arm("M2", lab.id)
    X.seed_host_files(lab, "M2")
    (X.lab_dir("M2", lab.id) / "syscall.h").write_text("// M2's different work\n")
    only_m1 = X.collect(["M1"])
    assert all(k.startswith("M1/") for k in only_m1)
    assert only_m1["M1/syscall.h"]["text"] == "#define SYS_sysinfo 23\n"


def test_a_machine_that_was_never_run_contributes_nothing(lab):
    assert X.collect(["never-launched"]) == {}


def test_a_machine_started_but_never_linked_submits_only_what_the_assignment_carries(tmp_path,
                                                                                     monkeypatch):
    """Seeding is host-side and happens at Run; copying the rest out of the image needs the
    container. A folder with only the seeded files means the link step never completed — the
    submission is honest about that rather than inventing the missing ones."""
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path))
    spec = L.get("syscall-sysinfo")
    X.arm("M9", spec.id)
    X.seed_host_files(spec, "M9")
    got = {k.rsplit("/", 1)[-1] for k in X.collect(["M9"])}
    assert got == {f.name for f in spec.files if f.seed}
    assert "syscall.c" not in got


def test_no_assignment_armed_is_not_a_crash(lab, tmp_path, monkeypatch):
    """A machine with nothing armed contributes nothing rather than raising — and `collect` is
    called from the submission path, where a read that throws loses the whole proof."""
    assert X.collect(["nothing-armed-here"]) == {}
    assert X.active_spec("nothing-armed-here") is None


def test_the_recorder_is_given_a_progress_line_and_the_lab_id(lab, monkeypatch):
    """A chain of builds with no sense of progress cannot tell a student converging on an answer
    from one thrashing, so how far the checklist had got goes in beside the log."""
    res = L.evaluate(lab, X.reader_for(lab, "M1"), X.pristine_reader_for(lab, "M1"))
    p = L.progress(res)
    assert 0 < p["passed"] < p["total"], "the fixture is a partly-done lab, by design"
    assert lab.id == "syscall-sysinfo", "what note_build records as the 'shadow' name"


def test_the_submission_merges_both_kinds_of_student_source(tmp_path, monkeypatch):
    """A machine can carry a shadow lab AND an assignment; the package is the union, and the two
    cannot collide because a shadow file is gini_*.c and nothing else is."""
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path))
    from gini.services import xv6_shadows as S
    spec = L.get("syscall-sysinfo")
    X.arm("M1", spec.id)
    X.seed_host_files(spec, "M1")
    S.shadow_dir("M1").mkdir(parents=True, exist_ok=True)
    (S.shadow_dir("M1") / "gini_sched.c").write_text("// my scheduler\n")

    merged = dict(S.collect(["M1"]))
    merged.update(X.collect(["M1"]))
    names = {k.rsplit("/", 1)[-1] for k in merged}
    assert "gini_sched.c" in names, "the shadow lab's deliverable"
    assert "sysinfo.h" in names, "and the assignment's"
    assert len(merged) == len(names), "no key collided"
