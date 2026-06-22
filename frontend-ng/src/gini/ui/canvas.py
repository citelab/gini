"""The topology canvas: a themed QGraphicsView scene with node + edge items.

Drives off the AppContext: dropping a palette item or an agent call adds a device to
the topology, which emits `device_added`, which the scene turns into a NodeItem. The
model stays the single source of truth; the scene is a view of it.
"""
from __future__ import annotations

import math

from PySide6.QtCore import (
    QEasingCurve, QPointF, QRect, QRectF, Qt, QTimer, QVariantAnimation,
)
from PySide6.QtGui import (
    QBrush, QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPolygonF,
)
from PySide6.QtWidgets import (
    QGraphicsDropShadowEffect, QGraphicsItem, QGraphicsObject, QGraphicsScene,
    QGraphicsView, QLabel,
)

from ..app import AppContext
from ..domain import pricing
from ..domain.topology import DeviceInstance, Link
from .theme import icons
from .theme.tokens import Theme

MIME = "application/x-gini-device"
GRID = 22
NODE_W, NODE_H = 138, 84
SIZE_STEP = 30         # px the node grows taller per size tier above S (vertical scaling)
CORNER_R = 13          # rounded-corner radius for bent connectors


def _qcolor(spec: str) -> QColor:
    if spec.startswith("rgba("):
        nums = [int(float(x)) for x in spec[5:-1].split(",")]
        return QColor(nums[0], nums[1], nums[2], nums[3] if len(nums) > 3 else 255)
    return QColor(spec)


