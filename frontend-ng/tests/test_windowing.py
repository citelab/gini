"""Labs are windows of their own, and there is a way back to them.

A class on Windows 11 reported seeing ONE gBuilder window: every lab was a `QDialog` parented to
the main window, which on Windows is an *owned* window — no taskbar button, skipped by Alt+Tab. It
never showed up here because macOS cycles owned windows with Cmd+` quite happily.

Two things have to hold, and they pull in opposite directions, which is why both are tested:

* a lab that stays on screen beside the canvas must be an independent window, or the original
  complaint is back;
* a dialog that blocks its parent must NOT be, or it can be pushed behind that parent — and a modal
  window you cannot see, while input goes nowhere, is indistinguishable from a frozen app.

Modal is the usual reason for the second, but not the only one: the first-run dialog and the
Fragment Manager are both modeless and stay owned on purpose. So the rule is not "modal vs
modeless" and the classification below does not pretend it is — an owned dialog has to carry the
reason it is owned. A dialog added later that is in neither column fails the suite, which is the
point: its author is made to say which kind it is rather than inheriting whichever default was in
force the day they wrote it.
"""
from __future__ import annotations

import inspect
import pkgutil

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, Qt                                    # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QWidget             # noqa: E402

from gini.ui.windowing import focus, open_windows, standalone            # noqa: E402

WINDOW, OWNED = "window", "owned"

#: Every `QDialog` in `gini.ui`, and which kind it is. An owned one carries the reason it stays
#: owned, because "why can I not Alt+Tab to this one?" is the question the next reader will have,
#: and "it is modal" is not always the answer — two of these are deliberately modeless and stay
#: owned anyway.
KIND = {
    # Modeless and standalone: opened with show(), lives alongside the canvas for as long as the
    # student wants it, and is therefore something they must be able to switch back to.
    "MachineLab": WINDOW, "Xv6Console": WINDOW, "TrapLab": WINDOW, "MemoryLab": WINDOW,
    "StorageLab": WINDOW, "SyscallLab": WINDOW, "LockLab": WINDOW, "RouterLab": WINDOW,
    "ZooLab": WINDOW, "CpuJourney": WINDOW, "TerminalView": WINDOW, "MarkDialog": WINDOW,
}

#: The other side of the rule, with the reason spelled out for each.
OWNED_BECAUSE = {
    # exec()'d: input is blocked until they are answered. A modal window that can hide behind its
    # parent looks exactly like a frozen app — input goes nowhere and the thing eating it is
    # invisible — so these must stay above the window they block.
    "AboutDialog": "modal", "AuthorDialog": "modal", "BoardSetupDialog": "modal",
    "FlashBoardDialog": "modal", "ResetBoardDialog": "modal", "CueCards": "modal",
    "ProofIssueDialog": "modal", "ProofVerifyDialog": "modal", "SettingsDialog": "modal",
    "SignInDialog": "modal", "SyscallBuilder": "modal",
    # Modeless, and owned on purpose:
    "FirstRunDialog":
        "modeless so the canvas stays usable while images download, but it is the thing the "
        "student is waiting on: it has to stay above gBuilder rather than be findable from it",
    "FragmentManager":
        "a Qt.Tool palette by the author's explicit choice (setWindowFlag(Qt.Tool, True)), which "
        "floats above the window it edits for; promoting it would overturn that decision",
}


def _dialog_classes() -> dict:
    """Every QDialog subclass defined in `gini.ui`, found by import rather than by list."""
    import gini.ui

    found = {}
    for mod in pkgutil.iter_modules(gini.ui.__path__):
        try:
            m = __import__(f"gini.ui.{mod.name}", fromlist=["*"])
        except Exception:                      # noqa: BLE001 — an optional dep, not our subject
            continue
        for name, obj in vars(m).items():
            if (isinstance(obj, type) and issubclass(obj, QDialog) and obj is not QDialog
                    and obj.__module__ == m.__name__):
                found[name] = obj
    return found


def test_every_dialog_in_the_ui_package_is_classified():
    unclassified = sorted(set(_dialog_classes()) - set(KIND) - set(OWNED_BECAUSE))
    assert not unclassified, (
        f"new dialog(s) {unclassified} — say which kind in tests/test_windowing.py. One that stays "
        f"on screen beside the canvas needs windowing.standalone(self) so Alt+Tab reaches it; one "
        f"that blocks its parent must not have it, and goes in OWNED_BECAUSE with its reason.")


def test_every_owned_dialog_says_why_it_is_owned():
    """A bare entry would let "it has always been like that" pass for a decision."""
    for name, reason in OWNED_BECAUSE.items():
        assert reason and len(reason) >= len("modal"), f"{name} is owned for no stated reason"
    assert not set(KIND) & set(OWNED_BECAUSE), "a dialog cannot be both"


def test_exactly_the_modeless_labs_promote_themselves_to_real_windows():
    """Per class, not per module — `machine_lab` holds one of each kind of code path and both of
    its dialogs are windows, so a file-level check would prove nothing."""
    for name, cls in sorted(_dialog_classes().items()):
        promotes = "standalone(self" in inspect.getsource(cls)
        want = KIND.get(name) == WINDOW
        assert promotes is want, (
            f"{name} {'calls' if promotes else 'does not call'} standalone(self) but is "
            f"classified {KIND.get(name, OWNED)!r}")


