"""About GINI — what it stands for, and exactly which build is running.

Written because there was no way to answer "which version is this?" from inside gBuilder. That is a
support question in every bug report, and the honest answer needs more than one number: `gini` is a
namespace package shared by THREE distributions, and they are installed separately. A toolkit newer
than its core is a real failure mode — it produced a `ModuleNotFoundError: No module named
'gini.services'` in a teacher's marking window — and a single version string would have hidden it.

So all three are listed, and disagreement is called out rather than left to be noticed. Where the
package is loaded FROM is shown too, because "am I running the checkout or the wheel?" is the other
half of the same question, and an editable install looks identical from the outside.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel, QVBoxLayout,
)

#: The name is a joke with a point, and it is the first thing anyone asks about it.
TAGLINE = "GINI Is Not Internet"

#: The distributions that make up an install, in dependency order.
DISTRIBUTIONS = ("gini-toolkit", "gini-core", "gini-teaching-center")


def versions() -> dict:
    """Each installed distribution's version, or "" for one that is not installed.

    The Teaching Center is legitimately absent on a student's machine, so a missing entry is
    reported as missing rather than as an error.
    """
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _v
    out = {}
    for dist in DISTRIBUTIONS:
        try:
            out[dist] = str(_v(dist))
        except (PackageNotFoundError, Exception):        # noqa: BLE001
            out[dist] = ""
    return out


def mismatch(vers: dict) -> str:
    """A warning when the installed pieces disagree, or "".

    Only the two that must move together are compared: gini-toolkit and gini-core share the `gini`
    namespace, and a toolkit expecting a newer core fails at IMPORT time, in front of whoever is
    using it. The Teaching Center is a separate server and may lag freely.
    """
    tk, core = vers.get("gini-toolkit", ""), vers.get("gini-core", "")
    if tk and core and tk != core:
        return (f"gini-toolkit {tk} and gini-core {core} are not the same build. They share the "
                f"`gini` namespace, so a mismatch can fail at import. Upgrade both.")
    return ""


def where() -> str:
    """Which copy of the package is actually loaded — the checkout or an installed wheel."""
    try:
        import gini.ui as _ui
        path = str(getattr(_ui, "__file__", "") or "")
        return path.rsplit("/gini/ui/", 1)[0] or path
    except Exception:                                    # noqa: BLE001
        return ""


class AboutDialog(QDialog):
    """Small, and deliberately full of the things a bug report needs."""

    def __init__(self, parent=None, theme=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("About GINI")
        self.setModal(True)
        t = getattr(theme, "theme", None)
        muted = getattr(t, "muted", "#6b7280")
        faint = getattr(t, "faint", "#9aa4b2")
        accent = getattr(t, "accent", "#b45309")

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 18)
        root.setSpacing(10)

        head = QHBoxLayout()
        try:
            from .branding import app_icon
            mark = QLabel()
            mark.setPixmap(app_icon().pixmap(48, 48))
            head.addWidget(mark)
        except Exception:                                # noqa: BLE001 — an icon is a nicety
            pass
        title = QLabel("GINI")
        title.setStyleSheet(f"font-size:26px;font-weight:800;color:{accent};")
        head.addWidget(title)
        head.addStretch(1)
        root.addLayout(head)

        sub = QLabel(TAGLINE)
        sub.setStyleSheet(f"font-size:15px;font-weight:600;color:{muted};")
        root.addWidget(sub)

        blurb = QLabel(
            "A graphical network and operating-system laboratory: build a topology on the "
            "canvas, run it on real containers, and watch what the kernel actually does.")
        blurb.setWordWrap(True)
        blurb.setStyleSheet(f"font-size:12px;color:{faint};")
        root.addWidget(blurb)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        root.addWidget(line)

        vers = versions()
        for dist in DISTRIBUTIONS:
            v = vers[dist]
            row = QLabel(f"{dist}   {v or 'not installed'}")
            row.setTextInteractionFlags(Qt.TextSelectableByMouse)   # so it can be pasted into a bug
            row.setStyleSheet(
                f"font-family:ui-monospace,Menlo,monospace;font-size:12px;"
                f"color:{faint if v else muted};")
            root.addWidget(row)

        warn = mismatch(vers)
        if warn:
            w = QLabel(warn)
            w.setWordWrap(True)
            w.setStyleSheet("font-size:12px;font-weight:700;"
                            f"color:{getattr(t, 'warning', '#b45309')};")
            root.addWidget(w)

        loc = where()
        if loc:
            p = QLabel(loc)
            p.setWordWrap(True)
            p.setTextInteractionFlags(Qt.TextSelectableByMouse)
            p.setStyleSheet(f"font-size:11px;color:{muted};"
                            "font-family:ui-monospace,Menlo,monospace;")
            root.addWidget(p)

        env = []
        try:
            import platform
            env.append(f"Python {platform.python_version()}")
        except Exception:                                # noqa: BLE001
            pass
        try:
            from PySide6 import __version__ as _qtv
            env.append(f"PySide6 {_qtv}")
        except Exception:                                # noqa: BLE001
            pass
        if env:
            e = QLabel("   ·   ".join(env))
            e.setStyleSheet(f"font-size:11px;color:{muted};")
            root.addWidget(e)

        root.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        root.addWidget(buttons)
        self.setMinimumWidth(420)
