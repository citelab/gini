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

from ..domain.routing_model import RouteHistory, collect_router_data, forwarding_tree
from .glass import apply_glass, paint_glass_panel

_NODE_R = 16
_LONGPRESS_MS = 380
_TL_H = 22                  # timeline strip height (bottom of the panel)
_LIVE_W = 44                # width of the LIVE chip at the timeline's right end


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
        # P4 convergence replay: scrub through recorded routing states
        self._history: RouteHistory | None = None
        self._scrub_t: float | None = None          # None = live; else the replayed instant
        self._scrub_drag = False

    # -- P4: replay state --------------------------------------------------- #
    @property
    def scrubbing(self) -> bool:
        return self._scrub_t is not None

    def set_history(self, history: RouteHistory) -> None:
        """Called by the controller after each poll. While scrubbing, the recording keeps
        growing underneath but the displayed (historical) model is left alone."""
        self._history = history
        self.update()                               # the timeline's live edge advanced

    def _timeline_rect(self):
        from PySide6.QtCore import QRectF
        return QRectF(12, self.height() - _TL_H - 6, self.width() - 24 - _LIVE_W, _TL_H)

    def _live_rect(self):
        from PySide6.QtCore import QRectF
        return QRectF(self.width() - _LIVE_W - 8, self.height() - _TL_H - 6, _LIVE_W, _TL_H)

    def _t_at_x(self, x: float) -> float:
        tl = self._timeline_rect()
        h = self._history
        span = (h.t_end - h.t_start) or 1.0
        frac = min(1.0, max(0.0, (x - tl.left()) / (tl.width() or 1.0)))
        return h.t_start + frac * span

    def _scrub_to(self, t: float) -> None:
        """Show the routing state that was in force at time t (near the live edge → live)."""
        h = self._history
        if h is None or len(h) == 0:
            return
        if t >= h.t_end - 0.5 or (h.t_end - h.t_start) <= 0.5:
            self.go_live()
            return
        self._scrub_t = t
        m = h.at(t)
        if m is not None:
            self.set_model(m)                       # re-traces the SPT root against THAT state

    def go_live(self) -> None:
        self._scrub_t = None
        if self._history is not None and self._history.latest() is not None:
            self.set_model(self._history.latest())
        self.update()

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
        bot = m + (_TL_H if (self._history is not None and len(self._history) > 0) else 0)
        w, h = self.width() - 2 * m, self.height() - m - bot
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
        # P4: convergence timeline (scrub bar) along the bottom
        if self._history is not None and len(self._history) > 0:
            self._paint_timeline(p)

    def _paint_timeline(self, p) -> None:
        t = self.theme.theme
        h = self._history
        tl = self._timeline_rect()
        cy = tl.center().y()
        span = (h.t_end - h.t_start) or 1.0

        def X(tt):
            return tl.left() + min(1.0, max(0.0, (tt - h.t_start) / span)) * tl.width()

        # track
        p.setPen(QPen(QColor(t.line), 2))
        p.drawLine(int(tl.left()), int(cy), int(tl.right()), int(cy))
        # change-point ticks: each recorded snapshot = the moment the routing state changed
        p.setPen(QPen(QColor(t.accent), 2))
        for ct in h.change_times():
            xx = int(X(ct))
            p.drawLine(xx, int(cy) - 5, xx, int(cy) + 5)
        # playhead: at the scrub instant, or the live edge
        cur = self._scrub_t if self._scrub_t is not None else h.t_end
        px = int(X(cur))
        knob = QColor(t.accent_for("amber")) if self.scrubbing else QColor(t.accent)
        p.setBrush(knob); p.setPen(QPen(knob, 1))
        p.drawEllipse(QPointF(px, cy), 5, 5)
        # LIVE chip (click to snap back) / replay badge
        lr = self._live_rect()
        p.setFont(QFont(self.font().family(), 8, QFont.Bold))
        if self.scrubbing:
            p.setBrush(QColor(0, 0, 0, 0)); p.setPen(QColor(t.line))
            p.drawRoundedRect(lr, 8, 8)
            p.setPen(QColor(t.muted))
            p.drawText(lr, Qt.AlignCenter, "LIVE")
            back = h.t_end - cur
            p.setPen(QColor(t.accent_for("amber")))
            p.drawText(int(tl.left()), int(tl.top()) - 12, int(tl.width()), 12,
                       Qt.AlignLeft, f"replay  ·  t−{back:.0f}s")
        else:
            p.setBrush(QColor(0, 0, 0, 0)); p.setPen(QColor(t.accent))
            p.drawRoundedRect(lr, 8, 8)
            p.setPen(QColor(t.accent))
            p.drawText(lr, Qt.AlignCenter, "● LIVE")

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
        pos = e.position() if hasattr(e, "position") else e.pos()
        # P4 replay controls first: the LIVE chip and the scrub timeline
        if self._history is not None and len(self._history) > 0:
            if self._live_rect().contains(pos):
                self.go_live()
                return
            if self._timeline_rect().contains(pos):
                self._scrub_drag = True
                self._scrub_to(self._t_at_x(pos.x()))
                return
        self._press_rid = self._hit(pos)
        if self._press_rid is not None:
            self._lp.start(_LONGPRESS_MS)
        else:                                         # tap empty space → clear the highlight + table
            self.clear_highlight()

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self._scrub_drag:
            pos = e.position() if hasattr(e, "position") else e.pos()
            self._scrub_to(self._t_at_x(pos.x()))

    def mouseReleaseEvent(self, _e) -> None:  # noqa: N802
        if self._scrub_drag:                          # let go of the scrubber → stay paused there
            self._scrub_drag = False
            return
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
        self.history = RouteHistory()               # P4: convergence recording for the scrub
        self.model_ready.connect(self._on_model)
        self._poll = QTimer(self); self._poll.timeout.connect(self.refresh)
        self._interval = interval_ms

    def _on_model(self, model, positions) -> None:
        import time
        if model is None:                           # rebuild failed — show nothing, not stale
            if not self.hud.scrubbing:
                self.hud.set_model(None, positions)
            return                                  # and never record a non-state in history
        self.history.push(model, time.monotonic())
        self.hud.set_history(self.history)          # timeline live-edge / new change ticks
        if not self.hud.scrubbing:                  # don't yank a replay back to live
            self.hud.set_model(model, positions)
        else:
            self.hud._positions = dict(positions)   # keep layout fresh for when we resume

    def _build(self):
        """Assemble the live model (blocking CLI reads — call off the GUI thread)."""
        return (collect_router_data(self._router_devices(), self._query, self._delay_prop),
                self._positions_of())

    def reset(self) -> None:
        """Forget everything: model, convergence history, and any scrub position.

        Must be called whenever the TOPOLOGY changes (open/new project, Run, Stop).
        Without it the HUD keeps drawing the previous network's routers over the new
        canvas -- confidently, and indistinguishably from live data.
        """
        self.history.clear()
        self.hud._scrub_t = None
        self.hud.set_history(self.history)
        self.hud.set_model(None, {})

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
                # A failed rebuild used to be swallowed here, which left the PREVIOUS
                # model on screen looking live. For a HUD whose whole value is showing
                # the network's real state, a confident stale picture is worse than an
                # empty one -- so surface the failure by clearing instead.
                self.model_ready.emit(None, self._positions_of())
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
        self.hud._scrub_t = None                    # reopen live (history survives the session)
        self.hud.hide()
