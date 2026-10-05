"""The Router Lab's flow band: the pipeline drawn left → right, packets moving through it, and the
traffic on every interface.

The old Router Lab stacked the pipeline vertically — five fixed boxes taking two fifths of the
window — with a full-height palette beside it and four always-open panels below, so on a laptop
the routing table and QoS sat under the fold, beneath a diagram that never changes. The band is
one compact strip instead, read the way a packet travels:

    tun1 ↓ 4.2 Mb/s ─┐   ┌──────┐  ┌─────┐  ┌───────┐  ┌─────────┐  ┌──────┐   ┌─ tun2 ↑ 3.9 Mb/s
    tun2 ↓  80 kb/s ─┼──▶│Parse │─▶│ ACL │─▶│ Route │─▶│ Rewrite │─▶│egress│──▶┼─ router itself
                     ┘   └──────┘  └─────┘  └───────┘  └─────────┘  └──────┘   └─ dropped  12

Three layers, one widget each, so each stays simple:

  * `IfaceRail` — the interfaces down each side, with live bits-per-second and a sparkline. In on
    the left (what each interface RECEIVES), out on the right (what it SENDS), plus two sinks a
    packet can end at that are not interfaces: the router itself, and dropped.
  * `StageChip` — one box per stage. These are real widgets (they take clicks, the step debugger
    highlights them), laid out in a row.
  * `PacketOverlay` — a transparent layer over all of it that animates packets from the rail,
    through the chips, to where the router says each one ended up. It takes no mouse input.

Packets come from the router's own `watch` ring (gini.domain.router_watch), which is OFF by
default and free while off. The animation is a faithful replay, not a simulation: every token is
one recorded packet, spaced by the router's own timestamps, ending at the stage that decided it —
forwarded out tunN, dropped by the module that dropped it, stopped at Route for want of a route,
stopped at Parse when its TTL ran out. When packets arrive faster than they can be drawn, the
band draws a sample and SAYS so; a visualizer that quietly thinned the traffic would teach the
wrong rate.

`RouterStream` is the feed: one persistent console session per open Lab, driven through QProcess
(no Python threads — see manual §17), so a poll costs a line written to a pipe instead of a fresh
`docker compose exec`.
"""
from __future__ import annotations

import collections
import time

from PySide6.QtCore import QObject, QPointF, QProcess, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from ..domain import router_watch as RW
from ..domain.router_modules import MODULE_BY_KEY
from .theme import ThemeManager, icons

#: Colour per protocol, by theme accent name. Anything else is drawn in the muted tone.
PROTO_ACCENT = {1: "blue", 6: "green", 17: "amber", RW.ARP: "purple"}

#: How long one packet takes to cross the band, and the most drawn at once. Past that, the band
#: samples — and says so in its caption.
TRAVEL_MS = 1100
MAX_IN_FLIGHT = 48
FRAME_MS = 33

#: Samples kept per interface for the sparkline (one per `ifstat` reading, about a second each).
SPARK_N = 40


# --------------------------------------------------------------------------- #
# stage chips
# --------------------------------------------------------------------------- #

