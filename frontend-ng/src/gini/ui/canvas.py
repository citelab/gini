"""The topology canvas: a themed QGraphicsView scene with node + edge items.

Drives off the AppContext: dropping a palette item or an agent call adds a device to
the topology, which emits `device_added`, which the scene turns into a NodeItem. The
model stays the single source of truth; the scene is a view of it.
"""
from __future__ import annotations

import math

from PySide6.QtCore import (
    QEasingCurve, QPointF, QRect, QRectF, QSizeF, Qt, QTimer, QVariantAnimation,
)
from PySide6.QtGui import (
    QBrush, QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPolygonF,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QGraphicsDropShadowEffect, QGraphicsItem, QGraphicsObject, QGraphicsScene,
    QGraphicsView, QLabel,
)

from ..app import AppContext
from ..domain import grouping, pricing
from ..domain.topology import DeviceInstance, Link
from .theme import icons
from .theme.manager import sp as _sp, ui_scale as _uiscale   # scale text + node cards by the UI setting
from .theme.tokens import Theme

MIME = "application/x-gini-device"
GRID = 22
# how many pixels of "near enough" the connect gesture allows around a node. Aiming at a 40px icon
# with a mouse is a fine-motor task; being 3px off should not silently cancel the mode.
_HIT_SLACK = 10
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
    s = _uiscale()                               # cards are drawn at this scale — use scaled extents
    nw = NODE_W * s
    ax, ay = a.pos().x(), a.pos().y()
    bx, by = b.pos().x(), b.pos().y()
    ah, bh = a.node_h() * s, b.node_h() * s      # per-node heights (size tiers differ) × card scale
    ca = QPointF(ax + nw / 2, ay + ah / 2)
    cb = QPointF(bx + nw / 2, by + bh / 2)
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
            ex, en = QPointF(ax + nw, ca.y()), QPointF(bx, cb.y())
        else:
            ex, en = QPointF(ax, ca.y()), QPointF(bx + nw, cb.y())
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


# default box sizes (px) per container type, and the minimum a box can be resized to.
_GROUP_DEFAULTS = {"vpc": (380.0, 260.0), "cloud_subnet": (300.0, 170.0),
                   "region": (480.0, 330.0)}
GROUP_MIN_W, GROUP_MIN_H = 170.0, 120.0
_GRIP = 16.0


class GroupItem(QGraphicsObject):
    """A resizable container box for grouping elements (VPC / Cloud Subnet / Region).

    Drawn behind the nodes as a translucent labelled rectangle. Whatever sits inside it
    (decided by geometry — see domain/grouping.py) becomes its child via parent_id, which
    the compiler turns into a real isolated Docker network for a VPC. Move the box and its
    contents travel with it; drag the bottom-right grip to resize."""

    def __init__(self, scene: "CanvasScene", inst: DeviceInstance) -> None:
        super().__init__()
        self._scene = scene
        self.inst = inst
        dw, dh = _GROUP_DEFAULTS.get(inst.type_key, (320.0, 200.0))
        if not inst.w or not inst.h:
            inst.w, inst.h = dw, dh
        self._resizing = False
        self._last = QPointF(inst.x, inst.y)     # must exist before setPos fires itemChange
        self.setFlags(
            QGraphicsItem.ItemIsMovable
            | QGraphicsItem.ItemIsSelectable
            | QGraphicsItem.ItemSendsGeometryChanges
        )
        self.setPos(inst.x, inst.y)
        self.setZValue(-5)                       # behind edges (z=1) and nodes (z=10)
        self.setAcceptHoverEvents(True)

    # geometry --------------------------------------------------------------- #
    def box_rect(self) -> QRectF:
        return QRectF(0, 0, float(self.inst.w), float(self.inst.h))

    def boundingRect(self) -> QRectF:
        return QRectF(-2, -2, float(self.inst.w) + 4, float(self.inst.h) + 4)

    def _grip_rect(self) -> QRectF:
        return QRectF(self.inst.w - _GRIP, self.inst.h - _GRIP, _GRIP, _GRIP)

    # painting --------------------------------------------------------------- #
    def paint(self, p: QPainter, *_):
        t = self._scene.theme
        dt = self.inst.type
        accent = _qcolor(t.accent_for(dt.accent.value))
        sel = self.isSelected()
        p.setRenderHint(QPainter.Antialiasing, True)
        fill = QColor(accent); fill.setAlpha(40 if sel else 26)
        p.setBrush(QBrush(fill))
        pen = QPen(accent, 2.0 if sel else 1.4)
        pen.setStyle(Qt.SolidLine if sel else Qt.DashLine)
        p.setPen(pen)
        p.drawRoundedRect(self.box_rect(), 14, 14)
        # title + subtitle (the VPC's CIDR / subnet's tier)
        p.setPen(accent)
        f = QFont(); f.setBold(True); f.setPointSize(_sp(10)); p.setFont(f)
        p.drawText(QRectF(14, 7, self.inst.w - 28, 16), Qt.AlignVCenter,
                   f"{dt.label}: {self.inst.name or dt.label}")
        sub = self._subtitle()
        if sub:
            p.setPen(_qcolor(t.muted))
            f2 = QFont(); f2.setPointSize(_sp(8)); p.setFont(f2)
            p.drawText(QRectF(14, 23, self.inst.w - 28, 13), Qt.AlignVCenter, sub)
        # resize grip (three corner ticks)
        g = self._grip_rect()
        p.setPen(QPen(accent, 1.5))
        for o in (3.0, 7.0, 11.0):
            p.drawLine(QPointF(g.right() - o, g.bottom() - 2),
                       QPointF(g.right() - 2, g.bottom() - o))

    def _subtitle(self) -> str:
        pr = self.inst.properties or {}
        if self.inst.type_key == "vpc":
            return pr.get("CIDR", "")
        if self.inst.type_key == "cloud_subnet":
            return " · ".join(x for x in (pr.get("CIDR", ""), pr.get("Tier", "")) if x)
        if self.inst.type_key == "region":
            return pr.get("Region", "") or pr.get("Zone", "")
        return ""

    # interaction ------------------------------------------------------------ #
    def mousePressEvent(self, e):
        if (e.button() == Qt.LeftButton
                and self._grip_rect().adjusted(-5, -5, 4, 4).contains(e.pos())):
            self._resizing = True
            self.setSelected(True)
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._resizing:
            self.prepareGeometryChange()
            self.inst.w = max(GROUP_MIN_W, e.pos().x())
            self.inst.h = max(GROUP_MIN_H, e.pos().y())
            self.update()
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        was_resizing = self._resizing
        self._resizing = False
        super().mouseReleaseEvent(e)
        self._scene.recompute_membership()       # box moved/resized -> reassign contents
        if was_resizing:
            self._scene.ctx.bus.topology_changed.emit()

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            new = self.pos()
            dx, dy = new.x() - self._last.x(), new.y() - self._last.y()
            self._last = QPointF(new)
            self.inst.x, self.inst.y = new.x(), new.y()
            if dx or dy:                          # drag the box -> its contents follow
                self._scene._move_children(self.inst.id, dx, dy)
        return super().itemChange(change, value)


