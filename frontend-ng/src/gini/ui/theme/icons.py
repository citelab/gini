"""License-safe flat line-icon set, recolored per theme/category and rendered to QIcon.

Icons are hand-authored single-color SVG line glyphs (our own work, no third-party
licensing) drawn on a 24x24 grid. They take their color from the active theme, so the
same glyph reads correctly on dark, light, and brand themes. Rendering goes through
QSvgRenderer at the target device-pixel ratio for crisp output on HiDPI displays.
"""
from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

try:
    from PySide6.QtSvg import QSvgRenderer
    _HAVE_SVG = True
except Exception:  # pragma: no cover
    _HAVE_SVG = False


# inner SVG body for each glyph (stroke-based unless listed in _FILLED)
_BODY: dict[str, str] = {
    # --- networking ---
    "router": '<rect x="2.5" y="9" width="19" height="9" rx="2"/><path d="M6.5 9V6M12 9V6M17.5 9V6"/><path d="M6.5 13.5h.01M10 13.5h.01M13.5 13.5h.01"/>',
    # dynamic routing: two circulating arrows — routers exchanging routes (RIP/OSPF)
    "dynroute": '<path d="M19 12a7 7 0 0 1-12.2 4.7"/><path d="M5 12a7 7 0 0 1 12.2-4.7"/>'
                '<path d="M6.5 13.2l.3 3.5 3.4-.9"/><path d="M17.5 10.8l-.3-3.5-3.4.9"/>',
    "switch": '<rect x="2.5" y="8" width="19" height="8" rx="2"/><path d="M6 16v2.5M10 16v2.5M14 16v2.5M18 16v2.5M6 8V5.5M18 8V5.5"/>',
    "hub": '<circle cx="12" cy="12" r="3"/><path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M18.4 5.6l-2.8 2.8M8.4 15.6l-2.8 2.8"/>',
    "host": '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
    "firewall": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9.3h18M3 14.6h18M9 4v5.3M15 9.3v5.3M9 14.6V20"/>',
    "wifi": '<path d="M5 12.5a10 10 0 0 1 14 0"/><path d="M8 15.5a6 6 0 0 1 8 0"/><path d="M11 18.4a1.6 1.6 0 0 1 2 0"/>',
    "cloud": '<path d="M7 18a4 4 0 0 1 0-8 5 5 0 0 1 9.6-1.4A3.5 3.5 0 0 1 18 18z"/>',
    # GINI32: a small board with a radiating antenna — a real chip, not a cloud
    "gini32": '<rect x="3" y="10" width="11" height="9" rx="1.5"/>'
              '<path d="M6 19v2M11 19v2M6 10V8.5M11 10V8.5"/>'
              '<path d="M17 9.5a4 4 0 0 1 0 5M19.5 7a7 7 0 0 1 0 10"/>',
    # --- sdn ---
    "ovs": '<rect x="2.5" y="9" width="19" height="7" rx="2"/><path d="M12 3v6"/><path d="M7 20l5-3 5 3"/>',
    "controller": '<rect x="4" y="4" width="16" height="16" rx="3"/><circle cx="12" cy="11" r="2.5"/><path d="M12 13.5V17M9.5 11H7M17 11h-2.5"/>',
    # --- containers & k8s ---
    "container": '<path d="M3 7.5 12 3l9 4.5v9L12 21l-9-4.5z"/><path d="M3 7.5 12 12l9-4.5M12 12v9"/>',
    "pod": '<path d="M12 2.5l8.2 4.7v9.6L12 21.5 3.8 16.8V7.2z"/><circle cx="12" cy="12" r="3"/>',
    "k8s_node": '<rect x="3" y="6" width="18" height="12" rx="2"/><circle cx="12" cy="12" r="2.5"/><path d="M7 4v2M17 4v2M7 18v2M17 18v2"/>',
    "k8s_cluster": '<path d="M12 2.5l8.2 4.7v9.6L12 21.5 3.8 16.8V7.2z"/><path d="M12 8v8M8.5 10v4M15.5 10v4"/>',
    "registry": '<rect x="3" y="9" width="18" height="11" rx="2"/><path d="M7 9V5h10v4"/><path d="M8 14h8"/>',
    # --- cloud networking ---
    "vpc": '<rect x="2.5" y="4" width="19" height="16" rx="3" stroke-dasharray="4 3"/><path d="M7 12h10M12 8v8"/>',
    "cloud_subnet": '<rect x="3.5" y="6" width="17" height="12" rx="2" stroke-dasharray="3 3"/><path d="M8 12h8"/>',
    "security_group": '<path d="M12 3l7 3v5c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6z"/><path d="M9.3 12l1.8 1.8L15 10"/>',
    "gateway": '<path d="M3 12h18"/><path d="M8 7l-4 5 4 5M16 7l4 5-4 5"/>',
    "load_balancer": '<circle cx="12" cy="5" r="2.3"/><circle cx="5" cy="19" r="2.3"/><circle cx="12" cy="19" r="2.3"/><circle cx="19" cy="19" r="2.3"/><path d="M12 7.3v3M12 10.3l-7 6.4M12 10.3v6.4M12 10.3l7 6.4"/>',
    # --- compute ---
    "instance": '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 3v3M12 3v3M15 3v3M9 18v3M12 18v3M15 18v3M3 9h3M3 12h3M3 15h3M18 9h3M18 12h3M18 15h3"/>',
    "instance_group": '<rect x="7" y="3.5" width="13" height="13" rx="2"/><path d="M4 7.5v13h13"/>',
    "region": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.6 2.4 4 5.6 4 9s-1.4 6.6-4 9c-2.6-2.4-4-5.6-4-9s1.4-6.6 4-9z"/>',
    # --- storage ---
    "object_store": '<path d="M5 7h14l-1.4 12.2a1 1 0 0 1-1 .9H7.4a1 1 0 0 1-1-.9z"/><path d="M3.5 7h17"/>',
    "block_volume": '<rect x="4" y="6" width="16" height="12" rx="2"/><path d="M8 6v12M4 12h4"/>',
    "database": '<ellipse cx="12" cy="6" rx="7" ry="3"/><path d="M5 6v12c0 1.7 3.1 3 7 3s7-1.3 7-3V6"/><path d="M5 12c0 1.7 3.1 3 7 3s7-1.3 7-3"/>',
    # --- serverless ---
    "function": '<path d="M6 20l6-8M18 20 9 4H6"/>',
    "api_gateway": '<rect x="3" y="6" width="18" height="12" rx="2"/><path d="M9 9l-2 3 2 3M15 9l2 3-2 3"/>',
    "queue": '<rect x="3" y="7.5" width="4" height="9" rx="1"/><rect x="10" y="7.5" width="4" height="9" rx="1"/><rect x="17" y="7.5" width="4" height="9" rx="1"/>',
    # --- edge & traffic ---
    "proxy": '<path d="M3 12h7M21 12h-7"/><rect x="9" y="8" width="6" height="8" rx="1.5"/><path d="M5 9l-2 3 2 3M19 9l2 3-2 3"/>',
    "web_app": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 9h18M6 7h.01M8.5 7h.01"/>',
    # --- streaming & messaging ---
    "stream": '<path d="M3 8c3 0 3-2 6-2s3 2 6 2 3-2 6-2M3 12c3 0 3-2 6-2s3 2 6 2 3-2 6-2M3 16c3 0 3-2 6-2s3 2 6 2 3-2 6-2"/>',
    "messaging": '<path d="M4 5h16v11H8l-4 3z"/><path d="M8 9h8M8 12h5"/>',
    # --- cache & nosql ---
    "cache": '<rect x="3.5" y="5" width="17" height="14" rx="2"/><path d="M11 8l-2.5 4H12l-2 4"/>',
    "nosql": '<path d="M12 3c4 4 4 14 0 18-4-4-4-14 0-18z"/><path d="M5.5 8.5c4 2 9 2 13 0M5.5 15.5c4-2 9-2 13 0"/>',
    # --- observability ---
    "metrics": '<path d="M3 20V4M3 20h18"/><path d="M7 16v-3M11.5 16V9M16 16v-5M20.5 16V7"/>',
    "dashboard": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 15l3-4 3 2 4-6"/>',
    "tracing": '<circle cx="5" cy="7" r="2"/><circle cx="12" cy="17" r="2"/><circle cx="19" cy="9" r="2"/><path d="M6.6 8.4l4 7M13.6 15.8l4-5.4"/>',
    # --- workload & testing ---
    "load_generator": '<path d="M12 13a8 8 0 0 1 8-8M12 13l5-5"/><path d="M4 19a8 8 0 0 1 2.3-12.7M20 19a8 8 0 0 0-2.3-12.7" stroke-dasharray="2 2.5"/>',
    # --- UI glyphs ---
    "new": '<path d="M12 5v14M5 12h14"/>',
    "open": '<path d="M3 7h6l2 2h10v9a2 2 0 0 1-2 2H3z"/>',
    "save": '<path d="M5 3h11l3 3v15H5z"/><path d="M8 3v5h7M8 13h8v8H8z"/>',
    "compile": '<path d="M9 6l-5 6 5 6M15 6l5 6-5 6"/>',
    "layout": '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4-4"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "minus": '<path d="M5 12h14"/>',
    "palette": '<circle cx="12" cy="12" r="9"/><circle cx="8.5" cy="9.5" r="1.1"/><circle cx="12" cy="8" r="1.1"/><circle cx="15.5" cy="9.5" r="1.1"/>',
    "robot": '<rect x="4" y="8" width="16" height="11" rx="3"/><path d="M12 8V4M9 13h.01M15 13h.01M9.5 16h5M2 12v3M22 12v3"/>',
    "send": '<path d="M4 12l16-7-7 16-2-7z"/>',
    "grid": '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18M15 3v18"/>',
    "trash": '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>',
    "link": '<path d="M9 15l6-6M8.5 8H6a4 4 0 0 0 0 8h2.5M15.5 16H18a4 4 0 0 0 0-8h-2.5"/>',
    "elbow": '<path d="M5 5v6a3 3 0 0 0 3 3h6a3 3 0 0 1 3 3v2"/><circle cx="5" cy="5" r="1.9"/><circle cx="20" cy="19" r="1.9"/>',
    "pencil": '<path d="M4 20.5h4L19 9.5a2 2 0 0 0 0-2.8l-1.7-1.7a2 2 0 0 0-2.8 0L3.5 16.5z"/><path d="M13.5 6.5l4 4"/>',
    "chevron_right": '<path d="M9 6l6 6-6 6"/>',
    "chevron_down": '<path d="M6 9l6 6 6-6"/>',
}