class StageChip(QFrame):
    """One stage of the pipeline, compact. Inline modules are clickable (to edit them)."""

    clicked = Signal(object)                 # the chip

    def __init__(self, theme: ThemeManager, stage) -> None:
        super().__init__()
        t = theme.theme
        self.stage = stage
        self._accent = t.accent_for(stage.accent)
        self._theme = theme
        self.setObjectName("Card")
        self.setFixedHeight(54)
        self.setMinimumWidth(84)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        lay = QVBoxLayout(self); lay.setContentsMargins(8, 5, 8, 5); lay.setSpacing(1)
        top = QHBoxLayout(); top.setSpacing(5)
        iname = {"ingress": "chevron_right", "egress": "chevron_right",
                 "mode": "controller"}.get(stage.kind)
        if iname is None:
            iname = MODULE_BY_KEY[stage.key].icon if stage.key in MODULE_BY_KEY else "dot"
        ic = QLabel(); ic.setPixmap(icons.render_pixmap(iname, self._accent, 14))
        name = QLabel(self._short(stage.label))
        name.setStyleSheet("font-weight:600; font-size:12px;")
        top.addWidget(ic); top.addWidget(name); top.addStretch(1)
        lay.addLayout(top)
        self.sub = QLabel(self._subtitle(stage)); self.sub.setObjectName("Faint")
        self.sub.setStyleSheet("font-size:10px;")
        lay.addWidget(self.sub)
        self.setToolTip(stage.label + ("\n\nClick to edit its parameters, move or remove it."
                                       if stage.kind == "inline" else
                                       "\n\nPart of the router's fixed base pipeline."))
        self.set_selected(False)
        if stage.kind == "inline":
            self.setCursor(Qt.PointingHandCursor)

    #: Chip names short enough to fit seven across a laptop screen; the full name is the tooltip.
    SHORT = {"Route lookup": "Route", "ACL / Firewall": "ACL", "Block IP": "Block",
             "Rate limit": "Rate", "QoS classifier": "QoS", "Tap / capture": "Tap",
             "Native VNF": "Native", "Lua VNF": "Lua"}

    @classmethod
    def _short(cls, label: str) -> str:
        if label.startswith("Lua VNF · "):              # a mirrored script: show its name
            return label.split("· ", 1)[1]
        return cls.SHORT.get(label, label)

    @staticmethod
    def _subtitle(stage) -> str:
        """The second line: what a module is set to, or that a base stage is fixed."""
        if stage.kind != "inline":
            return "fixed"
        detail = (getattr(stage, "detail", "") or "").strip()
        return detail if detail else "click to edit"

    def set_selected(self, on: bool, border: str | None = None) -> None:
        t = self._theme.theme
        b = border or (self._accent if on else t.line)
        w = 2 if on or border else 1
        self.setStyleSheet(f"QFrame#Card{{background:{t.panel2};border:{w}px solid {b};"
                           f"border-radius:10px;}}")

    def mousePressEvent(self, e):            # noqa: N802 - Qt naming
        if self.stage.kind == "inline":
            self.clicked.emit(self)
        super().mousePressEvent(e)


# --------------------------------------------------------------------------- #
# interface rails
# --------------------------------------------------------------------------- #

