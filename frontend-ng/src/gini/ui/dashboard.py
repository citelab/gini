"""The analytics strip below the Console — a live GINI $ "cloud bill" + memory watch.

Every billable element you place is a rented resource (see domain/pricing.py). While a
lab runs, this panel accrues `rate/hr × elapsed` in GINI $ so students *feel* cloud
pay-as-you-go. It also shows a per-category cost breakdown, resource counts, and a
MEMORY gauge: the lab's container memory against the Docker VM's total, coloured by
pressure, with a runaway warning when some container's memory keeps climbing (the
early signal of the leak-then-OOM-sweep failure mode).
"""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)

from ..domain.pricing import CATEGORY_ORDER

# category -> theme accent-token key (for the little coloured cost chips)
_CAT_ACCENT = {"Compute": "purple", "Serverless": "pink", "Kubernetes": "cyan",
               "Networking": "blue", "Services": "teal", "Observability": "amber"}


class Dashboard(QWidget):
    """Compact, short cost/analytics panel. Driven by the main window:
    `set_estimate()` on every topology change, `start()`/`stop()` on run/stop."""

    def __init__(self, theme, parent=None) -> None:
        super().__init__(parent)
        self.theme = theme                       # ThemeManager
        self._rate = 0.0                          # GINI $/hr currently billed
        self._count = 0
        self._by_cat: dict[str, dict] = {}
        self._accrued = 0.0                      # GINI $ accrued this session
        self._running = False
        self._start: float | None = None
        self.setMaximumHeight(104)

        root = QHBoxLayout(self)
        root.setContentsMargins(14, 8, 14, 8)
        root.setSpacing(18)

        self.total_lbl = self._value("GINI $ 0.00")
        self.total_lbl.setObjectName("DashTotal")
        root.addLayout(self._metric(self.total_lbl, "session cost"))
        self.rate_lbl = self._value("0.00")
        root.addLayout(self._metric(self.rate_lbl, "GINI $ / hr"))
        self.elapsed_lbl = self._value("—")
        root.addLayout(self._metric(self.elapsed_lbl, "elapsed"))
        self.count_lbl = self._value("0")
        root.addLayout(self._metric(self.count_lbl, "resources"))
        self.rps_lbl = self._value("—")
        root.addLayout(self._metric(self.rps_lbl, "req/s · lab"))
        self.mem_lbl = self._value("—")     # keeps #DashValue styling; colour via inline override
        self.mem_lbl.setToolTip("Lab container memory / Docker VM total")
        root.addLayout(self._metric(self.mem_lbl, "memory"))

        sep = QFrame(); sep.setFrameShape(QFrame.VLine); sep.setObjectName("DashSep")
        root.addWidget(sep)

        # cost breakdown by category (coloured chips)
        bd = QVBoxLayout(); bd.setSpacing(2)
        bd.addWidget(self._caption("COST BREAKDOWN"))
        self.cats_lbl = QLabel("—")
        self.cats_lbl.setTextFormat(Qt.RichText)
        self.cats_lbl.setObjectName("DashChips")
        bd.addWidget(self.cats_lbl)
        root.addLayout(bd)

        root.addStretch(1)

        right = QVBoxLayout(); right.setSpacing(4); right.setAlignment(Qt.AlignVCenter)
        self.bill_lbl = self._caption("● not running — projected rate")
        self.bill_lbl.setObjectName("DashState")
        right.addWidget(self.bill_lbl, alignment=Qt.AlignRight)
        # runaway-memory warning (hidden until the watchdog flags a container)
        self.mem_warn_lbl = QLabel("")
        self.mem_warn_lbl.setObjectName("DashMemWarn")
        self.mem_warn_lbl.setTextFormat(Qt.RichText)
        self.mem_warn_lbl.hide()
        right.addWidget(self.mem_warn_lbl, alignment=Qt.AlignRight)
        root.addLayout(right)

        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._on_tick)

        if hasattr(theme, "themeChanged"):
            theme.themeChanged.connect(self._restyle)
        self._restyle()
        self._render()

    # -- small builders ----------------------------------------------------- #
    def _value(self, text: str) -> QLabel:
        lbl = QLabel(text); lbl.setObjectName("DashValue")
        return lbl

    def _caption(self, text: str) -> QLabel:
        lbl = QLabel(text); lbl.setObjectName("DashCaption")
        return lbl

    def _metric(self, value: QLabel, caption: str) -> QVBoxLayout:
        box = QVBoxLayout(); box.setSpacing(1)
        box.addWidget(value)
        box.addWidget(self._caption(caption))
        return box

    # -- public API (called by the main window) ----------------------------- #
    def set_estimate(self, bill: dict) -> None:
        """Update the projected rate/breakdown from the current canvas (idle preview)."""
        self._rate = float(bill.get("rate_per_hr", 0.0))
        self._count = int(bill.get("count", 0))
        self._by_cat = dict(bill.get("by_category", {}))
        self._render()

    def reset(self) -> None:
        """Zero the session meter — used when a different project is loaded so the sticker
        doesn't carry the previous experiment's accrued bill."""
        self._accrued = 0.0
        self._running = False
        self._start = None
        self._tick.stop()
        self._render()

    def start(self, rate_per_hr: float) -> None:
        """Begin billing: reset the meter and accrue at `rate_per_hr`."""
        self._rate = float(rate_per_hr)
        self._accrued = 0.0
        self._running = True
        self._start = time.monotonic()
        if not self._tick.isActive():
            self._tick.start()
        self._render()

    def stop(self) -> None:
        """Freeze the meter at the final session cost (kept on screen until next run)."""
        if self._running and self._start is not None:
            self._accrued = self._rate * (time.monotonic() - self._start) / 3600.0
        self._running = False
        self._tick.stop()
        self._render()

    def set_memory(self, used_mib, vm_mib, runaways=None) -> None:
        """Fleet memory gauge + runaway warning (fed by MainWindow's memory watchdog).

        used_mib: sum of all lab containers' memory; vm_mib: the Docker VM's total;
        runaways: [Runaway] — containers whose memory keeps climbing. Pass used_mib
        None to clear (lab stopped)."""
        t = self.theme.theme
        if used_mib is None:
            self.mem_lbl.setText("—")
            self.mem_lbl.setStyleSheet("")
            self.mem_warn_lbl.hide()
            return
        if vm_mib:
            pct = 100.0 * used_mib / vm_mib
            self.mem_lbl.setText(f"{used_mib / 1024:.1f}/{vm_mib / 1024:.0f} GB")
            self.mem_lbl.setToolTip(
                f"Lab containers use {used_mib:.0f} MiB of the Docker VM's "
                f"{vm_mib:.0f} MiB ({pct:.0f}%)")
            col = (t.accent_for("red") if pct >= 90
                   else t.accent_for("amber") if pct >= 75 else t.accent_for("green"))
            self.mem_lbl.setStyleSheet(f"color: {col};")
        else:
            self.mem_lbl.setText(f"{used_mib / 1024:.1f} GB")
            self.mem_lbl.setStyleSheet("")
        if runaways:
            r = runaways[0]
            more = f" (+{len(runaways) - 1} more)" if len(runaways) > 1 else ""
            self.mem_warn_lbl.setText(
                f'<span style="color:{t.accent_for("red")};font-weight:700">'
                f'⚠ {r.svc} memory rising ~{r.slope_mib_per_min:.0f} MiB/min{more}</span>')
            self.mem_warn_lbl.setToolTip(
                "Sustained memory growth — possible runaway/leak. Check the container "
                "(its console, processes, or logs) before the VM runs out and the OOM "
                "killer takes the lab down.")
            self.mem_warn_lbl.show()
        else:
            self.mem_warn_lbl.hide()

    def set_fabric(self, totals: dict) -> None:
        """Lab-wide app metrics from the cloud fabric (request rate, services up)."""
        if totals and totals.get("services_total"):
            rps = totals.get("rps", 0.0)
            up, tot = totals.get("services_up", 0), totals.get("services_total", 0)
            self.rps_lbl.setText(f"{rps:,.0f}")
            self.rps_lbl.setToolTip(f"{up}/{tot} services up")
        else:
            self.rps_lbl.setText("—")

    # -- internals ---------------------------------------------------------- #
    def _on_tick(self) -> None:
        if self._running and self._start is not None:
            self._accrued = self._rate * (time.monotonic() - self._start) / 3600.0
        self._render()

    @staticmethod
    def _fmt_elapsed(secs: float) -> str:
        s = int(secs)
        h, rem = divmod(s, 3600)
        m, s = divmod(rem, 60)
        return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

    def _render(self) -> None:
        self.total_lbl.setText(f"GINI $ {self._accrued:,.2f}")
        self.rate_lbl.setText(f"{self._rate:,.2f}")
        self.count_lbl.setText(str(self._count))
        if self._running and self._start is not None:
            self.elapsed_lbl.setText(self._fmt_elapsed(time.monotonic() - self._start))
            self.bill_lbl.setText("● billing — accruing live")
        else:
            self.elapsed_lbl.setText("—")
            self.bill_lbl.setText("○ not running — projected rate")

        t = self.theme.theme
        chips = []
        for cat in CATEGORY_ORDER:
            d = self._by_cat.get(cat)
            if not d:
                continue
            col = t.accent_for(_CAT_ACCENT.get(cat, "blue"))
            chips.append(
                f'<span style="color:{col};font-weight:600">{cat}</span> '
                f'<span style="color:{t.text}">{d["rate"]:,.0f}</span>'
                f'<span style="color:{t.faint}">/hr·{d["count"]}</span>')
        self.cats_lbl.setText(
            ' &nbsp;&nbsp; '.join(chips) if chips
            else '<span style="color:%s">no billable resources yet</span>' % t.faint)

    def _restyle(self) -> None:
        t = self.theme.theme
        from .theme.manager import sp
        self.setStyleSheet(f"""
            QLabel#DashValue {{ color: {t.text}; font-size: {sp(17)}px; font-weight: 700; }}
            QLabel#DashTotal {{ color: {t.accent}; font-size: {sp(18)}px; font-weight: 800; }}
            QLabel#DashCaption {{ color: {t.faint}; font-size: {sp(9)}px;
                                  letter-spacing: 0.4px; text-transform: uppercase; }}
            QLabel#DashChips {{ font-size: {sp(12)}px; }}
            QLabel#DashState {{ color: {t.muted}; font-size: {sp(10)}px; }}
            QLabel#DashMemWarn {{ font-size: {sp(11)}px; }}
            QFrame#DashSep {{ color: {t.line2}; }}
        """)
        self._render()
