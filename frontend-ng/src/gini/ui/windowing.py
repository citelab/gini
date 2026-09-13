"""Making a lab its own window, and finding it again.

Two halves of one problem, reported by a class on Windows 11: every lab is a `QDialog` parented to
the main window, which on Windows makes it an OWNED window. It floats above gBuilder, but it gets
no taskbar button and Alt+Tab skips it, so a student sees ONE gBuilder window with everything
buried inside it. macOS hides this — Cmd+` cycles owned windows quite happily — which is why it
survived on the machine most of this was written on.

`standalone()` is the fix. `open_windows()` is what makes the fix safe, and it is not optional: an
independent window can be pushed BEHIND the main window, which an owned one never could. Trading
"I can't Alt+Tab to my Traps window" for "my Traps window vanished" would not be a fix, so there
has to be a way back, and a menu is the only one that works the same on all three platforms.

**THE TRAP.** Three widgets in this package already promote themselves with

    self.setWindowFlag(Qt.Window, True)

and copying that line onto a lab does NOTHING. `Qt.Dialog` is `0x3` and already CONTAINS the
`Qt.Window` bit (`0x1`), so setting it again leaves `0x3` — verified, not assumed. That line is
right for `cpu_lab`, `games_lab` and `fingerprint_lab` because those are `QWidget`s, whose parented
window type is `0x0`, i.e. not a window at all; the flag is what promotes them. A `QDialog` is
already a window and needs its TYPE changed instead, which is what `setWindowFlags()` does here.

**THE SECOND TRAP, and it cost a round trip to a Windows machine to find.** Changing the type is
still not enough while the widget has a Qt parent. This module first kept the parent deliberately,
reasoning that Qt would then go on destroying the window for us and `MainWindow._retire_lab` /
`MachineLab._retire` would keep working untouched. That reasoning was sound and the result did not
work: Qt passes the parent's HWND to Windows as the window's OWNER, and an *owned* window gets no
taskbar button and is skipped by Alt+Tab whatever its type says.

Measured on Windows 11 with gBuilder, Machine Lab, Process Scheduler and Traps open: ONE Alt+Tab
entry, and the main window could not be raised above any of the other three — an owner can never
come above the windows it owns. So the ownership has to go too, and `standalone()` drops the
parent. What the parent was doing is listed on that function, along with who does it now.

None of this reproduces on macOS, where all four windows behaved correctly the whole time. Do not
re-derive this from a Mac.
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt

#: How far a promoted window is offset from the window that opened it. Qt centres an owned dialog
#: on its parent; a top-level window is placed by the OS, which on a multi-monitor desk can be
#: nowhere near the thing the student just clicked. A fixed cascade is predictable and cheap.
CASCADE = 40

#: Promoted windows have NO Qt parent, so Qt no longer keeps them alive — Python is the only owner
#: left. Every call site does hold one (`self._trap_lab` and its siblings), but a registry here
#: means a future one that forgets gets a window that stays on screen rather than a window that
#: vanishes between two statements, which is a far worse thing to debug.
_HELD: dict[int, object] = {}


#: A normal, decorated, independent window. The hints are explicit because `setWindowFlags`
#: REPLACES the flag set rather than adding to it: taking the type alone would drop the close and
#: minimise buttons on some window managers, and a lab you cannot close is a worse bug than a lab
#: you cannot Alt+Tab to.
_WINDOW = (Qt.Window | Qt.WindowTitleHint | Qt.WindowSystemMenuHint
           | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)


def standalone(w, title: str = "") -> None:
    """Promote a parented dialog to an independent, Alt+Tab-able window.

    Call it in `__init__`, BEFORE the widget is first shown — changing window flags on a visible
    window makes Qt recreate the native handle, which hides it and needs an explicit `show()`.

    **The Qt parent is dropped, and that is the whole point.** Setting `Qt.Window` while keeping
    the parent was the first attempt and it is not enough: Qt hands the parent's HWND to Windows as
    the window's OWNER, and an owned window gets no taskbar button and is skipped by Alt+Tab no
    matter what its type says. It was measured on Windows 11 — three labs open, one Alt+Tab entry,
    and gBuilder unable to be raised above any of them, which is exactly how owned windows behave.
    So the ownership has to go, not just the type.

    What the parent was doing, and who does it now:

    * *keeping the widget alive* — `_HELD` here, plus the attribute every call site already sets;
    * *destroying it with the parent* — `MainWindow.closeEvent` and `MachineLab.closeEvent` call
      `close_all`/close their own children explicitly. Without that the app cannot quit: a
      parentless window counts as a primary window, so `quitOnLastWindowClosed` keeps waiting for
      labs nobody can see any more;
    * *placing it* — `CASCADE` from the opener, since nothing centres it on the parent now.

    `title` is a convenience, and it matters more than it looks: once a window has its own taskbar
    button and its own line in the Window menu, an empty or duplicated title is what the student
    has to choose from.
    """
    anchor = None
    parent = w.parentWidget()
    if parent is not None:
        top = parent.window()
        if top is not None and top.isVisible():
            g = top.frameGeometry()
            anchor = QPoint(g.left() + CASCADE, g.top() + CASCADE)
    # The two-argument form. `setParent(None)` alone resets the flags to Qt.Widget, which would
    # un-window the thing we are here to make a window.
    w.setParent(None, _WINDOW)
    if anchor is not None:
        w.move(anchor)
    key = id(w)
    _HELD[key] = w
    # Only the key is captured, never the widget: a closure over `w` would be a strong reference
    # that outlives it and defeats the pop.
    w.destroyed.connect(lambda *_: _HELD.pop(key, None))
    if title:
        w.setWindowTitle(title)


def close_all(exclude=None) -> int:
    """Close every promoted window. Returns how many were open.

    Called when the main window closes. It is not tidiness: these windows have no Qt parent to
    destroy them and they set `WA_QuitOnClose` like any widget, so Qt counts them as primary
    windows and `quitOnLastWindowClosed` will not fire while one is still up. Without this,
    closing gBuilder leaves the process running behind a lab window.
    """
    n = 0
    for w in list(_HELD.values()):
        if w is exclude:
            continue
        try:
            if w.isVisible():
                w.close()
                n += 1
        except RuntimeError:
            _HELD.pop(id(w), None)            # already gone; nothing to close
    return n


def open_windows(exclude=None) -> list:
    """Every visible top-level window, as `[(title, widget), …]` sorted by title.

    Read from `QApplication.topLevelWidgets()` on demand rather than from a registry we maintain.
    A registry would have to be kept correct across close, retire and `deleteLater`, and getting
    that wrong is how you end up with a menu entry that raises when clicked. Qt already knows the
    answer; asking it cannot go stale.

    Menus, tooltips and popups are top-level too, so anything without the `Qt.Window` bit or
    without a title is skipped.
    """
    from PySide6.QtWidgets import QApplication

    out = []
    for w in QApplication.topLevelWidgets():
        if w is exclude:
            continue
        try:
            if not w.isVisible() or not (w.windowFlags() & Qt.Window):
                continue
            title = w.windowTitle()
        except RuntimeError:
            continue                      # the C++ side is already gone; not a window any more
        if title:
            out.append((title, w))
    out.sort(key=lambda pair: pair[0].lower())
    return out


def focus(w) -> bool:
    """Bring one window to the front. False if it has gone away since the menu was built.

    Four steps, and Windows needs all four — this is where macOS and Windows part company, and
    testing it on a Mac proves nothing:

    * `isVisible()` first. It will NOT re-show a window that has been closed. A lab can close
      itself between the menu being built and the entry being clicked, and `show()` on a
      closed-but-not-yet-destroyed lab brings back a husk: `stop_polling()` has already run and its
      workers are joined, so it would sit there showing whatever was on screen when it closed and
      never update again. Refusing is honest — the menu is rebuilt every time it opens.
    * **`WindowActive` in the window state.** The one that was missing. On macOS `raise_()` is
      enough and this looks redundant; on Windows 11 the entry did nothing at all without it.
      Clearing `WindowMinimized` in the same call is what restores a minimised window, since a
      minimised window ignores `raise_()`.
    * `show()`, safe precisely because the visibility check above already passed, so it can only
      re-show a window that is already up. It is what makes Windows re-evaluate the window state
      just set.
    * `raise_()` then `activateWindow()` — z-order, then keyboard focus.

    `activateWindow()` may still be refused: Windows only lets the process that owns the foreground
    window, or one that just received input, steal focus. A menu click satisfies that, but if it
    ever does not, Windows flashes the taskbar button instead — which still tells the student where
    the window went.
    """
    try:
        if not w.isVisible():
            return False                  # closed since the menu was built; do not resurrect it
        w.setWindowState((w.windowState() & ~Qt.WindowMinimized) | Qt.WindowActive)
        w.show()
        w.raise_()
        w.activateWindow()
        return True
    except RuntimeError:
        return False                      # the C++ side is gone; nothing to focus
