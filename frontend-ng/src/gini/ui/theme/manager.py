"""Token -> QSS generator and the ThemeManager that applies it app-wide."""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from .tokens import Theme, get_theme


def _qc(spec: str) -> QColor:
    if spec.startswith("rgba("):
        n = [int(float(x)) for x in spec[5:-1].split(",")]
        return QColor(n[0], n[1], n[2], n[3] if len(n) > 3 else 255)
    return QColor(spec)


_FONT_STACK: str | None = None


def _ui_font_stack() -> str:
    """A font-family list whose FIRST entry is actually installed on this machine.

    Leading with a present family avoids Qt's slow missing-family alias lookup (the
    'Populating font family aliases … Replace uses of missing font family "Inter"'
    warning + delay seen on macOS, which lacks Inter). Inter is still used when present.
    """
    global _FONT_STACK
    if _FONT_STACK is not None:
        return _FONT_STACK
    prefs = ["Inter", "SF Pro Text", "Helvetica Neue", "Segoe UI", "Roboto", "Arial"]
    try:
        from PySide6.QtGui import QFontDatabase
        avail = set(QFontDatabase.families())
        present = [f for f in prefs if f in avail]
    except Exception:
        present = []
    present = present[:4] or ["Helvetica Neue"]
    _FONT_STACK = ", ".join(f'"{f}"' for f in present) + ", sans-serif"
    return _FONT_STACK


def build_palette(t: Theme) -> QPalette:
    """A full palette so even unstyled surfaces (scroll viewports, tab panes,
    plain QWidgets) follow the theme instead of falling back to system light."""
    p = QPalette()
    white = QColor("#ffffff")
    p.setColor(QPalette.Window, _qc(t.bg))
    p.setColor(QPalette.WindowText, _qc(t.text))
    p.setColor(QPalette.Base, _qc(t.bg3))
    p.setColor(QPalette.AlternateBase, _qc(t.bg2))
    p.setColor(QPalette.Text, _qc(t.text))
    p.setColor(QPalette.Button, _qc(t.panel2))
    p.setColor(QPalette.ButtonText, _qc(t.text))
    p.setColor(QPalette.ToolTipBase, _qc(t.panel2))
    p.setColor(QPalette.ToolTipText, _qc(t.text))
    p.setColor(QPalette.PlaceholderText, _qc(t.faint))
    p.setColor(QPalette.Highlight, _qc(t.accent))
    p.setColor(QPalette.HighlightedText, white)
    p.setColor(QPalette.BrightText, white)
    p.setColor(QPalette.Link, _qc(t.accent))
    for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, _qc(t.faint))
    return p


