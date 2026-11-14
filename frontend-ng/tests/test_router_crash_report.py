"""A router that dies says so, once, and names the crash report that outlived it.

The gRouter writes <name>-crash-<time>.txt into ~/.gini/captures on a fatal signal (backend
gr_crash.c, run_grouter.py). Stop deletes the container and its log, so that file is the only
record left -- and a red node on the canvas never told anyone it existed.
"""
import os
import time
from types import SimpleNamespace

from gini.app import paths
from gini.ui.main_window import MainWindow


def _captures(tmp_path, monkeypatch):
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path))
    d = paths.captures_dir()
    d.mkdir(parents=True)
    return d


def test_a_recent_report_is_found_and_an_old_one_is_not(tmp_path, monkeypatch):
    d = _captures(tmp_path, monkeypatch)
    old, new = d / "r1-crash-100.txt", d / "r1-crash-200.txt"
    old.write_text("old"); new.write_text("new")
    now = time.time()
    os.utime(old, (now - 3600, now - 3600))
    assert paths.recent_crash_report("r1", now=now) == new
    assert paths.recent_crash_report("r2", now=now) is None
    os.utime(new, (now - 3600, now - 3600))
    assert paths.recent_crash_report("r1", now=now) is None


def _window():
    logs = []
    w = SimpleNamespace(ctx=SimpleNamespace(log=lambda m, lvl: logs.append((lvl, m))))
    return w, logs


def test_a_router_that_was_up_and_stopped_is_reported_once_with_its_report(tmp_path, monkeypatch):
    d = _captures(tmp_path, monkeypatch)
    (d / "r1-crash-1.txt").write_text("report")
    w, logs = _window()
    for st in ("running", "running", "exited", "exited", "exited"):
        MainWindow._note_router_exit(w, "R1", st)
    assert len(logs) == 1
    level, msg = logs[0]
    assert level == "error" and "R1 crashed" in msg and "r1-crash-1.txt" in msg


def test_a_router_that_never_came_up_is_not_called_a_crash(tmp_path, monkeypatch):
    _captures(tmp_path, monkeypatch)
    w, logs = _window()
    MainWindow._note_router_exit(w, "R1", "exited")
    assert logs == []


def test_without_a_report_it_still_says_the_router_stopped(tmp_path, monkeypatch):
    _captures(tmp_path, monkeypatch)
    w, logs = _window()
    MainWindow._note_router_exit(w, "R1", "running")
    MainWindow._note_router_exit(w, "R1", "exited")
    assert logs and "stopped on its own" in logs[0][1]


def test_after_a_rerun_a_second_crash_is_reported_again(tmp_path, monkeypatch):
    d = _captures(tmp_path, monkeypatch)
    (d / "r1-crash-1.txt").write_text("report")
    w, logs = _window()
    for st in ("running", "exited", "running", "exited"):
        MainWindow._note_router_exit(w, "R1", st)
    assert len(logs) == 2
