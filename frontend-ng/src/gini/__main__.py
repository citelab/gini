"""Launch gBuilder.

    python -m gini                 # run the app
    python -m gini --demo          # run with a sample hybrid topology
    QT_QPA_PLATFORM=offscreen python -m gini --selftest   # headless smoke test
"""
from __future__ import annotations

import sys


def build_demo(api) -> None:
    """A small hybrid networks + cloud topology for first-run / selftest."""
    r1 = api.add_device("router", x=-260, y=-160)
    s1 = api.add_device("switch", x=-260, y=-20)
    h1 = api.add_device("host", x=-380, y=120)
    api.add_device("host", x=-160, y=120)
    api.connect(r1["name"], s1["name"])
    api.connect(s1["name"], h1["name"])

    vpc = api.add_device("vpc", x=160, y=-180)
    lb = api.add_device("load_balancer", x=160, y=-40)
    inst = api.add_device("instance", x=40, y=110)
    api.add_device("instance", x=280, y=110)
    db = api.add_device("database", x=160, y=240)
    api.connect(vpc["name"], lb["name"])
    api.connect(lb["name"], inst["name"])
    api.connect(inst["name"], db["name"])
    api.connect(r1["name"], vpc["name"], "hybrid uplink")


def _apply_branding(app) -> None:
    """Name + icon so the taskbar/dock shows the GINI mascot (not the Python launcher)."""
    from .ui.branding import app_icon, icon_path
    app.setApplicationName("gBuilder")
    app.setApplicationDisplayName("gBuilder")
    app.setOrganizationName("GINI")
    app.setWindowIcon(app_icon())
    if sys.platform == "win32":          # make Windows group + show OUR icon, not python.exe's
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("GINI.gBuilder")
        except Exception:
            pass
    elif sys.platform == "darwin":       # set the Dock icon when run from source (needs pyobjc)
        try:
            from AppKit import NSApplication, NSImage
            img = NSImage.alloc().initByReferencingFile_(icon_path())
            NSApplication.sharedApplication().setApplicationIconImage_(img)
        except Exception:
            pass                          # no pyobjc → bundle a .app for a permanent Dock icon


def _setup_preflight() -> dict | None:
    """What does this machine still need? Local and fast — no network, so it cannot delay launch.

    Returns the plan for the window to act on, or None if nothing is needed. There is no longer a
    separate `gini-setup` command to nudge people towards: a student who ran `pip install
    gini-toolkit` and typed `gbuilder` should not have to discover a second command, and the old
    message was printed to a terminal most of them never look at.
    """
    try:
        from .services import bootstrap
        from .version import __version__
        p = bootstrap.plan(__version__)
        return None if p["state"] == bootstrap.READY else p
    except Exception:                      # noqa: BLE001 — never block launch on the preflight
        return None


def launch_steps(plan: dict | None, args) -> dict:
    """What happens after the window is shown: the setup panel, the tour, or neither.

    They must not both run. The tour is a MODAL `CueCards(...).exec()` and the setup panel is a
    non-modal `show()`, so starting both put a "here are all the features" dialog in front of the
    one action that has to happen before any of those features work — and the panel underneath it
    could not even be clicked. Setup wins; the panel carries a button into the tour, so nothing is
    lost for the person who wanted it.

    Split out of `main()` so the rule is testable without a QApplication.
    """
    interactive = not ({"--demo", "--selftest"} & set(args))
    if plan and interactive:
        return {"setup": True, "tour": False}
    return {"setup": False, "tour": True}


def main() -> int:
    args = set(sys.argv[1:])
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    # QtWebEngine requires AA_ShareOpenGLContexts to be set BEFORE the application object is
    # constructed. The OS Zoo and the headful Desktop screen both embed a QWebEngineView, and both
    # import QtWebEngine LAZILY — inside the double-click handler, so a normal launch never pays
    # Chromium's start-up cost. That laziness means the import lands after QApplication exists,
    # which is the exact case this attribute covers; without it, constructing the first
    # QWebEngineView segfaults the process. Setting the attribute is cheap (a flag on
    # QCoreApplication — it does not load QtWebEngine), so it costs nothing on the launches that
    # never open a Zoo guest.
    #
    # The documented alternative is to `import PySide6.QtWebEngineWidgets` up here instead, but
    # that pulls Chromium into EVERY launch, and makes PySide6-Addons a hard requirement rather
    # than the optional extra the browser fallback in main_window is built around.
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    from .ui.app import GiniApplication          # QApplication + the ⌘Q guard, see ui/app.py
    app = QApplication.instance() or GiniApplication(sys.argv)
    _apply_branding(app)
    from .ui import MainWindow

    win = MainWindow(app)

    if "--demo" in args or "--selftest" in args:
        build_demo(win.api)

    if "--selftest" in args:
        s = win.api.summary()
        assert s["devices"] >= 9, s
        assert s["links"] >= 6, s
        explanation = win.api.explain_topology()
        assert "elements" in explanation
        # exercise theme swap + node creation paths
        win.theme.set_theme("Light")
        win.theme.set_theme("GINI Brand")
        print("SELFTEST OK:", s)
        print("EXPLAIN:", explanation)
        return 0

    if not ({"--demo", "--selftest"} & args):
        win.restore_last_project()                 # reopen last session's project

    plan = _setup_preflight()                      # local + fast; never delays the window
    win.show()
    from PySide6.QtCore import QTimer
    steps = launch_steps(plan, args)
    if steps["setup"]:
        # AFTER show(), so the panel appears over a real window rather than an empty screen — and
        # deferred, so a slow dialog construction cannot be mistaken for a slow launch.
        def _offer():
            try:
                from .ui.first_run import offer
                # on_tour: the tour is no longer launched over this panel, so the panel owns the
                # way into it. Nothing is lost; it just stops covering the thing that matters.
                win._first_run = offer(plan, win, on_tour=win.show_feature_tour)
            except Exception:                          # noqa: BLE001
                pass                                   # setup is a convenience, never a blocker
        QTimer.singleShot(700, _offer)
    if steps["tour"]:
        QTimer.singleShot(450, win.maybe_start_tour)   # feature tour, once the window is painted
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