class IfaceRail(QWidget):
    """The interfaces down one side of the band, with their live rate and a sparkline.

    `side="in"` shows what each interface RECEIVES; `side="out"` what it SENDS, plus two rows for
    the places a packet can end that are not interfaces: the router itself, and dropped.
    """

    ROW_H = 40

    def __init__(self, theme: ThemeManager, side: str) -> None:
        super().__init__()
        self.theme = theme
        self.side = side
        self.ifaces: dict[int, RW.IfStat] = {}
        self.rates: dict[int, float] = {}
        self.spark: dict[int, collections.deque] = {}
        self.dropped = 0
        self.local = 0                       # packets that ended at the router itself
        self.sent = 0                        # packets the router itself sent
        self.setFixedWidth(150)
        self.note = ""                       # e.g. "router image predates the meter"

    def keys(self) -> list:
        ks = sorted(self.ifaces)
        return ks + (["local", "drop"] if self.side == "out" else [])

    def preferred_height(self) -> int:
        return max(2, len(self.keys())) * self.ROW_H + 12

    def row_center(self, key) -> float:
        ks = self.keys()
        i = ks.index(key) if key in ks else 0
        top = (self.height() - len(ks) * self.ROW_H) / 2
        return top + i * self.ROW_H + self.ROW_H / 2

    def edge_x(self) -> float:
        """Where packets leave (in rail) or arrive (out rail), in this widget's coordinates."""
        return self.width() - 6 if self.side == "in" else 6

    def set_ifaces(self, ifaces: dict) -> None:
        if set(ifaces) != set(self.ifaces):
            self.ifaces = dict(ifaces)
            self.updateGeometry()
        else:
            self.ifaces = dict(ifaces)
        self.update()

    def set_rates(self, rates: dict) -> None:
        for i, r in rates.items():
            bps = r.rx_bps if self.side == "in" else r.tx_bps
            self.rates[i] = bps
            self.spark.setdefault(i, collections.deque(maxlen=SPARK_N)).append(bps)
        self.update()

    def paintEvent(self, _e):                 # noqa: N802 - Qt naming
        t = self.theme.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        ks = self.keys()
        if not ks or (not self.ifaces and self.side == "in"):
            p.setPen(QColor(t.faint))
            p.drawText(self.rect().adjusted(6, 0, -6, 0), Qt.AlignVCenter | Qt.TextWordWrap,
                       self.note or ("interfaces appear\nwhen the router runs"
                                     if self.side == "in" else ""))
            if self.side == "in":
                return
        f = QFont(self.font()); f.setPointSizeF(max(8.0, f.pointSizeF() - 1))
        for key in ks:
            y = self.row_center(key)
            box = QRectF(4, y - self.ROW_H / 2 + 3, self.width() - 8, self.ROW_H - 6)
            p.setPen(QPen(QColor(t.line), 1)); p.setBrush(QColor(t.panel2))
            p.drawRoundedRect(box, 8, 8)
            if key == "local":
                p.setPen(QColor(t.text)); p.setFont(f)
                p.drawText(box.adjusted(8, 0, -8, 0), Qt.AlignVCenter | Qt.AlignLeft,
                           f"router itself   in {self.local} · out {self.sent}")
                continue
            if key == "drop":
                p.setPen(QColor(t.danger)); p.setFont(f)
                p.drawText(box.adjusted(8, 0, -8, 0), Qt.AlignVCenter | Qt.AlignLeft,
                           f"✕ dropped   {self.dropped}")
                continue
            st = self.ifaces[key]
            bps = self.rates.get(key, 0.0)
            arrow = "↓" if self.side == "in" else "↑"
            p.setPen(QColor(t.text)); p.setFont(f)
            p.drawText(box.adjusted(8, 2, -8, 0), Qt.AlignTop | Qt.AlignLeft, st.name)
            p.setPen(QColor(t.muted))
            p.drawText(box.adjusted(8, 2, -8, 0), Qt.AlignTop | Qt.AlignRight,
                       f"{arrow} {RW.human_bps(bps)}")
            # sparkline along the bottom of the row
            hist = list(self.spark.get(key, ()))
            if len(hist) >= 2:
                top = max(max(hist), 1.0)
                x0, x1 = box.left() + 8, box.right() - 8
                yb, h = box.bottom() - 4, 11
                path = QPainterPath()
                for n, v in enumerate(hist):
                    x = x0 + (x1 - x0) * n / (SPARK_N - 1)
                    yy = yb - h * (v / top)
                    path.moveTo(x, yy) if n == 0 else path.lineTo(x, yy)
                p.setPen(QPen(QColor(t.accent_for("blue" if self.side == "in" else "green")),
                              1.4))
                p.setBrush(Qt.NoBrush)
                p.drawPath(path)
        p.end()


# --------------------------------------------------------------------------- #
# the packet overlay
# --------------------------------------------------------------------------- #

class _Token:
    __slots__ = ("points", "lengths", "total", "born", "color", "fate", "burst")

    def __init__(self, points, color, fate, born):
        self.points = points
        self.lengths = [((b.x() - a.x()) ** 2 + (b.y() - a.y()) ** 2) ** 0.5
                        for a, b in zip(points, points[1:])]
        self.total = sum(self.lengths) or 1.0
        self.born = born
        self.color = color
        self.fate = fate
        self.burst = fate in ("D", "N", "T")

    def at(self, frac: float) -> QPointF:
        d = self.total * min(1.0, max(0.0, frac))
        for (a, b), seg in zip(zip(self.points, self.points[1:]), self.lengths):
            if d <= seg or seg == 0:
                k = 0 if seg == 0 else d / seg
                return QPointF(a.x() + (b.x() - a.x()) * k, a.y() + (b.y() - a.y()) * k)
            d -= seg
        return self.points[-1]