def _blend(c1: QColor, c2: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(
        int(c1.red() + (c2.red() - c1.red()) * t),
        int(c1.green() + (c2.green() - c1.green()) * t),
        int(c1.blue() + (c2.blue() - c1.blue()) * t),
    )


def _ortho_waypoints(a: "NodeItem", b: "NodeItem") -> list[QPointF]:
    """Manhattan (horizontal/vertical) waypoints from A's border to B's border.

    Exits perpendicular to the nearer face and turns at the midline, giving a tidy
    two-bend 'Z' that the rounded-path builder softens into smooth elbows.
    """
    ax, ay = a.pos().x(), a.pos().y()
    bx, by = b.pos().x(), b.pos().y()
    ah, bh = a.node_h(), b.node_h()              # per-node heights (size tiers differ)
    ca = QPointF(ax + NODE_W / 2, ay + ah / 2)
    cb = QPointF(bx + NODE_W / 2, by + bh / 2)
    dx, dy = cb.x() - ca.x(), cb.y() - ca.y()
    if abs(dy) >= abs(dx):                       # stacked-ish -> exit top/bottom
        if dy >= 0:
            ex, en = QPointF(ca.x(), ay + ah), QPointF(cb.x(), by)
        else:
            ex, en = QPointF(ca.x(), ay), QPointF(cb.x(), by + bh)
        mid = (ex.y() + en.y()) / 2
        return [ex, QPointF(ex.x(), mid), QPointF(en.x(), mid), en]
    else:                                        # side-by-side -> exit left/right
        if dx >= 0:
            ex, en = QPointF(ax + NODE_W, ca.y()), QPointF(bx, cb.y())
        else:
            ex, en = QPointF(ax, ca.y()), QPointF(bx + NODE_W, cb.y())
        mid = (ex.x() + en.x()) / 2
        return [ex, QPointF(mid, ex.y()), QPointF(mid, en.y()), en]


def _rounded_path(pts: list[QPointF], r: float) -> QPainterPath:
    """A polyline through `pts` with each interior corner rounded by radius `r`."""
    path = QPainterPath()
    # drop near-duplicate / collinear-collapsed points so corners are well defined
    clean: list[QPointF] = []
    for p in pts:
        if not clean or (p - clean[-1]).manhattanLength() > 0.5:
            clean.append(p)
    if not clean:
        return path
    path.moveTo(clean[0])
    if len(clean) == 2:
        path.lineTo(clean[1])
        return path
    for i in range(1, len(clean) - 1):
        prev, cur, nxt = clean[i - 1], clean[i], clean[i + 1]
        vin, vout = cur - prev, nxt - cur
        lin = math.hypot(vin.x(), vin.y())
        lout = math.hypot(vout.x(), vout.y())
        if lin < 1e-6 or lout < 1e-6:
            continue
        ri = min(r, lin / 2, lout / 2)
        path.lineTo(cur - vin * (ri / lin))      # straight up to the corner
        path.quadTo(cur, cur + vout * (ri / lout))  # round through it
    path.lineTo(clean[-1])
    return path


class NodeItem(QGraphicsObject):
    """Visual card for one DeviceInstance."""

    def __init__(self, scene: "CanvasScene", inst: DeviceInstance) -> None:
        super().__init__()
        self._scene = scene
        self.inst = inst
        self.status = "idle"
        self.setFlags(
            QGraphicsItem.ItemIsMovable
            | QGraphicsItem.ItemIsSelectable
            | QGraphicsItem.ItemSendsGeometryChanges
        )
        self.setPos(inst.x, inst.y)
        self.setZValue(10)
        self.setAcceptHoverEvents(True)
        self._hover = 0.0
        self._spot = False        # tutor spotlight
        self._ring = False        # tutor highlight
        self._anim: QVariantAnimation | None = None
        self._shadow = QGraphicsDropShadowEffect()
        self.refresh_theme()
        self.setGraphicsEffect(self._shadow)

    def refresh_theme(self) -> None:
        t = self._scene.theme
        self._shadow.setColor(_qcolor(t.shadow))
        self._shadow.setBlurRadius(t.elevation + 10 * self._hover)
        self._shadow.setOffset(0, 3 + 2 * self._hover)

    # size tier (resizable elements grow taller; others stay at the base height) ----- #
    def _resizable(self) -> bool:
        return pricing.resizable(self.inst.type_key)

    def _size(self) -> int:
        return pricing.size_level(getattr(self.inst, "size", 1)) if self._resizable() else 1

    def node_h(self) -> float:
        return NODE_H + (self._size() - 1) * SIZE_STEP

    def _stepper_rects(self) -> tuple[QRectF, QRectF]:
        """(minus, plus) hit rectangles for the on-node size stepper, at bottom-right."""
        y = self.node_h() - 25
        return (QRectF(NODE_W - 46, y, 19, 19), QRectF(NODE_W - 24, y, 19, 19))

    def _bump_size(self, delta: int) -> None:
        new = pricing.size_level(self._size() + delta)
        if new == self._size():
            return
        self.prepareGeometryChange()
        self.inst.size = new
        self.update()
        self._scene.update_edges_for(self.inst.id)
        # rebill the dashboard + mark the project dirty (size is persisted in .gini)
        self._scene.ctx.bus.topology_changed.emit()

    def boundingRect(self) -> QRectF:
        return QRectF(-3, -3, NODE_W + 6, self.node_h() + 6)

    # hover lift -------------------------------------------------------------- #
    def hoverEnterEvent(self, e):
        self._animate_hover(1.0)
        super().hoverEnterEvent(e)

    def hoverLeaveEvent(self, e):
        self._animate_hover(0.0)
        super().hoverLeaveEvent(e)

    def _animate_hover(self, target: float) -> None:
        if self._scene.ctx.settings.reduced_motion:
            self._set_hover(target)
            return
        if self._anim is not None:
            self._anim.stop()
        anim = QVariantAnimation(self)
        anim.setStartValue(self._hover)
        anim.setEndValue(target)
        anim.setDuration(self._scene.theme.dur_fast)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.valueChanged.connect(lambda v: self._set_hover(float(v)))
        anim.start()
        self._anim = anim

    def _set_hover(self, v: float) -> None:
        self._hover = v
        self._shadow.setBlurRadius(self._scene.theme.elevation + 10 * v)
        self._shadow.setOffset(0, 3 + 2 * v)
        self.update()

    def paint(self, p: QPainter, opt, widget=None) -> None:
        t = self._scene.theme
        dt = self.inst.type
        accent = _qcolor(t.accent_for(dt.accent.value))
        p.setRenderHint(QPainter.Antialiasing, True)

        H = self.node_h()
        rect = QRectF(0.5, 0.5, NODE_W - 1, H - 1)
        selected = self.isSelected()
        hover = self._hover

        # selection / focus / tutor ring
        ring_strength = 1.0 if (selected or self._spot or self._ring) else hover
        if ring_strength > 0.01:
            ring = QColor(accent)
            ring.setAlpha(int(80 * ring_strength))
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(ring, 3))
            p.drawRoundedRect(rect.adjusted(-2, -2, 2, 2), 12, 12)
            if self._spot:
                glow = QColor(accent)
                glow.setAlpha(45)
                p.setPen(QPen(glow, 6))
                p.drawRoundedRect(rect.adjusted(-5, -5, 5, 5), 14, 14)

        p.setBrush(QBrush(_qcolor(t.panel2)))
        border = accent if selected else _blend(_qcolor(t.line2), accent, hover)
        p.setPen(QPen(border, 2 if selected else 1.4))
        p.drawRoundedRect(rect, 10, 10)

        # icon swatch
        soft = QColor(accent)
        soft.setAlpha(40)
        p.setBrush(soft)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(10, 10, 30, 30), 8, 8)
        px = icons.render_pixmap(dt.icon, t.accent_for(dt.accent.value), size=20)
        p.drawPixmap(15, 15, px)

        # name + type
        p.setPen(_qcolor(t.text))
        f = QFont(); f.setPointSize(11); f.setWeight(QFont.DemiBold); p.setFont(f)
        p.drawText(QRectF(48, 10, NODE_W - 56, 18), Qt.AlignVCenter, self.inst.name)
        p.setPen(_qcolor(t.faint))
        f2 = QFont(); f2.setPointSize(8); p.setFont(f2)
        p.drawText(QRectF(48, 27, NODE_W - 56, 14), Qt.AlignVCenter, dt.label)

        # primary IP (once compiled) — at-a-glance addressing on the node
        addr = self._scene.ctx.addressing.get(self.inst.name)
        if addr and addr.get("interfaces"):
            ifaces = addr["interfaces"]
            ip = ifaces[0]["ip"].split("/")[0]
            if len(ifaces) > 1:
                ip += f"  +{len(ifaces) - 1}"
            p.setPen(_qcolor(t.accent))
            fip = QFont(); fip.setStyleHint(QFont.Monospace); fip.setPointSize(8)
            p.setFont(fip)
            p.drawText(QRectF(48, 41, NODE_W - 56, 13), Qt.AlignVCenter, ip)

        # status chip
        chip_col, label = {
            "running": (_qcolor(t.success), "running"),
            "booting": (_qcolor(t.warning), "booting"),
            "stopping": (_qcolor(t.warning), "stopping"),
            "error": (_qcolor(t.danger), "error"),
        }.get(self.status, (_qcolor(t.muted), "idle"))
        chip_bg = QColor(chip_col); chip_bg.setAlpha(38)
        cr = QRectF(12, H - 24, 58, 16)
        p.setBrush(chip_bg); p.setPen(Qt.NoPen)
        p.drawRoundedRect(cr, 8, 8)
        p.setBrush(chip_col)
        p.drawEllipse(QRectF(cr.left() + 7, cr.center().y() - 3, 6, 6))
        p.setPen(chip_col)
        f3 = QFont(); f3.setPointSize(8); p.setFont(f3)
        p.drawText(cr.adjusted(20, 0, -2, 0), Qt.AlignVCenter, label)

        # size tier: capacity gauge + label in the grown body, and a + / - stepper
        if self._resizable():
            self._paint_size(p, t, accent, H)

        # advisory-lint warning badge (top-right) — clickable to ask GINI about it
        if self.inst.name in self._scene.ctx.warnings:
            warn = _qcolor(t.warning)
            bx, by = NODE_W - 22, 8
            p.setBrush(warn); p.setPen(Qt.NoPen)
            p.drawEllipse(QRectF(bx, by, 14, 14))
            p.setPen(QColor("#1a1205"))
            fb = QFont(); fb.setPointSize(9); fb.setBold(True); p.setFont(fb)
            p.drawText(QRectF(bx, by, 14, 14), Qt.AlignCenter, "!")

    def _paint_size(self, p: QPainter, t, accent: QColor, H: float) -> None:
        level = self._size()
        label, vcpu, _mem, _mult = pricing.size_tier(level)

        # + / - stepper (bottom-right) — dim the end-stops
        minus, plus = self._stepper_rects()
        for r, glyph, on in ((minus, "−", level > pricing.SIZE_MIN),
                             (plus, "+", level < pricing.SIZE_MAX)):
            bg = QColor(accent); bg.setAlpha(30 if on else 10)
            p.setBrush(bg)
            p.setPen(QPen(accent if on else _qcolor(t.line2), 1.2))
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 5, 5)
            p.setPen(accent if on else _qcolor(t.faint))
            fs = QFont(); fs.setPointSize(12); fs.setBold(True); p.setFont(fs)
            p.drawText(r, Qt.AlignCenter, glyph)

        # capacity caption + vertical gauge in the body the taller node opens up
        body_top, body_bot = 58.0, H - 30
        if body_bot - body_top >= 16:
            p.setPen(_qcolor(t.muted))
            fc = QFont(); fc.setPointSize(8); fc.setBold(True); p.setFont(fc)
            p.drawText(QRectF(14, body_top, NODE_W - 30, 14),
                       Qt.AlignVCenter | Qt.AlignLeft, f"{label} · {vcpu:g} vCPU")
            gx, gw, gtop, gbot = NODE_W - 16.0, 6.0, body_top + 16, body_bot
            p.setBrush(_qcolor(t.bg3)); p.setPen(QPen(_qcolor(t.line2), 1))
            p.drawRoundedRect(QRectF(gx, gtop, gw, gbot - gtop), 3, 3)
            fh = (gbot - gtop) * (level / pricing.SIZE_MAX)
            p.setBrush(accent); p.setPen(Qt.NoPen)
            p.drawRoundedRect(QRectF(gx, gbot - fh, gw, fh), 3, 3)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            self.inst.x = self.pos().x()
            self.inst.y = self.pos().y()
            self._scene.update_edges_for(self.inst.id)
        return super().itemChange(change, value)

    def _on_warning_badge(self, pos) -> bool:
        """True if `pos` (item coords) is on the lint warning badge."""
        if self.inst.name not in self._scene.ctx.warnings:
            return False
        badge = QRectF(NODE_W - 24, 6, 18, 18)   # a touch larger than drawn, easier to hit
        return badge.contains(pos)

    def mousePressEvent(self, e):
        # clicking the amber "!" badge asks GINI why it's flagged (and how to fix it),
        # rather than selecting/moving the node
        if e.button() == Qt.LeftButton and self._on_warning_badge(e.pos()):
            self._scene.ctx.bus.warning_explain_requested.emit(self.inst.id)
            e.accept()
            return
        # on-node size stepper (+ / -) — resize without selecting/moving the node
        if e.button() == Qt.LeftButton and self._resizable():
            minus, plus = self._stepper_rects()
            if minus.contains(e.pos()):
                self._bump_size(-1); e.accept(); return
            if plus.contains(e.pos()):
                self._bump_size(+1); e.accept(); return
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e):
        # double-click to "log in" — open a terminal/console for this device
        self._scene.ctx.bus.device_activated.emit(self.inst.id)
        super().mouseDoubleClickEvent(e)

    def contextMenuEvent(self, e):
        self.popup_menu(e.screenPos())
        e.accept()

    def popup_menu(self, screen_pos) -> None:
        """Build + show this node's action menu. Reused by the view's right-click handler
        (a plain right-click shows this; a right-drag connects instead)."""
        from PySide6.QtWidgets import QMenu
        self.setSelected(True)
        menu = QMenu()
        a_console = menu.addAction("Open console")     # web dashboard (Grafana, MinIO, …)
        a_login = menu.addAction("Log in")
        a_logs = menu.addAction("View logs")
        menu.addSeparator()
        a_del = menu.addAction("Delete")
        chosen = menu.exec(screen_pos)
        bus = self._scene.ctx.bus
        if chosen == a_console:
            bus.device_console_requested.emit(self.inst.id)
        elif chosen == a_login:
            bus.device_activated.emit(self.inst.id)
        elif chosen == a_logs:
            bus.device_logs_requested.emit(self.inst.id)
        elif chosen == a_del:
            bus.device_delete_requested.emit(self.inst.id)

    def set_status(self, status: str) -> None:
        self.status = status
        self.update()

    def set_spotlight(self, on: bool) -> None:
        self._spot = on
        self.update()

    def set_highlight(self, on: bool) -> None:
        self._ring = on
        self.update()