_FILLED = {"play", "stop", "dot"}
_BODY["play"] = '<path d="M8 5v14l11-7z"/>'
_BODY["stop"] = '<rect x="6" y="6" width="12" height="12" rx="2"/>'
_BODY["dot"] = '<circle cx="12" cy="12" r="6"/>'


def _svg(name: str, color: str, stroke_width: float) -> str:
    body = _BODY.get(name, _BODY["host"])
    if name in _FILLED:
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
                f'fill="{color}" stroke="none">{body}</svg>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
            f'stroke="{color}" stroke-width="{stroke_width}" stroke-linecap="round" '
            f'stroke-linejoin="round">{body}</svg>')


_cache: dict[tuple, QIcon] = {}


def render_pixmap(name: str, color: str, size: int = 22, stroke_width: float = 1.7,
                  ratio: float = 2.0) -> QPixmap:
    px = QPixmap(int(size * ratio), int(size * ratio))
    px.setDevicePixelRatio(ratio)
    px.fill(Qt.transparent)
    if _HAVE_SVG:
        renderer = QSvgRenderer(QByteArray(_svg(name, color, stroke_width).encode()))
        p = QPainter(px)
        p.setRenderHint(QPainter.Antialiasing, True)
        renderer.render(p, QRectF(0, 0, size, size))
        p.end()
    else:  # pragma: no cover - fallback shape
        p = QPainter(px)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setBrush(QColor(color))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(3, 3, size - 6, size - 6), 5, 5)
        p.end()
    return px


def icon(name: str, color: str, size: int = 22, stroke_width: float = 1.7) -> QIcon:
    key = (name, color, size, round(stroke_width, 2))
    if key not in _cache:
        _cache[key] = QIcon(render_pixmap(name, color, size, stroke_width))
    return _cache[key]


def clear_cache() -> None:
    _cache.clear()
