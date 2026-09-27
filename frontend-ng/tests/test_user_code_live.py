"""The User Code panel keeps up with the student's editor.

It worked out every file's state once, when it opened: a student edited `sysproc.c`, saved, and
the panel still said `untouched` until they closed and reopened it — and the wiring checklist
stayed just as stale. It now looks at the files' stats once a second and re-reads only when a
save has landed.

A stat poll rather than a QFileSystemWatcher, because most editors save by writing a new file and
renaming it over the old one, and a watcher on the file loses the path at the rename: live for
the first save, dead for every one after. `test_an_atomic_rename_save_is_seen_every_time` is
that case.
"""
from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from gini.domain import lab_spec as L
from gini.services import xv6_lab as X


class _Dev:
    name, type_key, properties = "M1", "xv6", {}


@pytest.fixture
def panel(tmp_path, monkeypatch):
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path / "gini"))
    from PySide6.QtWidgets import QApplication

    from gini.ui.theme import ThemeManager
    from gini.ui.user_code import UserCode
    app = QApplication.instance() or QApplication([])
    spec = L.get("syscall-sysinfo")
    X.arm("M1", spec.id)
    X.seed_host_files(spec, "M1")
    # The pristine copies the first Run takes from the image — made equal to the seeded files,
    # so every file starts out genuinely `untouched`.
    for f in spec.files:
        mine = X.lab_dir("M1", spec.id) / f.name
        orig = X.pristine_path("M1", f.name)
        orig.parent.mkdir(parents=True, exist_ok=True)
        orig.write_text(mine.read_text(encoding="utf-8") if mine.exists() else "",
                        encoding="utf-8")
        if not mine.exists():
            mine.write_text("", encoding="utf-8")
    w = UserCode(None, ThemeManager(app), device=_Dev(), provider=None, spec=spec, live=False)
    w.show()
    app.processEvents()
    yield w, spec, app
    w.close()
    w.deleteLater()
    app.processEvents()


def _state(w, name):
    return w._rows[name].text()


def _save(path, text):
    """Write, and move the mtime on — two saves inside one filesystem tick must still differ."""
    path.write_text(text, encoding="utf-8")
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))


def test_a_save_in_the_students_editor_shows_without_reopening(panel):
    w, spec, _ = panel
    f = X.lab_dir("M1", spec.id) / "sysproc.c"
    assert _state(w, "sysproc.c") == "untouched"
    _save(f, f.read_text(encoding="utf-8") + "\n// my first line\n")
    w._poll_files()
    assert _state(w, "sysproc.c") == "edited"


def test_undoing_the_edit_goes_back_to_untouched(panel):
    w, spec, _ = panel
    f = X.lab_dir("M1", spec.id) / "sysproc.c"
    orig = f.read_text(encoding="utf-8")
    _save(f, orig + "x")
    w._poll_files()
    _save(f, orig)
    w._poll_files()
    assert _state(w, "sysproc.c") == "untouched"


def test_an_atomic_rename_save_is_seen_every_time(panel):
    """The save style that defeats a file watcher: new file, renamed over the old one."""
    w, spec, _ = panel
    f = X.lab_dir("M1", spec.id) / "syscall.h"
    base = f.read_text(encoding="utf-8")
    for i in range(3):
        tmp = f.with_suffix(f".h.tmp{i}")
        _save(tmp, base + f"\n#define EXTRA{i} {i}\n")
        os.replace(tmp, f)
        w._poll_files()
        assert _state(w, "syscall.h") == "edited", f"save {i + 1} was not seen"
        _save(f, base)
        w._poll_files()
        assert _state(w, "syscall.h") == "untouched"


def test_the_checklist_goes_live_too(panel):
    """Same re-read, so a student sees a ✓ appear as they write the line — not on reopen."""
    w, spec, _ = panel
    before = w._progress.text()
    f = X.lab_dir("M1", spec.id) / "syscall.h"
    _save(f, f.read_text(encoding="utf-8") + "\n#define SYS_sysinfo 23\n")
    w._poll_files()
    assert w._progress.text() != before


def test_nothing_is_reread_when_nothing_changed(panel, monkeypatch):
    w, _, _ = panel
    calls = []
    monkeypatch.setattr(w, "refresh", lambda: calls.append(1))
    for _ in range(5):
        w._poll_files()
    assert calls == [], "the poll must only re-read after a save"


