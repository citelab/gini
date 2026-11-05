"""Multicast HUD — every multicast group's live distribution tree, drawn over the network.

A glass panel (upper-right of the canvas) showing the network the way the Network HUD does —
routers and machines where they sit on the canvas, switches as the hubs of their LANs — and on top
of it each active group's tree in that group's own colour: from the source to its first router,
out of every interface a router copies to, on to the next router or to the members there. Pruned
branches are dashed (dense mode saying "nobody here"), reverse-path refusals are a red badge on the
router, and while copies are flowing, dots move along the branches in the direction they travel.

It was a list once — a chip per group, a badge per member interface — which answered "is anything
joined?" and none of what a student asks next: where does the traffic go, which router copies it,
why does that machine not get it. Everything drawn here comes from what the routers publish
(`mcast_tree.lua`'s `G` and `S` lines; see domain/mcast_map.py) and, on a shared LAN only, from the
machines' own `/proc/net/igmp` — a router cannot know which machine on a LAN joined, and the
picture does not guess.

**Motion is cheap and stops itself.** Dots are drawn only on branches that are carrying copies,
by a 30 fps timer that runs only while the panel is visible and something is moving; the
**motion** chip turns it off. A still panel repaints only when a poll lands.

Pure rendering over a `McastMap` + a `McastTracker` (rates, events); `McastHudController` polls
`gpipe cp status` on every router off the GUI thread and feeds both.
"""
from __future__ import annotations

import math
import time
from itertools import pairwise

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from ..domain.mcast import McastTracker, parse_cp_status
from ..domain.mcast_map import (
    McastMap,
    build,
    describe_event,
    parse_igmp,
    parse_sources,
    runs_forwarder,
    segments,
    shared_lans,
)
from .glass import apply_glass, paint_glass_panel
from .worker_host import run_off_gui

_CHIP_H = 20
_ROUTER_R = 13
_HOST_W, _HOST_H = 30, 18
_FPS_MS = 33
#: Seconds a dot takes to cross one branch. Slow enough to follow, fast enough to read as flow.
_CROSS_S = 1.4
#: Below this a branch is not carrying anything worth animating (see McastTracker's rates).
_LIVE_RATE = 0.5
#: Machines on shared LANs are asked about their own memberships at most this often.
_ASK_HOSTS_EVERY = 3


