"""CPU & Registers face of the xv6 Machine — the HARDWARE / privilege view.

Deliberately NOT a subset of the Process Scheduler (which is about *which* process runs). This is the
CPU's privilege-and-trap machinery, all read from the live kernel with no halt:

  • Mode-time bar — user / kernel / idle split of the last second, sampled at each timer tick by the
    trap entry path (usertrap=user, kerneltrap=kernel|idle). The honest 'how busy, and where'.
  • CSR / interrupt strip — the three S-mode interrupt sources (enabled + pending) from sie/sip, the
    came-from privilege (sstatus.SPP), the trap vector (stvec), and the last trap cause (scause).
  • Register tiles — the saved user register file per hart, as colored tiles grouped by role.

The momentary global interrupt bit (sstatus.SIE) reads 0 during a dump (we're inside a handler), so
the strip leans on the enable CONFIG (sie) for the honest interrupt state — see interrupt_sources().
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)

from ..domain.xv6 import (
    interrupt_sources, mode_split, scause_str, sstatus_flags,
)
from .theme import ThemeManager, icons
from .theme.manager import scale_css as _scss

# register tiles grouped by role -> (row of reg names, accent key)
_REG_GROUPS = [
    ("instruction", ["pc"], "blue"),
    ("stack", ["sp", "s0"], "amber"),
    ("return", ["ra"], "purple"),
    ("arguments", ["a0", "a7"], "green"),
    ("paging", ["satp"], "red"),
    ("mem size", ["sz"], "slate"),
]


def _decode_satp(satp: str) -> str:
    """Sv39 satp -> mode + physical page-table root (PPN << 12)."""
    try:
        v = int(satp, 16)
    except (TypeError, ValueError):
        return ""
    mode = (v >> 60) & 0xF
    ppn = v & ((1 << 44) - 1)
    return {0: "Bare", 8: "Sv39", 9: "Sv48"}.get(mode, f"mode {mode}") + f" · root {hex(ppn << 12)}"


class SegmentBar(QWidget):
    """A horizontal proportional bar: [(label, fraction, color)] painted end to end."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._segs: list = []
        self.setMinimumHeight(30)

    def set_segments(self, segs) -> None:
        self._segs = [s for s in segs if s[1] > 0]
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        x = 0.0
        for label, frac, color in self._segs:
            seg_w = w * frac
            p.setBrush(QColor(color))
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(int(x) + 1, 0, int(seg_w) - 2, h, 5, 5)
            if seg_w > 46:                       # only label a wide-enough segment
                p.setPen(QColor("#ffffff"))
                p.drawText(int(x), 0, int(seg_w), h, Qt.AlignCenter,
                           f"{label} {round(frac * 100)}%")
            x += seg_w