def build_qss(t: Theme) -> str:
    return f"""
* {{
    font-family: {_ui_font_stack()};
    font-size: 13px;
    color: {t.text};
    outline: none;
}}
QMainWindow, QDialog {{ background: {t.bg}; }}
QWidget#Sidebar, QWidget#Inspector, QWidget#Console {{ background: {t.panel}; }}
QFrame#Card {{ background: {t.panel2}; border: 1px solid {t.line}; border-radius: 10px; }}

QToolBar {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {t.panel}, stop:1 {t.panel2});
    border: none; border-bottom: 1px solid {t.line2};
    spacing: 4px; padding: 7px 12px;
}}
QToolBar::separator {{ background: {t.line}; width: 1px; margin: 6px 8px; border-radius: 1px; }}

QToolButton, QPushButton {{
    background: transparent; color: {t.muted};
    border: 1px solid transparent; border-radius: 8px; padding: 7px 10px;
}}
QToolButton:hover, QPushButton:hover {{ background: {t.bg3}; color: {t.text}; border-color: {t.line2}; }}
QToolButton:pressed, QPushButton:pressed {{ background: {t.bg2}; }}
QToolButton:checked {{ background: {t.accent_soft}; color: {t.accent}; border-color: {t.accent}; }}

/* Run = filled green primary, Stop = danger red — the toolbar's two key actions */
QToolButton#RunBtn {{
    background: {t.success}; border: 1px solid {t.success}; border-radius: 8px;
    margin: 0 1px; padding: 7px 13px;
}}
QToolButton#RunBtn:hover {{ background: {t.success}; border-color: {t.text}; }}
QToolButton#RunBtn:pressed {{ background: {t.success}; }}
QToolButton#StopBtn {{ color: {t.danger}; border: 1px solid transparent; }}
QToolButton#StopBtn:hover {{ background: {t.danger_soft}; border-color: {t.danger}; color: {t.danger}; }}

QPushButton#Primary {{ background: {t.success}; color: #ffffff; border: none; font-weight: 500; }}
QPushButton#Primary:hover {{ background: {t.success}; }}
QPushButton#Danger {{ color: {t.danger}; }}
QPushButton#Accent {{ background: {t.accent}; color: #ffffff; border: none; font-weight: 500; }}

/* GINI mode buttons — clear pressed/unpressed (toggle) state with an accent glow */
QPushButton#ModeBtn {{
    background: {t.bg3}; color: {t.muted};
    border: 1px solid {t.line}; border-radius: 13px;
    padding: 5px 12px; font-weight: 500;
}}
QPushButton#ModeBtn:hover {{ color: {t.text}; border-color: {t.accent}; }}
QPushButton#ModeBtn:checked {{
    background: {t.accent_soft}; color: {t.accent};
    border: 1px solid {t.accent}; font-weight: 600;
}}
QPushButton#WizardBtn:checked {{
    background: {t.accent_soft}; color: {t.accent2}; border: 1px solid {t.accent2};
}}

QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox {{
    background: {t.bg3}; color: {t.text};
    border: 1px solid {t.line}; border-radius: 7px; padding: 6px 9px;
    selection-background-color: {t.accent};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus {{ border-color: {t.accent}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}

QLabel#PanelHead {{ color: {t.faint}; font-size: 11px; font-weight: 600;
                    letter-spacing: 1px; text-transform: uppercase; }}
QLabel#Muted {{ color: {t.muted}; }}
QLabel#Faint {{ color: {t.faint}; }}

QListWidget, QTreeWidget, QTreeView, QListView {{
    background: transparent; border: none; }}
QListWidget::item, QTreeWidget::item {{
    border-radius: 7px; padding: 5px 6px; margin: 1px 2px; color: {t.text}; }}
QListWidget::item:hover, QTreeWidget::item:hover {{ background: {t.bg3}; }}
QListWidget::item:selected, QTreeWidget::item:selected {{
    background: {t.accent_soft}; color: {t.text}; }}
QHeaderView::section {{ background: transparent; color: {t.faint};
    border: none; border-bottom: 1px solid {t.line}; padding: 4px 6px; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QAbstractScrollArea {{ background: transparent; }}
QTabWidget::pane {{ background: {t.panel}; border: none; border-top: 1px solid {t.line}; }}
QTabWidget > QWidget {{ background: {t.panel}; }}
QTabBar::tab {{ background: transparent; color: {t.muted}; padding: 7px 12px;
    border: none; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {t.text}; border-bottom-color: {t.accent}; }}
QTabBar::tab:hover {{ color: {t.text}; }}

QDockWidget {{ titlebar-close-icon: none; titlebar-normal-icon: none; }}
QDockWidget::title {{ background: {t.panel}; color: {t.faint};
    padding: 8px 12px; border-bottom: 1px solid {t.line}; }}

QMenuBar {{ background: {t.panel}; border-bottom: 1px solid {t.line}; padding: 3px 6px; }}
QMenuBar::item {{ background: transparent; padding: 5px 10px; border-radius: 6px; color: {t.muted}; }}
QMenuBar::item:selected {{ background: {t.bg3}; color: {t.text}; }}
QMenu {{ background: {t.panel2}; border: 1px solid {t.line}; border-radius: 8px; padding: 5px; }}
QMenu::item {{ padding: 6px 22px 6px 14px; border-radius: 6px; color: {t.text}; }}
QMenu::item:selected {{ background: {t.accent_soft}; }}

QStatusBar {{ background: {t.panel}; border-top: 1px solid {t.line}; color: {t.muted}; }}
QStatusBar::item {{ border: none; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t.line2}; border-radius: 5px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {t.muted}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {t.line2}; border-radius: 5px; min-width: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QAbstractScrollArea::corner {{ background: {t.bg}; border: none; }}

QToolTip {{ background: {t.panel2}; color: {t.text};
    border: 1px solid {t.line}; border-radius: 6px; padding: 5px 8px; }}
QSplitter::handle {{ background: {t.line}; }}
"""


class ThemeManager(QObject):
    themeChanged = Signal(str)

    def __init__(self, app: QApplication, name: str = "dark") -> None:
        super().__init__()
        self._app = app
        self.theme: Theme = get_theme(name)
        # Fusion respects QPalette + QSS identically on macOS/Linux/Windows, so the
        # dark theme reaches every widget (the native macOS style does not).
        self._app.setStyle("Fusion")

    def apply(self) -> None:
        self._app.setPalette(build_palette(self.theme))
        self._app.setStyleSheet(build_qss(self.theme))

    def set_theme(self, name: str) -> None:
        self.theme = get_theme(name)
        self.apply()
        self.themeChanged.emit(self.theme.name)
