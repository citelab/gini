"""``qt`` — the interpreter gBuilder actually runs on, whether PySide6 imports in it, whether a
QApplication can start, and the display facts Qt reads.

PySide6 is where a machine that looks fine actually fails, and the failure names a C library or a
platform plugin rather than a package, so the import error is kept verbatim.
"""
from __future__ import annotations

import os

from .. import platforms
from . import probe
from ._common import first_line, gbuilder_python

X_LIBS = ("libxcb-cursor", "libxcb-xinerama", "libxkbcommon-x11", "libEGL", "libGL")

PYSIDE_IMPORT = "import PySide6, PySide6.QtWidgets; print(PySide6.__version__)"
PYSIDE_OFFSCREEN = "from PySide6.QtWidgets import QApplication; QApplication([]); print('ok')"


@probe("qt", platforms=platforms.ALL,
       describe="the interpreter gBuilder uses, PySide6, and what Qt needs to draw")
def qt(ctx):
    py, how = gbuilder_python(ctx)
    if py is None:
        ctx.absent("gbuilder.python", "no gbuilder launcher or pipx venv found")
    else:
        ctx.ok("gbuilder.python", py)
        ctx.ok("gbuilder.python.found_by", how)
        ctx.from_result("gbuilder.python.version", ctx.run([py, "--version"], timeout=10),
                        lambda r: first_line(r.stdout or r.stderr))
        res = ctx.run([py, "-c", PYSIDE_IMPORT], timeout=30)
        ctx.from_result("pyside6", res, lambda r: first_line(r.stdout))
        res = ctx.run([py, "-c", PYSIDE_OFFSCREEN], timeout=60, env={"QT_QPA_PLATFORM": "offscreen"})
        ctx.from_result("pyside6.offscreen", res, lambda r: first_line(r.stdout))

    if ctx.platform == platforms.LINUX:
        res = ctx.run(["ldconfig", "-p"], timeout=10)
        if res.status == "absent":
            res = ctx.run(["/sbin/ldconfig", "-p"], timeout=10)
        for lib in X_LIBS:
            if res.ok:
                ctx.ok("lib.%s" % lib, sum(1 for ln in res.stdout.splitlines() if lib in ln))
            else:
                ctx.error("lib.%s" % lib, res.evidence())
    else:
        for lib in X_LIBS:
            ctx.na("lib.%s" % lib)

    if ctx.platform == platforms.WINDOWS:
        ctx.na("display")
        ctx.na("wayland")
    else:
        ctx.ok("display", os.environ.get("DISPLAY", "") or "(unset)")
        ctx.ok("wayland", os.environ.get("WAYLAND_DISPLAY", "") or "(unset)")
    ctx.ok("qpa", os.environ.get("QT_QPA_PLATFORM", "") or "(unset)")
