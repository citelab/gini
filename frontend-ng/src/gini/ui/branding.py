"""App branding — the GINI mascot used as the window / taskbar / dock icon."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtGui import QIcon

ASSETS = Path(__file__).parent / "assets"
APP_ICON_PNG = ASSETS / "app_icon.png"
APP_ICON_ICNS = ASSETS / "app_icon.icns"


def icon_path() -> str:
    return str(APP_ICON_PNG)


@lru_cache(maxsize=1)
def app_icon() -> QIcon:
    """The GINI mascot as a QIcon (empty if the asset is missing — never crashes)."""
    return QIcon(str(APP_ICON_PNG)) if APP_ICON_PNG.exists() else QIcon()
