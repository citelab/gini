"""Multicast HUD — a toggle-on live view of the network's multicast state.

A glass panel (upper-right of the canvas) showing what the routers' multicast
forwarders (``mcast_tree.lua``) are actually doing: every active group as a clickable
**chip**; for the selected group, one row per router with a badge for each member
interface and its live replication rate (copies/s, with an activity bar), so you can
watch a receiver's join graft a branch and the carousel's copies start flowing. A
ticker along the bottom shows the most recent join/leave events.

Pure rendering over a ``McastTracker`` (domain/mcast.py); the controller polls
``gpipe cp status`` on every router and feeds the tracker. See the Multicast File
Distribution capstone in the "Network Multicasting" chapter.
"""
from __future__ import annotations

import time

from PySide6.QtCore import QObject, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..domain.mcast import McastTracker, parse_cp_status
from .glass import apply_glass, paint_glass_panel

_CHIP_H = 22
_ROW_H = 26


class McastHud(QWidget):
    def __init__(self, parent, theme) -> None:
        super().__init__(parent)
        self.theme = theme
        self._tracker: McastTracker | None = None
        self._selected: str | None = None
        self._chip_rects: list[tuple[QRectF, str]] = []
        self.resize(460, 340)
        self.setMouseTracking(True)
        apply_glass(self)

    def set_tracker(self, tracker: McastTracker) -> None:
        self._tracker = tracker
        groups = tracker.groups() if tracker else []
        if self._selected not in groups:
            self._selected = groups[0] if groups else None
        self.update()

    # -- paint ------------------------------------------------------------- #
    def paintEvent(self, _e) -> None:  # noqa: N802
        t = self.theme.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        paint_glass_panel(p, self.rect(), self.theme, "MULTICAST")

        groups = self._tracker.groups() if self._tracker else []
        if not groups:
            p.setPen(QColor(t.faint))
            p.drawText(self.rect(), Qt.AlignCenter,
                       "— no multicast state —\nload mcast_tree.lua on the routers\n"
                       "(gpipe cp add lua /scripts/mcast_tree.lua)")
            return

        # group chips (wrap) ----------------------------------------------
        self._chip_rects = []
        x, y = 12, 28
        p.setFont(QFont(self.font().family(), 8, QFont.Bold))
        for g in groups:
            w = p.fontMetrics().horizontalAdvance(g) + 18
            if x + w > self.width() - 12:
                x = 12; y += _CHIP_H + 4
            r = QRectF(x, y, w, _CHIP_H)
            sel = (g == self._selected)
            p.setBrush(QColor(t.accent) if sel else QColor(t.panel2))
            p.setPen(QColor(t.accent) if sel else QColor(t.line))
            p.drawRoundedRect(r, 8, 8)
            p.setPen(QColor("#ffffff") if sel else QColor(t.text))
            p.drawText(r, Qt.AlignCenter, g)
            self._chip_rects.append((r, g))
            x += w + 6
        top = y + _CHIP_H + 10

        if self._selected:
            top = self._paint_group(p, self._selected, top)
        self._paint_events(p, top)

    def _paint_group(self, p, group: str, top: int) -> int:
        """One row per router: name, then a badge per member interface with its live
        copies/s and an activity bar. Returns the y below the rows."""
        t = self.theme.theme
        rows = self._tracker.routers_for(group)
        rates = [self._tracker.rate(s.router, group, i) for s in rows for i in s.ifaces]
        rmax = max(rates + [1.0])

        y = top
        name_w = 12 + max([p.fontMetrics().horizontalAdvance(s.router) for s in rows] + [40])
        for s in rows:
            p.setFont(QFont(self.font().family(), 8, QFont.Bold))
            p.setPen(QColor(t.text))
            p.drawText(12, y, name_w, _ROW_H, Qt.AlignLeft | Qt.AlignVCenter, s.router)
            x = 12 + name_w + 4
            p.setFont(QFont(self.font().family(), 8))
            for i in s.ifaces:
                rate = self._tracker.rate(s.router, group, i)
                label = f"if{i}"
                sub = f"{rate:.0f}/s" if rate >= 0.5 else f"{s.copies.get(i, 0)}"
                w = max(p.fontMetrics().horizontalAdvance(label + "  " + sub) + 16, 56)
                r = QRectF(x, y + 2, w, _ROW_H - 6)
                active = rate >= 0.5
                p.setBrush(QColor(t.accent_for("teal")) if active else QColor(t.panel2))
                p.setPen(QColor(t.accent_for("teal")) if active else QColor(t.line))
                p.drawRoundedRect(r, 6, 6)
                p.setPen(QColor("#ffffff") if active else QColor(t.muted))
                p.drawText(r, Qt.AlignCenter, f"{label}  {sub}")
                # activity bar under the badge, proportional to the rate
                if active:
                    frac = min(1.0, rate / rmax)
                    p.setPen(QPen(QColor(t.accent), 2))
                    bx = int(r.left())
                    p.drawLine(bx, int(r.bottom()) + 2,
                               bx + int(frac * r.width()), int(r.bottom()) + 2)
                x += w + 6
            y += _ROW_H + 4
        if not rows:
            p.setPen(QColor(t.faint))
            p.drawText(12, y, self.width() - 24, _ROW_H, Qt.AlignLeft | Qt.AlignVCenter,
                       "no router has members for this group yet")
            y += _ROW_H
        return y + 6

    def _paint_events(self, p, top: int) -> None:
        t = self.theme.theme
        evs = self._tracker.recent_events(6) if self._tracker else []
        if not evs:
            return
        p.setFont(QFont(self.font().family(), 7))
        p.setPen(QColor(t.faint))
        p.drawText(12, top, self.width() - 24, 12, Qt.AlignLeft, "events")
        y = top + 14
        now = time.monotonic()
        for e in reversed(evs):
            if y > self.height() - 14:
                break
            colour = t.accent_for("teal") if e.kind == "join" else t.accent_for("amber")
            p.setPen(QColor(colour))
            age = max(0, int(now - e.t))
            p.drawText(12, y, self.width() - 24, 13, Qt.AlignLeft,
                       f"{e.label()}   ·   {age}s ago")
            y += 14

    # -- interaction ------------------------------------------------------- #
    def mousePressEvent(self, e) -> None:  # noqa: N802
        pos = e.position() if hasattr(e, "position") else e.pos()
        for r, g in self._chip_rects:
            if r.contains(pos):
                self._selected = g
                self.update()
                return


