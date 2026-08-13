"""Games hub — lists every registered Diagnose game and opens the selected one.

A thin shell over game_catalog: a list of games on the left, the chosen DiagnoseGameWidget on the
right. Each Lab can also embed its own game directly (open_game), so this is the central launcher,
not the only door.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QStackedWidget, QVBoxLayout, QWidget,
)

from . import game_catalog
from .theme import ThemeManager, icons
from .theme.manager import scale_css as _scss


def open_game(parent, theme, device, state, game_id: str, live: bool):
    """Open a single game (from a Lab's embedded 'Play' button) as its own window."""
    entry = game_catalog.get(game_id)
    if entry is None:
        return None
    w = entry.build(theme, state, live)
    w.setWindowFlag(Qt.Window, True)
    w.setWindowTitle(f"{entry.title} — {device.name}")
    w.resize(760, 560)
    w.show(); w.raise_()
    return w


class GamesLab(QWidget):
    def __init__(self, parent, theme: ThemeManager, device, state, live=False) -> None:
        super().__init__(parent)
        self.setWindowFlag(Qt.Window, True)
        self.theme = theme
        self.device = device
        self.state = state
        self.live = live
        self._built: dict = {}

        t = theme.theme
        self.setWindowTitle(f"Games Lab — {device.name}")
        self.resize(820, 600)
        self.setStyleSheet(f"QWidget{{background:{t.bg};}}")
        root = QVBoxLayout(self)

        head = QHBoxLayout()
        ic = QLabel(); ic.setPixmap(icons.render_pixmap("robot", t.accent_for("purple"), 22))
        title = QLabel(f"  Games Lab — {device.name}")
        title.setStyleSheet(_scss(f"color:{t.text};font-size:16px;font-weight:600;"))
        head.addWidget(ic); head.addWidget(title); head.addStretch(1)
        chip = QLabel("live" if live else "offline demo")
        chip.setStyleSheet(f"color:{t.success if live else t.muted};background:{t.panel2};"
                           f"border:1px solid {t.line};border-radius:9px;padding:2px 10px;"
                           "font-size:11px;")
        head.addWidget(chip)
        root.addLayout(head)

        body = QHBoxLayout()
        self._list = QListWidget()
        self._list.setFixedWidth(220)
        self._list.setStyleSheet(
            f"QListWidget{{background:{t.panel};color:{t.text};border:1px solid {t.line};"
            "border-radius:8px;font-size:12px;padding:4px;}"
            f"QListWidget::item{{padding:6px 4px;}}"
            f"QListWidget::item:selected{{background:{t.panel2};color:{t.text};}}")
        self._entries = game_catalog.catalog()
        for e in self._entries:
            QListWidgetItem(f"{e.title}\n{e.subtitle}", self._list)
        self._list.currentRowChanged.connect(self._show)
        body.addWidget(self._list)

        self._stack = QStackedWidget()
        self._stack.setStyleSheet(f"QStackedWidget{{border:1px solid {t.line};border-radius:8px;}}")
        body.addWidget(self._stack, 1)
        root.addLayout(body, 1)

        if self._entries:
            self._list.setCurrentRow(0)

    def _show(self, row) -> None:
        if not (0 <= row < len(self._entries)):
            return
        entry = self._entries[row]
        if entry.id not in self._built:
            w = entry.build(self.theme, self.state, self.live)
            self._built[entry.id] = w
            self._stack.addWidget(w)
        self._stack.setCurrentWidget(self._built[entry.id])