class EdgeItem(QGraphicsObject):
    def __init__(self, scene: "CanvasScene", link: Link) -> None:
        super().__init__()
        self._scene = scene
        self.link = link
        self.setZValue(1)
        self._path = QPainterPath()
        self._packet_t: float | None = None
        self._packet_color: QColor | None = None
        self._flow_anim: QVariantAnimation | None = None
        self.refresh()

    def flow(self, color: str | None = None, duration: int = 900) -> None:
        """Animate a packet dot travelling along the edge (tutor + 'alive' feedback)."""
        self._packet_color = _qcolor(color) if color else None
        if self._scene.ctx.settings.reduced_motion:
            return
        if self._flow_anim is not None:
            self._flow_anim.stop()
        anim = QVariantAnimation(self)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setDuration(duration)
        anim.valueChanged.connect(self._set_packet)
        anim.finished.connect(lambda: self._set_packet(None))
        anim.start()
        self._flow_anim = anim

    def _set_packet(self, v) -> None:
        self._packet_t = None if v is None else float(v)
        self.update()

    def refresh(self) -> None:
        nodes = self._scene.nodes
        a = nodes.get(self.link.source_id)
        b = nodes.get(self.link.target_id)
        if not a or not b:
            return
        self.prepareGeometryChange()
        style = getattr(self._scene.ctx.settings, "connector_style", "orthogonal")
        if style == "straight":
            ca = a.pos() + QPointF(NODE_W / 2, a.node_h() / 2)
            cb = b.pos() + QPointF(NODE_W / 2, b.node_h() / 2)
            path = QPainterPath(ca)
            path.lineTo(cb)
            self._path = path
        else:
            self._path = _rounded_path(_ortho_waypoints(a, b), CORNER_R)
        self.update()

    def boundingRect(self) -> QRectF:
        if self._path.isEmpty():
            return QRectF()
        return self._path.boundingRect().adjusted(-4, -4, 4, 4)

    def paint(self, p: QPainter, opt, widget=None) -> None:
        if self._path.isEmpty():
            return
        t = self._scene.theme
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setBrush(Qt.NoBrush)                    # open path: stroke only, never fill
        p.setPen(QPen(_qcolor(t.line2), 2))
        p.drawPath(self._path)
        if self._packet_t is not None:
            pt = self._path.pointAtPercent(self._packet_t)
            col = self._packet_color or _qcolor(t.accent)
            p.setBrush(col)
            p.setPen(Qt.NoPen)
            p.drawEllipse(pt, 5, 5)