class NodeItem(QGraphicsObject):
    """Visual card for one DeviceInstance."""

    def __init__(self, scene: "CanvasScene", inst: DeviceInstance) -> None:
        super().__init__()
        self._scene = scene
        self.inst = inst
        self.status = "idle"
        # GINI32 only: the hotspot address of the real board currently checked in, or ""
        # when none is. Observed hardware state, so it is never saved with the topology.
        self.board_addr = ""
        self.setFlags(
            QGraphicsItem.ItemIsMovable
            | QGraphicsItem.ItemIsSelectable
            | QGraphicsItem.ItemSendsGeometryChanges
        )
        self.setPos(inst.x, inst.y)
        self.setScale(_uiscale())          # the whole card (art + text) grows with the text-size setting
        self.setZValue(10)
        self.setAcceptHoverEvents(True)
        self._hover = 0.0
        self._spot = False        # tutor spotlight
        self._ring = False        # tutor highlight
        self._xray = None         # X-ray: "target" (this node is being inspected) or None
        self._anim: QVariantAnimation | None = None
        self._shadow = QGraphicsDropShadowEffect()
        self.refresh_theme()
        self.setGraphicsEffect(self._shadow)

    def refresh_theme(self) -> None:
        t = self._scene.theme
        self._shadow.setColor(_qcolor(t.shadow))
        self._shadow.setBlurRadius(t.elevation + 10 * self._hover)
        self._shadow.setOffset(0, 4 + 2 * self._hover)

    # size tier (resizable elements grow taller; others stay at the base height) ----- #
    def _resizable(self) -> bool:
        return pricing.resizable(self.inst.type_key)

    # xv6 goes up to XL (4 vCPU). The Size must never advertise more cores than the machine
    # actually runs, and everything below this already handles four: SIZE_TIERS maps XL to 4.0
    # vCPU, `_xv6_harts` clamps to min(4, ...), and xv6's own NCPU is 8. This constant was the
    # only thing holding it at L.
    #
    # Four is worth having: contention is impossible on one core and thin on two, so the Lock Lab
    # and the scheduler's Gantt only get interesting as harts are added.
    _XV6_MAX_LEVEL = 4        # XL

    def _size(self) -> int:
        if not self._resizable():
            return 1
        lvl = pricing.size_level(getattr(self.inst, "size", 1))
        return min(lvl, self._XV6_MAX_LEVEL) if self.inst.type_key == "xv6" else lvl

    def node_h(self) -> float:
        return NODE_H + (self._size() - 1) * SIZE_STEP

    def _stepper_rects(self) -> tuple[QRectF, QRectF]:
        """(minus, plus) hit rectangles for the on-node size stepper, at bottom-right."""
        y = self.node_h() - 25
        return (QRectF(NODE_W - 46, y, 19, 19), QRectF(NODE_W - 24, y, 19, 19))

    def _bump_size(self, delta: int) -> None:
        # xv6's CPU count is fixed at boot (-smp), so changing vCPUs requires a stop + rerun —
        # block the stepper while the machine is running (other elements resize live).
        if self.inst.type_key == "xv6" and getattr(self._scene, "running", False):
            self._scene.ctx.log("Stop the topology to change the xv6 Machine's vCPUs, "
                                "then Run again.", "info")
            return
        new = pricing.size_level(self._size() + delta)
        if self.inst.type_key == "xv6":
            new = min(new, self._XV6_MAX_LEVEL)     # + can't push xv6 past L (2 vCPU)
        if new == self._size():
            return
        self.prepareGeometryChange()
        self.inst.size = new
        self.update()
        self._scene.update_edges_for(self.inst.id)
        # resized -> live CPU update if running; rebill the dashboard; persist in .gini
        self._scene.ctx.bus.device_resized.emit(self.inst.id)
        self._scene.ctx.bus.topology_changed.emit()

    def set_xray(self, state) -> None:
        """Ring this element while it's being X-rayed ('target'), or None to clear."""
        if state == self._xray:
            return
        self._xray = state
        self.update()

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
        self._shadow.setOffset(0, 4 + 2 * v)
        self.update()

    def paint(self, p: QPainter, opt, widget=None) -> None:
        t = self._scene.theme
        dt = self.inst.type
        accent = _qcolor(t.accent_for(dt.accent.value))
        p.setRenderHint(QPainter.Antialiasing, True)
        # A SLOT-TAGGED element is scaffolding — a stand-in for a composition parameter, not part of
        # the fragment being authored. Render it recessive so the canvas tells the truth: the bright,
        # un-hulled elements are YOUR delta; the faded ones get replaced at compose time.
        if getattr(self.inst, "slot", ""):
            p.setOpacity(0.55)

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

        # Mission: a reddish glow when the game master has flagged this element as off-task or
        # wrongly wired (non-destructive — clears the instant the student fixes it)
        if self._mission_flagged():
            dgl = _qcolor(t.danger); dgl.setAlpha(70)
            p.setBrush(Qt.NoBrush); p.setPen(QPen(dgl, 6))
            p.drawRoundedRect(rect.adjusted(-5, -5, 5, 5), 14, 14)

        # X-ray: ring the element currently being inspected (its ghosts float around it)
        if self._xray == "target":
            halo = QColor(accent); halo.setAlpha(60)
            p.setBrush(Qt.NoBrush); p.setPen(QPen(halo, 6))
            p.drawRoundedRect(rect.adjusted(-4, -4, 4, 4), 13, 13)
            edge = QColor(accent); edge.setAlpha(220)
            p.setPen(QPen(edge, 2.4))
            p.drawRoundedRect(rect.adjusted(-1.5, -1.5, 1.5, 1.5), 11, 11)

        p.setBrush(QBrush(_qcolor(t.node_fill())))
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
        f = QFont(); f.setPointSize(_sp(11)); f.setWeight(QFont.DemiBold); p.setFont(f)
        p.drawText(QRectF(48, 10, NODE_W - 56, 18), Qt.AlignVCenter, self.inst.name)
        p.setPen(_qcolor(t.faint))
        f2 = QFont(); f2.setPointSize(_sp(8)); p.setFont(f2)
        p.drawText(QRectF(48, 27, NODE_W - 56, 14), Qt.AlignVCenter, dt.label)

        # primary IP (once compiled) — at-a-glance addressing on the node
        addr = self._scene.ctx.addressing.get(self.inst.name)
        ip = ""
        if addr and addr.get("interfaces"):
            ifaces = addr["interfaces"]
            ip = ifaces[0]["ip"].split("/")[0]
            if len(ifaces) > 1:
                ip += f"  +{len(ifaces) - 1}"
        elif getattr(self, "board_addr", ""):
            # A GINI32 board is not in the compiled addressing table — it has no
            # container and the canvas never assigns it a machine address. Its meaningful
            # address is the hotspot gateway it raises for real devices, which only
            # exists while the hardware is actually checked in. Set (and cleared) by
            # _poll_boards, so an unplugged board stops advertising an address it no
            # longer answers on.
            ip = self.board_addr
        if ip:
            p.setPen(_qcolor(t.accent))
            fip = QFont(); fip.setStyleHint(QFont.Monospace); fip.setPointSize(_sp(8))
            p.setFont(fip)
            p.drawText(QRectF(48, 41, NODE_W - 56, 13), Qt.AlignVCenter, ip)

        # status chip
        chip_col, label = {
            "running": (_qcolor(t.success), "running"),
            "booting": (_qcolor(t.warning), "booting"),
            "stopping": (_qcolor(t.warning), "stopping"),
            "error": (_qcolor(t.danger), "error"),
            "ready": (_qcolor(t.accent), "ready"),      # a rider whose donor is up — runnable
        }.get(self.status, (_qcolor(t.muted), "idle"))
        # Real hardware does not "run" — it is either there or it is not. Saying
        # "running" about a board on someone's desk invites the wrong mental model,
        # and "idle" reads as "fine, just quiet" when it actually means "absent".
        if self.inst.type_key == "gini32":
            chip_col, label = {
                "running":  (_qcolor(t.success), "connected"),
                "searching": (_qcolor(t.warning), "searching"),
                "error":    (_qcolor(t.danger), "no board"),
            }.get(self.status, (_qcolor(t.muted), "offline"))
        chip_bg = QColor(chip_col); chip_bg.setAlpha(38)
        cr = QRectF(12, H - 24, 62, 16)
        p.setBrush(chip_bg); p.setPen(Qt.NoPen)
        p.drawRoundedRect(cr, 8, 8)
        p.setBrush(chip_col)
        p.drawEllipse(QRectF(cr.left() + 7, cr.center().y() - 3, 6, 6))
        p.setPen(chip_col)
        f3 = QFont(); f3.setPointSize(_sp(8)); p.setFont(f3)
        p.drawText(cr.adjusted(20, 0, -2, 0), Qt.AlignVCenter, label)

        # size tier: capacity gauge + label in the grown body, and a + / - stepper
        if self._resizable():
            self._paint_size(p, t, accent, H)

        # Mission: red error badge (top-right) when flagged off-task / wrongly wired
        if self._mission_flagged():
            bx, by = NODE_W - 24, 6
            p.setBrush(_qcolor(t.danger)); p.setPen(Qt.NoPen)
            p.drawEllipse(QRectF(bx, by, 18, 18))
            p.setPen(QColor("#ffffff"))
            fb = QFont(); fb.setPointSize(_sp(11)); fb.setBold(True); p.setFont(fb)
            p.drawText(QRectF(bx, by - 1, 18, 18), Qt.AlignCenter, "!")

        # advisory-lint warning badge (top-right) — clickable to ask GINI about it
        if (self.inst.name in self._scene.ctx.warnings and not self._off_goal()
                and not self._mission_flagged()):
            warn = _qcolor(t.warning)
            bx, by = NODE_W - 22, 8
            p.setBrush(warn); p.setPen(Qt.NoPen)
            p.drawEllipse(QRectF(bx, by, 14, 14))
            p.setPen(QColor("#1a1205"))
            fb = QFont(); fb.setPointSize(_sp(9)); fb.setBold(True); p.setFont(fb)
            p.drawText(QRectF(bx, by, 14, 14), Qt.AlignCenter, "!")

        # Wizard: flag an element that isn't part of the active goal (click ✕ to remove)
        if self._off_goal():
            bx, by = NODE_W - 24, 6
            p.setBrush(_qcolor(t.danger)); p.setPen(Qt.NoPen)
            p.drawEllipse(QRectF(bx, by, 18, 18))
            p.setPen(QColor("#ffffff"))
            fb = QFont(); fb.setPointSize(_sp(10)); fb.setBold(True); p.setFont(fb)
            p.drawText(QRectF(bx, by - 1, 18, 18), Qt.AlignCenter, "✕")

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
            fs = QFont(); fs.setPointSize(_sp(12)); fs.setBold(True); p.setFont(fs)
            p.drawText(r, Qt.AlignCenter, glyph)

        # capacity caption + vertical gauge in the body the taller node opens up
        body_top, body_bot = 58.0, H - 30
        if body_bot - body_top >= 16:
            p.setPen(_qcolor(t.muted))
            fc = QFont(); fc.setPointSize(_sp(8)); fc.setBold(True); p.setFont(fc)
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
        if self.inst.name not in self._scene.ctx.warnings or self._off_goal():
            return False
        badge = QRectF(NODE_W - 24, 6, 18, 18)   # a touch larger than drawn, easier to hit
        return badge.contains(pos)

    def _off_goal(self) -> bool:
        """True if a Wizard objective is active and this element isn't part of it."""
        m = getattr(self._scene.ctx, "mission", None)
        return m is not None and not m.allows(self.inst.type_key)

    def _mission_flagged(self) -> bool:
        """True if a Mission has flagged this element (off-task or wrongly wired)."""
        return self.inst.id in getattr(self._scene.ctx, "mission_flags", {})

    def _on_offgoal_badge(self, pos) -> bool:
        return self._off_goal() and QRectF(NODE_W - 26, 4, 22, 22).contains(pos)

    def mousePressEvent(self, e):
        # clicking the amber "!" badge asks GINI why it's flagged (and how to fix it),
        # rather than selecting/moving the node
        if e.button() == Qt.LeftButton and self._on_offgoal_badge(e.pos()):
            self._scene.ctx.bus.device_delete_requested.emit(self.inst.id)   # ✕ -> remove
            e.accept()
            return
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

    def mouseReleaseEvent(self, e):
        super().mouseReleaseEvent(e)
        # dragging a node into / out of a VPC box reassigns its membership
        self._scene.recompute_membership()

    def mouseDoubleClickEvent(self, e):
        # double-click to "log in" — open a terminal/console for this device. LEFT button only:
        # a fast second RIGHT click arrives here too (Qt turns it into a DblClick), and opening a
        # console because the student wired two elements in quick succession is nobody's intent.
        if e.button() != Qt.LeftButton:
            e.ignore()
            return
        self._scene.ctx.bus.device_activated.emit(self.inst.id)
        super().mouseDoubleClickEvent(e)

    def contextMenuEvent(self, e):
        self.popup_menu(e.screenPos())
        e.accept()

    @staticmethod
    def action_gates(running: bool, is_router: bool) -> dict:
        """Which run-dependent menu actions are enabled. Console/logs need the lab up;
        Log in does too, except a Router (its Router Lab opens offline)."""
        return {"console": running, "logs": running, "login": running or is_router}

    def popup_menu(self, screen_pos) -> None:
        """Build + show this node's action menu. Reused by the view's right-click handler
        (a plain right-click shows this; a right-drag connects instead)."""
        from PySide6.QtWidgets import QMenu

        from ..services.compiler import _role
        self.setSelected(True)
        menu = QMenu()
        menu.setToolTipsVisible(True)
        a_console = menu.addAction("Open console")     # web dashboard (Grafana, MinIO, …)
        a_login = menu.addAction("Log in")
        a_logs = menu.addAction("View logs")
        menu.addSeparator()
        a_del = menu.addAction("Delete")
        # these talk to live containers, so gate them on the lab being up. A Router is the
        # exception for Log in — its Router Lab opens offline (with the local trace).
        running = getattr(self._scene, "running", False)
        gates = self.action_gates(running, _role(self.inst.type_key) == "router")
        for act, key in ((a_console, "console"), (a_login, "login"), (a_logs, "logs")):
            act.setEnabled(gates[key])
            if not gates[key]:
                act.setToolTip("Run the topology first")
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

    def set_board_addr(self, addr: str) -> None:
        """Show (or clear) a real board's hotspot address. Repaints only on change —
        this is called from a 3 s poll, and repainting every node every tick would put
        the canvas under constant needless load."""
        if addr == self.board_addr:
            return
        self.board_addr = addr
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
        # a link is a first-class citizen: click to select, Delete/trash to remove it.
        # The hit area is the stroked path (see shape()), not the bounding box — so
        # clicking the empty space near a wire still deselects/box-selects as before.
        self.setFlag(QGraphicsItem.ItemIsSelectable)
        self._path = QPainterPath()
        self._packet_t: float | None = None
        self._packet_color: QColor | None = None
        self._flow_anim: QVariantAnimation | None = None
        self.refresh()

    def shape(self) -> QPainterPath:  # noqa: N802
        """Clickable region = the wire itself, fattened to a finger-friendly ~12px."""
        if self._path.isEmpty():
            return QPainterPath()
        from PySide6.QtGui import QPainterPathStroker
        stroker = QPainterPathStroker()
        stroker.setWidth(12)
        stroker.setCapStyle(Qt.RoundCap)
        return stroker.createStroke(self._path)

    def _is_attach(self) -> bool:
        return getattr(self.link, "kind", "link") == "attach"

    def _rider_role(self) -> str:
        """'source' | 'sink' | '' — read from the rider end (source_id) of an attach edge."""
        dev = self._scene.ctx.topology.devices.get(self.link.source_id)
        return getattr(dev.type, "role", "") if dev is not None else ""

    def flow(self, color: str | None = None, duration: int = 900) -> None:
        """Animate a packet dot travelling along the edge (tutor + 'alive' feedback)."""
        if self._is_attach():
            return                                   # attach edges carry no traffic — never animate
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
            s = _uiscale()
            ca = a.pos() + QPointF(NODE_W * s / 2, a.node_h() * s / 2)
            cb = b.pos() + QPointF(NODE_W * s / 2, b.node_h() * s / 2)
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
        if self._is_attach():
            self._paint_attach(p, t)
            if self.isSelected():                 # selection ring for attach tethers too
                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(_qcolor(t.accent), 1.6, Qt.DashLine))
                p.drawPath(self._path)
            return
        if self.isSelected():                     # selected wire: accent + a soft halo
            halo = _qcolor(t.accent); halo.setAlpha(60)
            p.setPen(QPen(halo, 7))
            p.drawPath(self._path)
            p.setPen(QPen(_qcolor(t.accent), 2.4))
        else:
            p.setPen(QPen(_qcolor(t.line2), 2))
        p.drawPath(self._path)
        if self._packet_t is not None:
            pt = self._path.pointAtPercent(self._packet_t)
            col = self._packet_color or _qcolor(t.accent)
            p.setBrush(col)
            p.setPen(Qt.NoPen)
            p.drawEllipse(pt, 5, 5)

    def _paint_attach(self, p: QPainter, t) -> None:
        """A rider mount: a DOTTED tether with a bold, accent-coloured polarity glyph at the MIDPOINT
        (always on the visible line, whatever the length) — a big arrowhead pointing toward the donor
        for a source (it injects INTO it), a diamond for a sink (it observes). Reads as an annotation
        ('runs on'), never a cable."""
        import math
        dev = self._scene.ctx.topology.devices.get(self.link.source_id)
        role = getattr(dev.type, "role", "") if dev is not None else ""
        gcol = _qcolor(t.line)                   # match the dotted tether — big shape, subtle colour

        pen = QPen(_qcolor(t.line), 2.4)
        pen.setCapStyle(Qt.RoundCap)
        pen.setDashPattern([1.4, 2.4])
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(self._path)

        c = self._path.pointAtPercent(0.5)                   # glyph at the midpoint of the tether
        a0 = self._path.pointAtPercent(0.42)
        a1 = self._path.pointAtPercent(0.58)
        ang = math.atan2(a1.y() - a0.y(), a1.x() - a0.x())   # tangent, pointing toward the donor
        dx, dy = math.cos(ang), math.sin(ang)
        px, py = -math.sin(ang), math.cos(ang)               # perpendicular
        p.setPen(QPen(_qcolor(t.bg), 1.4))                   # thin bg halo so it reads on any node
        p.setBrush(gcol)
        if role == "sink":                                   # a bold diamond (observer)
            r = 8.0
            p.drawPolygon(QPolygonF([
                QPointF(c.x() + r * dx, c.y() + r * dy), QPointF(c.x() + r * px, c.y() + r * py),
                QPointF(c.x() - r * dx, c.y() - r * dy), QPointF(c.x() - r * px, c.y() - r * py)]))
        else:                                                # a big arrowhead into the donor
            L, W = 16.0, 9.0
            tip = QPointF(c.x() + (L / 2) * dx, c.y() + (L / 2) * dy)
            bx, by = c.x() - (L / 2) * dx, c.y() - (L / 2) * dy
            p.drawPolygon(QPolygonF([tip, QPointF(bx + W * px, by + W * py),
                                     QPointF(bx - W * px, by - W * py)]))