class PacketOverlay(QWidget):
    """Transparent layer over the band that draws packets travelling through it."""

    def __init__(self, band: "FlowBand") -> None:
        super().__init__(band)
        self.band = band
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.tokens: list[_Token] = []
        self.pending: collections.deque = collections.deque()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._next_release = 0.0
        self._last_router_ms = None

    def enqueue(self, events: list) -> int:
        """Queue recorded packets for drawing. Returns how many were left out as a sample."""
        dropped = 0
        for e in events:
            self.pending.append(e)
        while len(self.pending) > MAX_IN_FLIGHT * 2:
            self.pending.popleft()
            dropped += 1
        if self.pending and not self._timer.isActive():
            self._timer.start(FRAME_MS)
        return dropped

    def clear(self) -> None:
        self.tokens.clear()
        self.pending.clear()
        self._timer.stop()
        self.update()

    def _tick(self) -> None:
        now = time.monotonic()
        # release queued packets, spaced by the router's own clock (clamped so a burst is still
        # readable and a lull does not stall the queue)
        while self.pending and now >= self._next_release and len(self.tokens) < MAX_IN_FLIGHT:
            e = self.pending.popleft()
            gap = 0.08
            if self._last_router_ms is not None:
                gap = min(0.4, max(0.04, (e.ms - self._last_router_ms) / 1000.0))
            self._last_router_ms = e.ms
            self._next_release = now + gap
            tok = self.band.token_for(e, now)
            if tok is not None:
                self.tokens.append(tok)
        alive = []
        for tok in self.tokens:
            age = (now - tok.born) * 1000
            if age < TRAVEL_MS + (380 if tok.burst else 0):
                alive.append(tok)
            elif age >= TRAVEL_MS:
                self.band.landed(tok)
        self.tokens = alive
        if not self.tokens and not self.pending:
            self._timer.stop()
        self.update()

    def paintEvent(self, _e):                 # noqa: N802 - Qt naming
        if not self.tokens:
            return
        t = self.band.theme.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        now = time.monotonic()
        for tok in self.tokens:
            age = (now - tok.born) * 1000
            frac = age / TRAVEL_MS
            if frac <= 1.0:
                # a short fading trail, then the packet itself
                for k, alpha in ((0.06, 50), (0.03, 110)):
                    pt = tok.at(frac - k)
                    c = QColor(tok.color); c.setAlpha(alpha)
                    p.setPen(Qt.NoPen); p.setBrush(c)
                    p.drawEllipse(pt, 4.0, 4.0)
                pt = tok.at(frac)
                p.setPen(QPen(QColor(t.panel), 1.2)); p.setBrush(QColor(tok.color))
                p.drawEllipse(pt, 5.5, 5.5)
            elif tok.burst:
                # the stage that stopped it: an expanding red ring and an ✕
                k = min(1.0, (age - TRAVEL_MS) / 380.0)   # the paint can run a frame late
                end = tok.points[-1]
                red = QColor(t.danger); red.setAlpha(int(255 * (1 - k)))
                p.setPen(QPen(red, 2)); p.setBrush(Qt.NoBrush)
                p.drawEllipse(end, 6 + 14 * k, 6 + 14 * k)
                s = 5
                p.drawLine(QPointF(end.x() - s, end.y() - s), QPointF(end.x() + s, end.y() + s))
                p.drawLine(QPointF(end.x() - s, end.y() + s), QPointF(end.x() + s, end.y() - s))
        p.end()


# --------------------------------------------------------------------------- #
# the band
# --------------------------------------------------------------------------- #

