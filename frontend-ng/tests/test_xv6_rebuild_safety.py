"""The two ways a Load can silently do the wrong thing (backend/xv6/gini_agent.py).

Both were found by building a real syscall lab, and both are dangerous for the same reason:
they do not look like failures. The student presses Load, the agent answers "loaded", QEMU
comes back — and something is wrong that will be blamed on their code.

**The disk image.** `mkfs` opens fs.img O_TRUNC and writes two megabytes into it, while the
running QEMU holds that same file open read-write and may write its own dirty blocks back at
any moment. Two writers, one inode. It survived every manual test, which is exactly the problem
with it. The fix turns on the difference between an open file (an INODE) and a build target (a
PATH), so these tests check inodes, not contents.

**The stale build.** A bind mount caches file attributes; a file just written on the host can
still report its old mtime inside the container, and then `make` builds nothing and QEMU
restarts on the previous kernel. Reproduced on Docker Desktop/macOS by
backend/xv6/lab_feasibility.sh, which prints it as "make: 'kernel/kernel' is up to date." at
the moment the student pressed Load.

Pure-Python, no container.
"""
import importlib.util
from pathlib import Path

import pytest

AGENT = Path(__file__).resolve().parents[2] / "backend" / "xv6" / "gini_agent.py"


@pytest.fixture(scope="module")
def ga():
    if not AGENT.exists():
        pytest.skip("backend/xv6/gini_agent.py not present")
    spec = importlib.util.spec_from_file_location("gini_agent", AGENT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def tree(ga, tmp_path, monkeypatch):
    """Redirect the three disk-image paths into a tmp dir and seed a 'live' image."""
    monkeypatch.setattr(ga, "XV6_DIR", str(tmp_path))
    monkeypatch.setattr(ga, "FS_IMG", str(tmp_path / "fs.img"))
    monkeypatch.setattr(ga, "FS_NEW", str(tmp_path / "fs-new.img"))
    monkeypatch.setattr(ga, "FS_LIVE", str(tmp_path / "fs-live.img"))
    (tmp_path / "fs.img").write_bytes(b"LIVE-DISK")
    return tmp_path


def _inode(p):
    return Path(p).stat().st_ino


def test_a_kernel_only_load_never_touches_the_disk_image(ga, tree, monkeypatch):
    """The common case. Regenerating the disk would silently delete any file the student made
    inside xv6 this session, which is not what "rebuild my kernel" means."""
    monkeypatch.setattr(ga, "_fs_is_stale", lambda: False)
    monkeypatch.setattr(ga, "_make", lambda *a, **k: pytest.fail("make must not run"))
    before = _inode(tree / "fs.img")
    ok, log, staged = ga._stage_fs()
    assert (ok, staged) == (True, False)
    assert _inode(tree / "fs.img") == before
    assert (tree / "fs.img").read_bytes() == b"LIVE-DISK"


def test_mkfs_never_writes_to_the_file_qemu_has_open(ga, tree, monkeypatch):
    """The point of the whole exercise: the built image is a DIFFERENT inode."""
    live = _inode(tree / "fs.img")

    def fake_make(targets, timeout=180):
        assert not Path(ga.FS_IMG).exists(), "the live image was still at the build target path"
        Path(ga.FS_IMG).write_bytes(b"NEW-DISK")      # what mkfs does, at a free path
        return True, ""

    monkeypatch.setattr(ga, "_fs_is_stale", lambda: True)
    monkeypatch.setattr(ga, "_make", fake_make)
    ok, log, staged = ga._stage_fs()
    assert (ok, staged) == (True, True)
    assert Path(ga.FS_NEW).read_bytes() == b"NEW-DISK"
    assert _inode(tree / "fs.img") == live            # QEMU's disk is untouched, still in place
    assert (tree / "fs.img").read_bytes() == b"LIVE-DISK"
    assert not Path(ga.FS_LIVE).exists()


def test_the_new_image_is_only_swapped_in_while_qemu_is_stopped(ga, tree, monkeypatch):
    Path(ga.FS_NEW).write_bytes(b"NEW-DISK")
    staged = _inode(ga.FS_NEW)
    ga._swap_fs()
    assert (tree / "fs.img").read_bytes() == b"NEW-DISK"
    assert _inode(tree / "fs.img") == staged
    assert not Path(ga.FS_NEW).exists()


def test_a_failed_disk_build_puts_the_live_image_back(ga, tree, monkeypatch):
    """The dangerous path: the failure happens AFTER the live image has been parked."""
    live = _inode(tree / "fs.img")

    def fake_make(targets, timeout=180):
        Path(ga.FS_IMG).write_bytes(b"HALF-WRIT")     # mkfs died partway through
        return False, "user/hi.c:3:24: error: 'notdeclared' undeclared"

    monkeypatch.setattr(ga, "_fs_is_stale", lambda: True)
    monkeypatch.setattr(ga, "_make", fake_make)
    ok, log, staged = ga._stage_fs()
    assert (ok, staged) == (False, False)
    assert "notdeclared" in log
    assert (tree / "fs.img").read_bytes() == b"LIVE-DISK"
    assert _inode(tree / "fs.img") == live            # the SAME file QEMU still has open
    assert not Path(ga.FS_NEW).exists()
    assert not Path(ga.FS_LIVE).exists()


def test_a_failed_build_leaves_the_machine_running(ga, tree, monkeypatch):
    """A student whose code does not compile keeps the machine they had."""
    calls = []
    monkeypatch.setattr(ga, "_make", lambda t, timeout=180: (False, "kernel/proc.c:1:1: error: x"))
    monkeypatch.setattr(ga._QEMU, "restart", lambda **k: calls.append("restart"))
    ok, log = ga._rebuild()
    assert ok is False and "error" in log
    assert calls == []


def test_a_successful_load_restarts_qemu_with_the_swap_as_its_hook(ga, tree, monkeypatch):
    seen = {}
    monkeypatch.setattr(ga, "_make", lambda t, timeout=180: (True, ""))
    monkeypatch.setattr(ga, "_stage_fs", lambda: (True, "", True))
    monkeypatch.setattr(ga._QEMU, "restart", lambda on_down=None: seen.update(hook=on_down))
    ok, log = ga._rebuild()
    assert (ok, log) == (True, "loaded")
    assert seen["hook"] is ga._swap_fs          # the swap runs while the machine is down


def test_nothing_is_swapped_when_the_disk_did_not_change(ga, tree, monkeypatch):
    seen = {}
    monkeypatch.setattr(ga, "_make", lambda t, timeout=180: (True, ""))
    monkeypatch.setattr(ga, "_stage_fs", lambda: (True, "", False))
    monkeypatch.setattr(ga._QEMU, "restart", lambda on_down=None: seen.update(hook=on_down))
    assert ga._rebuild() == (True, "loaded")
    assert seen["hook"] is None


def test_restart_runs_its_hook_after_stop_and_before_start(ga, monkeypatch):
    """Ordering is the whole guarantee — a swap before the stop would race the live QEMU."""
    order = []
    q = ga.Qemu()
    monkeypatch.setattr(q, "stop", lambda: order.append("stop"))
    monkeypatch.setattr(q, "start", lambda: order.append("start"))
    monkeypatch.setattr(ga.time, "sleep", lambda *_: None)
    q.restart(on_down=lambda: order.append("swap"))
    assert order == ["stop", "swap", "start"]


def test_a_crash_with_the_live_image_parked_is_recovered_at_startup(ga, tree):
    """Otherwise the container comes up with no fs.img and QEMU cannot open its drive."""
    Path(ga.FS_IMG).rename(ga.FS_LIVE)
    ga._recover_disk_image()
    assert Path(ga.FS_IMG).read_bytes() == b"LIVE-DISK"
    assert not Path(ga.FS_LIVE).exists()


def test_a_finished_build_that_never_reached_its_restart_is_applied(ga, tree):
    """fs-new.img is only ever named on success, so it is always safe to apply."""
    Path(ga.FS_NEW).write_bytes(b"NEW-DISK")
    ga._recover_disk_image()
    assert Path(ga.FS_IMG).read_bytes() == b"NEW-DISK"
    assert not Path(ga.FS_NEW).exists()


def test_recovery_does_nothing_to_a_healthy_tree(ga, tree):
    before = _inode(tree / "fs.img")
    ga._recover_disk_image()
    assert _inode(tree / "fs.img") == before
    assert sorted(p.name for p in tree.iterdir()) == ["fs.img"]


# -- the stale build ------------------------------------------------------------------ #

def test_the_student_s_files_are_touched_before_every_build(ga, tmp_path, monkeypatch):
    """Being the last writer is what stops a cached mtime from fooling make."""
    a, b = tmp_path / "gini_sched.c", tmp_path / "gini_vm.c"
    a.write_text("// sched"); b.write_text("// vm")
    import os
    old = os.stat(a).st_mtime
    os.utime(a, (old - 500, old - 500))
    os.utime(b, (old - 500, old - 500))
    monkeypatch.setattr(ga, "SHADOWS", {"sched": (str(a), ""), "vm": (str(b), "")})
    ga._touch_sources()
    assert os.stat(a).st_mtime > old - 500
    assert os.stat(b).st_mtime > old - 500


def test_touching_survives_a_file_that_is_not_there(ga, tmp_path, monkeypatch):
    """A lab file the student has not created yet must not break the build."""
    monkeypatch.setattr(ga, "SHADOWS", {"sched": (str(tmp_path / "nope.c"), "")})
    ga._touch_sources()                      # must not raise


def test_a_load_touches_before_it_builds(ga, tree, monkeypatch):
    """Ordering is the guarantee: touching after make would fix nothing."""
    order = []
    monkeypatch.setattr(ga, "_touch_sources", lambda: order.append("touch"))
    monkeypatch.setattr(ga, "_make", lambda t, timeout=180: (order.append("make"), (True, ""))[1])
    monkeypatch.setattr(ga, "_stage_fs", lambda: (True, "", False))
    monkeypatch.setattr(ga._QEMU, "restart", lambda on_down=None: order.append("restart"))
    ga._rebuild()
    assert order == ["touch", "make", "restart"]