class McastHud(QWidget):
    def __init__(self, parent, theme) -> None:
        super().__init__(parent)
        self.theme = theme
        self._map: McastMap | None = None
        self._tracker: McastTracker | None = None
        self._addressing: dict = {}
        self._positions: dict = {}
        self._forwarders: set[str] = set()
        self._routers: list[str] = []
        self._focus: str | None = None
        self.motion = True
        self._chip_rects: list[tuple[QRectF, str]] = []
        self._motion_rect = QRectF()
        self._t0 = time.monotonic()
        self._anim = QTimer(self)
        self._anim.setInterval(_FPS_MS)
        self._anim.timeout.connect(self.update)
        self.resize(500, 400)
        self.setMouseTracking(True)
        apply_glass(self)

    # -- data ---------------------------------------------------------------- #
    def set_state(self, mmap: McastMap | None, tracker: McastTracker | None,
                  addressing: dict | None = None, positions: dict | None = None,
                  forwarders=None, routers=None) -> None:
        self._map = mmap
        self._tracker = tracker
        if addressing is not None:
            self._addressing = addressing
        if positions is not None:
            self._positions = positions
        if forwarders is not None:
            self._forwarders = set(forwarders)
        if routers is not None:
            self._routers = list(routers)
        if self._focus is not None and (mmap is None or self._focus not in mmap.groups):
            self._focus = None
        self._sync_motion()
        self.update()

    def set_tracker(self, tracker: McastTracker) -> None:
        """Kept for callers of the list-era HUD: the tracker alone, no map."""
        self._tracker = tracker
        self.update()

    def animating(self) -> bool:
        """Is anything moving — a branch carrying copies, with motion on?"""
        return self.motion and bool(self._live_edges())

    def _sync_motion(self) -> None:
        want = self.isVisible() and self.animating()
        if want and not self._anim.isActive():
            self._anim.start()
        elif not want and self._anim.isActive():
            self._anim.stop()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._sync_motion()

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        self._anim.stop()

    # -- geometry ------------------------------------------------------------ #
    def _map_rect(self) -> QRectF:
        return QRectF(16, 58, self.width() - 32, self.height() - 58 - 66)

    def _fit(self) -> dict[str, QPointF]:
        """Every router, machine and switch on a segment, placed by its canvas position."""
        names = set()
        for s in segments(self._addressing).values():
            names.update(r for r, _ in s.routers)
            names.update(s.hosts)
            if s.hub:
                names.add(s.hub)
        pts = {n: self._positions[n] for n in names if n in self._positions}
        box = self._map_rect().adjusted(_HOST_W / 2 + 4, _HOST_H / 2 + 8,
                                        -_HOST_W / 2 - 4, -_HOST_H / 2 - 4)
        if not pts:
            return {}
        xs = [p[0] for p in pts.values()]
        ys = [p[1] for p in pts.values()]
        sx = (max(xs) - min(xs)) or 1.0
        sy = (max(ys) - min(ys)) or 1.0
        scale = min(box.width() / sx, box.height() / sy)
        ox = box.left() + (box.width() - sx * scale) / 2
        oy = box.top() + (box.height() - sy * scale) / 2
        return {n: QPointF(ox + (p[0] - min(xs)) * scale, oy + (p[1] - min(ys)) * scale)
                for n, p in pts.items()}

    def _path(self, fit, a: str, b: str, via: str | None, offset: float) -> list[QPointF] | None:
        if a not in fit or b not in fit:
            return None
        pts = [fit[a]] + ([fit[via]] if via and via in fit else []) + [fit[b]]
        if offset:
            out = []
            for k, p in enumerate(pts):
                q0 = pts[max(0, k - 1)]
                q1 = pts[min(len(pts) - 1, k + 1)]
                dx, dy = q1.x() - q0.x(), q1.y() - q0.y()
                n = math.hypot(dx, dy) or 1.0
                out.append(QPointF(p.x() - dy / n * offset, p.y() + dx / n * offset))
            pts = out
        return pts

    # -- what is live -------------------------------------------------------- #
    def _rate(self, e) -> float:
        tr = self._tracker
        if tr is None:
            return 0.0
        if e.router:
            return tr.rate(e.router, e.group, e.iface)
        # source -> first router: as busy as that router's busiest copy of the group
        return max((v for (r, g, _i), v in tr.rates.items() if r == e.b and g == e.group),
                   default=0.0)

    def _live_edges(self) -> list:
        if not self._map:
            return []
        return [e for v in self._map.groups.values() for e in v.edges
                if e.kind == "data" and self._rate(e) >= _LIVE_RATE]

    # -- paint --------------------------------------------------------------- #
    def paintEvent(self, _e) -> None:
        t = self.theme.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        paint_glass_panel(p, self.rect(), self.theme, "MULTICAST")
        self._paint_chips(p)
        fit = self._fit()
        if not fit:
            p.setPen(QColor(t.faint))
            p.drawText(self._map_rect(), Qt.AlignCenter, "— nothing running —")
            return
        self._paint_network(p, fit)
        self._paint_trees(p, fit)
        self._paint_nodes(p, fit)
        self._paint_footer(p)

    def _colour(self, name: str, alpha: int = 255) -> QColor:
        c = QColor(self.theme.theme.accent_for(name))
        c.setAlpha(alpha)
        return c

    def _dim(self, group: str) -> bool:
        return self._focus is not None and group != self._focus

    def _paint_chips(self, p) -> None:
        t = self.theme.theme
        self._chip_rects = []
        x, y = 12, 30
        p.setFont(QFont(self.font().family(), 8, QFont.Bold))
        fm = p.fontMetrics()
        groups = self._map.order() if self._map else []
        for g in groups:
            view = self._map.groups[g]
            w = fm.horizontalAdvance(g) + 26
            r = QRectF(x, y, w, _CHIP_H)
            on = self._focus in (None, g)
            p.setPen(QPen(self._colour(view.colour, 255 if on else 90), 1.4))
            p.setBrush(self._colour(view.colour, 70 if self._focus == g else 22))
            p.drawRoundedRect(r, 9, 9)
            p.setBrush(self._colour(view.colour, 255 if on else 90))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(x + 10, y + _CHIP_H / 2), 4, 4)
            p.setPen(QColor(t.text) if on else QColor(t.faint))
            p.drawText(QRectF(x + 16, y, w - 18, _CHIP_H), Qt.AlignVCenter | Qt.AlignLeft, g)
            self._chip_rects.append((r, g))
            x += w + 6
            if x > self.width() - 90:
                break
        label = "motion"
        w = fm.horizontalAdvance(label) + 22
        self._motion_rect = QRectF(self.width() - w - 12, 8, w, _CHIP_H - 2)
        p.setPen(QPen(QColor(t.accent if self.motion else t.line), 1.2))
        p.setBrush(QColor(t.panel2))
        p.drawRoundedRect(self._motion_rect, 8, 8)
        p.setPen(QColor(t.text if self.motion else t.faint))
        p.drawText(self._motion_rect, Qt.AlignCenter, ("▶ " if self.motion else "❚❚ ") + label)

    def _paint_network(self, p, fit) -> None:
        """The drawn network, faint: every segment, through its switch where it has one."""
        t = self.theme.theme
        line = QColor(t.line)
        line.setAlpha(150)
        p.setPen(QPen(line, 1.2))
        for s in segments(self._addressing).values():
            devs = [r for r, _ in s.routers] + s.hosts
            devs = [d for d in dict.fromkeys(devs) if d in fit]
            if s.hub and s.hub in fit:
                for d in devs:
                    p.drawLine(fit[d], fit[s.hub])
            elif len(devs) == 2:
                p.drawLine(fit[devs[0]], fit[devs[1]])
            elif len(devs) > 2:
                c = QPointF(sum(fit[d].x() for d in devs) / len(devs),
                            sum(fit[d].y() for d in devs) / len(devs))
                for d in devs:
                    p.drawLine(fit[d], c)

    def _paint_trees(self, p, fit) -> None:
        if not self._map:
            return
        # How many groups ride each drawn TRUNK, so they run side by side, not on top. The
        # trunk of an edge through a switch is router -> switch: branches to two different
        # machines on one LAN share it, and keyed by their end-to-end pairs they were drawn
        # exactly on top of each other there.
        def trunk(e):
            return frozenset((e.a, e.via if e.via else e.b))
        lanes: dict[frozenset, list[str]] = {}
        for g, v in self._map.groups.items():
            for e in v.edges:
                lane = lanes.setdefault(trunk(e), [])
                if g not in lane:
                    lane.append(g)
        now = time.monotonic() - self._t0
        for g, v in self._map.groups.items():
            dim = self._dim(g)
            for e in v.edges:
                lane = lanes[trunk(e)]
                k = lane.index(g)
                off = (k - (len(lane) - 1) / 2) * 4.0
                # keep a trunk's lanes on the same side whichever way the edge points
                ends = (e.a, e.via if e.via else e.b)
                if ends != tuple(sorted(ends)):
                    off = -off
                pts = self._path(fit, e.a, e.b, e.via, off)
                if not pts:
                    continue
                path = QPainterPath(pts[0])
                for q in pts[1:]:
                    path.lineTo(q)
                if e.kind == "pruned":
                    pen = QPen(self._colour(v.colour, 60 if dim else 170), 1.4, Qt.DashLine)
                    p.setPen(pen)
                    p.setBrush(Qt.NoBrush)
                    p.drawPath(path)
                    if not dim:
                        self._pruned_mark(p, pts, v.colour)
                    continue
                rate = self._rate(e)
                live = rate >= _LIVE_RATE
                width = 3.2 if live else 2.0
                p.setPen(QPen(self._colour(v.colour, 50 if dim else (235 if live else 140)),
                              width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                p.setBrush(Qt.NoBrush)
                p.drawPath(path)
                if not self.motion or not live:
                    self._arrow(p, pts, self._colour(v.colour, 50 if dim else 220))
                if self.motion and live and not dim:
                    n = 1 + min(2, int(rate // 2))
                    for j in range(n):
                        f = (now / _CROSS_S + j / n) % 1.0
                        q = _along(pts, f)
                        p.setPen(QPen(QColor(255, 255, 255, 170), 1))
                        p.setBrush(self._colour(v.colour))
                        p.drawEllipse(q, 3.4, 3.4)

    def _arrow(self, p, pts, colour: QColor) -> None:
        """A small head near the far end, so direction reads with motion off."""
        a, b = pts[-2], pts[-1]
        dx, dy = b.x() - a.x(), b.y() - a.y()
        n = math.hypot(dx, dy) or 1.0
        ux, uy = dx / n, dy / n
        tip = QPointF(b.x() - ux * (_HOST_W / 2 + 4), b.y() - uy * (_HOST_W / 2 + 4))
        left = QPointF(tip.x() - ux * 7 - uy * 4, tip.y() - uy * 7 + ux * 4)
        right = QPointF(tip.x() - ux * 7 + uy * 4, tip.y() - uy * 7 - ux * 4)
        p.setPen(Qt.NoPen)
        p.setBrush(colour)
        p.drawPolygon(QPolygonF([tip, left, right]))

    def _pruned_mark(self, p, pts, colour: str) -> None:
        q = _along(pts, 0.5)
        p.setPen(QPen(self._colour(colour, 220), 1.6))
        for dx, dy in ((-3, -3), (-3, 3)):
            p.drawLine(QPointF(q.x() + dx, q.y() + dy), QPointF(q.x() - dx, q.y() - dy))

    def _paint_nodes(self, p, fit) -> None:
        t = self.theme.theme
        segs = segments(self._addressing)
        routers = {r for s in segs.values() for r, _ in s.routers}
        hosts = {h for s in segs.values() for h in s.hosts}
        hubs = {s.hub for s in segs.values() if s.hub}
        mm = self._map
        p.setFont(QFont(self.font().family(), 7, QFont.Bold))
        for name in hubs:
            if name in fit:
                p.setPen(QPen(QColor(t.line), 1))
                p.setBrush(QColor(t.panel2))
                p.drawRect(QRectF(fit[name].x() - 4, fit[name].y() - 4, 8, 8))
        for name in sorted(hosts):
            if name not in fit:
                continue
            c = fit[name]
            box = QRectF(c.x() - _HOST_W / 2, c.y() - _HOST_H / 2, _HOST_W, _HOST_H)
            rings = []
            if mm:
                for g, v in mm.groups.items():
                    if name in v.members:
                        rings.append((v.colour, Qt.SolidLine, g))
                    elif name in v.maybe:
                        rings.append((v.colour, Qt.DotLine, g))
            for k, (col, style, g) in enumerate(rings):
                grow = 3 + 3 * k
                p.setPen(QPen(self._colour(col, 60 if self._dim(g) else 255), 1.6, style))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(box.adjusted(-grow, -grow, grow, grow), 6 + k, 6 + k)
            p.setPen(QPen(QColor(t.line), 1.2))
            p.setBrush(QColor(t.panel2))
            p.drawRoundedRect(box, 4, 4)
            p.setPen(QColor(t.text))
            p.drawText(box, Qt.AlignCenter, name[:5])
            if mm:                                   # a source: a ▶ in its group's colour
                k = 0
                for g, v in mm.groups.items():
                    if name in v.sources:
                        cx, cy = box.left() - 2 + 9 * k, box.top() - 4
                        p.setPen(Qt.NoPen)
                        p.setBrush(self._colour(v.colour, 70 if self._dim(g) else 255))
                        p.drawPolygon(QPolygonF([QPointF(cx, cy - 5), QPointF(cx, cy + 5),
                                                 QPointF(cx + 8, cy)]))
                        k += 1
        for name in sorted(routers):
            if name not in fit:
                continue
            c = fit[name]
            running = name in self._forwarders
            p.setPen(QPen(QColor(t.line if running else t.faint), 1.8,
                          Qt.SolidLine if running else Qt.DashLine))
            p.setBrush(QColor(t.panel2 if running else t.panel))
            p.drawEllipse(c, _ROUTER_R, _ROUTER_R)
            p.setPen(QColor(t.text if running else t.faint))
            p.drawText(QRectF(c.x() - _ROUTER_R, c.y() - 6, 2 * _ROUTER_R, 12),
                       Qt.AlignCenter, name[:4])
            drops = (mm.drops.get(name, 0) if mm else 0)
            if drops:
                b = QRectF(c.x() + _ROUTER_R - 6, c.y() - _ROUTER_R - 8, 26, 13)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(t.accent_for("red")))
                p.drawRoundedRect(b, 6, 6)
                p.setPen(QColor("#ffffff"))
                p.drawText(b, Qt.AlignCenter, f"✕{drops}")

    def _paint_footer(self, p) -> None:
        t = self.theme.theme
        y = self.height() - 62
        p.setFont(QFont(self.font().family(), 8))
        p.setPen(QColor(t.muted))
        p.drawText(QRectF(12, y, self.width() - 24, 14), Qt.AlignLeft | Qt.AlignVCenter,
                   self.summary())
        evs = self._tracker.recent_events(3) if self._tracker else []
        p.setFont(QFont(self.font().family(), 7))
        now = time.monotonic()
        for k, e in enumerate(reversed(evs)):
            view = self._map.groups.get(e.group) if self._map else None
            colour = t.accent_for(view.colour) if view else t.muted
            p.setPen(QColor(colour))
            p.drawText(QRectF(12, y + 16 + 13 * k, self.width() - 24, 12),
                       Qt.AlignLeft | Qt.AlignVCenter,
                       f"{describe_event(self._addressing, e)}   ·   "
                       f"{max(0, int(now - e.t))}s ago")

    def summary(self) -> str:
        """The one line under the map. Says what is going on — or exactly why nothing is."""
        routers = [r for r in self._routers]
        if routers and not self._forwarders:
            return ("No multicast forwarder is running. On every router:  "
                    "gpipe cp add lua /scripts/mcast_tree.lua 1000")
        missing = [r for r in routers if r not in self._forwarders]
        lacking = (f"  ·  not running it: {', '.join(missing)}" if missing else "")
        if not self._map or not self._map.groups:
            on = ", ".join(sorted(self._forwarders))
            return (f"Forwarder running on {on} — no group yet: start a receiver (it joins) "
                    f"or a sender{lacking}")
        if self._focus:
            v = self._map.groups[self._focus]
            to = v.members or v.maybe
            rate = max((self._rate(e) for e in v.edges if e.kind == "data" and e.router),
                       default=0.0)
            return (f"{v.group}  ·  from {', '.join(v.sources) or 'no sender yet'}  ·  to "
                    f"{', '.join(to) or 'no member yet'}"
                    + (f"  ·  {rate:.0f}/s" if rate >= _LIVE_RATE else "") + lacking)
        n = len(self._map.groups)
        return (f"{n} group{'s' if n != 1 else ''}  ·  click one to follow it"
                f"  ·  dashed = pruned{lacking}")

    # -- interaction --------------------------------------------------------- #
    def mousePressEvent(self, e) -> None:
        pos = e.position() if hasattr(e, "position") else e.pos()
        if self._motion_rect.contains(pos):
            self.motion = not self.motion
            self._sync_motion()
            self.update()
            return
        for r, g in self._chip_rects:
            if r.contains(pos):
                self._focus = None if self._focus == g else g
                self.update()
                return


def _along(pts: list[QPointF], f: float) -> QPointF:
    """The point a fraction `f` of the way along a polyline."""
    segs = [(a, b, math.hypot(b.x() - a.x(), b.y() - a.y())) for a, b in pairwise(pts)]
    total = sum(s[2] for s in segs) or 1.0
    d = f * total
    for a, b, n in segs:
        if d <= n or n == 0:
            u = 0.0 if n == 0 else d / n
            return QPointF(a.x() + (b.x() - a.x()) * u, a.y() + (b.y() - a.y()) * u)
        d -= n
    return pts[-1]


class McastHudController(QObject):
    """Owns a McastHud and refreshes it live off the GUI thread. Injected callables
    (never imports main_window):

        self._mhud = McastHudController(
            self.canvas, self.theme,
            routers=lambda: [d.name for d in devs.values()
                             if d.type_key in ("router", "firewall")],
            query=self.element_query_checked,  # (name, cmd) -> text; RAISES on failure
            addressing=lambda: self.ctx.addressing,
            positions=lambda: {d.name: (d.x, d.y) for d in devs.values()},
            host_shell=self._machine_shell)    # (machine, cmd) -> text
        self._mhud.show_topright()             # toggle off -> self._mhud.close()

    `query` must raise when it did not reach the router. A router counts as polled only
    when its query returned, and a polled router with no group rows has lost them all —
    so a failure passed off as text wiped the router's groups for one poll.

    `host_shell` is asked only about machines on SHARED segments, every few polls: a router
    knows that someone on an interface joined, not who, and on a LAN of several machines the
    machines themselves are the only ones who can say.
    """

    rows_ready = Signal(object, object, float, object)  # (rows, polled, tnow, extras)

    def __init__(self, parent, theme, routers, query, interval_ms: int = 1200,
                 addressing=None, positions=None, host_shell=None) -> None:
        super().__init__(parent)
        self.hud = McastHud(parent, theme)
        self._routers = routers
        self._query = query
        self._addressing = addressing or (dict)
        self._positions = positions or (dict)
        self._host_shell = host_shell
        self._tracker = McastTracker()
        self._colours: dict[str, str] = {}
        self._joined: dict[str, set[str]] = {}
        self._polls = 0
        self._busy = False
        self.rows_ready.connect(self._on_rows)
        self._poll = QTimer(self)
        self._poll.timeout.connect(self.refresh)
        self._interval = interval_ms

    def _on_rows(self, rows, polled, tnow: float, extras=None) -> None:
        extras = extras or {}
        self._tracker.ingest(rows, tnow, polled=set(polled))
        addressing = extras.get("addressing") or {}
        if "joined" in extras:
            self._joined.update(extras["joined"])
        mmap = build(addressing, rows, extras.get("trees") or [], self._joined, self._colours)
        self.hud.set_state(mmap, self._tracker, addressing, extras.get("positions") or {},
                           extras.get("forwarders") or set(), extras.get("routers") or [])

    def refresh(self) -> None:
        if self._busy:
            return

        # Snapshot the router list HERE, on the GUI thread -- see the same note in
        # flow_hud.refresh(). `_routers()` iterates ctx.topology.devices, which a project
        # load replaces outright; iterating it from the worker raises inside a thread whose
        # only handler is a `finally`, so the poll vanishes with no explanation. The
        # addressing and positions are GUI-thread data too, and cross as finished copies.
        try:
            names = list(self._routers())
            addressing = dict(self._addressing() or {})
            positions = dict(self._positions() or {})
        except Exception:                       # noqa: BLE001
            return                              # topology in flux — skip this tick, not fatal
        self._busy = True
        self._polls += 1
        ask_hosts = self._host_shell is not None and self._polls % _ASK_HOSTS_EVERY == 1

        def work():
            try:
                tnow = time.monotonic()
                rows, polled, trees, forwarders = [], [], [], set()
                for name in names:
                    try:
                        text = self._query(name, "gpipe cp status")
                    except Exception:           # noqa: BLE001, S112 - not reached: not polled
                        continue
                    rows.extend(parse_cp_status(text, router=name))
                    trees.extend(parse_sources(text, router=name))
                    if runs_forwarder(text):
                        forwarders.add(name)
                    polled.append(name)
                extras = {"trees": trees, "forwarders": forwarders, "routers": names,
                          "addressing": addressing, "positions": positions}
                if ask_hosts:
                    joined = {}
                    for h in shared_lans(addressing, rows):
                        # An empty answer is NO answer (the shell returns "" on any failure),
                        # never "joined nothing" — that would wipe a member off the picture.
                        try:
                            text = self._host_shell(h, "cat /proc/net/igmp") or ""
                        except Exception:       # noqa: BLE001 - unknown, never "joined nothing"
                            text = ""
                        if text.strip():
                            joined[h] = parse_igmp(text)
                    extras["joined"] = joined
                self.rows_ready.emit(rows, polled, tnow, extras)
            finally:
                self._busy = False
        run_off_gui(self, work)

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
        self._colours = {}
        self._joined = {}

    def close(self) -> None:
        self._poll.stop()
        self.hud.hide()
