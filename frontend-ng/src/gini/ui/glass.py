"""Shared frosted-glass look for the on-canvas HUD overlays (Network HUD, Flow HUD).

Two ingredients make a panel read as glass: the widget must be a genuinely translucent
pane so the canvas shows through it (`apply_glass`), and the fill must be a soft vertical
gradient with a light sheen along the top edge rather than a flat wash
(`paint_glass_panel`).
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPen


def apply_glass(widget) -> None:
    """Make an overlay widget a true translucent pane, so what is behind it (the canvas)
    shows through the glass rather than a solid widget background."""
    widget.setAttribute(Qt.WA_TranslucentBackground, True)
    widget.setAutoFillBackground(False)


def paint_glass_panel(p, rect, theme, title: str | None = None, radius: int = 14) -> None:
    """Paint a frosted-glass rounded panel filling `rect`. Call this first in paintEvent,
    with antialiasing already enabled."""
    t = theme.theme
    r = rect.adjusted(1, 1, -1, -1)
    base = QColor(t.panel)

    # translucent vertical gradient: brighter and more see-through at the top, denser below
    top = QColor(base); top.setAlpha(110)
    midtop = QColor(base); midtop.setAlpha(158)
    bottom = QColor(base); bottom.setAlpha(200)
    g = QLinearGradient(QPointF(r.left(), r.top()), QPointF(r.left(), r.bottom()))
    g.setColorAt(0.0, top)
    g.setColorAt(0.14, midtop)
    g.setColorAt(1.0, bottom)
    p.setPen(Qt.NoPen)
    p.setBrush(g)
    p.drawRoundedRect(r, radius, radius)

    # a bright sheen just inside the top edge — the glass catching light
    sheen = QColor(255, 255, 255, 55)
    p.setPen(QPen(sheen, 1))
    p.drawLine(r.left() + radius, r.top() + 2, r.right() - radius, r.top() + 2)

    # soft translucent rim
    rim = QColor(t.line); rim.setAlpha(170)
    p.setPen(QPen(rim, 1))
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(r, radius, radius)

    if title:
        f = QFont(); f.setPointSize(9); f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(t.muted))
        p.drawText(12, 8, rect.width() - 24, 16, Qt.AlignLeft, title)