class CpuLab(QWidget):
    snap_ready = Signal(object)

    def __init__(self, parent, theme: ThemeManager, device, state, live=False) -> None:
        super().__init__(parent)
        self.setWindowFlag(Qt.Window, True)
        self.theme = theme
        self.device = device
        self.state = state
        self.live = live
        self._closed = False
        self._busy = False
        self._prev_modetime: dict | None = None

        t = theme.theme
        self.setWindowTitle(f"CPU & Registers — {device.name}")
        self.resize(860, 620)
        self.setStyleSheet(f"QWidget{{background:{t.bg};}}")
        root = QVBoxLayout(self)
        root.setSpacing(12)

        head = QHBoxLayout()
        ic = QLabel(); ic.setPixmap(icons.render_pixmap("host", t.accent_for("red"), 22))
        title = QLabel(f"  CPU & Registers — {device.name}")
        title.setStyleSheet(_scss(f"color:{t.text};font-size:16px;font-weight:600;"))
        head.addWidget(ic); head.addWidget(title); head.addStretch(1)
        mode = QLabel("live" if live else "offline demo")
        mode.setStyleSheet(
            f"color:{t.success if live else t.muted};background:{t.panel2};"
            f"border:1px solid {t.line};border-radius:9px;padding:2px 10px;font-size:11px;")
        head.addWidget(mode)
        root.addLayout(head)

        # 1) mode-time bar
        self._bar = SegmentBar()
        self._bar_note = QLabel()
        self._bar_note.setStyleSheet(_scss(f"color:{t.muted};font-size:11px;"))
        root.addWidget(self._panel("Where the CPU spends time  ·  user / kernel / idle (last ~1s)",
                                   self._bar, self._bar_note))

        # 2) CSR / interrupt strip
        self._csr_box = QWidget()
        self._csr_lay = QHBoxLayout(self._csr_box)
        self._csr_lay.setContentsMargins(0, 0, 0, 0)
        self._csr_lay.setSpacing(6)
        self._csr_note = QLabel()
        self._csr_note.setStyleSheet(_scss(f"color:{t.muted};font-size:11px;"))
        root.addWidget(self._panel("Traps & interrupts  ·  control CSRs (this hart)",
                                   self._csr_box, self._csr_note))

        # 3) register tiles (per core)
        self._tiles_box = QWidget()
        self._tiles_lay = QVBoxLayout(self._tiles_box)
        self._tiles_lay.setContentsMargins(0, 0, 0, 0)
        self._tiles_lay.setSpacing(10)
        root.addWidget(self._panel("Register file  ·  saved user context, per core",
                                   self._tiles_box), 1)
        root.addStretch(0)

        self.snap_ready.connect(self._on_snap)
        self._poll = QTimer(self)
        self._poll.timeout.connect(lambda: self._fetch())
        self._render()
        if self.live:
            self._poll.start(1000)
            self._fetch()

    # -- panels / chips / tiles -------------------------------------------- #
    def _panel(self, title, *inners) -> QFrame:
        t = self.theme.theme
        f = QFrame(); f.setStyleSheet(
            _scss(f"QFrame{{background:{t.panel2};border:1px solid {t.line};border-radius:10px;}}"))
        v = QVBoxLayout(f); v.setContentsMargins(12, 9, 12, 11); v.setSpacing(7)
        h = QLabel(title)
        h.setStyleSheet(_scss(f"color:{t.muted};font-size:11px;font-weight:600;border:none;"))
        v.addWidget(h)
        for inner in inners:
            inner.setStyleSheet((inner.styleSheet() or "") + "border:none;")
            v.addWidget(inner)
        return f

    def _chip(self, text, color, filled=False, dot=False) -> QLabel:
        t = self.theme.theme
        lbl = QLabel(("● " if dot else "") + text)
        if filled:
            lbl.setStyleSheet(_scss(
                f"color:#ffffff;background:{color};border-radius:8px;padding:3px 9px;"
                "font-size:11px;font-weight:600;"))
        else:
            lbl.setStyleSheet(_scss(
                f"color:{t.muted};background:{t.panel};border:1px solid {t.line};"
                "border-radius:8px;padding:3px 9px;font-size:11px;"))
        return lbl

    def _reg_tile(self, name, value, color, subtitle="") -> QFrame:
        t = self.theme.theme
        f = QFrame()
        f.setStyleSheet(_scss(
            f"QFrame{{background:{t.panel};border:1px solid {t.line};"
            f"border-left:3px solid {color};border-radius:7px;}}"))
        v = QVBoxLayout(f); v.setContentsMargins(9, 5, 9, 6); v.setSpacing(1)
        n = QLabel(name.upper())
        n.setStyleSheet(_scss(f"color:{color};font-size:10px;font-weight:700;border:none;"))
        val = QLabel(value)
        val.setStyleSheet(_scss(f"color:{t.text};font-family:monospace;font-size:13px;border:none;"))
        v.addWidget(n); v.addWidget(val)
        if subtitle:
            s = QLabel(subtitle)
            s.setStyleSheet(_scss(f"color:{t.faint};font-size:10px;border:none;"))
            v.addWidget(s)
        return f

    @staticmethod
    def _clear(layout) -> None:
        while layout.count():
            it = layout.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)

    # -- data -------------------------------------------------------------- #
    def _fetch(self) -> None:
        if self._busy or self._closed:
            return
        self._busy = True
        import threading

        def work():
            try:
                self.state.refresh()
                if not self._closed:
                    self.snap_ready.emit(None)
            except (Exception, RuntimeError):
                pass
        threading.Thread(target=work, daemon=True).start()

    def _on_snap(self, _obj) -> None:
        self._busy = False
        if not self._closed:
            self._render()

    def _render(self) -> None:
        snap = self.state.latest
        if snap is None:
            return
        t = self.theme.theme
        self._render_modebar(snap, t)
        self._render_csr(snap, t)
        self._render_tiles(snap, t)

    def _render_modebar(self, snap, t) -> None:
        if not snap.modetime:
            self._bar.set_segments([])
            self._bar_note.setText("mode-time needs the kernel rebuild (MODETIME line).")
            return
        split = mode_split(self._prev_modetime, snap.modetime)
        self._prev_modetime = dict(snap.modetime)
        self._bar.set_segments([
            ("user", split["user"], t.accent_for("green")),
            ("kernel", split["kernel"], t.accent_for("amber")),
            ("idle", split["idle"], t.accent_for("slate")),
        ])
        if sum(split.values()) == 0:
            self._bar_note.setText("sampling… (needs a second timer tick to show a delta)")
        else:
            self._bar_note.setText(
                f"user {round(split['user']*100)}%  ·  kernel {round(split['kernel']*100)}%  "
                f"·  idle {round(split['idle']*100)}%   — sampled at each timer interrupt")

    def _render_csr(self, snap, t) -> None:
        self._clear(self._csr_lay)
        csr = snap.csr
        if not csr:
            self._csr_note.setText("control CSRs need the kernel rebuild (CSR line).")
            return
        # interrupt sources: enabled -> filled+colored, pending -> a dot
        for src in interrupt_sources(csr.get("sie", 0), csr.get("sip", 0)):
            self._csr_lay.addWidget(self._chip(
                src["name"], t.accent_for("purple"),
                filled=src["enabled"], dot=src["pending"]))
        self._csr_lay.addSpacing(8)
        flags = sstatus_flags(csr.get("sstatus", 0))
        came = "user" if flags["SPP"] == "U" else "kernel"
        self._csr_lay.addWidget(self._chip(
            f"came from {came}", t.accent_for("blue"), filled=True))
        self._csr_lay.addStretch(1)
        self._csr_note.setText(
            f"trap vector (stvec) {hex(csr.get('stvec', 0))}   ·   "
            f"last trap (scause): {scause_str(csr.get('scause'))}   ·   "
            "interrupt state = enabled sources above (global SIE reads 0 mid-dump)")

    def _render_tiles(self, snap, t) -> None:
        self._clear(self._tiles_lay)
        cpu_regs = snap.cpu_regs or ({0: snap.cpu} if snap.cpu else {})
        if not cpu_regs:
            self._tiles_lay.addWidget(QLabel("no running process on any core"))
            return
        for c in sorted(cpu_regs):
            cs = cpu_regs[c]
            pid = cs.key("pid")
            sec = QFrame()
            sv = QVBoxLayout(sec); sv.setContentsMargins(0, 0, 0, 0); sv.setSpacing(6)
            hdr = QLabel(f"CPU {c}" + (f"  ·  pid {pid}" if pid not in ("—",) else "  ·  idle"))
            hdr.setStyleSheet(_scss(f"color:{t.text};font-size:12px;font-weight:600;border:none;"))
            sv.addWidget(hdr)
            grid = QGridLayout(); grid.setSpacing(7)
            col = 0
            for _group, names, key in _REG_GROUPS:
                color = t.accent_for(key)
                for name in names:
                    val = cs.key(name)
                    sub = _decode_satp(val) if name == "satp" else ""
                    grid.addWidget(self._reg_tile(name, val, color, sub), 0, col)
                    col += 1
            grid.setColumnStretch(col, 1)
            gw = QWidget(); gw.setLayout(grid)
            sv.addWidget(gw)
            self._tiles_lay.addWidget(sec)

    def closeEvent(self, e) -> None:  # noqa: N802
        self._closed = True
        self._poll.stop()
        try:
            self.snap_ready.disconnect(self._on_snap)
        except (RuntimeError, TypeError):
            pass
        super().closeEvent(e)