class FlowBand(QWidget):
    """Rails + stage chips + packet overlay, in one strip."""

    chip_clicked = Signal(object)            # a StageChip for an inline module

    def __init__(self, theme: ThemeManager) -> None:
        super().__init__()
        self.theme = theme
        self.chips: list[StageChip] = []
        self.watching = False
        self.rail_in = IfaceRail(theme, "in")
        self.rail_out = IfaceRail(theme, "out")
        row = QHBoxLayout(self); row.setContentsMargins(4, 4, 4, 4); row.setSpacing(0)
        row.addWidget(self.rail_in)
        self.chip_host = QWidget()
        self.chip_row = QHBoxLayout(self.chip_host)
        self.chip_row.setContentsMargins(22, 0, 22, 0)
        self.chip_row.setSpacing(18)
        row.addWidget(self.chip_host, 1)
        row.addWidget(self.rail_out)
        self.overlay = PacketOverlay(self)
        self.overlay.raise_()
        self._sync_height()

    # -- layout --------------------------------------------------------------- #
    def _sync_height(self) -> None:
        h = max(118, self.rail_in.preferred_height(), self.rail_out.preferred_height())
        self.setFixedHeight(h + 8)

    def resizeEvent(self, e):                 # noqa: N802 - Qt naming
        self.overlay.setGeometry(self.rect())
        super().resizeEvent(e)

    def set_stages(self, stages) -> list:
        while self.chip_row.count():
            item = self.chip_row.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self.chips = []
        for st in stages:
            chip = StageChip(self.theme, st)
            chip.clicked.connect(self.chip_clicked)
            self.chip_row.addWidget(chip, 1)
            self.chips.append(chip)
        self.overlay.clear()
        self.update()
        return self.chips

    def set_ifaces(self, ifaces: dict) -> None:
        self.rail_in.set_ifaces(ifaces)
        self.rail_out.set_ifaces(ifaces)
        self._sync_height()

    def set_rates(self, rates: dict) -> None:
        self.rail_in.set_rates(rates)
        self.rail_out.set_rates(rates)

    def set_note(self, text: str) -> None:
        self.rail_in.note = text
        self.rail_in.update()

    # -- arrows between chips --------------------------------------------------- #
    def paintEvent(self, _e):                 # noqa: N802 - Qt naming
        t = self.theme.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(t.faint), 1.6)
        p.setPen(pen)
        pts = [self._chip_center(c) for c in self.chips]
        for k in range(len(pts) - 1):
            a, b = pts[k], pts[k + 1]
            ca, cb = self.chips[k], self.chips[k + 1]
            x0 = a.x() + ca.width() / 2 + 2
            x1 = b.x() - cb.width() / 2 - 2
            p.drawLine(QPointF(x0, a.y()), QPointF(x1, b.y()))
            p.drawLine(QPointF(x1, b.y()), QPointF(x1 - 5, b.y() - 4))
            p.drawLine(QPointF(x1, b.y()), QPointF(x1 - 5, b.y() + 4))
        # the rails fan into the first chip and out of the last
        if pts:
            first, last = pts[0], pts[-1]
            fx = first.x() - self.chips[0].width() / 2
            lx = last.x() + self.chips[-1].width() / 2
            for key in self.rail_in.keys():
                y = self.rail_in.y() + self.rail_in.row_center(key)
                x = self.rail_in.x() + self.rail_in.edge_x()
                p.drawLine(QPointF(x, y), QPointF(fx - 2, first.y()))
            for key in self.rail_out.keys():
                y = self.rail_out.y() + self.rail_out.row_center(key)
                x = self.rail_out.x() + self.rail_out.edge_x()
                if key in ("local", "drop"):
                    continue
                p.drawLine(QPointF(lx + 2, last.y()), QPointF(x, y))
        if self.chips:
            host = self.chip_host.geometry()
            y = self.lane_y()
            first, last = self._lane_point(self.chips[0]), self._lane_point(self.chips[-1])
            if self.watching:
                # the lane packets ride, under the stages
                lane = QPen(QColor(t.line), 1, Qt.DashLine)
                p.setPen(lane)
                p.drawLine(QPointF(first.x() - 30, y), QPointF(last.x() + 30, y))
            else:
                p.setPen(QColor(t.faint))
                f = QFont(self.font()); f.setPointSizeF(max(8.0, f.pointSizeF() - 1))
                p.setFont(f)
                p.drawText(QRectF(host.left(), y - 2, host.width(), 20),
                           Qt.AlignHCenter | Qt.AlignVCenter,
                           "packet watch is off — turn it on (top right) to see packets move")
        p.end()

    def _chip_center(self, chip) -> QPointF:
        c = chip.mapTo(self, chip.rect().center())
        return QPointF(c.x(), c.y())

    #: Packets ride a lane this far under the chips, so they never cover a stage's name.
    LANE_GAP = 10

    def _lane_point(self, chip) -> QPointF:
        """Where a packet passes a stage: on the lane, directly under the chip."""
        c = chip.mapTo(self, chip.rect().center())
        bottom = chip.mapTo(self, chip.rect().bottomLeft()).y()
        return QPointF(c.x(), bottom + self.LANE_GAP)

    def lane_y(self) -> float:
        return self._lane_point(self.chips[0]).y() if self.chips else 0.0

    # -- turning a recorded packet into a path ---------------------------------- #
    def _find(self, pred):
        return next((c for c in self.chips if pred(c.stage)), None)

    def token_for(self, e: RW.PacketEvent, born: float):
        """The path one recorded packet takes, ending where the router says it ended."""
        if not self.chips:
            return None
        rin = self.rail_in
        start = QPointF(rin.x() + rin.edge_x(), rin.y() + rin.row_center(e.in_if))
        pts = [start]
        order = list(self.chips)
        parse = self._find(lambda s: s.key == "parse")
        route = self._find(lambda s: s.key == "route")
        inline = [c for c in order if c.stage.kind == "inline"]

        def through(upto):
            for c in order:
                pts.append(self._lane_point(c))
                if c is upto:
                    break

        ro = self.rail_out
        local = QPointF(ro.x() + ro.edge_x(), ro.y() + ro.row_center("local"))
        if e.fate == "S":
            # SENT by the router: out of "router itself", through egress, to its interface.
            # It never passed Parse, Route or Rewrite on the way in — it did not come in.
            egress = self._find(lambda s: s.kind == "egress") or order[-1]
            t = self.theme.theme
            color = QColor(t.accent_for(PROTO_ACCENT[e.proto]) if e.proto in PROTO_ACCENT
                           else t.muted)
            return _Token([local, self._lane_point(egress),
                           QPointF(ro.x() + ro.edge_x(), ro.y() + ro.row_center(e.out_if))],
                          color, e.fate, born)
        if e.fate == "A":
            # An ARP that arrived: in at ingress and straight to the router itself. It is not
            # routed — it never reaches Parse/Route/Rewrite — which is the lesson.
            through(self._find(lambda s: s.kind == "ingress") or order[0])
            pts.append(local)
        elif e.fate == "T":
            through(parse or order[0])
        elif e.fate == "D":
            stop = next((c for c in inline if c.stage.index == e.module), None)
            if stop is None and e.module_type:        # editor not in sync: match by type
                stop = next((c for c in inline if c.stage.key == e.module_type), None)
            through(stop or route or order[-1])
        elif e.fate == "N":
            through(route or order[-1])
        elif e.fate == "L":
            through(parse or order[0])
            pts.append(local)
        else:                                          # forwarded
            through(order[-1])
            pts.append(QPointF(ro.x() + ro.edge_x(), ro.y() + ro.row_center(e.out_if)))
        t = self.theme.theme
        color = QColor(t.accent_for(PROTO_ACCENT[e.proto]) if e.proto in PROTO_ACCENT else t.muted)
        return _Token(pts, color, e.fate, born)

    def landed(self, tok: _Token) -> None:
        """A packet finished its trip: count it where it ended."""
        if tok.fate in ("D", "N", "T"):
            self.rail_out.dropped += 1
        elif tok.fate in ("L", "A"):
            self.rail_out.local += 1
        elif tok.fate == "S":
            self.rail_out.sent += 1
        self.rail_out.update()

    def show_packets(self, events: list) -> int:
        return self.overlay.enqueue(events)

    def set_watching(self, on: bool) -> None:
        self.watching = on
        if not on:
            self.overlay.clear()
        self.update()


