"""Process Fingerprints — behavioral-signature view over the Machine Lab's real telemetry.

  • Explore — a radar (signature) for the selected process + a scatter BOARD where programs self-sort
    into CPU-bound vs IO-bound / compute vs kernel.
  • Classify game — the shared DiagnoseGameWidget (domain/diagnose.py) fed by the process case source:
    a mystery radar with the name hidden; guess the class, scored by a confusion matrix vs the oracle.

Live: a FingerprintAccumulator ingests procdump states + the syscall/trap rings each poll. Demo: the
canned demo_features(), so the panel and the game work fully offline.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QTabWidget, QVBoxLayout, QWidget,
)

from ..domain.fingerprint import FingerprintAccumulator, demo_features, fingerprint, scatter_xy
from ..domain.games.process_game import PROCESS_SPEC, demo_cases, live_cases
from ..domain.xv6 import parse_sctrace, parse_traptrace
from .diagnose_game import DiagnoseGameWidget
from .game_renderers import RadarChart, ScatterBoard
from .theme import ThemeManager, icons
from .theme.manager import scale_css as _scss

_PALETTE = ["blue", "green", "purple", "amber", "teal", "pink", "orange", "indigo", "red", "cyan"]


class FingerprintLab(QWidget):
    refreshed = Signal()

    def __init__(self, parent, theme: ThemeManager, device, state, live=False) -> None:
        super().__init__(parent)
        self.setWindowFlag(Qt.Window, True)
        self.theme = theme
        self.device = device
        self.state = state
        self.live = live
        self._closed = False
        self._busy = False
        self._acc = FingerprintAccumulator()
        self._seen_sc: set = set()
        self._seen_tr: set = set()

        t = theme.theme
        self.setWindowTitle(f"Process Fingerprints Lab — {device.name}")
        self.resize(780, 620)
        self.setStyleSheet(f"QWidget{{background:{t.bg};}}")
        root = QVBoxLayout(self)

        head = QHBoxLayout()
        ic = QLabel(); ic.setPixmap(icons.render_pixmap("metrics", t.accent_for("purple"), 22))
        title = QLabel(f"  Process Fingerprints Lab — {device.name}")
        title.setStyleSheet(_scss(f"color:{t.text};font-size:16px;font-weight:600;"))
        head.addWidget(ic); head.addWidget(title); head.addStretch(1)
        self._reset_btn = QPushButton("Reset")
        self._reset_btn.setStyleSheet(self._btn_css())
        self._reset_btn.clicked.connect(self._reset)
        head.addWidget(self._reset_btn)
        chip = QLabel("live" if live else "offline demo")
        chip.setStyleSheet(f"color:{t.success if live else t.muted};background:{t.panel2};"
                           f"border:1px solid {t.line};border-radius:9px;padding:2px 10px;"
                           "font-size:11px;")
        head.addWidget(chip)
        root.addLayout(head)

        self._tabs = QTabWidget()
        self._tabs.setStyleSheet(
            f"QTabWidget::pane{{border:1px solid {t.line};border-radius:8px;}}"
            f"QTabBar::tab{{background:{t.panel2};color:{t.muted};padding:6px 14px;"
            f"border:1px solid {t.line};border-bottom:none;border-top-left-radius:7px;"
            "border-top-right-radius:7px;margin-right:2px;}"
            f"QTabBar::tab:selected{{color:{t.text};background:{t.panel};}}")
        root.addWidget(self._tabs, 1)
        self._build_explore()
        # the classify game is the shared engine, fed by the process case source (live or demo)
        self._game = DiagnoseGameWidget(theme, PROCESS_SPEC, self._game_source, RadarChart(theme),
                                        live=self.live)
        self._tabs.addTab(self._game, "Classify game")

        self.refreshed.connect(self._refresh_views)
        self._poll = QTimer(self); self._poll.timeout.connect(self._tick)
        self._refresh_views()
        if self.live:
            self._poll.start(1200)

    # -- styling ----------------------------------------------------------- #
    def _btn_css(self) -> str:
        t = self.theme.theme
        return (f"QPushButton{{color:{t.text};background:{t.panel2};border:1px solid {t.line};"
                "border-radius:7px;padding:4px 12px;}"
                f"QPushButton:hover{{border-color:{t.accent};}}")

    def _lbl(self, text) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(_scss(f"color:{self.theme.theme.muted};font-size:11px;font-weight:600;"))
        return lbl

    # -- Explore tab ------------------------------------------------------- #
    def _build_explore(self) -> None:
        t = self.theme.theme
        page = QWidget(); lay = QHBoxLayout(page)
        left = QVBoxLayout()
        left.addWidget(self._lbl("processes"))
        self._list = QListWidget()
        self._list.setStyleSheet(
            f"QListWidget{{background:{t.panel};color:{t.text};border:1px solid {t.line};"
            "border-radius:8px;font-size:12px;padding:3px;}"
            f"QListWidget::item:selected{{background:{t.panel2};color:{t.text};}}")
        self._list.setFixedWidth(150)
        self._list.currentRowChanged.connect(lambda _r: self._draw_radar())
        left.addWidget(self._list, 1)
        lay.addLayout(left)

        mid = QVBoxLayout()
        mid.addWidget(self._lbl("signature  ·  radar"))
        self._radar = RadarChart(self.theme)
        mid.addWidget(self._radar, 1)
        lay.addLayout(mid, 1)

        right = QVBoxLayout()
        right.addWidget(self._lbl("behavior map  ·  all processes"))
        self._board = ScatterBoard(self.theme)
        right.addWidget(self._board, 1)
        lay.addLayout(right, 1)
        self._tabs.addTab(page, "Explore")

    # -- data -------------------------------------------------------------- #
    def _tick(self) -> None:
        if self._closed or self._busy:
            return
        import threading
        threading.Thread(target=self._accumulate, daemon=True).start()

    def _accumulate(self) -> None:
        self._busy = True
        try:
            self.state.refresh()
            procs = self.state.latest.procs if self.state.latest else []
            sc = self._new_events(self._read("sc"), parse_sctrace, self._seen_sc,
                                  lambda e: (e.pid, e.num, e.a0, e.ret))
            tr = self._new_events(self._read("traps"), parse_traptrace, self._seen_tr,
                                  lambda e: (e.pid, e.kind, e.epc, e.tval))
            self._acc.observe(procs, sc, tr, dt=1.2)
            if not self._closed:
                self.refreshed.emit()
        except (Exception, RuntimeError):
            pass
        finally:
            self._busy = False

    def _read(self, method) -> str:
        fn = getattr(self.state.provider, method, None)
        try:
            return fn() if callable(fn) else ""
        except Exception:
            return ""

    @staticmethod
    def _new_events(text, parse, seen, sig):
        out = []
        for e in parse(text):
            s = sig(e)
            if s not in seen:
                seen.add(s); out.append(e)
        if len(seen) > 4000:
            seen.clear()
        return out

    def _collect(self) -> dict:
        """{pid: (name, fp)} — accumulator (live) or the canned demo set (offline)."""
        if self.live:
            return {pid: (self._acc.feats[pid].name or f"pid{pid}", fp)
                    for pid, fp in self._acc.fingerprints().items()}
        return {f.pid: (f.name, fingerprint(f)) for f in demo_features()}

    def _game_source(self) -> list:
        """Cases for the classify game — live fingerprints when running, else the demo deck."""
        if self.live:
            fps = self._acc.fingerprints()
            names = {pid: self._acc.feats[pid].name for pid in fps}
            return live_cases(fps, names)
        return demo_cases()

    def _color(self, i) -> str:
        return _PALETTE[i % len(_PALETTE)]

    def _refresh_views(self) -> None:
        data = list(self._collect().items())
        cur = self._list.currentRow()
        self._list.blockSignals(True)
        self._list.clear()
        for pid, (name, _fp) in data:
            QListWidgetItem(f"{name}  ·  {pid}", self._list)
        self._list.setCurrentRow(cur if 0 <= cur < len(data) else (0 if data else -1))
        self._list.blockSignals(False)
        self._draw_radar()
        pts = [(*scatter_xy(fp), name, self._color(i))
               for i, (pid, (name, fp)) in enumerate(data)]
        self._board.set_points(pts)

    def _draw_radar(self) -> None:
        data = list(self._collect().items())
        row = self._list.currentRow()
        if 0 <= row < len(data):
            _pid, (name, fp) = data[row]
            self._radar.set_series([(name, fp, self._color(row))])
        else:
            self._radar.set_series([])

    def _reset(self) -> None:
        self._acc.reset(); self._seen_sc.clear(); self._seen_tr.clear()
        self._game._reset()
        self._refresh_views()

    def closeEvent(self, e) -> None:  # noqa: N802
        self._closed = True
        self._poll.stop()
        super().closeEvent(e)