class CalloutItem(QGraphicsObject):
    """An anchored speech bubble the AI tutor places on a node."""

    def __init__(self, scene: "CanvasScene", node: NodeItem, text: str) -> None:
        super().__init__()
        self._scene = scene
        self._node = node
        self.text = text
        self.setZValue(60)
        self._font = QFont()
        self._font.setPointSize(_sp(10))
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
        self.groups: dict[str, GroupItem] = {}     # VPC/Subnet/Region container boxes
        self.running = False                        # lab up? gates console/logs/login actions
        self.setSceneRect(-2000, -2000, 4000, 4000)
        self._cascade = 0
        self._callouts: list[CalloutItem] = []
        self._spotlit: list[NodeItem] = []
        self._highlit: list[NodeItem] = []

        ctx.bus.device_added.connect(self._on_device_added)
        ctx.bus.device_removed.connect(self._on_device_removed)
        ctx.bus.link_added.connect(self._on_link_added)
        ctx.bus.link_removed.connect(self._on_link_removed)
        ctx.bus.device_changed.connect(self._on_device_changed)
        ctx.bus.addressing_changed.connect(self._refresh_node_labels)
        ctx.bus.warnings_changed.connect(self._on_warnings)
        ctx.bus.mission_flags_changed.connect(self._on_mission_flags)
        # tutor "present" channel
        ctx.bus.present_spotlight.connect(self._on_spotlight)
        ctx.bus.present_highlight.connect(self._on_highlight)
        ctx.bus.present_callout.connect(self._on_callout)
        ctx.bus.present_packet.connect(self._on_packet)
        ctx.bus.present_clear.connect(self._on_clear_stage)
        ctx.bus.addressing_changed.connect(self._on_addressing)
        ctx.bus.edges_restyled.connect(self._on_restyle)
        ctx.bus.mission_changed.connect(self._on_mission)

    def _on_mission(self, _mission) -> None:
        for n in self.nodes.values():                # show/hide off-goal ✕ badges
            n.update()

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
        scale = _uiscale()
        for n in self.nodes.values():
            n.refresh_theme()
            n.setScale(scale)              # follow the current text-size setting (card grows with text)
            n.update()
        for g in self.groups.values():
            g.update()
        for e in self.edges.values():
            e.refresh()                    # re-route: node extents changed with the scale
            e.update()

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        painter.fillRect(rect, _qcolor(self.theme.bg))
        if self.ctx.settings.grid:
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
            self._draw_vignette(painter, rect)
        self._draw_slot_hulls(painter)

    def _draw_slot_hulls(self, painter: QPainter) -> None:
        """Draw a labelled hull around each group of slot-tagged elements.

        A fragment's slot is a *parameter*: while authoring, the canvas holds a stand-in provider so
        you can wire your delta to something real. Without this, that scaffolding is indistinguishable
        from the fragment itself — you see a whole network and can't tell which third is yours. The
        hull is DERIVED (the bounding box of everything sharing a slot tag), so nothing is positioned
        by hand and it stays right when nodes move. Label = slot · what fills it · how many."""
        groups: dict[str, list] = {}
        for n in self.nodes.values():
            s = getattr(n.inst, "slot", "")
            if s:
                groups.setdefault(s, []).append(n)
        if not groups:
            return
        t = self.theme
        painter.setRenderHint(QPainter.Antialiasing, True)
        f = QFont(); f.setPointSize(9); f.setBold(True)
        painter.setFont(f)
        for name, items in sorted(groups.items()):
            box = None
            source = ""
            for n in items:
                source = source or getattr(n.inst, "slot_source", "")
                r = QRectF(n.pos(), QSizeF(NODE_W * n.scale(), n.node_h() * n.scale()))
                box = r if box is None else box.united(r)
            if box is None:
                continue
            box = box.adjusted(-18, -26, 18, 18)
            col = _qcolor(t.line)
            fill = QColor(col); fill.setAlpha(16)
            painter.setBrush(fill)
            pen = QPen(col, 1.4)
            pen.setStyle(Qt.DashLine)          # dashed = a placeholder, not a real boundary
            painter.setPen(pen)
            painter.drawRoundedRect(box, 12, 12)
            lbl = QColor(col); lbl.setAlpha(210)
            painter.setPen(QPen(lbl, 1))
            painter.drawText(QRectF(box.left() + 10, box.top() + 4, box.width() - 20, 18),
                             Qt.AlignLeft | Qt.AlignVCenter,
                             self._slot_label(name, len(items), source))

    def _slot_label(self, name: str, n_items: int, source: str = "") -> str:
        """`nets · cap-lan · 3 elements` — the slot, WHAT is filling it, and the group's size.

        The composer labels members `root_lans0`, `root_lans1`, … — the trailing index is the member
        number, so strip it for display and show the slot's own name."""
        import re
        base = name.split("_")[-1] if "_" in name else name
        base = re.sub(r"\d+$", "", base) or base
        head = f"{base} · {source}" if source else base
        return f"{head}   ·   {n_items} element(s)"

    def _draw_vignette(self, painter: QPainter, rect: QRectF) -> None:
        """A soft radial shade toward the viewport edges so the board reads as a
        lit surface with depth. Anchored in scene coords (consistent across the
        partial redraws drawBackground receives)."""
        views = self.views()
        if not views:
            return
        vp = views[0].viewport().rect()
        center = views[0].mapToScene(vp.center())
        corner = views[0].mapToScene(vp.topLeft())
        radius = math.hypot(corner.x() - center.x(), corner.y() - center.y())
        if radius <= 0:
            return
        edge = QColor(0, 0, 0, 78) if self.theme.dark else QColor(22, 30, 46, 30)
        clear = QColor(edge.red(), edge.green(), edge.blue(), 0)
        grad = QRadialGradient(center, radius)
        grad.setColorAt(0.0, clear)
        grad.setColorAt(0.58, clear)
        grad.setColorAt(1.0, edge)
        painter.fillRect(rect, QBrush(grad))

    # -- model -> scene ----------------------------------------------------- #
    def _on_device_added(self, device_id: str) -> None:
        inst = self.ctx.topology.devices[device_id]
        if inst.x == 0 and inst.y == 0:
            self._cascade += 1
            inst.x = -120 + (self._cascade % 6) * 150
            inst.y = -90 + (self._cascade % 4) * 110
        if inst.type_key in grouping.BOX_TYPES:       # VPC / Subnet / Region -> a box
            group = GroupItem(self, inst)
            self.groups[device_id] = group
            self.addItem(group)
        else:
            node = NodeItem(self, inst)
            self.nodes[device_id] = node
            self.addItem(node)
        self.recompute_membership()                  # a new box may capture nodes, or vice versa
        m = getattr(self.ctx, "mission", None)       # Wizard: flag an off-goal drop
        if m is not None and not m.allows(inst.type_key):
            self.ctx.bus.present_callout.emit(
                device_id, f"Off-goal — not part of “{m.goal}”. Click the ✕ to remove it.")

    def _on_link_removed(self, link_id: str) -> None:
        edge = self.edges.pop(link_id, None)
        if edge:
            self.removeItem(edge)

    def _on_device_removed(self, device_id: str) -> None:
        node = self.nodes.pop(device_id, None)
        if node:
            self.removeItem(node)
        group = self.groups.pop(device_id, None)
        if group:
            self.removeItem(group)
            # children of a deleted box fall back to no VPC (the flat bridge)
            for d in self.ctx.topology.devices.values():
                if d.parent_id == device_id:
                    d.parent_id = None
        for lid in [l.id for l in list(self.ctx.topology.links.values())]:
            pass  # links already pruned in model; clean orphan edges below
        for eid, edge in list(self.edges.items()):
            if eid not in self.ctx.topology.links:
                self.removeItem(edge)
                self.edges.pop(eid, None)
        if group:
            self.recompute_membership()

    def _move_children(self, parent_id: str, dx: float, dy: float) -> None:
        """Move every item that belongs to `parent_id` by (dx, dy) so a box's contents
        travel with it (nested boxes recurse via their own itemChange)."""
        for item in list(self.nodes.values()) + list(self.groups.values()):
            if item.inst.parent_id == parent_id:
                item.setPos(item.pos().x() + dx, item.pos().y() + dy)

    def recompute_membership(self) -> None:
        """Reassign every element's parent_id from where it now sits relative to the boxes.
        Emits topology_changed (so the compiler re-reads VPC membership) only on a change."""
        if not self.groups:
            # no boxes: clear any stale memberships (e.g., last box just deleted)
            changed = False
            for d in self.ctx.topology.devices.values():
                if d.parent_id is not None:
                    d.parent_id = None
                    changed = True
            if changed:
                self.ctx.bus.topology_changed.emit()
            return
        boxes = [(g.inst.id, g.pos().x(), g.pos().y(), float(g.inst.w), float(g.inst.h))
                 for g in self.groups.values()]
        centers: dict = {}
        for n in self.nodes.values():
            c = n.sceneBoundingRect().center()
            centers[n.inst.id] = (c.x(), c.y())
        for g in self.groups.values():
            centers[g.inst.id] = (g.pos().x() + g.inst.w / 2.0,
                                  g.pos().y() + g.inst.h / 2.0)
        parent_of = {d.id: d.parent_id for d in self.ctx.topology.devices.values()}
        changed = False
        for did, pid in grouping.recompute(centers, boxes, parent_of).items():
            dev = self.ctx.topology.devices.get(did)
            if dev is not None and dev.parent_id != pid:
                dev.parent_id = pid
                changed = True
        if changed:
            self.ctx.bus.topology_changed.emit()

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
        group = self.groups.get(device_id)
        if group:
            group.update()                      # repaint title/CIDR after an edit

    def _refresh_node_labels(self) -> None:
        for node in self.nodes.values():
            node.update()                       # repaint IP labels after addressing changes

    def _on_warnings(self) -> None:
        warns = self.ctx.warnings
        for node in self.nodes.values():
            msgs = warns.get(node.inst.name)
            node.setToolTip("\n".join(msgs) if msgs else "")
            node.update()

    def _on_mission_flags(self) -> None:
        flags = getattr(self.ctx, "mission_flags", {})
        for node in self.nodes.values():
            reason = flags.get(node.inst.id)
            if reason:
                node.setToolTip(reason)
            node.update()                       # repaint red badge/glow (or clear it)

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

    def _prune_dead(self) -> None:
        """Drop tracked items whose C++ object Qt has already deleted.

        Python dict membership and Qt object lifetime are INDEPENDENT: an item can be destroyed
        underneath us (a scene clear, a parent going away, deleteLater) while our wrapper is still
        sitting in self.nodes, and the next attribute access on it raises

            RuntimeError: Internal C++ object (NodeItem) already deleted.

        That was reaching the user as a bare traceback out of a bus handler, which also stopped
        every later slot on that signal from running — so an overlay stayed on screen with no
        explanation. Pruning makes the state converge instead: whatever went wrong, the next clear
        is clean.

        The log line is deliberate and names the element. This is a GUARD, not a root-cause fix —
        the path that orphans the item has not been reproduced, and that message is the evidence
        needed to find it.
        """
        for name, book in (("node", self.nodes), ("edge", self.edges), ("group", self.groups)):
            for key, item in list(book.items()):
                try:
                    item.opacity()                     # cheapest call that touches the C++ object
                except RuntimeError:
                    book.pop(key, None)
                    self.ctx.bus.log.emit(
                        "error", f"canvas: dropped a deleted {name} still tracked as {key} — "
                                 f"please report this with what you had just done")
        for attr in ("_spotlit", "_highlit", "_callouts"):
            live = []
            for item in getattr(self, attr, []):
                try:
                    item.opacity()
                    live.append(item)
                except RuntimeError:
                    pass
            setattr(self, attr, live)

    def _on_clear_stage(self) -> None:
        self._prune_dead()                             # never raise out of a bus handler
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


