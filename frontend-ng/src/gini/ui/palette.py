"""Searchable, categorized device palette. Items drag onto the canvas."""
from __future__ import annotations

from PySide6.QtCore import QMimeData, QSize, Qt, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QLineEdit, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..domain import by_category
from .canvas import MIME
from .theme import ThemeManager, icons

KEY_ROLE = Qt.UserRole + 1


class _Tree(QTreeWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setHeaderHidden(True)
        self.setIndentation(10)
        self.setRootIsDecorated(True)
        self.setDragEnabled(True)
        self.setIconSize(QSize(22, 22))
        self.setExpandsOnDoubleClick(False)

    def startDrag(self, actions) -> None:
        item = self.currentItem()
        if item is None:
            return
        key = item.data(0, KEY_ROLE)
        if not key:
            return
        mime = QMimeData()
        mime.setData(MIME, key.encode())
        drag = QDrag(self)
        drag.setMimeData(mime)
        ic = item.icon(0)
        if not ic.isNull():
            drag.setPixmap(ic.pixmap(28, 28))
        drag.exec(Qt.CopyAction)


class Palette(QWidget):
    element_selected = Signal(str)      # type_key clicked (for explain mode)

    def __init__(self, theme: ThemeManager) -> None:
        super().__init__()
        self.setObjectName("Sidebar")
        self.theme = theme
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 12, 10, 10)
        lay.setSpacing(8)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search devices…")
        self.search.addAction(
            icons.icon("search", theme.theme.faint, 16),
            QLineEdit.LeadingPosition,
        )
        self.search.textChanged.connect(self._filter)
        lay.addWidget(self.search)

        self.tree = _Tree()
        self.tree.itemClicked.connect(self._on_item_clicked)
        lay.addWidget(self.tree, 1)
        self._populate()
        theme.themeChanged.connect(self._restyle)

    def _on_item_clicked(self, item, _col) -> None:
        key = item.data(0, KEY_ROLE)
        if key:
            self.element_selected.emit(key)

    def _populate(self) -> None:
        self.tree.clear()
        t = self.theme.theme
        for cat, items in by_category().items():
            top = QTreeWidgetItem([cat.value])
            top.setFlags(Qt.ItemIsEnabled)
            from .theme.manager import sp
            f = top.font(0); f.setPointSize(sp(10)); f.setBold(True); top.setFont(0, f)
            self.tree.addTopLevelItem(top)
            top.setExpanded(True)
            for d in items:
                child = QTreeWidgetItem([d.label])
                child.setData(0, KEY_ROLE, d.key)
                child.setIcon(0, icons.icon(d.icon, t.accent_for(d.accent.value), 22))
                child.setToolTip(0, d.description)
                top.addChild(child)

    def _filter(self, text: str) -> None:
        text = text.strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            visible_children = 0
            for j in range(top.childCount()):
                ch = top.child(j)
                show = text in ch.text(0).lower() or text in (ch.toolTip(0) or "").lower()
                ch.setHidden(bool(text) and not show)
                visible_children += 0 if ch.isHidden() else 1
            top.setHidden(bool(text) and visible_children == 0)
            if text:
                top.setExpanded(True)

    def _restyle(self, _name: str) -> None:
        self._populate()
        self.search.actions() and self.search.removeAction(self.search.actions()[0])
        self.search.addAction(
            icons.icon("search", self.theme.theme.faint, 16),
            QLineEdit.LeadingPosition,
        )
