"""Zoo Lab — a historical OS from the OS Zoo, running under emulation with its screen embedded.

Open it by double-clicking an OS Zoo element (FreeDOS, Plan 9, ReactOS, or a bring-your-own
classic OS) once the topology is running. The container boots the guest under QEMU with a VNC
framebuffer and serves it as a web page (noVNC); this dialog embeds that page in a
`QWebEngineView`, so the OS runs right inside gBuilder — mouse, keyboard, boot and all.

The embedded page is the full noVNC client (`vnc.html`), which already carries its own toolbar
for power/keys (Ctrl-Alt-Del), clipboard and fullscreen; this wrapper just frames it and adds
Reload / Open-in-browser. Importing this module requires QtWebEngine — the caller catches an
ImportError and falls back to opening the same URL in the system browser, so the feature works
with no extra dependency.

Sibling of the Machine Lab (xv6): there you inspect an OS's internals; here you *use* a whole
historical OS live.
"""
from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWebEngineWidgets import QWebEngineView   # ships in PySide6-Addons (or full PySide6)
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from .theme import ThemeManager, icons
from .theme.manager import scale_css as _scss


class ZooLab(QDialog):
    """Embedded screen for one OS Zoo guest (noVNC in a QWebEngineView) + a small control strip."""

    def __init__(self, parent, theme: ThemeManager, device, url: str) -> None:
        super().__init__(parent)
        self.theme = theme
        self.device = device
        self.url = url

        t = theme.theme
        self.setWindowTitle(f"Zoo Lab — {device.name}")
        self.resize(1024, 820)                 # 1024x768 guest + room for the header/controls
        self.setStyleSheet(f"QDialog{{background:{t.bg};}}")

        root = QVBoxLayout(self)
        self._build_header(root)
        self._view = QWebEngineView(self)
        self._view.setUrl(QUrl(url))
        root.addWidget(self._view, 1)

    # -- header / controls --------------------------------------------------- #
    def _build_header(self, root) -> None:
        t = self.theme.theme
        head = QHBoxLayout()
        ic = QLabel(); ic.setPixmap(icons.render_pixmap("host", t.accent_for("orange"), 24))
        title = QLabel(f"  {self.device.name} — {self._label()}")
        title.setStyleSheet(_scss(f"color:{t.text};font-size:16px;font-weight:600;"))
        head.addWidget(ic); head.addWidget(title); head.addStretch(1)

        tk = self.device.type_key
        try:
            from ..domain import os_zoo
            chip = ("bring your own image" if tk == "oszoo_byo"
                    else "boots out of the box" if os_zoo.get(tk) else "over noVNC")
        except Exception:
            chip = "over noVNC"
        tier = QLabel(chip)
        tier.setStyleSheet(
            f"color:{t.muted};background:{t.panel2};border:1px solid {t.line};"
            "border-radius:9px;padding:2px 10px;font-size:11px;")
        head.addWidget(tier)

        reload_btn = QPushButton("  Reload")
        reload_btn.setIcon(icons.icon("link", t.accent_for("blue"), 14))
        reload_btn.setToolTip("Reconnect the embedded screen")
        reload_btn.clicked.connect(lambda: self._view.setUrl(QUrl(self.url)))
        reload_btn.setStyleSheet(self._btn_css())
        head.addWidget(reload_btn)

        browser = QPushButton("  Open in browser")
        browser.setIcon(icons.icon("open", t.accent_for("green"), 14))
        browser.setToolTip("Open this OS's screen in your web browser instead")
        browser.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self.url)))
        browser.setStyleSheet(self._btn_css())
        head.addWidget(browser)
        root.addLayout(head)

    def _label(self) -> str:
        tk = self.device.type_key
        try:
            from ..domain import os_zoo
            if tk != "oszoo_byo":
                o = os_zoo.get(tk)
                if o:
                    return o.label
            elif tk == "oszoo_byo":
                img = (self.device.properties or {}).get("Image", "")
                return f"classic OS ({img})" if img else "classic OS (set an image)"
        except Exception:
            pass
        try:                                       # generic (e.g. a Desktop machine): element label
            from ..domain.devices import REGISTRY
            dt = REGISTRY.get(tk)
            if dt:
                return dt.label
        except Exception:
            pass
        return tk

    def _btn_css(self) -> str:
        t = self.theme.theme
        return _scss(
            f"QPushButton{{color:{t.text};background:{t.panel2};border:1px solid {t.line};"
            f"border-radius:8px;padding:4px 12px;font-size:12px;}}"
            f"QPushButton:hover{{border-color:{t.accent};}}")