def test_the_poll_is_a_timer_owned_by_the_panel(panel):
    """Parented to the panel, so it dies with it — no thread, nothing to join."""
    w, _, _ = panel
    assert w._watch.isActive()
    assert w._watch.parent() is w


def test_a_hidden_panel_does_no_work(panel, monkeypatch):
    w, spec, app = panel
    w.hide()
    app.processEvents()
    calls = []
    monkeypatch.setattr(w, "refresh", lambda: calls.append(1))
    _save(X.lab_dir("M1", spec.id) / "sysproc.c", "changed while hidden")
    w._poll_files()
    assert calls == []


def test_the_timer_notices_on_its_own(panel):
    """End to end, no hand-cranking: save, wait, and the panel has caught up within ~2 polls."""
    from gini.ui.user_code import WATCH_MS
    w, spec, app = panel
    f = X.lab_dir("M1", spec.id) / "kalloc.c"
    _save(f, f.read_text(encoding="utf-8") + "\n// edited in my editor\n")
    deadline = time.monotonic() + 3 * WATCH_MS / 1000
    while _state(w, "kalloc.c") != "edited" and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)
    assert _state(w, "kalloc.c") == "edited"


# --------------------------------------------------------------------------- #
# Revert asks before it throws a student's work away
# --------------------------------------------------------------------------- #

def _answer(monkeypatch, button):
    """Stand in for the question dialog, and remember that it was asked."""
    from PySide6.QtWidgets import QMessageBox
    asked = []

    def fake(*a, **k):
        asked.append(a)
        return button
    monkeypatch.setattr(QMessageBox, "question", staticmethod(fake))
    return asked


def test_cancelling_the_question_keeps_the_students_work(panel, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    w, spec, _ = panel
    f = X.lab_dir("M1", spec.id) / "syscall.h"
    mine = f.read_text(encoding="utf-8") + "\n#define SYS_sysinfo 23\n"
    _save(f, mine)
    asked = _answer(monkeypatch, QMessageBox.StandardButton.Cancel)
    w._ask_revert("syscall.h")
    assert asked, "a Revert of an edited file must ask first"
    assert f.read_text(encoding="utf-8") == mine, "cancel must leave the file exactly as it was"


def test_saying_yes_reverts_that_file_only(panel, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    w, spec, _ = panel
    d = X.lab_dir("M1", spec.id)
    _save(d / "syscall.h", (d / "syscall.h").read_text(encoding="utf-8") + "\n// mine\n")
    other = (d / "sysproc.c").read_text(encoding="utf-8") + "\n// also mine\n"
    _save(d / "sysproc.c", other)
    _answer(monkeypatch, QMessageBox.StandardButton.Yes)
    w._ask_revert("syscall.h")
    assert (d / "syscall.h").read_text(encoding="utf-8") == \
        X.pristine_path("M1", "syscall.h").read_text(encoding="utf-8")
    assert (d / "sysproc.c").read_text(encoding="utf-8") == other


def test_an_untouched_file_is_not_asked_about(panel, monkeypatch):
    """Nothing to lose, so no dialog — one that guards nothing trains people to click through."""
    from PySide6.QtWidgets import QMessageBox
    w, _, _ = panel
    asked = _answer(monkeypatch, QMessageBox.StandardButton.Yes)
    w._ask_revert("syscall.h")
    assert asked == []


def test_the_button_goes_through_the_question(panel, monkeypatch):
    """Wired to _ask_revert, not straight to _revert — the whole point."""
    from PySide6.QtWidgets import QMessageBox, QPushButton
    w, spec, _ = panel
    f = X.lab_dir("M1", spec.id) / "syscall.h"
    mine = f.read_text(encoding="utf-8") + "\n// careful\n"
    _save(f, mine)
    asked = _answer(monkeypatch, QMessageBox.StandardButton.Cancel)
    buttons = [b for b in w.findChildren(QPushButton) if b.text() == "Revert"]
    assert buttons, "each file row has a Revert button"
    for b in buttons:
        b.click()
    assert asked, "clicking Revert must ask"
    assert f.read_text(encoding="utf-8") == mine