class CalloutItem(QGraphicsObject):
    """An anchored speech bubble the AI tutor places on a node."""

    def __init__(self, scene: "CanvasScene", node: NodeItem, text: str) -> None:
        super().__init__()
        self._scene = scene
        self._node = node
        self.text = text
        self.setZValue(60)
        self._font = QFont()
        self._font.setPointSize(10)
        fm = QFontMetrics(self._font)
        self._pad = 11
        self._w = 200
        br = fm.boundingRect(QRect(0, 0, self._w - 2 * self._pad, 1000),
                             Qt.TextWordWrap, text)
        self._h = br.height() + 2 * self._pad
        self._below = False
        self.reposition()

    def reposition(self) -> None:
        np = self._node.pos()
        above_y = np.y() - self._h - 16
        self._below = False
        scene = self.scene()
        views = scene.views() if scene else []
        if views:
            vis = views[0].mapToScene(views[0].viewport().rect()).boundingRect()
            if above_y < vis.top() + 6:           # not enough room above -> go below
                self._below = True
        y = (np.y() + NODE_H + 16) if self._below else above_y
        self.setPos(np.x() + NODE_W - 30, y)

    def boundingRect(self) -> QRectF:
        return QRectF(0, -12, self._w, self._h + 24)

    def paint(self, p: QPainter, opt, widget=None) -> None:
        t = self._scene.theme
        p.setRenderHint(QPainter.Antialiasing, True)
        accent = _qcolor(t.accent)
        bubble = QRectF(0, 0, self._w, self._h)
        p.setBrush(_qcolor(t.panel2))
        p.setPen(QPen(accent, 1.6))
        p.drawRoundedRect(bubble, 10, 10)
        # tail points toward the node (down if bubble is above it, up if below)
        if self._below:
            tip, base_y = -11.0, 1.0
        else:
            tip, base_y = self._h + 11, self._h - 1
        p.setBrush(_qcolor(t.panel2))
        p.setPen(Qt.NoPen)
        p.drawPolygon(QPolygonF([QPointF(26, base_y), QPointF(46, base_y), QPointF(26, tip)]))
        p.setPen(QPen(accent, 1.6))
        p.drawLine(QPointF(26, base_y), QPointF(26, tip))
        p.drawLine(QPointF(26, tip), QPointF(46, base_y))
        p.setPen(_qcolor(t.text))
        p.setFont(self._font)
        p.drawText(bubble.adjusted(self._pad, self._pad, -self._pad, -self._pad),
                   Qt.TextWordWrap, self.text)


