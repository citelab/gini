"""The User Code Lab — one tile per A-Lab, and the place a machine's assignment is chosen.

Before this existed there was no place to choose. `xv6_lab.active_spec()` answered "which
assignment?" with "the only one, if there is exactly one" — so shipping a SECOND assignment made
the User Code face disappear for both, silently, with the YAML sitting right there on disk. The
hub is the choice that rule was standing in for.

**One assignment is armed per MACHINE**, and that comes from the kernel rather than from here:
two syscall assignments both edit `syscall.h`, `syscall.c`, `sysproc.c`, `user.h` and `usys.pl`,
so two of them wired into one tree collide in all five. Across machines there is no collision and
none is invented — M1 on A-Lab 01 while M2 is on A-Lab 02 is a thing a student may do, and the
hub opened from M2 shows M2's answer. See `docs/design/user-code-lab.md`.

Switching is never destructive. Each assignment keeps its own folder under the machine's lab
directory, so coming back to one left a fortnight ago finds it as it was, with Revert still
anchored to the right originals.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from ..domain import lab_spec as _ls
from ..services import xv6_lab as _lab
from .machine_lab import LayerCard
from .theme import ThemeManager
from .theme.manager import scale_css as _scss
from .windowing import standalone

#: The accents a tile may wear, in the order they are handed out.
#:
#: Measured, not chosen by eye: these are the hues that stay at least 26 ΔE (CIE76) apart from
#: EVERY other hue in the list, in ALL SEVEN themes. Seven is the ceiling — there is no eight-hue
#: set with that property, which is worth knowing before anyone adds a ninth assignment and
#: wonders why two tiles match.
#:
#: `slate` is last on purpose. It is the one that reads as "disabled" rather than as a colour, and
#: every valid seven-set contains it — so it is unavoidable at seven tiles and unused below that.
LAB_HUES = ("red", "blue", "green", "purple", "amber", "pink", "slate")


def hue_for(spec, index: int) -> str:
    """The tile's accent: the spec's own if it pinned one, otherwise by position."""
    return getattr(spec, "hue", "") or LAB_HUES[index % len(LAB_HUES)]


class UserCodeLab(QDialog):
    """The hub. Tiles come from `lab_spec.catalog()`, so shipping an assignment is shipping a
    YAML — nothing here enumerates them."""

    def __init__(self, parent, theme: ThemeManager, device=None, provider=None,
                 recorder=None, live: bool = False, on_log=None, on_relink=None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.device = device
        self.provider = provider
        self.live = bool(live)
        self._recorder = recorder
        self.on_log = on_log
        # Supplied by whoever owns the orchestrator, because this window does not and should not.
        # Re-linking is an exec into a running container; the lab UI has no business holding the
        # engine. Same split as `on_console`.
        self._on_relink = on_relink
        self._panel = None
        self._cards: dict = {}

        t = theme.theme
        # A real window, not an owned dialog: a student leaves this open beside the canvas and
        # alt-tabs back to it, the same as the per-assignment panel it opens.
        standalone(self, f"User Code Lab — {self._machine() or 'xv6'}")
        self.resize(940, 560)
        self.setStyleSheet(f"QDialog{{background:{t.bg};}}")
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)
        self._build_header(root)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        body = QWidget()
        self._col = QVBoxLayout(body)
        self._col.setContentsMargins(2, 2, 2, 2)
        self._col.setSpacing(8)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)
        self._build_tiles()
        self._col.addStretch(1)
        self.refresh()

    # -- chrome -------------------------------------------------------------- #
    def _machine(self) -> str:
        return str(getattr(self.device, "name", "") or "")

    def _build_header(self, root) -> None:
        t = self.theme.theme
        row = QHBoxLayout()
        title = QLabel("Your assignments")
        title.setStyleSheet(_scss(f"color:{t.text};font-size:15px;font-weight:700;border:none;"))
        row.addWidget(title)
        row.addStretch(1)
        reveal = QPushButton("  Reveal folder")
        reveal.setStyleSheet(self._btn_css())
        reveal.clicked.connect(self._reveal)
        row.addWidget(reveal)
        root.addLayout(row)

        self._intro = QLabel()
        self._intro.setWordWrap(True)
        self._intro.setStyleSheet(_scss(f"color:{t.muted};font-size:12px;border:none;"))
        root.addWidget(self._intro)

    def _btn_css(self) -> str:
        t = self.theme.theme
        return (f"QPushButton{{color:{t.muted};background:{t.panel2};border:1px solid {t.line};"
                f"border-radius:8px;padding:4px 12px;font-size:12px;}}"
                f"QPushButton:hover{{border-color:{t.accent};color:{t.text};}}")

    def _reveal(self) -> None:
        """Open the machine's lab directory — every assignment it has, one folder each."""
        import subprocess
        import sys
        d = _lab.machine_root(self._machine())
        try:
            d.mkdir(parents=True, exist_ok=True)
            if sys.platform == "darwin":
                subprocess.Popen(["open", str(d)])
            elif sys.platform.startswith("win"):
                subprocess.Popen(["explorer", str(d)])
            else:
                subprocess.Popen(["xdg-open", str(d)])
        except Exception as e:                     # noqa: BLE001 — never take the hub down
            self._log("warn", f"could not open {d}: {e}")

    def _log(self, level: str, msg: str) -> None:
        if self.on_log:
            try:
                self.on_log(level, msg)
            except Exception:                      # noqa: BLE001
                pass

    # -- the tiles ----------------------------------------------------------- #
    def _build_tiles(self) -> None:
        t = self.theme.theme
        specs = _ls.catalog()
        if not specs:
            lbl = QLabel("No assignments are installed.")
            lbl.setStyleSheet(_scss(f"color:{t.muted};font-size:12px;border:none;"))
            self._col.addWidget(lbl)
            return

        band = QFrame()
        band.setObjectName("LabBand")
        band.setStyleSheet(f"QFrame#LabBand{{background:{t.panel2};"
                           f"border:1px solid {t.line};border-radius:12px;}}")
        v = QVBoxLayout(band)
        v.setContentsMargins(12, 8, 12, 12)
        v.setSpacing(8)
        cap = QLabel("ASSIGNMENTS")
        cap.setStyleSheet(_scss(f"color:{t.faint};font-size:10px;font-weight:700;"
                                "letter-spacing:2px;border:none;"))
        v.addWidget(cap)

        # Wrapped at three per row rather than one long row: at 6-8 tiles a single row makes every
        # card too narrow for its own summary, which is the line that tells a student what the
        # assignment IS.
        row = None
        for i, spec in enumerate(specs):
            if i % 3 == 0:
                row = QHBoxLayout()
                row.setSpacing(8)
                v.addLayout(row)
            card = LayerCard(self.theme, spec.title,
                             spec.summary or "An xv6 assignment.", hue_for(spec, i))
            card.clicked.connect(lambda s=spec: self._choose(s))
            self._cards[spec.id] = card
            row.addWidget(card)
        # Keep the last row's cards the same width as a full row's.
        for _ in range((-len(specs)) % 3):
            row.addStretch(1)
        self._col.addWidget(band)

    def refresh(self) -> None:
        """Re-read each assignment's progress on THIS machine, and light the armed one.

        Pure file reads — a few small files per tile — so this is called on open and whenever the
        per-lab panel closes, and needs no poll.
        """
        machine = self._machine()
        armed = _lab.armed_id(machine)
        for spec in _ls.catalog():
            card = self._cards.get(spec.id)
            if card is None:
                continue
            card.set_live(spec.id == armed)
            card.set_stat(self._stat_for(spec, machine))
        self._intro.setText(
            "Pick the assignment you are working on. One at a time per machine — a kernel can "
            "only carry one. Your work in the others is kept."
            if armed else
            "Pick an assignment to start. Nothing is armed on this machine yet, so the kernel "
            "is the one the image shipped with.")

    def _stat_for(self, spec, machine: str) -> str:
        """`A 6/6 · B 3/6`, or a word when there is nothing to count yet."""
        if not getattr(spec, "released", True):
            return "not released yet"
        try:
            p = _lab.progress_for(spec, machine)
        except Exception:                          # noqa: BLE001 — a tile must never raise
            return ""
        if not p.get("total"):
            return ""
        if not p.get("passed"):
            return "not started"
        parts = {k: v for k, v in (p.get("parts") or {}).items() if k}
        if parts:
            return " · ".join(f"{k} {done}/{total}" for k, (done, total) in sorted(parts.items()))
        return f"{p['passed']}/{p['total']} wired"

    # -- arming -------------------------------------------------------------- #
    def _choose(self, spec) -> None:
        """Arm this assignment on this machine, re-link the tree, and open its panel.

        Arming is written before the re-link, not after: if the exec fails (a machine that is not
        running, a container that is not taking execs yet) the student's CHOICE has still been
        recorded, so the next Run links the right assignment. The other order would lose the
        choice on exactly the machines where it is least obvious that anything went wrong.
        """
        machine = self._machine()
        if not getattr(spec, "released", True):
            # Shown but not armable. A course puts the whole term's tiles up from the start so
            # students can see what is coming; arming one with no files and no checks would open
            # an empty panel, which reads as GINI being broken rather than the lab being unwritten.
            self._log("info", f"{spec.title} is not released yet.")
            return
        already = _lab.armed_id(machine) == spec.id
        if not already:
            if not _lab.arm(machine, spec.id):
                self._log("error", f"could not arm {spec.title} on {machine}")
                return
            # Recorded because a marker reading the chain should be able to see which assignment
            # the work on each machine was for, and when the student moved between them. The
            # per-build attribution already exists (`note_build` carries the id); this is the
            # switch itself.
            from .lab_record import record
            record(self._recorder, "note_tune", machine, "assignment", "", spec.id)
            self._log("ok", f"{spec.title} is now armed on {machine}.")
            if self._on_relink and self.live:
                self._on_relink(machine, spec)
            elif self.live:
                self._log("warn", "Press Run again so the machine picks up the new assignment.")
        self.refresh()
        self._open_panel(spec)

    def _open_panel(self, spec) -> None:
        from .user_code import UserCode
        old = self._panel
        self._panel = None
        if old is not None:
            try:
                old.close()
                old.deleteLater()
            except RuntimeError:
                pass
        self._panel = UserCode(self, self.theme, device=self.device, provider=self.provider,
                               spec=spec, live=self.live, recorder=self._recorder)
        # The tile's progress is stale the moment they edit anything, so re-read on the way back.
        self._panel.finished.connect(lambda _r=0: self.refresh())
        self._panel.show()
        self._panel.raise_()