class LiveClientItem(QGraphicsObject):
    """A real device sitting on a GINI32 board's radio, right now.

    This is the one thing on the canvas that nobody drew. It is OBSERVED: the board
    reports which devices are associated to its hotspot, and each becomes one of
    these, hanging off the board. It is never part of the topology, never saved, and
    cannot be selected or moved — because the person holding the phone owns it, not
    the person editing the diagram. It vanishes when the device walks away.
    """
    W, H = 118.0, 40.0

    def __init__(self, theme, mac: str, ip: str) -> None:
        super().__init__()
        self._theme = theme
        self.mac = mac
        self.ip = ip
        self.setZValue(-0.5)                 # behind real elements
        self.setAcceptedMouseButtons(Qt.NoButton)
        # "?" is a real value here, not a missing key: a stock-built firmware cannot read
        # the DHCP leases (that needs GB_HAVE_STA_IPS) and reports every station as
        # `mac/?`. Rendering the sentinel straight into the sentence produced "on this
        # board's Wi-Fi as ?", which reads like a bug in the canvas rather than a
        # limitation of the board. Say what is actually known instead.
        where = (f"on this board's Wi-Fi as {ip}" if ip and ip != "?"
                 else "on this board's Wi-Fi (address not reported by the board)")
        self.setToolTip(f"{mac}\n{where}\n"
                        f"(a real device — not part of the saved topology)")
        self.setOpacity(0.0)
        self._fade(0.96)

    def _fade(self, to: float) -> None:
        anim = QVariantAnimation(self)
        anim.setStartValue(self.opacity())
        anim.setEndValue(to)
        anim.setDuration(220)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.valueChanged.connect(self.setOpacity)
        anim.start(QVariantAnimation.DeleteWhenStopped)
        self._anim = anim

    def boundingRect(self) -> QRectF:
        return QRectF(-2, -2, self.W + 4, self.H + 4)

    def paint(self, p: QPainter, *_) -> None:
        t = self._theme                       # in canvas.py `theme` IS the palette
        p.setRenderHint(QPainter.Antialiasing, True)
        accent = QColor(t.accent_for("green"))

        # dashed = "here now, not drawn": visually distinct from every real element
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, self.W, self.H), 9, 9)
        p.fillPath(path, QBrush(QColor(t.panel2)))
        pen = QPen(accent, 1.2, Qt.DashLine)
        pen.setDashPattern([4, 3])
        p.setPen(pen)
        p.drawPath(path)

        # a small radio glyph, so it reads as "arrived over the air"
        p.setPen(QPen(accent, 1.4))
        cx, cy = 15.0, self.H / 2
        for r in (3.5, 7.0):
            p.drawArc(QRectF(cx - r, cy - r, r * 2, r * 2), -50 * 16, 100 * 16)
        p.setBrush(QBrush(accent))
        p.drawEllipse(QPointF(cx - 1, cy), 1.6, 1.6)

        f = QFont(p.font()); f.setPointSizeF(8.4); f.setBold(True)
        p.setFont(f); p.setPen(QPen(QColor(t.text)))
        p.drawText(QRectF(28, 5, self.W - 34, 15), Qt.AlignVCenter | Qt.AlignLeft, self.ip)
        f.setBold(False); f.setPointSizeF(7.2)
        p.setFont(f); p.setPen(QPen(QColor(t.muted)))
        fm = QFontMetrics(f)
        p.drawText(QRectF(28, 20, self.W - 34, 14), Qt.AlignVCenter | Qt.AlignLeft,
                   fm.elidedText(self.mac, Qt.ElideMiddle, int(self.W - 36)))