class CanvasScene(QGraphicsScene):
    def __init__(self, ctx: AppContext, theme: Theme) -> None:
        super().__init__()
        self.ctx = ctx
        self.theme = theme
        self.nodes: dict[str, NodeItem] = {}
        self.edges: dict[str, EdgeItem] = {}
        self.setSceneRect(-2000, -2000, 4000, 4000)
        self._cascade = 0
        self._callouts: list[CalloutItem] = []
        self._spotlit: list[NodeItem] = []
        self._highlit: list[NodeItem] = []

        ctx.bus.device_added.connect(self._on_device_added)
        ctx.bus.device_removed.connect(self._on_device_removed)
        ctx.bus.link_added.connect(self._on_link_added)
        ctx.bus.device_changed.connect(self._on_device_changed)
        ctx.bus.addressing_changed.connect(self._refresh_node_labels)
        ctx.bus.warnings_changed.connect(self._on_warnings)
        # tutor "present" channel
        ctx.bus.present_spotlight.connect(self._on_spotlight)
        ctx.bus.present_highlight.connect(self._on_highlight)
        ctx.bus.present_callout.connect(self._on_callout)
        ctx.bus.present_packet.connect(self._on_packet)
        ctx.bus.present_clear.connect(self._on_clear_stage)
        ctx.bus.addressing_changed.connect(self._on_addressing)
        ctx.bus.edges_restyled.connect(self._on_restyle)

    def _on_restyle(self) -> None:
        """Re-route every edge after the connector style (bent/straight) changes."""
        for edge in self.edges.values():
            edge.refresh()

    def _on_addressing(self) -> None:
        for n in self.nodes.values():
            n.update()

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.update()
        for n in self.nodes.values():
            n.refresh_theme()
            n.update()
        for e in self.edges.values():
            e.update()

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        painter.fillRect(rect, _qcolor(self.theme.bg))
        if not self.ctx.settings.grid:
            return
        painter.setPen(QPen(_qcolor(self.theme.grid), 1))
        left = int(rect.left()) - (int(rect.left()) % GRID)
        top = int(rect.top()) - (int(rect.top()) % GRID)
        x = left
        while x < rect.right():
            painter.drawLine(int(x), int(rect.top()), int(x), int(rect.bottom()))
            x += GRID
        y = top
        while y < rect.bottom():
            painter.drawLine(int(rect.left()), int(y), int(rect.right()), int(y))
            y += GRID

    # -- model -> scene ----------------------------------------------------- #
    def _on_device_added(self, device_id: str) -> None:
        inst = self.ctx.topology.devices[device_id]
        if inst.x == 0 and inst.y == 0:
            self._cascade += 1
            inst.x = -120 + (self._cascade % 6) * 150
            inst.y = -90 + (self._cascade % 4) * 110
        node = NodeItem(self, inst)
        self.nodes[device_id] = node
        self.addItem(node)

    def _on_device_removed(self, device_id: str) -> None:
        node = self.nodes.pop(device_id, None)
        if node:
            self.removeItem(node)
        for lid in [l.id for l in list(self.ctx.topology.links.values())]:
            pass  # links already pruned in model; clean orphan edges below
        for eid, edge in list(self.edges.items()):
            if eid not in self.ctx.topology.links:
                self.removeItem(edge)
                self.edges.pop(eid, None)

    def _on_link_added(self, link_id: str) -> None:
        link = self.ctx.topology.links[link_id]
        edge = EdgeItem(self, link)
        self.edges[link_id] = edge
        self.addItem(edge)
        edge.flow()      # subtle 'alive' feedback on connect

    def _on_device_changed(self, device_id: str) -> None:
        node = self.nodes.get(device_id)
        if node:
            node.prepareGeometryChange()        # size tier may change the node height
            node.update()
            self.update_edges_for(device_id)

    def _refresh_node_labels(self) -> None:
        for node in self.nodes.values():
            node.update()                       # repaint IP labels after addressing changes

    def _on_warnings(self) -> None:
        warns = self.ctx.warnings
        for node in self.nodes.values():
            msgs = warns.get(node.inst.name)
            node.setToolTip("\n".join(msgs) if msgs else "")
            node.update()

    def update_edges_for(self, device_id: str) -> None:
        for edge in self.edges.values():
            if device_id in (edge.link.source_id, edge.link.target_id):
                edge.refresh()

    # -- tutor "present" handlers ------------------------------------------- #
    def _on_spotlight(self, dev_ids) -> None:
        self._on_clear_stage()
        if not dev_ids:
            return
        targets = set(dev_ids)
        for nid, node in self.nodes.items():
            if nid in targets:
                node.set_spotlight(True)
                node.setOpacity(1.0)
                self._spotlit.append(node)
            else:
                node.setOpacity(0.7)       # gently dim — topology stays clearly legible
        for edge in self.edges.values():
            edge.setOpacity(0.6)

    def _on_highlight(self, dev_ids) -> None:
        for n in self._highlit:
            n.set_highlight(False)
        self._highlit = []
        for nid in (dev_ids or []):
            node = self.nodes.get(nid)
            if node:
                node.set_highlight(True)
                self._highlit.append(node)

    def _on_callout(self, dev_id, text) -> None:
        node = self.nodes.get(dev_id)
        if node is None:
            return
        c = CalloutItem(self, node, text)
        self._callouts.append(c)
        self.addItem(c)
        c.reposition()      # now in-scene: can flip below the node if it'd clip the top

    def _on_packet(self, dev_ids) -> None:
        ids = list(dev_ids or [])
        for a, b in zip(ids, ids[1:]):
            edge = self._edge_between(a, b)
            if edge:
                edge.flow()

    def _edge_between(self, a: str, b: str) -> "EdgeItem | None":
        for e in self.edges.values():
            if {e.link.source_id, e.link.target_id} == {a, b}:
                return e
        return None

    def _on_clear_stage(self) -> None:
        for n in self._spotlit:
            n.set_spotlight(False)
        for n in self._highlit:
            n.set_highlight(False)
        self._spotlit = []
        self._highlit = []
        for node in self.nodes.values():
            node.setOpacity(1.0)
        for edge in self.edges.values():
            edge.setOpacity(1.0)
        for c in self._callouts:
            self.removeItem(c)
        self._callouts = []


