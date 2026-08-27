"""Network HUD — a toggle-on model view of the whole network's AUTHENTIC forwarding state.

A gray/glass panel (meant for the upper-right corner of the canvas) drawing the network as a
node-and-stick diagram: a **circle** for an IP router, a **square** for an OpenFlow switch. Minimal
info in each node; **click** a node → highlight the forwarding it produces, built by walking real
next-hops and real flow rules in domain/routing_model.py — never a Dijkstra — with loops /
dead-ends / ECMP shown honestly; **hold** a node → its full routing table (router) or flow table
(switch). Edges carry the configured delay-VNF latency.

Tracing is the click because it is what the panel is FOR. It sat behind a 380 ms hold once, and an
unselected panel looked exactly like a dark network — silence either way — which cost a long live
debugging session before the panel was taught to say which it was.

Two things the picture is careful about:

**The mode follows the root.** Click a router and you get the L3 forwarding tree, switches
appearing inside it as transit. Click a switch and you get L2 reachability across its fabric.
A pure-SDN network has no routers to pick, so it always reads in L2.

**Colour says who decided.** A lit edge takes the colour of the node at its SOURCE: `computed` (the
theme accent) where a router's longest-prefix match chose the next hop, `programmed` (purple) where
an installed flow rule chose the egress port. So an L3 hop across an SDN segment changes colour at
the square, which is exactly where the kind of decision changes. Classic switches are deliberately
not nodes at all — their forwarding is self-learned, and there is no decision to inspect.

The widget is pure rendering over a `RoutingModel` + node positions; the live model is assembled by
`domain.routing_model.collect_network_data(...)` and handed in via `set_model`.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..domain.routing_model import (
    KIND_OVS, RouteHistory, collect_network_data, collect_router_data, decision_kind, trace,
)
from .glass import apply_glass, paint_glass_panel

_NODE_R = 16
_LONGPRESS_MS = 380
_TL_H = 22                  # timeline strip height (bottom of the panel)
_LIVE_W = 44                # width of the LIVE chip at the timeline's right end
# Colour by DECISION, not by layer. "computed" is the theme accent so a plain IP network looks
# exactly as it always did; "programmed" is purple, chosen to sit clear of red (a fault) and amber
# (replay), and to stay distinct from every theme accent (sand/blue/green).
_PROGRAMMED = "purple"
_CONTROL = "slate"          # the OFC overlay: recessive on purpose — it is not a data path


class RoutingHud(QWidget):
    def __init__(self, parent, theme, model=None, positions=None) -> None:
        super().__init__(parent)
        self.theme = theme
        self._model = model
        self._positions = dict(positions or {})     # {rid: (canvas_x, canvas_y)}
        self._trace = None                          # TraceResult for the selected root
        self._root = None                           # the node being traced (clicked)
        self._controllers: dict = {}                # {rid: name} — control-plane decoration only
        self._table_rid = None                      # node whose table card is shown (hold)
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
        # keep the current root highlighted across live refreshes (convergence). `nodes`, not
        # `routers`: an OVS can be the root too, and re-tracing it re-reads the flow tables.
        if self._root is not None and model is not None and self._root in model.nodes:
            self._trace = trace(model, self._root)
        else:
            # Drop the TRACE but KEEP the root. A poll that fails, or a model that briefly
            # arrives without this node, used to destroy the selection outright -- so the
            # highlight vanished and never came back, and the only cure was to select it
            # again. The root is the user's choice, not a fact about the last poll; holding
            # it means the picture re-lights by itself when the network answers again.
            self._trace = None
        self.update()

    def clear_highlight(self) -> None:
        self._trace = None; self._root = None; self._table_rid = None
        self.update()

    # -- layout: fit canvas positions into the panel ----------------------- #
    def _fit(self) -> dict:
        if not self._model:
            return {}
        # Controllers are laid out with the rest but are NOT model nodes: they are decoration
        # for the control-plane overlay, and keeping them out of the model is what structurally
        # guarantees one can never turn up inside a forwarding tree.
        rids = list(self._model.nodes) + [c for c in self._controllers
                                          if c not in self._model.nodes]
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
        paint_glass_panel(p, self.rect(), self.theme, "NETWORK")
        if not self._model:
            p.setPen(QColor(t.faint))
            p.drawText(self.rect(), Qt.AlignCenter, "— nothing running —")
            return
        fit = self._fit()
        hi = self._trace.edges_used if self._trace else set()
        fault = getattr(self._trace, "fault_edges", set()) if self._trace else set()
        # edges. A lit edge is coloured by the node that DECIDED it -- its source -- so an L3
        # hop crossing a switch changes colour at the square, where the decision changes hands.
        for e in self._model.edges:
            if e.a not in fit or e.b not in fit:
                continue
            pa, pb = fit[e.a], fit[e.b]
            src = e.a if (e.a, e.b) in hi else (e.b if (e.b, e.a) in hi else None)
            faulty = bool(fault and ({(e.a, e.b), (e.b, e.a)} & fault))
            if faulty:
                # A forwarding LOOP, painted on the cycle itself. In L3 the fault shows as a
                # red ring on the destination that cannot be reached, but in L2 the
                # destination is a host MAC and hosts are not drawn — so the cycle is the
                # only place the failure can be seen.
                p.setPen(QPen(QColor(t.accent_for("red")), 3, Qt.DashLine))
            elif src is None:
                p.setPen(QPen(QColor(t.line), 1.4))
            else:
                kind = decision_kind(self._model, src)
                lit_c = t.accent if kind == "computed" else t.accent_for(_PROGRAMMED)
                p.setPen(QPen(QColor(lit_c), 3))
            p.drawLine(pa, pb)
            if e.latency_ms is not None:              # latency label at the midpoint
                mid = QPointF((pa.x() + pb.x()) / 2, (pa.y() + pb.y()) / 2)
                p.setPen(QColor(t.text if src else t.muted))
                p.setFont(QFont(self.font().family(), 8))
                p.drawText(int(mid.x()) - 20, int(mid.y()) - 14, 40, 12,
                           Qt.AlignCenter, f"{e.latency_ms:g}ms")
        self._paint_control_overlay(p, fit)
        # nodes
        loops = self._trace.loops if self._trace else set()
        deadends = self._trace.deadends if self._trace else set()
        for rid, pt in fit.items():
            node = self._model.nodes.get(rid)
            if node is None:                          # a controller: drawn by the overlay, not here
                continue
            is_ovs = (node.kind == KIND_OVS)
            is_root = (rid == self._root)
            root_c = QColor(t.accent_for(_PROGRAMMED)) if is_ovs else QColor(t.accent)
            p.setBrush(root_c if is_root else QColor(t.panel2))
            # A fault ring means a router black-holes or loops. `unprogrammed` is deliberately
            # NOT a fault: a switch with no rule for a MAC yet is mid-learning, not broken.
            bad = rid in loops or rid in deadends
            # A switch we could not READ this poll. It must look different from one that
            # answered with no rules: the picture going dark should say "no answer", never
            # imply the network stopped forwarding.
            mute = is_ovs and not getattr(node, "reachable", True)
            pen = QPen(QColor(t.accent_for("red")) if bad else QColor(t.line), 2)
            if rid in loops:
                pen.setStyle(Qt.DashLine)
            if mute:
                p.setBrush(QColor(t.panel))
                pen = QPen(QColor(t.faint), 1.4, Qt.DotLine)
            p.setPen(pen)
            if is_ovs:
                p.drawRoundedRect(QRectF(pt.x() - _NODE_R, pt.y() - _NODE_R,
                                         2 * _NODE_R, 2 * _NODE_R), 4, 4)
            else:
                p.drawEllipse(pt, _NODE_R, _NODE_R)
            p.setPen(QColor(t.faint) if mute else
                     (QColor("#ffffff") if is_root else QColor(t.text)))
            p.setFont(QFont(self.font().family(), 8, QFont.Bold))
            p.drawText(int(pt.x()) - _NODE_R, int(pt.y()) - 6, 2 * _NODE_R, 12,
                       Qt.AlignCenter, node.name[:4])
        # Say why nothing is lit. An empty picture has several indistinguishable causes and
        # the panel used to render all of them as silence, which is what made this so hard
        # to pin down from a screenshot.
        if self._root is None:
            p.setPen(QColor(t.faint))
            p.setFont(QFont(self.font().family(), 8))
            p.drawText(12, self.height() - _TL_H - 22, self.width() - 24, 14, Qt.AlignLeft,
                       "click a node here to trace its forwarding  ·  hold for its table")
        elif self._trace is not None and not self._trace.edges_used:
            why = "no rules programmed for any destination yet"
            if self._model.ovs and not any(n.reachable for n in self._model.ovs.values()):
                why = "switches are not answering"
            elif any(not n.port_peer for n in self._model.ovs.values()):
                why = "no verified port map — cannot follow any rule"
            p.setPen(QColor(t.faint))
            p.setFont(QFont(self.font().family(), 8))
            p.drawText(12, self.height() - _TL_H - 22, self.width() - 24, 14, Qt.AlignLeft,
                       f"{self._model.nodes[self._root].name}: {why}")
        # tapped node's table card — routes for a router, flows for a switch
        if self._table_rid in self._model.nodes and self._table_rid in fit:
            node = self._model.nodes[self._table_rid]
            near = fit[self._table_rid]
            if node.kind == KIND_OVS:
                self._paint_flows(p, near, node)
            else:
                self._paint_table(p, near, node)
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
        # Flow-table ACTIVITY, below the axis: the controller installing rules and the
        # switches ageing them out. Drawn short, faint and downward so it can never be
        # mistaken for a forwarding change -- with per-microflow matching the table moves
        # constantly while forwarding stays put, and conflating the two would put the noise
        # back that the signature exists to keep out. Height tracks how many rules are
        # loaded, so a burst of programming reads as a swell rather than a row of ticks.
        act = getattr(h, "activity", None) or []
        if act:
            nmax = max(n for _, n in act) or 1
            p.setPen(QPen(QColor(t.faint), 1))
            for at, n in act:
                xx = int(X(at))
                p.drawLine(xx, int(cy) + 2, xx, int(cy) + 2 + int(6 * n / nmax))
        # change-point ticks ABOVE the axis: each recorded snapshot is a moment the
        # network's actual FORWARDING changed, and each is a scrub target.
        p.setPen(QPen(QColor(t.accent), 2))
        for ct in h.change_times():
            xx = int(X(ct))
            p.drawLine(xx, int(cy) - 6, xx, int(cy))
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

    def _paint_control_overlay(self, p, fit: dict) -> None:
        """A dashed tether from each switch to the controller programming it.

        The control plane is drawn but held apart on purpose: it is how the rules GOT there,
        never a path a packet takes. Keeping it in the recessive slate and dashed means it
        can never be misread as an edge in a forwarding tree — which is also why a controller
        never appears inside one.
        """
        t = self.theme.theme
        col = QColor(t.accent_for(_CONTROL))
        p.setPen(QPen(col, 1.2, Qt.DotLine))
        for rid, node in self._model.ovs.items():
            ctrl = getattr(node, "controller", None)
            if ctrl and rid in fit and ctrl in fit:
                p.drawLine(fit[rid], fit[ctrl])
        # the controllers themselves: small dashed boxes, clearly not forwarding nodes
        r = _NODE_R - 4
        for cid, cname in self._controllers.items():
            if cid not in fit:
                continue
            pt = fit[cid]
            p.setBrush(QColor(t.panel2)); p.setPen(QPen(col, 1.4, Qt.DashLine))
            p.drawRect(QRectF(pt.x() - r, pt.y() - r, 2 * r, 2 * r))
            p.setPen(col); p.setFont(QFont(self.font().family(), 7, QFont.Bold))
            p.drawText(int(pt.x()) - _NODE_R, int(pt.y()) - 5, 2 * _NODE_R, 11,
                       Qt.AlignCenter, cname[:4])

    def _paint_flows(self, p, near: QPointF, node) -> None:
        """The tapped switch's real flow table: match → action, highest priority first."""
        t = self.theme.theme
        rows = sorted(node.flows or [], key=lambda f: -(f.priority or 0))[:8]
        w, rh = 230, 16
        h = 22 + rh * max(len(rows), 1)
        x = min(max(8, int(near.x()) + 20), self.width() - w - 8)
        y = min(max(8, int(near.y()) - 10), self.height() - h - 8)
        card = QColor(t.panel); card.setAlpha(245)
        p.setBrush(card); p.setPen(QColor(t.accent_for(_PROGRAMMED)))
        p.drawRoundedRect(x, y, w, h, 8, 8)
        p.setPen(QColor(t.text)); p.setFont(QFont(self.font().family(), 8, QFont.Bold))
        p.drawText(x + 8, y + 4, w - 16, 14, Qt.AlignLeft, f"{node.name}  ·  flow table")
        p.setFont(QFont("monospace", 8))
        if not rows:
            p.setPen(QColor(t.faint))
            # The distinction the whole reachability flag exists for: "it answered, and it has
            # nothing" is a fact about the network; "it did not answer" is a fact about us.
            p.drawText(x + 8, y + 20, w - 16, rh, Qt.AlignLeft,
                       "no rules installed yet" if getattr(node, "reachable", True)
                       else "switch did not answer this poll")
            return
        for i, f in enumerate(rows):
            p.setPen(QColor(t.muted))
            p.drawText(x + 8, y + 20 + i * rh, w - 16, rh, Qt.AlignLeft,
                       f"{f.match_summary()} → {f.action_summary()}"[:44])

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

    # -- interaction: click = trace, hold = table -------------------------- #
    def _hit(self, pos) -> str | None:
        """The node under `pos`, or the nearest one within reach.

        Generous on purpose. Nodes are ~32 px in a panel that scales a whole topology into
        a corner, a switch is drawn SQUARE (so a circular test of radius NODE_R+3 missed its
        corners outright), and until this was widened a click that fell a few pixels short
        counted as "empty space" and wiped the selection.
        """
        best, best_d2 = None, None
        reach2 = (_NODE_R + 10) ** 2
        for rid, pt in self._fit().items():
            if rid not in self._model.nodes:          # controllers are decoration, not targets
                continue
            d2 = (pt.x() - pos.x()) ** 2 + (pt.y() - pos.y()) ** 2
            if d2 <= reach2 and (best_d2 is None or d2 < best_d2):
                best, best_d2 = rid, d2
        return best

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
        if self._lp.isActive():                       # released before the hold fired → a CLICK
            self._lp.stop()
            if self._press_rid is not None:
                # A plain click TRACES. Tracing is what this panel is for, so it must be the
                # easy gesture: requiring a 380 ms hold for the primary action meant a panel
                # that looked identical whether you had never selected a root or the network
                # was genuinely dark, and a click that fell 20 px short silently cleared the
                # selection instead. Holding still shows the table (see _fire_longpress).
                self._root = self._press_rid
                # `trace` picks the mode from the root: a router gives the L3 forwarding
                # tree, a switch gives L2 reachability across its fabric.
                self._trace = trace(self._model, self._root) if self._model else None
                self._table_rid = None
                self.update()
        self._press_rid = None

    def _fire_longpress(self) -> None:
        """Held rather than clicked → show that node's table (routes, or the flow table)."""
        if self._press_rid is not None and self._model is not None:
            self._table_rid = self._press_rid
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

    Wire-up (one line in MainWindow, e.g. behind a 'Network HUD' toolbar toggle):

        self._rhud = RoutingHudController(
            self.canvas, self.theme,
            router_devices=lambda: [(d.id, d.name) for d in self.ctx.topology.devices.values()
                                    if d.type_key in ("router", "firewall")],
            query=self.element_query,                 # (name, cmd) -> text
            delay_prop=lambda rid, k: self.ctx.topology.devices[rid].properties.get(k, ""),
            positions_of=lambda: {d.id: (d.x, d.y) for d in self.ctx.topology.devices.values()})
        self._rhud.show_topright()                    # toggle off → self._rhud.close()
    """

    model_ready = Signal(object, object, object)      # (RoutingModel, positions, controllers)

    def __init__(self, parent, theme, router_devices, query, delay_prop, positions_of,
                 switch_devices=None, neighbours_of=None, mac_of=None, topo_links=None,
                 passthrough_of=None, controllers_of=None, log=None,
                 interval_ms: int = 2500) -> None:
        super().__init__(parent)
        self.hud = RoutingHud(parent, theme)
        self._router_devices = router_devices
        self._query = query
        self._delay_prop = delay_prop
        self._positions_of = positions_of
        # SDN side — all optional, so an IP-only wire-up behaves exactly as it always did.
        self._switch_devices = switch_devices
        self._neighbours_of = neighbours_of
        self._mac_of = mac_of
        self._topo_links = topo_links
        self._passthrough_of = passthrough_of
        self._controllers_of = controllers_of
        # Static port map + last-good flow dumps, for the lifetime of this run.
        # See collect_network_data: it is what stops a timed-out poll blanking the picture.
        self._run_cache: dict = {}
        self._log = log                             # callable(str) -> None; see _diagnose
        self._last_diag = None
        self._busy = False
        self.history = RouteHistory()               # P4: convergence recording for the scrub
        self.model_ready.connect(self._on_model)
        self._poll = QTimer(self); self._poll.timeout.connect(self.refresh)
        self._interval = interval_ms

    def _on_model(self, model, positions, controllers=None) -> None:
        import time
        self.hud._controllers = dict(controllers or {})
        if model is None:                           # rebuild failed — show nothing, not stale
            if not self.hud.scrubbing:
                self.hud.set_model(None, positions)
            self._diagnose(None)
            return                                  # and never record a non-state in history
        self.history.push(model, time.monotonic())
        self.hud.set_history(self.history)          # timeline live-edge / new change ticks
        if not self.hud.scrubbing:                  # don't yank a replay back to live
            self.hud.set_model(model, positions)
        else:
            self.hud._positions = dict(positions)   # keep layout fresh for when we resume
        self._diagnose(model)

    def _diagnose(self, model) -> None:
        """Report only what the PANEL cannot show for itself, and only when it changes.

        A dark HUD has several indistinguishable causes -- the switch did not answer, it
        answered with no rules, the port map is empty, nothing is selected, or the panel is
        silently in replay -- and every one of them renders as the same empty picture. That
        ambiguity cost a long live debugging session, so it is worth reporting.

        But most of those now say so ON THE PANEL, where the person is already looking. Only
        the two that do not are logged here: a poll that failed outright, and a switch that
        stopped answering. A healthy network logs NOTHING; a recovery logs one line. The
        Console is for things that need attention, not a running commentary.
        """
        if self._log is None:
            return
        if model is None:
            state = "Network HUD: poll failed — no model built"
        else:
            mute = [n.name for n in model.ovs.values() if not n.reachable]
            noports = [n.name for n in model.ovs.values() if n.reachable and not n.port_peer]
            if mute:
                state = ("Network HUD: no answer from " + ", ".join(sorted(mute))
                         + " — showing their last known rules")
            elif noports:
                state = ("Network HUD: no verified port map for " + ", ".join(sorted(noports))
                         + " — their rules cannot be followed")
            else:
                state = None                        # healthy: say nothing at all
        if state == self._last_diag:
            return                                  # unchanged — including healthy-and-quiet
        first_ever = self._last_diag is None and state is None
        self._last_diag = state
        if first_ever:
            return                                  # never announce a healthy start
        try:
            self._log(state or "Network HUD: all switches answering again")
        except Exception:
            pass

    def _build(self):
        """Assemble the live model (blocking CLI reads — call off the GUI thread)."""
        switches = list(self._switch_devices()) if self._switch_devices else []
        if not switches:
            # No SDN in this topology: take the original path, where adjacency is inferred
            # from the routers' own connected routes rather than from the drawn links.
            model = collect_router_data(self._router_devices(), self._query, self._delay_prop)
        else:
            model = collect_network_data(
                self._router_devices(), switches, self._query, self._delay_prop,
                neighbours_of=self._neighbours_of,
                mac_of=self._mac_of() if self._mac_of else None,
                topo_links=self._topo_links() if self._topo_links else None,
                passthrough=self._passthrough_of() if self._passthrough_of else None,
                run_cache=self._run_cache)
        controllers = self._controllers_of() if self._controllers_of else {}
        return model, self._positions_of(), controllers

    def reset(self) -> None:
        """Forget everything: model, convergence history, and any scrub position.

        Must be called whenever the TOPOLOGY changes (open/new project, Run, Stop).
        Without it the HUD keeps drawing the previous network's routers over the new
        canvas -- confidently, and indistinguishably from live data.
        """
        self.history.clear()
        self.hud._scrub_t = None
        self.hud._controllers = {}
        # Port numbering belongs to the RUN that produced it: a recompiled topology can order
        # its links differently, so a carried-over map would point at the wrong neighbours.
        self._run_cache = {}
        self.hud.set_history(self.history)
        # An explicit reset DOES drop the root — unlike a failed poll, which must not. The
        # node the user picked belongs to the network that just went away.
        self.hud.clear_highlight()
        self.hud.set_model(None, {})

    def refresh(self) -> None:
        if self._busy:
            return
        self._busy = True
        import threading

        def work():
            try:
                model, pos, ctrls = self._build()
                self.model_ready.emit(model, pos, ctrls)
            except Exception:
                # A failed rebuild used to be swallowed here, which left the PREVIOUS
                # model on screen looking live. For a HUD whose whole value is showing
                # the network's real state, a confident stale picture is worse than an
                # empty one -- so surface the failure by clearing instead.
                self.model_ready.emit(None, self._positions_of(), {})
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