class McastHudController(QObject):
    """Owns a McastHud and refreshes it live off the GUI thread. Injected callables
    (never imports main_window):

        self._mhud = McastHudController(
            self.canvas, self.theme,
            routers=lambda: [d.name for d in devs.values()
                             if d.type_key in ("router", "firewall")],
            query=self.element_query)          # (name, cmd) -> text
        self._mhud.show_topright()             # toggle off -> self._mhud.close()
    """

    rows_ready = Signal(object, object, float)     # (rows, polled_names, tnow)

    def __init__(self, parent, theme, routers, query, interval_ms: int = 1200) -> None:
        super().__init__(parent)
        self.hud = McastHud(parent, theme)
        self._routers = routers
        self._query = query
        self._tracker = McastTracker()
        self._busy = False
        self.rows_ready.connect(self._on_rows)
        self._poll = QTimer(self)
        self._poll.timeout.connect(self.refresh)
        self._interval = interval_ms

    def _on_rows(self, rows, polled, tnow: float) -> None:
        self._tracker.ingest(rows, tnow, polled=set(polled))
        self.hud.set_tracker(self._tracker)

    def refresh(self) -> None:
        if self._busy:
            return
        import threading

        # Snapshot the router list HERE, on the GUI thread -- see the same note in
        # flow_hud.refresh(). `_routers()` iterates ctx.topology.devices, which a project
        # load replaces outright; iterating it from the worker raises inside a thread whose
        # only handler is a `finally`, so the poll vanishes with no explanation.
        try:
            names = list(self._routers())
        except Exception:
            return                              # topology in flux — skip this tick, not fatal
        self._busy = True

        def work():
            try:
                tnow = time.monotonic()
                rows, polled = [], []
                for name in names:
                    try:
                        rows.extend(parse_cp_status(
                            self._query(name, "gpipe cp status"), router=name))
                        polled.append(name)
                    except Exception:
                        pass
                self.rows_ready.emit(rows, polled, tnow)
            finally:
                self._busy = False
        threading.Thread(target=work, daemon=True).start()

    def show_topright(self) -> None:
        par = self.hud.parentWidget()
        if par is not None:
            self.hud.move(max(0, par.width() - self.hud.width() - 16), 16)
        self.hud.show(); self.hud.raise_()
        self.refresh()
        self._poll.start(self._interval)

    def reset(self) -> None:
        """Forget every tracked group. Called when the TOPOLOGY is swapped: the membership
        belongs to the previous network's routers, and keeping it would show a tree for a
        network that is no longer on screen."""
        self._tracker = McastTracker()

    def close(self) -> None:
        self._poll.stop()
        self.hud.hide()