def test_promoting_a_dialog_changes_its_window_type_and_keeps_its_parent():
    """The trap this module was written around: `setWindowFlag(Qt.Window, True)` is a NO-OP on a
    QDialog, because Qt.Dialog (0x3) already contains the Qt.Window bit (0x1). Only replacing the
    type works — and the parent has to survive it, or `_retire_lab` stops being able to clean up."""
    app = QApplication.instance() or QApplication([])
    parent = QWidget()
    d = QDialog(parent)
    assert int(d.windowFlags() & Qt.WindowType_Mask) == int(Qt.Dialog)

    d.setWindowFlag(Qt.Window, True)                      # the line that looks like it works
    assert int(d.windowFlags() & Qt.WindowType_Mask) == int(Qt.Dialog), "no-op is no longer a no-op"

    standalone(d, "A Lab — M1")
    assert int(d.windowFlags() & Qt.WindowType_Mask) == int(Qt.Window)
    assert d.parent() is parent, "lost its parent: Qt would no longer clean it up with the window"
    assert d.windowFlags() & Qt.WindowCloseButtonHint, "a lab you cannot close is a worse bug"
    assert d.windowTitle() == "A Lab — M1"
    del app


def test_a_real_lab_becomes_a_window_without_being_orphaned():
    from gini.ui.theme import ThemeManager
    from gini.ui.trap_lab import TrapLab

    app = QApplication.instance() or QApplication([])

    class _Dev:
        type_key, name, properties = "xv6", "M1", {"Timeslice": "1"}

    parent = QWidget()
    lab = TrapLab(parent, ThemeManager(app), _Dev(), traps_source=lambda: "")
    assert int(lab.windowFlags() & Qt.WindowType_Mask) == int(Qt.Window)
    assert lab.parent() is parent


def test_open_windows_lists_only_visible_titled_windows():
    app = QApplication.instance() or QApplication([])
    shown, hidden, untitled = QWidget(), QWidget(), QWidget()
    for w, title in ((shown, "Shown"), (hidden, "Hidden"), (untitled, "")):
        w.setWindowFlags(Qt.Window)
        w.setWindowTitle(title)
    shown.show()
    untitled.show()
    app.processEvents()

    titles = [t for t, _ in open_windows()]
    assert "Shown" in titles
    assert "Hidden" not in titles, "a window nobody can see is not somewhere to switch to"
    assert "" not in titles, "menus, tooltips and popups are top-level too"
    assert shown not in [w for _, w in open_windows(exclude=shown)]
    shown.close()
    untitled.close()


def test_focus_refuses_a_window_that_has_closed_since_the_menu_was_built():
    """Otherwise the entry resurrects a husk. A retired lab has already had `stop_polling()` run
    and its workers joined, so re-showing it displays whatever was on screen when it closed and
    never updates again."""
    app = QApplication.instance() or QApplication([])
    w = QWidget()
    w.setWindowFlags(Qt.Window)
    w.setWindowTitle("A Lab")
    w.show()
    app.processEvents()
    assert focus(w) is True

    w.close()
    app.processEvents()
    assert focus(w) is False
    assert not w.isVisible()


def test_focus_returns_false_for_a_widget_whose_c_plus_plus_side_is_gone():
    app = QApplication.instance() or QApplication([])
    w = QWidget()
    w.setWindowFlags(Qt.Window)
    w.setWindowTitle("Going")
    w.show()
    app.processEvents()
    w.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert focus(w) is False                # a stale menu entry must not raise, it must do nothing


def test_a_dialog_built_inline_is_promoted_or_modal_like_any_other():
    """The class sweep would miss these, and one of them is the Process Scheduler — which is the
    window the student who reported this named first.

    Read from the source rather than by opening every window, because the rule is about the code:
    a function that constructs a `QDialog` either `exec()`s it (modal, stays owned) or must call
    `standalone()` on it (modeless, needs its own taskbar button). Neither is a judgement call.
    """
    import ast
    import pathlib

    import gini.ui

    offenders = []
    for path in sorted(pathlib.Path(gini.ui.__path__[0]).glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
            body = ast.dump(fn)
            builds = any(isinstance(n, ast.Call) and getattr(n.func, "id", "") == "QDialog"
                         for n in ast.walk(fn))
            if builds and "attr='exec'" not in body and "id='standalone'" not in body:
                offenders.append(f"{path.name}:{fn.lineno} {fn.name}")
    assert not offenders, (
        f"{offenders} builds a QDialog it neither exec()s nor passes to windowing.standalone(). "
        f"If it is modal, exec() it; if it stays on screen, promote it.")


def test_focus_restores_a_minimised_window_and_marks_it_active():
    """The active bit is the line that is easy to read as redundant and delete. On macOS it IS
    redundant — `raise_()` alone works — so a Mac suite would stay green while the Window menu
    silently stopped doing anything on Windows 11, which is the bug it was added for. Pin it here
    so the deletion fails somewhere.
    """
    app = QApplication.instance() or QApplication([])
    w = QWidget()
    w.setWindowFlags(Qt.Window)
    w.setWindowTitle("Behind")
    w.show()
    app.processEvents()

    w.showMinimized()
    app.processEvents()
    assert focus(w) is True
    app.processEvents()
    assert not (w.windowState() & Qt.WindowMinimized), "a minimised window ignores raise_()"
    assert w.windowState() & Qt.WindowActive, (
        "WindowActive is what Windows needs; without it the menu entry does nothing there")
    w.close()