class CanvasView(QGraphicsView):
    def __init__(self, ctx: AppContext, theme: Theme) -> None:
        self.scene_ = CanvasScene(ctx, theme)
        super().__init__(self.scene_)
        self.ctx = ctx
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setAcceptDrops(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._connect_mode = False
        self._connect_first: str | None = None
        self._zoom = 1.0
        # right-drag-to-connect (the book's gesture): we drive right-click ourselves so a
        # plain right-click shows the node menu and a right-DRAG creates a link.
        self.setContextMenuPolicy(Qt.PreventContextMenu)
        self._rc_from: NodeItem | None = None
        self._rc_moved = False
        self._rc_start = None
        self._rc_line = None

        # tutor narration banner (overlaid on the viewport)
        self._narration = QLabel(self)
        self._narration.setObjectName("Narration")
        self._narration.setWordWrap(True)
        self._narration.hide()
        self._narr_timer = QTimer(self)
        self._narr_timer.setSingleShot(True)
        self._narr_timer.timeout.connect(self._narration.hide)
        ctx.bus.present_narrate.connect(self._show_narration)
        ctx.bus.present_clear.connect(self._narration.hide)

    def _show_narration(self, text: str) -> None:
        th = self.scene_.theme
        self._narration.setStyleSheet(
            f"#Narration{{background:{th.panel2};color:{th.text};"
            f"border:1.5px solid {th.accent};border-radius:12px;"
            f"padding:10px 14px;font-size:13px;}}")
        self._narration.setText("\U0001F916  " + text)
        self._position_narration()
        self._narration.show()
        self._narration.raise_()
        self._narr_timer.start(7000)

    def _position_narration(self) -> None:
        margin = 18
        w = min(560, max(200, self.viewport().width() - 2 * margin))
        self._narration.setFixedWidth(w)
        self._narration.adjustSize()
        x = (self.width() - self._narration.width()) // 2
        y = self.height() - self._narration.height() - margin
        self._narration.move(x, y)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        if self._narration.isVisible():
            self._position_narration()

    # zoom with Ctrl+wheel ------------------------------------------------- #
    def wheelEvent(self, e) -> None:
        if e.modifiers() & Qt.ControlModifier:
            factor = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
            new = self._zoom * factor
            if 0.3 < new < 3.0:
                self._zoom = new
                self.scale(factor, factor)
        else:
            super().wheelEvent(e)

    def zoom_by(self, factor: float) -> None:
        new = self._zoom * factor
        if 0.3 < new < 3.0:
            self._zoom = new
            self.scale(factor, factor)

    def reset_zoom(self) -> None:
        self.resetTransform()
        self._zoom = 1.0

    # connect mode --------------------------------------------------------- #
    def set_connect_mode(self, on: bool) -> None:
        self._connect_mode = on
        self._connect_first = None
        self.setCursor(Qt.CrossCursor if on else Qt.ArrowCursor)
        self.setDragMode(QGraphicsView.NoDrag if on else QGraphicsView.RubberBandDrag)

    def _node_at(self, view_pos) -> "NodeItem | None":
        item = self.itemAt(view_pos)
        while item is not None and not isinstance(item, NodeItem):
            item = item.parentItem()
        return item if isinstance(item, NodeItem) else None

    def mousePressEvent(self, e) -> None:
        if self._connect_mode and e.button() == Qt.LeftButton:
            node = self._node_at(e.pos())
            if node is not None:
                devices = self.ctx.topology.devices
                # drop a stale first endpoint (its device was deleted, or the project was
                # reloaded between clicks) so we never link to a gone device
                if self._connect_first is not None and self._connect_first not in devices:
                    self._connect_first = None
                if self._connect_first is None:
                    self._connect_first = node.inst.id
                elif self._connect_first != node.inst.id:
                    try:
                        self.ctx.add_link(self._connect_first, node.inst.id)
                    except Exception as ex:          # noqa: BLE001
                        self.ctx.log(f"Couldn't connect: {ex}", "info")
                    self._connect_first = None
                return
        # right-button on a node: maybe a connect-drag, maybe the context menu (decided
        # on release by whether the mouse moved)
        if e.button() == Qt.RightButton:
            node = self._node_at(e.pos())
            if node is not None:
                self._rc_from = node
                self._rc_start = e.pos()
                self._rc_moved = False
                e.accept()
                return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:
        if self._rc_from is not None:
            if not self._rc_moved and (e.pos() - self._rc_start).manhattanLength() > 6:
                self._rc_moved = True
            if self._rc_moved:
                self._draw_rc_line(e.pos())
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        if self._rc_from is not None and e.button() == Qt.RightButton:
            self._clear_rc_line()
            src = self._rc_from
            self._rc_from = None
            if self._rc_moved:                       # right-DRAG -> connect to the target
                target = self._node_at(e.pos())
                if target is not None and target.inst.id != src.inst.id:
                    try:
                        self.ctx.add_link(src.inst.id, target.inst.id)
                    except Exception as ex:          # noqa: BLE001
                        self.ctx.log(f"Couldn't connect: {ex}", "info")
            else:                                    # plain right-click -> context menu
                src.popup_menu(e.globalPosition().toPoint())
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def _draw_rc_line(self, view_pos) -> None:
        from PySide6.QtCore import QLineF
        from PySide6.QtGui import QColor, QPen
        from PySide6.QtWidgets import QGraphicsLineItem
        p1 = self._rc_from.sceneBoundingRect().center()
        p2 = self.mapToScene(view_pos)
        if self._rc_line is None:
            self._rc_line = QGraphicsLineItem()
            pen = QPen(QColor(self.scene_.theme.accent), 2.0, Qt.DashLine)
            self._rc_line.setPen(pen)
            self._rc_line.setZValue(5)
            self.scene_.addItem(self._rc_line)
        self._rc_line.setLine(QLineF(p1, p2))

    def _clear_rc_line(self) -> None:
        if self._rc_line is not None:
            self.scene_.removeItem(self._rc_line)
            self._rc_line = None

    # drag & drop from palette --------------------------------------------- #
    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasFormat(MIME):
            e.acceptProposedAction()

    def dragMoveEvent(self, e) -> None:
        if e.mimeData().hasFormat(MIME):
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:
        if not e.mimeData().hasFormat(MIME):
            return
        key = bytes(e.mimeData().data(MIME)).decode()
        sp = self.mapToScene(e.position().toPoint())
        self.ctx.add_device(key, x=sp.x() - NODE_W / 2, y=sp.y() - NODE_H / 2)
        e.acceptProposedAction()
