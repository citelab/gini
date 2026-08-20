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


def _setup_preflight() -> None:
    """Soft check: Demo mode always works; live Run needs the container runtime + images from
    `gini-setup`. We only nudge here — never block — so the app is explorable immediately."""
    try:
        from . import __version__
        from .setup import marker
        if not marker.is_setup_done():
            print("[gini] Runtime not set up yet — Demo mode works now. To enable live Run: "
                  "`gini-setup` (installs the runtime + pulls images), or from a source "
                  "checkout `gini-setup --build` (builds the images locally).")
        elif marker.needs_update(__version__):
            print(f"[gini] App is {__version__} but images were set up for {marker.setup_version()} "
                  "— run `gini-setup --update` to refresh them.")
    except Exception:
        pass


def main() -> int:
    args = set(sys.argv[1:])
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
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

    _setup_preflight()                             # non-blocking: Demo always works
    win.show()
    from PySide6.QtCore import QTimer
    QTimer.singleShot(450, win.maybe_start_tour)   # feature tour, once the window is painted
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
