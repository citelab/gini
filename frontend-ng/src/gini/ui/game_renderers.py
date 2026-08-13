"""Signature renderers for the Diagnose games (and the Fingerprint Explore tab).

Each renderer is a small QWidget painter with a `show_signature(sig)` method the generic
DiagnoseGameWidget calls to draw a mystery case. RadarChart/ScatterBoard also back the Fingerprint
Explore tab. GanttSnippet (policy game) and EventCard (fault/trap games) live here too.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPolygonF
from PySide6.QtWidgets import QWidget

from ..domain.fingerprint import AXIS_LABEL, FEATURE_AXES


class RadarChart(QWidget):
    """A pentagon radar over FEATURE_AXES; one or more translucent polygons."""

    def __init__(self, theme, parent=None) -> None:
        super().__init__(parent)
        self.theme = theme
        self._series: list = []          # [(label, fp, color_key)]
        self.setMinimumSize(320, 280)

    def set_series(self, series) -> None:
        self._series = series
        self.update()

    def show_signature(self, fp) -> None:
        """DiagnoseGameWidget renderer hook: draw a single mystery fingerprint."""
        self.set_series([("?", fp, "purple")] if fp else [])

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = self.theme.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        cx, cy = self.width() / 2, self.height() / 2 + 4
        r = min(cx, cy) - 52                       # leave room so the edge labels never clip
        n = len(FEATURE_AXES)
        p.setPen(QColor(t.line))
        for frac in (0.25, 0.5, 0.75, 1.0):
            poly = QPolygonF([self._pt(cx, cy, r * frac, i, n) for i in range(n)])
            p.drawPolygon(poly)
        p.setPen(QColor(t.muted))
        bw = 60
        for i, ax in enumerate(FEATURE_AXES):
            pt = self._pt(cx, cy, r + 16, i, n)
            if pt.x() > cx + 5:
                x, flag = pt.x(), Qt.AlignLeft
            elif pt.x() < cx - 5:
                x, flag = pt.x() - bw, Qt.AlignRight
            else:
                x, flag = pt.x() - bw / 2, Qt.AlignHCenter
            p.drawText(int(x), int(pt.y() - 8), bw, 16, flag | Qt.AlignVCenter, AXIS_LABEL[ax])
        for _label, fp, key in self._series:
            col = QColor(t.accent_for(key))
            poly = QPolygonF([self._pt(cx, cy, r * fp[ax], i, n)
                              for i, ax in enumerate(FEATURE_AXES)])
            fill = QColor(col); fill.setAlpha(60)
            p.setBrush(fill); p.setPen(col)
            p.drawPolygon(poly)

    @staticmethod
    def _pt(cx, cy, rad, i, n) -> QPointF:
        ang = -math.pi / 2 + i * 2 * math.pi / n
        return QPointF(cx + rad * math.cos(ang), cy + rad * math.sin(ang))


class ScatterBoard(QWidget):
    """The behavior map: x = CPU-bound <-> IO-bound, y = compute <-> heavy-kernel."""

    def __init__(self, theme, parent=None) -> None:
        super().__init__(parent)
        self.theme = theme
        self._points: list = []          # [(x, y, label, color_key)]
        self.setMinimumSize(300, 240)

    def set_points(self, points) -> None:
        self._points = points
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = self.theme.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        m = 30
        w, h = self.width() - 2 * m, self.height() - 2 * m
        p.setPen(QColor(t.line))
        p.drawRect(m, m, w, h)
        p.drawLine(int(m + w / 2), m, int(m + w / 2), m + h)
        p.drawLine(m, int(m + h / 2), m + w, int(m + h / 2))
        p.setPen(QColor(t.muted))
        p.drawText(m, m + h + 4, w, 16, Qt.AlignLeft, "IO-bound")
        p.drawText(m, m + h + 4, w, 16, Qt.AlignRight, "CPU-bound")
        p.save(); p.translate(m - 8, m + h); p.rotate(-90)
        p.drawText(0, 0, h, 16, Qt.AlignLeft, "compute")
        p.drawText(0, 0, h, 16, Qt.AlignRight, "kernel")
        p.restore()
        for x, y, label, key in self._points:
            px = m + x * w
            py = m + (1 - y) * h
            col = QColor(t.accent_for(key))
            p.setBrush(col); p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(px, py), 6, 6)
            p.setPen(QColor(t.text))
            p.drawText(int(px + 8), int(py + 4), label)


class GanttSnippet(QWidget):
    """A short running-pid timeline (the guess-the-policy signature). set via a list of pids."""

    def __init__(self, theme, parent=None) -> None:
        super().__init__(parent)
        self.theme = theme
        self._slots: list = []           # [pid or None] over time
        self.setMinimumSize(320, 130)

    def show_signature(self, slots) -> None:
        self._slots = list(slots or [])
        self.update()

    def _color(self, pid) -> str:
        keys = ["blue", "green", "purple", "amber", "teal", "pink", "orange"]
        return keys[pid % len(keys)] if pid is not None else "slate"

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = self.theme.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self._slots:
            p.setPen(QColor(t.faint))
            p.drawText(self.rect(), Qt.AlignCenter, "— no timeline —")
            return
        m = 12
        w = (self.width() - 2 * m) / max(len(self._slots), 1)
        h = self.height() - 2 * m
        from ..domain.xv6 import short_pid
        for i, pid in enumerate(self._slots):
            x = m + i * w
            col = QColor(t.accent_for(self._color(pid))) if pid is not None else QColor(t.panel2)
            p.fillRect(int(x), m, int(w) + 1, h, col)
            if pid is not None and (i == 0 or self._slots[i - 1] != pid) and w >= 14:
                p.setPen(QColor("#111111"))
                p.drawText(int(x), m, int(w), h, Qt.AlignCenter, short_pid(pid))


class RefStringCard(QWidget):
    """A page-reference string + frames/policy — the signature for the paging cluster games.
    signature = {"refs": [...], "frames": n, "policy": "LRU"?, "note": str?}."""

    def __init__(self, theme, parent=None) -> None:
        super().__init__(parent)
        self.theme = theme
        self._s: dict = {}
        self.setMinimumSize(320, 160)

    def show_signature(self, s) -> None:
        self._s = s or {}
        self.update()

    def _color(self, pg) -> str:
        keys = ["blue", "green", "purple", "amber", "teal", "pink", "orange", "indigo"]
        return keys[pg % len(keys)]

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = self.theme.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = self._s
        if not s:
            p.setPen(QColor(t.faint)); p.drawText(self.rect(), Qt.AlignCenter, "— no run —")
            return
        m = 14
        head = f"frames: {s.get('frames', '?')}"
        if s.get("policy"):
            head += f"      policy: {s['policy']}"
        p.setPen(QColor(t.text))
        p.drawText(m, m, self.width() - 2 * m, 18, Qt.AlignLeft, head)
        # the reference string as a wrapped row of numbered cells
        refs = s.get("refs", [])
        cw, ch, gap = 26, 24, 6
        x, y = m, m + 30
        p.setFont(self.font())
        for pg in refs:
            if x + cw > self.width() - m:
                x = m; y += ch + gap
            col = QColor(t.accent_for(self._color(pg)))
            p.setBrush(QColor(t.panel)); p.setPen(col)
            p.drawRoundedRect(x, y, cw, ch, 5, 5)
            p.setPen(QColor(t.text))
            p.drawText(x, y, cw, ch, Qt.AlignCenter, str(pg))
            x += cw + gap
        if s.get("note"):
            p.setPen(QColor(t.muted))
            p.drawText(m, self.height() - 22, self.width() - 2 * m, 16, Qt.AlignLeft, s["note"])


class TranslateCard(QWidget):
    """A page table + a target VA — the address-translation signature.
    signature = {"rows": [(va_base, pa_base, perms)], "va": int}."""

    def __init__(self, theme, parent=None) -> None:
        super().__init__(parent)
        self.theme = theme
        self._s: dict = {}
        self.setMinimumSize(340, 200)

    def show_signature(self, s) -> None:
        self._s = s or {}
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = self.theme.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = self._s
        if not s:
            p.setPen(QColor(t.faint)); p.drawText(self.rect(), Qt.AlignCenter, "— no mapping —")
            return
        m = 14
        va = s.get("va", 0)
        rows = s.get("rows", [])
        p.setPen(QColor(t.muted))
        p.drawText(m, m, self.width() - 2 * m, 16, Qt.AlignLeft, "page table  ·  leaf mappings")
        y = m + 22
        for vb, pb, perm in rows:
            hit = vb <= va < vb + 4096
            p.setPen(QColor(t.accent if hit else t.line))
            p.setBrush(QColor(t.panel2) if hit else QColor(t.panel))
            p.drawRoundedRect(m, y, self.width() - 2 * m, 22, 5, 5)
            p.setPen(QColor(t.text))
            p.drawText(m + 8, y, self.width() - 2 * m, 22, Qt.AlignVCenter,
                       f"VA {hex(vb)}   →   PA {hex(pb)}    {perm}")
            y += 26
        p.setPen(QColor(t.accent_for("purple")))
        f = self.font(); f.setBold(True); p.setFont(f)
        p.drawText(m, y + 6, self.width() - 2 * m, 22, Qt.AlignLeft,
                   f"translate  VA = {hex(va)}   →   PA = ?")


class PagingCard(QWidget):
    """The thrashing signature: labeled meters (fault rate, locality, working-set growth) + the
    frames-vs-working-set comparison. signature = the run_features() dict."""

    def __init__(self, theme, parent=None) -> None:
        super().__init__(parent)
        self.theme = theme
        self._f: dict = {}
        self.setMinimumSize(320, 200)

    def show_signature(self, f) -> None:
        self._f = f or {}
        self.update()

    def _meter(self, p, x, y, w, label, val, key) -> None:
        t = self.theme.theme
        p.setPen(QColor(t.muted))
        p.drawText(int(x), int(y), 120, 16, Qt.AlignLeft | Qt.AlignVCenter, label)
        bx = x + 124
        bw = w - 124
        p.setBrush(QColor(t.panel)); p.setPen(QColor(t.line))
        p.drawRoundedRect(int(bx), int(y + 2), int(bw), 12, 4, 4)
        p.setBrush(QColor(t.accent_for(key))); p.setPen(Qt.NoPen)
        p.drawRoundedRect(int(bx), int(y + 2), int(bw * max(0.0, min(1.0, val))), 12, 4, 4)
        p.setPen(QColor(t.text))
        p.drawText(int(bx + bw - 44), int(y), 44, 16, Qt.AlignRight | Qt.AlignVCenter,
                   f"{round(val * 100)}%")

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = self.theme.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        f = self._f
        if not f:
            p.setPen(QColor(t.faint)); p.drawText(self.rect(), Qt.AlignCenter, "— no run —")
            return
        m = 14; w = self.width() - 2 * m
        self._meter(p, m, m, w, "fault rate", f.get("fault_rate", 0), "red")
        self._meter(p, m, m + 26, w, "locality", f.get("locality", 0), "green")
        self._meter(p, m, m + 52, w, "working-set growth", min(1.0, f.get("ws_growth", 0)), "amber")
        # frames vs working set, as a comparison line
        y = m + 84
        fr = f.get("frames", 0); ws = f.get("working_set", 0)
        p.setPen(QColor(t.text))
        p.drawText(m, y, w, 18, Qt.AlignLeft,
                   f"frames: {fr}      working set: {ws}      distinct pages: {f.get('unique_pages', 0)}")
        # a little bar comparison: frames vs working set (scaled to the larger)
        scale = max(fr, ws, 1)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(t.accent_for("blue")))
        p.drawRoundedRect(m, y + 24, int((w) * fr / scale), 12, 4, 4)
        p.setBrush(QColor(t.accent_for("purple")))
        p.drawRoundedRect(m, y + 40, int((w) * ws / scale), 12, 4, 4)
        p.setPen(QColor(t.muted))
        p.drawText(m, y + 22, w, 12, Qt.AlignLeft, "")
        p.drawText(m, y + 54, w, 14, Qt.AlignLeft, "blue = frames · purple = working set")


class EventCard(QWidget):
    """A single trap/fault event as labeled tiles (the fault-type / trap-cause signature).
    signature = dict of {field_label: value_string} in display order."""

    def __init__(self, theme, parent=None) -> None:
        super().__init__(parent)
        self.theme = theme
        self._fields: list = []          # [(label, value)]
        self.setMinimumSize(320, 130)

    def show_signature(self, fields) -> None:
        if isinstance(fields, dict):
            fields = list(fields.items())
        self._fields = list(fields or [])
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = self.theme.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self._fields:
            p.setPen(QColor(t.faint))
            p.drawText(self.rect(), Qt.AlignCenter, "— no event —")
            return
        m, gap = 14, 10
        n = len(self._fields)
        tw = (self.width() - 2 * m - gap * (n - 1)) / max(n, 1)
        th = min(80, self.height() - 2 * m)
        y = (self.height() - th) / 2
        keys = ["blue", "purple", "amber", "green", "teal"]
        for i, (label, value) in enumerate(self._fields):
            x = m + i * (tw + gap)
            col = QColor(t.accent_for(keys[i % len(keys)]))
            p.setBrush(QColor(t.panel)); p.setPen(col)
            p.drawRoundedRect(int(x), int(y), int(tw), int(th), 7, 7)
            p.setPen(col)
            p.drawText(int(x + 8), int(y + 6), int(tw - 12), 14, Qt.AlignLeft, str(label).upper())
            p.setPen(QColor(t.text))
            p.drawText(int(x + 8), int(y + 24), int(tw - 12), int(th - 30),
                       Qt.AlignLeft | Qt.TextWordWrap, str(value))
