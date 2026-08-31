"""About GINI — what it stands for, and exactly which build is running.

Written because there was no way to answer "which version is this?" from inside gBuilder, which is
a support question in every bug report.

ONE version, deliberately. An earlier draft listed gini-core and gini-teaching-center alongside it
and warned when the toolkit and core disagreed — machinery for a failure that `pyproject.toml`
already prevents, since gini-toolkit declares `gini-core>=<release>` as a floor and pip will not
resolve a stale core beside a newer toolkit. A dependency that is correctly managed is not
something to make a user check.

Where the package is loaded FROM is shown, because that is the other half of the same question: an
editable checkout and an installed wheel look identical from the outside, and only one of them
changes when you edit a file.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel, QVBoxLayout,
)

#: The name is a joke with a point, and it is the first thing anyone asks about it.
TAGLINE = "GINI Is Not Internet"

#: Where it comes from, and on what terms. Stated because a student should know whose lab built
#: the thing they are learning on, and because "GINI" on its own is a name nobody can look up.
#:
#: No copyright line: nothing requires the notice to appear in the UI. MIT's own condition is that
#: it ship WITH the software, which the LICENSE file does.
#:
#: MIT is the stated intent. The packaging metadata does not yet agree with it — the three
#: pyproject files declare GPL-3.0-or-later — and reconciling that is its own piece of work, not
#: something to settle from a dialog box. If you are here to change this string, that is the thing
#: to check first.
HOME = "Developed at McGill University   ·   MIT Licensed"


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

        from ..version import gini_version
        ver = QLabel(f"version {gini_version()}")
        ver.setTextInteractionFlags(Qt.TextSelectableByMouse)   # so it can be pasted into a bug
        ver.setStyleSheet(
            f"font-family:ui-monospace,Menlo,monospace;font-size:13px;color:{faint};")
        root.addWidget(ver)

        home = QLabel(HOME)
        home.setStyleSheet(f"font-size:12px;color:{muted};")
        root.addWidget(home)

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
