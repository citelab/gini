"""Routing HUD — a toggle-on model view of the whole network's AUTHENTIC routing state.

A gray/glass panel (meant for the upper-right corner of the canvas) drawing the router graph as a
circle-and-stick diagram. Minimal info in each circle; **tap** a router → its full routing table;
**long-press** a router → highlight the forwarding tree it produces (built by walking real next-hops
in domain/routing_model.py — never a Dijkstra), with loops / dead-ends / ECMP shown honestly. Edges
carry the configured delay-VNF latency.

The widget is pure rendering over a `RoutingModel` + node positions; the live model is assembled by
`domain.routing_model.collect_router_data(...)` and handed in via `set_model`.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QObject, QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..domain.routing_model import collect_router_data, forwarding_tree
from .glass import apply_glass, paint_glass_panel

_NODE_R = 16
_LONGPRESS_MS = 380


class RoutingHud(QWidget):
    def __init__(self, parent, theme, model=None, positions=None) -> None:
        super().__init__(parent)
        self.theme = theme
        self._model = model
        self._positions = dict(positions or {})     # {rid: (canvas_x, canvas_y)}
        self._trace = None                          # TraceResult from the last long-press
        self._root = None                           # long-pressed router (SPT root)
        self._table_rid = None                      # tapped router whose table is shown
        self.resize(360, 300)
        self.setMouseTracking(True)
        apply_glass(self)
        self._press_rid = None
        self._lp = QTimer(self); self._lp.setSingleShot(True)
        self._lp.timeout.connect(self._fire_longpress)

    # -- data -------------------------------------------------------------- #
    def set_model(self, model, positions=None) -> None:
        self._model = model
        if positions is not None:
            self._positions = dict(positions)
        # keep the current SPT root highlighted across live refreshes (convergence)
        if self._root is not None and model is not None and self._root in model.routers:
            self._trace = forwarding_tree(model, self._root)
        else:
            self._trace = None; self._root = None
        self.update()

    def clear_highlight(self) -> None:
        self._trace = None; self._root = None; self._table_rid = None
        self.update()

    # -- layout: fit canvas positions into the panel ----------------------- #
    def _fit(self) -> dict:
        if not self._model:
            return {}
        rids = list(self._model.routers)
        m = 30
        w, h = self.width() - 2 * m, self.height() - 2 * m
        pts = {r: self._positions[r] for r in rids if r in self._positions}
        if len(pts) < len(rids) or not pts:          # missing positions → circle layout fallback
            n = max(len(rids), 1)
            cx, cy, rad = self.width() / 2, self.height() / 2, min(w, h) / 2 - _NODE_R
            return {r: QPointF(cx + rad * math.cos(2 * math.pi * i / n),
                               cy + rad * math.sin(2 * math.pi * i / n))
                    for i, r in enumerate(rids)}
        xs = [p[0] for p in pts.values()]; ys = [p[1] for p in pts.values()]
        minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
        sx = (maxx - minx) or 1.0; sy = (maxy - miny) or 1.0
        scale = min(w / sx, h / sy)
        ox = m + (w - sx * scale) / 2; oy = m + (h - sy * scale) / 2
        return {r: QPointF(ox + (pts[r][0] - minx) * scale, oy + (pts[r][1] - miny) * scale)
                for r in rids}

    # -- paint ------------------------------------------------------------- #
    def paintEvent(self, _e) -> None:  # noqa: N802
        t = self.theme.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        paint_glass_panel(p, self.rect(), self.theme, "ROUTING")
        if not self._model:
            p.setPen(QColor(t.faint))
            p.drawText(self.rect(), Qt.AlignCenter, "— no routers running —")
            return
        fit = self._fit()
        hi = self._trace.edges_used if self._trace else set()
        # edges
        for e in self._model.edges:
            if e.a not in fit or e.b not in fit:
                continue
            pa, pb = fit[e.a], fit[e.b]
            lit = (e.a, e.b) in hi or (e.b, e.a) in hi
            p.setPen(QPen(QColor(t.accent) if lit else QColor(t.line), 3 if lit else 1.4))
            p.drawLine(pa, pb)
            if e.latency_ms is not None:              # latency label at the midpoint
                mid = QPointF((pa.x() + pb.x()) / 2, (pa.y() + pb.y()) / 2)
                p.setPen(QColor(t.text if lit else t.muted))
                p.setFont(QFont(self.font().family(), 8))
                p.drawText(int(mid.x()) - 20, int(mid.y()) - 14, 40, 12,
                           Qt.AlignCenter, f"{e.latency_ms:g}ms")
        # nodes
        loops = self._trace.loops if self._trace else set()
        deadends = self._trace.deadends if self._trace else set()
        for rid, pt in fit.items():
            node = self._model.routers[rid]
            is_root = (rid == self._root)
            fill = QColor(t.accent) if is_root else QColor(t.panel2)
            p.setBrush(fill)
            ring = QColor(t.accent_for("red")) if (rid in loops or rid in deadends) else QColor(t.line)
            pen = QPen(ring, 2)
            if rid in loops:
                pen.setStyle(Qt.DashLine)
            p.setPen(pen)
            p.drawEllipse(pt, _NODE_R, _NODE_R)
            p.setPen(QColor("#ffffff") if is_root else QColor(t.text))
            p.setFont(QFont(self.font().family(), 8, QFont.Bold))
            p.drawText(int(pt.x()) - _NODE_R, int(pt.y()) - 6, 2 * _NODE_R, 12,
                       Qt.AlignCenter, node.name[:4])
        # tapped router's table card
        if self._table_rid in self._model.routers and self._table_rid in fit:
            self._paint_table(p, fit[self._table_rid], self._model.routers[self._table_rid])

    def _paint_table(self, p, near: QPointF, node) -> None:
        t = self.theme.theme
        rows = node.table[:8]
        w, rh = 190, 16
        h = 22 + rh * max(len(rows), 1)
        x = min(max(8, int(near.x()) + 20), self.width() - w - 8)
        y = min(max(8, int(near.y()) - 10), self.height() - h - 8)
        card = QColor(t.panel); card.setAlpha(245)
        p.setBrush(card); p.setPen(QColor(t.accent))
        p.drawRoundedRect(x, y, w, h, 8, 8)
        p.setPen(QColor(t.text)); p.setFont(QFont(self.font().family(), 8, QFont.Bold))
        p.drawText(x + 8, y + 4, w - 16, 14, Qt.AlignLeft, f"{node.name}  ·  route table")
        p.setFont(QFont("monospace", 8))
        for i, e in enumerate(rows):
            p.setPen(QColor(t.muted))
            p.drawText(x + 8, y + 20 + i * rh, w - 16, rh, Qt.AlignLeft,
                       f"{e.network}/{_plen(e.netmask)} → {e.nexthop_str()}")

    # -- interaction: tap = table, long-press = SPT ------------------------ #
    def _hit(self, pos) -> str | None:
        for rid, pt in self._fit().items():
            if (pt.x() - pos.x()) ** 2 + (pt.y() - pos.y()) ** 2 <= (_NODE_R + 3) ** 2:
                return rid
        return None

    def mousePressEvent(self, e) -> None:  # noqa: N802
        self._press_rid = self._hit(e.position() if hasattr(e, "position") else e.pos())
        if self._press_rid is not None:
            self._lp.start(_LONGPRESS_MS)
        else:                                         # tap empty space → clear the highlight + table
            self.clear_highlight()

    def mouseReleaseEvent(self, _e) -> None:  # noqa: N802
        if self._lp.isActive():                       # released before long-press fired → a tap
            self._lp.stop()
            if self._press_rid is not None:
                self._table_rid = self._press_rid     # show its table
                self.update()
        self._press_rid = None

    def _fire_longpress(self) -> None:
        if self._press_rid is not None and self._model is not None:
            self._root = self._press_rid
            self._trace = forwarding_tree(self._model, self._root)
            self._table_rid = None
            self.update()


def _plen(netmask: str) -> int:
    try:
        import ipaddress
        return bin(int(ipaddress.IPv4Address(netmask))).count("1")
    except Exception:
        return 0


class RoutingHudController(QObject):
    """Owns a RoutingHud and refreshes it live off the GUI thread. Everything it needs from the app
    is injected as callables, so it neither imports main_window nor gets tested against it.

    Wire-up (one line in MainWindow, e.g. behind a 'Routing HUD' toolbar toggle):

        self._rhud = RoutingHudController(
            self.canvas, self.theme,
            router_devices=lambda: [(d.id, d.name) for d in self.ctx.topology.devices.values()
                                    if d.type_key in ("router", "firewall")],
            query=self.element_query,                 # (name, cmd) -> text
            delay_prop=lambda rid, k: self.ctx.topology.devices[rid].properties.get(k, ""),
            positions_of=lambda: {d.id: (d.x, d.y) for d in self.ctx.topology.devices.values()})
        self._rhud.show_topright()                    # toggle off → self._rhud.close()
    """

    model_ready = Signal(object, object)              # (RoutingModel, positions)

    def __init__(self, parent, theme, router_devices, query, delay_prop, positions_of,
                 interval_ms: int = 2500) -> None:
        super().__init__(parent)
        self.hud = RoutingHud(parent, theme)
        self._router_devices = router_devices
        self._query = query
        self._delay_prop = delay_prop
        self._positions_of = positions_of
        self._busy = False
        self.model_ready.connect(lambda m, p: self.hud.set_model(m, p))
        self._poll = QTimer(self); self._poll.timeout.connect(self.refresh)
        self._interval = interval_ms

    def _build(self):
        """Assemble the live model (blocking CLI reads — call off the GUI thread)."""
        return (collect_router_data(self._router_devices(), self._query, self._delay_prop),
                self._positions_of())

    def refresh(self) -> None:
        if self._busy:
            return
        self._busy = True
        import threading

        def work():
            try:
                model, pos = self._build()
                self.model_ready.emit(model, pos)
            except Exception:
                pass
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

    def close(self) -> None:
        self._poll.stop()
        self.hud.hide()