class GhostItem(QGraphicsObject):
    """A translucent preview of a placeable neighbour, drawn during X-ray. Tap it and the
    view turns it into a real element wired to the long-pressed node. A ghost with
    ``type_key is None`` is the non-actionable '+N more' chip."""
    W, H = 156.0, 52.0

    def __init__(self, view, target_id, type_key, why, required) -> None:
        super().__init__()
        self._view = view
        self.target_id = target_id
        self.type_key = type_key
        self.why = why
        self.required = required
        self._hover = False
        self.setAcceptHoverEvents(type_key is not None)
        self.setOpacity(0.0)
        self._fade_in()

    def _fade_in(self) -> None:
        if self._view.ctx.settings.reduced_motion:
            self.setOpacity(0.95 if self.type_key else 0.7)
            return
        anim = QVariantAnimation(self)
        anim.setStartValue(0.0)
        anim.setEndValue(0.95 if self.type_key else 0.7)
        anim.setDuration(160)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.valueChanged.connect(lambda v: self.setOpacity(float(v)))
        anim.start()
        self._anim = anim

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, self.W, self.H)

    def hoverEnterEvent(self, e):
        self._hover = True; self.setOpacity(1.0); self.update()

    def hoverLeaveEvent(self, e):
        self._hover = False; self.setOpacity(0.95); self.update()

    def paint(self, p: QPainter, opt, widget=None) -> None:
        from ..domain.devices import REGISTRY
        t = self._view.scene_.theme
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(1, 1, self.W - 2, self.H - 2)

        if self.type_key is None:                # the "+N more" chip
            p.setBrush(_qcolor(t.panel2))
            p.setPen(QPen(_qcolor(t.line2), 1.4, Qt.DashLine))
            p.drawRoundedRect(rect, 10, 10)
            p.setPen(_qcolor(t.muted))
            f = QFont(); f.setPointSize(_sp(10)); f.setBold(True); p.setFont(f)
            p.drawText(rect, Qt.AlignCenter, self.why)
            return

        dt = REGISTRY[self.type_key]
        accent = _qcolor(t.accent_for(dt.accent.value))
        p.setBrush(_qcolor(t.panel2 if not self._hover else t.panel))
        pen = QPen(accent, 2.4 if self._hover else 1.6)
        pen.setStyle(Qt.SolidLine if self._hover else Qt.DashLine)
        p.setPen(pen)
        p.drawRoundedRect(rect, 10, 10)

        soft = QColor(accent); soft.setAlpha(40)
        p.setBrush(soft); p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(9, 11, 30, 30), 7, 7)
        p.drawPixmap(14, 16, icons.render_pixmap(dt.icon, t.accent_for(dt.accent.value), size=20))

        p.setPen(_qcolor(t.text))
        f = QFont(); f.setPointSize(_sp(10)); f.setWeight(QFont.DemiBold); p.setFont(f)
        p.drawText(QRectF(46, 7, self.W - 52, 16), Qt.AlignVCenter, dt.label)

        sub = QRectF(46, 26, self.W - 52, 16)
        if self._hover:
            p.setPen(accent)
            fh = QFont(); fh.setPointSize(_sp(8)); fh.setBold(True); p.setFont(fh)
            p.drawText(sub, Qt.AlignVCenter, "＋  tap to add")
        else:
            p.setPen(_qcolor(t.faint))
            fw = QFont(); fw.setPointSize(_sp(8)); p.setFont(fw)
            fm = p.fontMetrics()
            txt = ("needed · " if self.required else "") + self.why
            p.drawText(sub, Qt.AlignVCenter, fm.elidedText(txt, Qt.ElideRight, int(self.W - 54)))

        if self.required:                        # a small accent dot = "this link is needed"
            p.setBrush(accent); p.setPen(Qt.NoPen)
            p.drawEllipse(QRectF(self.W - 16, 9, 7, 7))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.type_key is not None:
            self._view._activate_ghost(self)
            e.accept()
            return
        super().mousePressEvent(e)


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
        # connect mode accepts EITHER gesture from the same press: a drag (release on the target) or
        # a click-click (press again on the target). Both are natural, students use both, and having
        # only one of them work was the single most frustrating thing in the app.
        self._cn_from: NodeItem | None = None
        self._cn_start = None
        self._cn_dragged = False

        # X-ray: long-press a node to reveal what it can connect to
        self._lp_node: NodeItem | None = None
        self._lp_start = None
        self._lp_timer = QTimer(self)
        self._lp_timer.setSingleShot(True)
        self._lp_timer.timeout.connect(self._on_lp_timeout)
        self._xray_on = False
        self._xray_items: list = []             # ghost cards + connector lines on the scene
        self._ghosts: list = []                 # the clickable ghost cards
        self._xray_target: NodeItem | None = None
        self._xray_timer = QTimer(self)         # auto-dismiss the overlay after a while
        self._xray_timer.setSingleShot(True)
        self._xray_timer.timeout.connect(self.clear_xray)

        # tutor narration banner (overlaid on the viewport)
        self._narration = QLabel(self)
        self._narration.setObjectName("Narration")
        self._narration.setWordWrap(True)
        self._narration.hide()
        self._narr_timer = QTimer(self)
        self._narr_timer.setSingleShot(True)
        self._narr_timer.timeout.connect(self._narration.hide)
        ctx.bus.present_narrate.connect(self._show_narration)
        ctx.bus.focus_requested.connect(self.focus_on_devices)   # e.g. a staged mission board
        ctx.bus.present_clear.connect(self._narration.hide)
        # Wizard: the assistant resolves goal-relevant neighbours (LLM) and hands them back
        ctx.bus.wizard_ghosts_requested.connect(self._on_wizard_requested)
        ctx.bus.wizard_ghosts_ready.connect(self._on_wizard_ghosts)
        ctx.bus.mission_changed.connect(lambda m: m is None and self.clear_xray())

    def set_live_clients(self, live: dict) -> dict:
        """Reconcile the devices currently on each board's radio with what is drawn.

        `live` maps a GINI32 element id -> [{mac, ip}, ...]. Returns
        {'joined': [...], 'left': [...]} so the caller can narrate the change.
        These items are ephemeral by construction: nothing here touches the topology.
        """
        existing = getattr(self, "_live_clients", None)
        if existing is None:
            existing = self._live_clients = {}        # (device_id, mac) -> item

        want: dict[tuple, dict] = {}
        for did, clients in (live or {}).items():
            for c in clients:
                want[(did, c.get("mac", ""))] = c

        joined, left = [], []

        for key in list(existing):
            if key not in want:
                item = existing.pop(key)
                if item.scene() is not None:
                    item.scene().removeItem(item)
                left.append(f"{key[1]}")

        for key, c in want.items():
            if key in existing:
                continue
            did, mac = key
            node = self.scene_.nodes.get(did)
            if node is None:
                continue
            item = LiveClientItem(self.scene_.theme, mac, c.get("ip", "?"))
            self.scene_.addItem(item)
            existing[key] = item
            joined.append(f"{mac} ({c.get('ip', '?')})")

        # lay each board's devices out beneath it, stacked
        for did in {k[0] for k in existing}:
            node = self.scene_.nodes.get(did)
            if node is None:
                continue
            mine = [existing[k] for k in sorted(existing) if k[0] == did]
            for i, item in enumerate(mine):
                item.setPos(node.x() + (NODE_W - LiveClientItem.W) / 2,
                            node.y() + NODE_H + 16 + i * (LiveClientItem.H + 6))

        return {"joined": joined, "left": left}

    def clear_live_clients(self) -> None:
        """Drop every observed device — the lab stopped, so we know nothing."""
        for item in getattr(self, "_live_clients", {}).values():
            if item.scene() is not None:
                item.scene().removeItem(item)
        self._live_clients = {}

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

    # -- X-ray: long-press spawns ghost previews of the valid neighbours ----- #
    MAX_GHOSTS = 8                               # cap the ring so it stays readable

    def _on_lp_timeout(self) -> None:
        """The long-press timer expired. Decide whether it was REALLY a long press.

        Stopping the timer on release is not enough on its own. If the GUI thread stalls between
        the press and the release — which it does on a slow machine while something expensive is
        being built — the queued timer expiry is delivered BEFORE the queued mouse release, and an
        ordinary click opens the X-ray ring. Reported as "I click on the router and it is
        immediately registered as a long press. I never long-pressed."

        Qt's live button state is not queued, so it reports what the mouse is doing NOW rather
        than what the event backlog has got round to. If the button is already up, this was a
        click that the stall made look slow.

        The gesture decision lives here and the ring lives in _fire_xray, so the two can be
        tested apart: the X-ray's CONTENT does not depend on a physical button being held.
        """
        from PySide6.QtWidgets import QApplication
        if not (QApplication.mouseButtons() & Qt.LeftButton):
            self._lp_node = None
            return
        self._fire_xray()

    def _fire_xray(self) -> None:
        from ..domain import connection_rules as cr
        node = self._lp_node
        self._lp_node = None
        if node is None or node.inst.id not in self.ctx.topology.devices:
            return
        if getattr(self.ctx, "mission", None) is not None:
            # Wizard: the assistant resolves goal-relevant neighbours via the model (async)
            self.ctx.bus.wizard_ghosts_requested.emit(node.inst.id)
            return
        partners = cr.partners_for(node.inst.type_key)
        if not partners:
            return
        self.clear_xray()
        self._xray_target = node
        node.set_xray("target")                  # ring the pressed element
        self._build_ghosts(node, partners)
        self._xray_on = True
        self._xray_timer.start(15000)

    # -- Wizard X-ray: async, model-filtered ghosts --------------------------- #
    def _on_wizard_requested(self, device_id: str) -> None:
        node = self.scene_.nodes.get(device_id)
        if node is None:
            return
        self.clear_xray()
        self._xray_target = node
        self._xray_on = True
        node.set_xray("target")
        self._show_thinking(node)                 # placeholder until the model answers
        self._xray_timer.start(20000)

    def _show_thinking(self, node) -> None:
        import math
        c = node.sceneBoundingRect().center()
        g = GhostItem(self, node.inst.id, None, "thinking…", False)
        g.setPos(c.x() - GhostItem.W / 2, c.y() - 215.0 - GhostItem.H / 2)
        g.setZValue(20)
        self.scene_.addItem(g)
        self._xray_items.append(g)

    def _on_wizard_ghosts(self, device_id: str, items) -> None:
        if not self._xray_on or self._xray_target is None \
                or self._xray_target.inst.id != device_id:
            return
        for it in self._xray_items:                # drop the "thinking…" chip (keep the ring)
            self.scene_.removeItem(it)
        self._xray_items.clear(); self._ghosts.clear()
        self._build_ghosts_items(self._xray_target, list(items))

    def _build_ghosts(self, node, partners) -> None:
        import math
        from PySide6.QtGui import QColor, QPen
        from PySide6.QtWidgets import QGraphicsLineItem
        shown = partners[: self.MAX_GHOSTS]
        extra = len(partners) - len(shown)
        slots = len(shown) + (1 if extra > 0 else 0)
        c = node.sceneBoundingRect().center()
        rx, ry = 215.0, 175.0                    # elliptical ring (wider than tall)
        pen = QPen(QColor(self.scene_.theme.accent), 1.8, Qt.DashLine)
        for i in range(slots):
            ang = -math.pi / 2 + i * (2 * math.pi / slots)
            gx, gy = c.x() + rx * math.cos(ang), c.y() + ry * math.sin(ang)
            line = QGraphicsLineItem(c.x(), c.y(), gx, gy)
            line.setPen(pen); line.setOpacity(0.5); line.setZValue(5)
            self.scene_.addItem(line); self._xray_items.append(line)
            if extra > 0 and i == slots - 1:
                g = GhostItem(self, node.inst.id, None, f"+{extra} more", False)
            else:
                p = shown[i]
                g = GhostItem(self, node.inst.id, p.type_key, p.why, p.required)
            g.setPos(gx - GhostItem.W / 2, gy - GhostItem.H / 2)
            g.setZValue(20)
            self.scene_.addItem(g); self._xray_items.append(g)
            if g.type_key is not None:
                self._ghosts.append(g)

    def _build_ghosts_items(self, node, items) -> None:
        """Build the ghost ring from model-chosen (type_key, reason) pairs."""
        import math
        from PySide6.QtGui import QColor, QPen
        from PySide6.QtWidgets import QGraphicsLineItem
        if not items:
            self._show_thinking(node)             # nothing relevant — leave a gentle note
            self._xray_items[-1].why = "no goal-relevant options here"
            self._xray_items[-1].update()
            return
        shown = items[: self.MAX_GHOSTS]
        extra = len(items) - len(shown)
        slots = len(shown) + (1 if extra > 0 else 0)
        c = node.sceneBoundingRect().center()
        rx, ry = 215.0, 175.0
        pen = QPen(QColor(self.scene_.theme.accent), 1.8, Qt.DashLine)
        for i in range(slots):
            ang = -math.pi / 2 + i * (2 * math.pi / slots)
            gx, gy = c.x() + rx * math.cos(ang), c.y() + ry * math.sin(ang)
            line = QGraphicsLineItem(c.x(), c.y(), gx, gy)
            line.setPen(pen); line.setOpacity(0.5); line.setZValue(5)
            self.scene_.addItem(line); self._xray_items.append(line)
            if extra > 0 and i == slots - 1:
                g = GhostItem(self, node.inst.id, None, f"+{extra} more", False)
            else:
                type_key, reason = shown[i]
                g = GhostItem(self, node.inst.id, type_key, reason, False)
            g.setPos(gx - GhostItem.W / 2, gy - GhostItem.H / 2)
            g.setZValue(20)
            self.scene_.addItem(g); self._xray_items.append(g)
            if g.type_key is not None:
                self._ghosts.append(g)

    def _ghost_at(self, view_pos):
        item = self.itemAt(view_pos)
        while item is not None and not isinstance(item, GhostItem):
            item = item.parentItem()
        return item if isinstance(item, GhostItem) else None

    def _activate_ghost(self, ghost) -> None:
        """Tap a ghost -> create that element where it sits, wired to the pressed node."""
        if ghost.type_key is None:               # the "+N more" chip is not actionable
            self.clear_xray()
            return
        pos, target_id, type_key = ghost.pos(), ghost.target_id, ghost.type_key
        self.clear_xray()
        if target_id not in self.ctx.topology.devices:
            return
        try:
            inst = self.ctx.add_device(type_key, x=pos.x(), y=pos.y())
            self.ctx.connect(target_id, inst.id)
            self.ctx.select(inst.id)
        except Exception as ex:                  # noqa: BLE001
            self.ctx.log(f"Couldn't add element: {ex}", "info")
            return
        if getattr(self.ctx, "mission", None) is not None:   # auto-walk: ghosts for the new one
            self.ctx.bus.wizard_ghosts_requested.emit(inst.id)

    def clear_xray(self) -> None:
        self._xray_on = False
        self._xray_timer.stop()
        for it in self._xray_items:
            self.scene_.removeItem(it)
        self._xray_items.clear()
        self._ghosts.clear()
        if self._xray_target is not None:
            self._xray_target.set_xray(None)
            self._xray_target = None

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key_Escape and self._xray_on:
            self.clear_xray()
            e.accept()
            return
        super().keyPressEvent(e)

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

    # -- finding things that are off-screen ---------------------------------- #
    def visible_scene_rect(self) -> QRectF:
        return self.mapToScene(self.viewport().rect()).boundingRect()

    def focus_on_rect(self, rect: QRectF, *, margin: float = 70.0) -> None:
        """Bring `rect` into view — zoom out to fit only if it doesn't already fit, else just pan."""
        if rect.isNull() or rect.isEmpty():
            return
        r = rect.adjusted(-margin, -margin, margin, margin)
        vis = self.visible_scene_rect()
        if r.width() > vis.width() or r.height() > vis.height():
            self.fitInView(r, Qt.KeepAspectRatio)
            self._zoom = self.transform().m11()
        else:
            self.centerOn(r.center())

    def focus_on_devices(self, ids=None) -> None:
        """Frame the given devices (or everything, when ids is None). Used after a mission STAGES a
        board — otherwise the pre-built elements can land off-screen and the student thinks the
        mission placed nothing (they shouldn't have to hunt for it)."""
        items = list(self.scene_.nodes.values()) + list(self.scene_.groups.values())
        if ids:
            want = set(ids)
            items = [i for i in items if i.inst.id in want]
        if not items:
            return
        rect = items[0].sceneBoundingRect()
        for it in items[1:]:
            rect = rect.united(it.sceneBoundingRect())
        self.focus_on_rect(rect)

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:
        """Edge markers for elements that are OFF-SCREEN — a coloured dot pinned to the edge of the
        viewport in the direction of each hidden element, so a student can always see that something
        exists out there and which way to scroll."""
        super().drawForeground(painter, rect)
        scene = self.scene_
        if scene is None:
            return
        vis = self.visible_scene_rect()
        z = max(self._zoom, 0.05)
        inset = 13.0 / z                       # keep the dot ~13px inside the edge, at any zoom
        bounds = vis.adjusted(inset, inset, -inset, -inset)
        if bounds.width() <= 0 or bounds.height() <= 0:
            return
        radius = 5.0 / z
        theme = getattr(scene, "theme", None)
        painter.save()
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            for it in list(scene.nodes.values()) + list(scene.groups.values()):
                b = it.sceneBoundingRect()
                if vis.intersects(b):
                    continue                   # visible — nothing to point at
                c = b.center()
                x = min(max(c.x(), bounds.left()), bounds.right())
                y = min(max(c.y(), bounds.top()), bounds.bottom())
                try:
                    col = _qcolor(theme.accent_for(it.inst.type.accent.value))
                except Exception:
                    col = QColor(120, 140, 170)
                painter.setBrush(QBrush(col))
                painter.setPen(QPen(QColor(255, 255, 255, 200), 1.5 / z))
                painter.drawEllipse(QPointF(x, y), radius, radius)
        finally:
            painter.restore()                  # never leave the painter unbalanced

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
        self._end_connect_gesture()              # never leave a half-drawn wire behind
        self.setCursor(Qt.CrossCursor if on else Qt.ArrowCursor)
        self.setDragMode(QGraphicsView.NoDrag if on else QGraphicsView.RubberBandDrag)

    @staticmethod
    def _as_node(item) -> "NodeItem | None":
        """Climb from a graphics item to the NodeItem that owns it (an icon, a label and a badge are
        all children of the node)."""
        while item is not None and not isinstance(item, NodeItem):
            item = item.parentItem()
        return item if isinstance(item, NodeItem) else None

    def _node_at(self, view_pos, *, slack: int = 0) -> "NodeItem | None":
        """The node under the cursor — the ONE hit test every gesture uses.

        Two things it has to get right, both of which used to make wiring feel like a coin flip:

        * Look THROUGH whatever is on top. `itemAt()` returns only the topmost item, so an edge
          crossing the icon, a VPC box, or a callout would mask the node entirely: the press landed
          on "empty canvas" even though the cursor was dead-centre on the element. That's why the
          gesture worked on a fresh canvas and got flakier the more you drew — the failure rate was
          really the odds of something overlapping the icon. We scan the whole stack, top to bottom,
          and take the first NodeItem in it.

        * `slack` widens the hit box by a few pixels, because a 3px miss on a 40px icon is a miss by
          the user's standards but a hit by their intent. EVERY connect gesture passes slack now —
          the right-drag path didn't, which was the other half of the coin flip.
        """
        for it in self.items(view_pos):              # the full stack under the cursor, topmost first
            node = self._as_node(it)
            if node is not None:
                return node
        if slack <= 0:
            return None
        from PySide6.QtCore import QRect
        rect = QRect(view_pos.x() - slack, view_pos.y() - slack, slack * 2, slack * 2)
        for it in self.items(rect):
            node = self._as_node(it)
            if node is not None:
                return node
        return None

    def mousePressEvent(self, e) -> None:
        if self._xray_on:
            ghost = self._ghost_at(e.pos()) if e.button() == Qt.LeftButton else None
            if ghost is not None:                # tap a ghost -> add it (already connected)
                self._activate_ghost(ghost)
                e.accept()
                return
            self.clear_xray()                    # click anywhere else dismisses the overlay
        # a left-click on empty canvas exits sticky modes (connect / explain). Emit BEFORE
        # the connect handling so the mode is off by the time we get there. In connect mode we
        # allow a few px of slack, or a near-miss on an icon cancels the mode mid-gesture.
        if e.button() == Qt.LeftButton and self._node_at(
                e.pos(), slack=_HIT_SLACK if self._connect_mode else 0) is None:
            self.ctx.bus.canvas_background_clicked.emit()
        # arm the long-press X-ray on a plain left-press over a node (cancelled by drag)
        if (e.button() == Qt.LeftButton and not self._connect_mode):
            node = self._node_at(e.pos())
            if node is not None:
                self._lp_node = node
                self._lp_start = e.pos()
                self._lp_timer.start(480)
        if self._connect_mode and e.button() == Qt.LeftButton:
            node = self._node_at(e.pos(), slack=_HIT_SLACK)
            if node is not None:
                devices = self.ctx.topology.devices
                # drop a stale first endpoint (its device was deleted, or the project was
                # reloaded between clicks) so we never link to a gone device
                if self._connect_first is not None and self._connect_first not in devices:
                    self._connect_first = None
                if self._connect_first is None:
                    # ARM. This press may become either gesture — a drag (release on the target) or
                    # a click-click (press again on the target). We commit to neither yet; both end
                    # up in the same place, so the student can't pick "the wrong one".
                    self._connect_first = node.inst.id
                    self._cn_from = node
                    self._cn_start = e.pos()
                    self._cn_dragged = False
                elif self._connect_first != node.inst.id:
                    self._link(self._connect_first, node.inst.id)
                    self._end_connect_gesture()
                e.accept()
                return
        # right-button on a node: maybe a connect-drag, maybe the context menu (decided
        # on release by whether the mouse moved). Same slack as connect mode — this path had none,
        # so a 3px miss meant the press fell through and the drag simply never started.
        if e.button() == Qt.RightButton:
            node = self._node_at(e.pos(), slack=_HIT_SLACK)
            if node is not None:
                self._trace(f"right-PRESS at {e.pos().x()},{e.pos().y()} → armed on {node.inst.name}")
            else:
                self._trace(f"right-PRESS at {e.pos().x()},{e.pos().y()} → NO NODE. "
                            + self._miss_report(e.pos()))
            if node is not None:
                self._rc_from = node
                self._rc_start = e.pos()
                self._rc_moved = False
                e.accept()
                return
        super().mousePressEvent(e)

    def _miss_report(self, view_pos) -> str:
        """When a press finds no node, say exactly WHY — the two possible answers need different
        fixes and 'sometimes it works' cannot distinguish them:

          * items under the cursor are non-node things → something is MASKING the node (hit test);
          * nothing under the cursor and the nearest node is N px away → the press really did land
            on empty canvas, and the question becomes why the cursor was where the user didn't think
            it was (coordinate-space bug: widget vs viewport vs scene).
        """
        under = [type(i).__name__ for i in self.items(view_pos)]
        sp = self.mapToScene(view_pos)
        best, best_d = None, 1e9
        for n in self.scene_.nodes.values():
            r = n.sceneBoundingRect()
            dx = max(r.left() - sp.x(), 0, sp.x() - r.right())
            dy = max(r.top() - sp.y(), 0, sp.y() - r.bottom())
            d = (dx * dx + dy * dy) ** 0.5
            if d < best_d:
                best, best_d = n, d
        near = (f"nearest node {best.inst.name} is {best_d:.0f}px away (scene)"
                if best is not None else "no nodes on the canvas")
        return (f"under cursor: {under or '[]'} · scene pt ({sp.x():.0f},{sp.y():.0f}) · "
                f"{near} · zoom={self._zoom:.2f}")

    def _trace(self, msg: str) -> None:
        """Gesture tracing, off by default. Wiring bugs are reported as 'sometimes it works' — which
        is unfalsifiable without knowing WHICH of the four steps dropped the gesture. Run with
        GINI_TRACE_GESTURES=1 and every press/drag/release decision prints to the Console, so a bug
        report becomes a transcript instead of a hunch."""
        import os
        if os.environ.get("GINI_TRACE_GESTURES"):
            self.ctx.log(f"[gesture] {msg}", "info")

    def mouseDoubleClickEvent(self, e) -> None:
        """THE BUG behind "sometimes the press just doesn't happen".

        Qt does not send a second Press for a rapid second click. The sequence is:

            Press → Release → **DblClick** → Release

        so the second press arrives as `mouseDoubleClickEvent` — a completely different handler,
        which we never overrode. `mousePressEvent` was therefore never called, the gesture never
        armed, and nothing was even logged (which is what made this so hard to see: the failure was
        an ABSENCE). Wire elements slowly and everything works; wire them quickly, one after
        another — exactly what you do hooking six machines to a router — and every other gesture
        evaporates.

        This is NOT a right-button problem. CONNECT MODE HAS IT TOO: a fast second left-drag is
        swallowed identically. It only *seemed* reliable because aiming in connect mode is slower,
        so you rarely beat the double-click interval. Removing the right-drag would have left the
        bug in the gesture we kept.

        A double-click is still a press. Treat it as one — for whichever button is mid-gesture.
        Left double-click OUTSIDE connect mode keeps its real meaning: log in to the device."""
        wiring = e.button() == Qt.RightButton or (self._connect_mode
                                                  and e.button() == Qt.LeftButton)
        if wiring:
            btn = "right" if e.button() == Qt.RightButton else "left"
            self._trace(f"{btn}-DOUBLECLICK (Qt sent DblClick instead of Press) → treating as press")
            self.mousePressEvent(e)
            return
        super().mouseDoubleClickEvent(e)      # a plain left double-click still opens the console

    def mouseMoveEvent(self, e) -> None:
        if self._lp_node is not None and self._lp_start is not None:
            if (e.pos() - self._lp_start).manhattanLength() > 6:   # moving = a drag, not a hold
                self._lp_timer.stop()
                self._lp_node = None
        # connect mode: once a first endpoint is armed, the wire FOLLOWS THE CURSOR — whether you're
        # dragging or you clicked once and let go. Without this there is no feedback at all after the
        # first click, which is why the mode felt broken rather than merely unfamiliar.
        if self._connect_mode and self._cn_from is not None:
            if not self._cn_dragged and (e.pos() - self._cn_start).manhattanLength() > 4:
                self._cn_dragged = True
            self._draw_wire(self._cn_from, e.pos())
            e.accept()
            return
        if self._rc_from is not None:
            if not self._rc_moved and (e.pos() - self._rc_start).manhattanLength() > 6:
                self._rc_moved = True
            if self._rc_moved:
                self._draw_wire(self._rc_from, e.pos())
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        self._lp_timer.stop()                    # a hold that's released isn't a long-press
        self._lp_node = None
        # connect mode, left-release: if you DRAGGED, this release is the second endpoint. If you
        # merely clicked, stay armed and keep the wire on the cursor for a second click.
        if (self._connect_mode and self._cn_from is not None
                and e.button() == Qt.LeftButton and self._cn_dragged):
            target = self._node_at(e.pos(), slack=_HIT_SLACK)
            if target is not None and target.inst.id != self._cn_from.inst.id:
                self._link(self._cn_from.inst.id, target.inst.id)
                self._end_connect_gesture()
            else:
                # dragged out to nowhere — abandon the whole gesture rather than leaving a hidden
                # armed endpoint that would surprise the next click
                self._end_connect_gesture()
            e.accept()
            return
        if self._rc_from is not None and e.button() == Qt.RightButton:
            self._clear_rc_line()
            src = self._rc_from
            self._rc_from = None
            tgt = self._node_at(e.pos(), slack=_HIT_SLACK)
            self._trace(f"right-RELEASE: moved={self._rc_moved} target="
                        + (tgt.inst.name if tgt is not None else "NONE (dropped on empty canvas)"))
            if self._rc_moved:                       # right-DRAG -> connect to the target
                target = self._node_at(e.pos(), slack=_HIT_SLACK)   # …and slack on the DROP too
                if target is not None and target.inst.id != src.inst.id:
                    try:
                        self.ctx.connect(src.inst.id, target.inst.id)
                    except Exception as ex:          # noqa: BLE001
                        self.ctx.log(f"Couldn't connect: {ex}", "info")
            else:                                    # plain right-click -> context menu
                src.popup_menu(e.globalPosition().toPoint())
            e.accept()
            return
        super().mouseReleaseEvent(e)

    # -- one implementation of "draw a wire from a node to the cursor" -------- #
    def _link(self, src_id: str, dst_id: str) -> None:
        try:
            self.ctx.connect(src_id, dst_id)
        except Exception as ex:                  # noqa: BLE001 — grammar refusals land here
            self.ctx.log(f"Couldn't connect: {ex}", "info")

    def _end_connect_gesture(self) -> None:
        self._connect_first = None
        self._cn_from = None
        self._cn_dragged = False
        self._clear_rc_line()

    def _draw_wire(self, from_node, view_pos) -> None:
        from PySide6.QtCore import QLineF
        from PySide6.QtGui import QColor, QPen
        from PySide6.QtWidgets import QGraphicsLineItem
        p1 = from_node.sceneBoundingRect().center()
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