# --------------------------------------------------------------------------- #
# the feed: one persistent console session
# --------------------------------------------------------------------------- #

class RouterStream(QObject):
    """A long-lived router console, one command at a time, replies split at the prompt.

    QProcess on the GUI thread — its output arrives as a signal — so nothing here owns a thread,
    and the process dies with the Lab that parented it. A command waits for the previous reply;
    a repeated request for one already queued is ignored, so a slow router gets fewer polls
    rather than a growing backlog.
    """

    watch_dump = Signal(object)              # RW.WatchDump
    ifstat = Signal(object)                  # {iface id: RW.IfStat}
    state = Signal(str)                      # human-readable status for the Lab

    STALL_S = 6.0

    def __init__(self, parent, argv: list, cwd: str = "") -> None:
        super().__init__(parent)
        self.argv = list(argv)
        self.cwd = cwd
        self.proc: QProcess | None = None
        self.feed = RW.ConsoleFeed()
        self.queue: list[str] = []
        self.inflight: str | None = None
        self._sent_at = 0.0
        self.unsupported: set[str] = set()
        self._watchdog = QTimer(self)
        self._watchdog.timeout.connect(self._check_stall)

    def start(self) -> None:
        if self.proc is not None or not self.argv:
            return
        self.feed = RW.ConsoleFeed()
        self.queue, self.inflight = [], None
        p = QProcess(self)
        if self.cwd:
            p.setWorkingDirectory(self.cwd)
        p.setProcessChannelMode(QProcess.MergedChannels)
        p.readyReadStandardOutput.connect(self._read)
        p.finished.connect(self._finished)
        p.start(self.argv[0], self.argv[1:])
        self.proc = p
        self._watchdog.start(1000)
        self.state.emit("connecting to the router…")

    def stop(self, farewell: str = "") -> None:
        p, self.proc = self.proc, None
        self._watchdog.stop()
        if p is None:
            return
        try:
            if farewell and p.state() == QProcess.Running:
                p.write((farewell + "\n").encode())
                p.waitForBytesWritten(300)
            p.closeWriteChannel()
            if not p.waitForFinished(400):
                p.kill()
                p.waitForFinished(300)
        except RuntimeError:
            pass

    def request(self, cmd: str) -> None:
        if self.proc is None or cmd in self.queue or cmd == self.inflight:
            return
        self.queue.append(cmd)
        self._pump()

    def _pump(self) -> None:
        if self.proc is None or self.inflight or not self.queue or not self.feed.ready:
            return
        self.inflight = self.queue.pop(0)
        self._sent_at = time.monotonic()
        self.proc.write((self.inflight + "\n").encode())

    def _read(self) -> None:
        if self.proc is None:
            return
        text = bytes(self.proc.readAllStandardOutput()).decode("utf-8", "replace")
        was_ready = self.feed.ready
        replies = self.feed.feed(text)
        if self.feed.ready and not was_ready:
            self.state.emit("live")
        for reply in replies:
            cmd, self.inflight = self.inflight, None
            self._handle(cmd or "", reply)
        self._pump()

    def _handle(self, cmd: str, reply: str) -> None:
        if cmd.startswith("watch dump"):
            d = RW.parse_watch(reply)
            if not d.ok:
                self.unsupported.add("watch")
                self.state.emit("this router image predates packet watch — rebuild gini-grouter")
                return
            self.watch_dump.emit(d)
        elif cmd == "ifstat":
            s = RW.parse_ifstat(reply)
            if not s and "IFSTAT" not in reply:
                self.unsupported.add("ifstat")
                self.state.emit("this router image predates the bandwidth meter — rebuild "
                                "gini-grouter")
                return
            self.ifstat.emit(s)

    def _check_stall(self) -> None:
        if self.inflight and time.monotonic() - self._sent_at > self.STALL_S:
            self.state.emit("the router stopped answering — reconnecting")
            self.stop()
            self.start()

    def _finished(self, *_a) -> None:
        if self.proc is not None:            # died on its own, not stopped by us
            self.proc = None
            self._watchdog.stop()
            self.state.emit("router console closed (is the topology still running?)")
