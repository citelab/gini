"""The Window menu — the way back to a lab that has gone behind gBuilder.

It is not decoration. Making the labs independent top-level windows (see `ui/windowing`) bought
Alt+Tab on Windows and cost the guarantee that a lab is always ABOVE the main window, which an
owned window gave for free. Trading "I cannot Alt+Tab to my Traps window" for "my Traps window
vanished" would not be a fix, so the menu is the other half of that change — and it has to work
the same on all three platforms, because the gesture that replaces it does not.

Also here: Help → Check for Updates, which is wired the same way every other network read in this
window is — off the GUI thread, back through a signal.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent                                    # noqa: E402
from PySide6.QtWidgets import QApplication                           # noqa: E402

from gini.ui.main_window import MainWindow                           # noqa: E402


def _win():
    app = QApplication.instance() or QApplication([])
    w = MainWindow(app)
    w.show()
    app.processEvents()
    return app, w


def _labels(menu):
    return [a.text() for a in menu.actions() if not a.isSeparator()]


def _lab(win, title="Traps & Interrupts — M1"):
    """A real lab, opened the way the app opens one."""
    from gini.ui.trap_lab import TrapLab

    class _Dev:
        type_key, name, properties = "xv6", "M1", {"Timeslice": "1"}

    lab = TrapLab(win, win.theme, _Dev(), traps_source=lambda: "")
    lab.setWindowTitle(title)
    lab.show()
    QApplication.instance().processEvents()
    return lab


def test_the_menu_bar_offers_window_between_teacher_and_help():
    """Position is the convention on all three platforms, and a menu a student cannot find is the
    same as no menu."""
    _, w = _win()
    names = [a.text() for a in w.menuBar().actions()]
    assert "&Window" in names
    assert names.index("&Window") == names.index("&Help") - 1


def test_every_open_window_is_listed_including_this_one():
    _, w = _win()
    lab = _lab(w)
    w._window_menu.aboutToShow.emit()
    labels = _labels(w._window_menu)
    assert any("Traps" in t for t in labels)
    assert any(w.windowTitle() in t for t in labels), (
        "the main window belongs in its own list — a student in front of a lab needs the way back")
    lab.close()


def test_an_ampersand_in_a_title_is_not_eaten_as_a_mnemonic():
    """Half the labs are named for a device, and 'Traps & Interrupts — M1' would otherwise show as
    'Traps  Interrupts' with the I underlined."""
    _, w = _win()
    lab = _lab(w)
    w._window_menu.aboutToShow.emit()
    assert any("Traps && Interrupts" in t for t in _labels(w._window_menu))
    lab.close()


def test_the_first_nine_windows_get_a_number_to_pick_them_with():
    _, w = _win()
    lab = _lab(w)
    w._window_menu.aboutToShow.emit()
    numbered = [t for t in _labels(w._window_menu) if t.startswith(("&1", "&2"))]
    assert len(numbered) == 2
    lab.close()


def test_the_list_is_rebuilt_every_time_so_a_closed_lab_leaves_no_entry():
    """Built on aboutToShow and never cached. A cached entry pointing at a retired lab is exactly
    the menu item that raises when clicked."""
    _, w = _win()
    lab = _lab(w)
    w._window_menu.aboutToShow.emit()
    assert any("Traps" in t for t in _labels(w._window_menu))

    lab.close()
    QApplication.instance().processEvents()
    w._window_menu.aboutToShow.emit()
    assert not any("Traps" in t for t in _labels(w._window_menu))


def test_an_entry_left_over_from_a_destroyed_lab_does_nothing_rather_than_raising():
    _, w = _win()
    lab = _lab(w)
    w._window_menu.aboutToShow.emit()
    entry = next(a for a in w._window_menu.actions() if "Traps" in a.text())

    lab.close()
    lab.setParent(None)
    lab.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    entry.trigger()                      # the exact race: menu open, lab retired, then a click
    w._window_menu.aboutToShow.emit()    # and the rebuild must survive it too


def test_clicking_an_entry_raises_that_window():
    _, w = _win()
    lab = _lab(w)
    w._window_menu.aboutToShow.emit()
    entry = next(a for a in w._window_menu.actions() if "Traps" in a.text())
    entry.trigger()
    QApplication.instance().processEvents()
    assert lab.isVisible()
    lab.close()


def test_minimise_and_bring_all_to_front_do_not_need_a_lab_to_be_open():
    _, w = _win()
    labels = _labels(w._window_menu)
    assert "&Minimise" in labels and "Bring &All to Front" in labels
    w._minimise_active()
    w._bring_all_to_front()
    QApplication.instance().processEvents()


def test_bring_all_to_front_leaves_the_window_that_was_in_front_in_front():
    """Raising the main window last would be simpler and wrong: it would bury the lab the student
    was reading under gBuilder every time they came back from another app."""
    app, w = _win()
    lab = _lab(w)
    lab.activateWindow()
    app.processEvents()
    if app.activeWindow() is not lab:
        pytest.skip("this platform plugin does not track an active window")
    w._bring_all_to_front()
    app.processEvents()
    assert app.activeWindow() is lab
    lab.close()


def test_the_theme_guard_and_the_window_menu_agree_on_what_a_window_is():
    """One enumeration, in ui/windowing. Two would drift, and the menu offering a window the theme
    guard cannot see is a bug nobody would think to look for in the theme code."""
    _, w = _win()
    lab = _lab(w)
    w._window_menu.aboutToShow.emit()
    assert w._open_windows() == ["Traps & Interrupts — M1"]
    assert any("Traps" in t for t in _labels(w._window_menu))
    lab.close()


# --- Help → Check for Updates ------------------------------------------------------------- #

def test_help_offers_a_check_for_updates():
    _, w = _win()
    # Hold the QAction: PySide hands back a QMenu wrapper that dies with the action it came from,
    # so `next(a.menu() for a in …)` reads a deleted C++ object. (This is why MainWindow keeps its
    # own reference to the Window menu rather than looking it up when it needs it.)
    act = next(a for a in w.menuBar().actions() if a.text() == "&Help")
    assert any("Updates" in a.text() for a in act.menu().actions())


@pytest.fixture
def boxes(monkeypatch):
    """Real QMessageBoxes, built by the real code path, that never enter their modal loop.

    A hand-written fake would have to grow every constant and method the dialog touches, and would
    then pass while the real one raised — which is exactly what the first draft of this did. A
    subclass overriding `exec()` keeps all of Qt's behaviour and removes only the part that would
    block the suite forever.
    """
    import PySide6.QtWidgets as QtW
    seen = []

    class _NoModal(QtW.QMessageBox):
        def exec(self):
            seen.append(self)
            return 0

    monkeypatch.setattr(QtW, "QMessageBox", _NoModal)
    return seen


def test_the_check_runs_off_the_gui_thread_and_reports_back_on_it(monkeypatch, boxes):
    """The read is HTTP, so it may not run on the GUI thread; the dialog is Qt, so it may not run
    anywhere else. `run_off_gui` plus a signal is how every other network read in this window does
    it, and doing it differently here is how the widget-destruction bug got in the first time."""
    import threading

    from gini.services import update_check as uc
    from gini.ui.worker_host import join_owner

    _, w = _win()
    where = {}

    def fake_check(timeout=uc.TIMEOUT):
        where["worker"] = threading.current_thread() is not threading.main_thread()
        return {"ok": True, "installed": "6.11.3", "latest": "6.11.3", "newer": False,
                "kind": uc.PIPX, "command": ""}

    monkeypatch.setattr(uc, "check", fake_check)
    w._check_updates()
    join_owner(w)
    QApplication.instance().processEvents()

    assert where.get("worker") is True, "the PyPI read ran on the GUI thread"
    assert boxes, "the answer never reached a dialog"
    assert boxes[0].thread() is QApplication.instance().thread(), (
        "the dialog was built off the GUI thread")
    assert "up to date" in boxes[0].text()


def test_the_dialog_offers_a_command_to_copy_exactly_when_one_is_safe_to_run(boxes):
    """The UI half decides nothing; it renders `advice`. This checks the wiring honours that."""
    from gini.services import update_check as uc

    _, w = _win()
    w._on_update_checked({"ok": True, "installed": "6.11.3", "latest": "6.11.4", "newer": True,
                          "kind": uc.SOURCE, "command": ""})
    assert "Copy command" not in [b.text() for b in boxes[-1].buttons()], (
        "a source checkout was offered a command to copy — running it would replace their tree")
    assert "git pull" in boxes[-1].informativeText()

    w._on_update_checked({"ok": True, "installed": "6.11.3", "latest": "6.11.4", "newer": True,
                          "kind": uc.PIPX, "command": "pipx upgrade gini-toolkit"})
    assert "Copy command" in [b.text() for b in boxes[-1].buttons()]
    assert "pipx upgrade gini-toolkit" in boxes[-1].informativeText()


def test_a_failed_check_is_a_warning_that_does_not_blame_the_students_machine(boxes):
    _, w = _win()
    w._on_update_checked({"ok": False, "installed": "6.11.3", "latest": "", "newer": False,
                          "kind": "unknown", "command": "",
                          "error": "Could not reach PyPI to ask (timed out). This does not affect "
                                   "anything you are working on."})
    assert "could not check" in boxes[-1].text()
    assert "Copy command" not in [b.text() for b in boxes[-1].buttons()]
    from PySide6.QtWidgets import QMessageBox
    assert boxes[-1].icon() == QMessageBox.Warning
