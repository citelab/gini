"""Themeable design tokens.

A Theme is a flat bag of color tokens. The QSS generator and the custom-painted
canvas both read from it, so re-theming the whole app is a matter of swapping one
Theme object. New themes can be added by appending to THEMES — including ones
loaded from JSON at runtime.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Theme:
    name: str
    dark: bool
    # surfaces
    bg: str
    bg2: str
    bg3: str
    panel: str
    panel2: str
    # borders
    line: str
    line2: str
    # text
    text: str
    muted: str
    faint: str
    # primary accent
    accent: str
    accent2: str
    accent_soft: str
    # semantic
    success: str
    success_soft: str
    warning: str
    warning_soft: str
    danger: str
    danger_soft: str
    # canvas
    grid: str
    # category accents (icon recolor + node accent), keyed by Accent.value
    accents: dict[str, str] = field(default_factory=dict)
    # fill for element cards ON the canvas — a lighter, theme-tinted surface so nodes
    # pop off the background (empty = fall back to panel2, i.e. the dark themes' behaviour)
    node: str = ""
    # depth + motion
    shadow: str = "rgba(0,0,0,120)"
    elevation: int = 22          # node drop-shadow blur radius (deeper = more depth)
    dur_fast: int = 120          # ms — hover/press
    dur_base: int = 200          # ms — transitions

    def accent_for(self, key: str) -> str:
        return self.accents.get(key, self.accent)

    def node_fill(self) -> str:
        """The canvas element-card fill (see `node`)."""
        return self.node or self.panel2


_CATEGORY_DARK = {
    "blue": "#4c8dff", "green": "#3fb950", "purple": "#a371f7", "teal": "#39c5cf",
    "cyan": "#2dd4bf", "indigo": "#7c8cff", "amber": "#e3a008", "pink": "#f778ba",
    "slate": "#8b98a8", "orange": "#f0883e", "red": "#f85149",
}
_CATEGORY_LIGHT = {
    "blue": "#2f6fe0", "green": "#1a7f37", "purple": "#8250df", "teal": "#1b7c83",
    "cyan": "#0e9384", "indigo": "#4f57d2", "amber": "#9a6700", "pink": "#bf3989",
    "slate": "#57606a", "orange": "#bc4c00", "red": "#cf222e",
}
_CATEGORY_BRAND = {
    "blue": "#5aa0ff", "green": "#46c98a", "purple": "#b78cff", "teal": "#43d6c4",
    "cyan": "#34e0c8", "indigo": "#8aa0ff", "amber": "#f0b429", "pink": "#ff8ad0",
    "slate": "#9fb0c8", "orange": "#ff9e4f", "red": "#ff6b66",
}


DARK = Theme(
    name="Dark", dark=True,
    bg="#0e1116", bg2="#151a21", bg3="#1b212b", panel="#11161d", panel2="#161c24",
    line="#232b36", line2="#2d3744",
    text="#e6edf3", muted="#9aa7b4", faint="#697682",
    accent="#4c8dff", accent2="#2f6fe0", accent_soft="rgba(76,141,255,40)",
    success="#3fb950", success_soft="rgba(63,185,80,38)",
    warning="#d29922", warning_soft="rgba(210,153,34,38)",
    danger="#f85149", danger_soft="rgba(248,81,73,38)",
    grid="rgba(255,255,255,12)",
    shadow="rgba(0,0,0,150)",
    accents=_CATEGORY_DARK,
)

LIGHT = Theme(
    name="Light", dark=False,
    bg="#eef1f5", bg2="#e7ebf1", bg3="#dfe4ec", panel="#ffffff", panel2="#f6f8fb",
    line="#d7dde6", line2="#c4ccd8",
    text="#131a23", muted="#3f4b5a", faint="#5e6b7b",
    accent="#2f6fe0", accent2="#2560c8", accent_soft="rgba(47,111,224,28)",
    success="#1a7f37", success_soft="rgba(26,127,55,26)",
    warning="#9a6700", warning_soft="rgba(154,103,0,26)",
    danger="#cf222e", danger_soft="rgba(207,34,46,26)",
    grid="rgba(20,30,50,16)",
    shadow="rgba(60,80,120,50)",
    node="#fdfeff",
    accents=_CATEGORY_LIGHT,
)

BRAND = Theme(
    name="GINI Brand", dark=True,
    bg="#0b1020", bg2="#121833", bg3="#18204a", panel="#0e1428", panel2="#141c3a",
    line="#23305c", line2="#2f3e74",
    text="#eaf0ff", muted="#9fb0d6", faint="#6f80ad",
    accent="#43d6c4", accent2="#2bb6a6", accent_soft="rgba(67,214,196,40)",
    success="#46c98a", success_soft="rgba(70,201,138,38)",
    warning="#f0b429", warning_soft="rgba(240,180,41,38)",
    danger="#ff6b6b", danger_soft="rgba(255,107,107,38)",
    grid="rgba(120,160,255,16)",
    shadow="rgba(0,0,25,160)",
    accents=_CATEGORY_BRAND,
)

HIGH_CONTRAST = Theme(
    name="High Contrast", dark=True,
    bg="#000000", bg2="#0a0a0a", bg3="#161616", panel="#000000", panel2="#0d0d0d",
    line="#5a5a5a", line2="#7c7c7c",
    text="#ffffff", muted="#d6d6d6", faint="#a6a6a6",
    accent="#5aa3ff", accent2="#3a86ff", accent_soft="rgba(90,163,255,60)",
    success="#3fe06a", success_soft="rgba(63,224,106,60)",
    warning="#ffcc33", warning_soft="rgba(255,204,51,60)",
    danger="#ff5a5a", danger_soft="rgba(255,90,90,60)",
    grid="rgba(255,255,255,30)",
    shadow="rgba(0,0,0,210)",
    accents={"blue": "#5aa3ff", "green": "#3fe06a", "purple": "#c08cff",
             "teal": "#3fe0d0", "cyan": "#3fe0c0", "indigo": "#9aa8ff",
             "amber": "#ffcc33", "pink": "#ff7ad0", "slate": "#c8d0d8",
             "orange": "#ff9e4f", "red": "#ff6b66"},
)


# --- light family: warmer/cooler tints of the Light theme ------------------- #
SAND = Theme(
    name="Sand", dark=False,
    bg="#f4efe4", bg2="#ece5d6", bg3="#e3dac7", panel="#fdfaf3", panel2="#f4efe4",
    line="#ddd2be", line2="#cabca3",
    text="#2b2418", muted="#5d5240", faint="#7e715c",
    accent="#b46e28", accent2="#985c1c", accent_soft="rgba(180,110,40,30)",
    success="#4c7a2f", success_soft="rgba(76,122,47,26)",
    warning="#9a6700", warning_soft="rgba(154,103,0,26)",
    danger="#c0392b", danger_soft="rgba(192,57,43,26)",
    grid="rgba(90,70,35,16)",
    shadow="rgba(90,70,40,55)",
    node="#fffdf6",
    accents=_CATEGORY_LIGHT,
)

BLUE = Theme(
    name="Blue", dark=False,
    bg="#eef2f9", bg2="#e4ecf6", bg3="#d9e3f2", panel="#ffffff", panel2="#f3f7fc",
    line="#d2ddec", line2="#becde2",
    text="#111a2b", muted="#3c4a63", faint="#5b6a85",
    accent="#2f6fe0", accent2="#2560c8", accent_soft="rgba(47,111,224,28)",
    success="#1a7f37", success_soft="rgba(26,127,55,26)",
    warning="#9a6700", warning_soft="rgba(154,103,0,26)",
    danger="#cf222e", danger_soft="rgba(207,34,46,26)",
    grid="rgba(30,60,120,16)",
    shadow="rgba(40,70,130,55)",
    node="#fafdff",
    accents=_CATEGORY_LIGHT,
)

GREEN = Theme(
    name="Green", dark=False,
    bg="#eef4ee", bg2="#e4eee4", bg3="#d8e7d8", panel="#fbfdfb", panel2="#f0f7f0",
    line="#d1e0d1", line2="#bcd0bc",
    text="#14231a", muted="#3e5245", faint="#5d7163",
    accent="#2e8b57", accent2="#237045", accent_soft="rgba(46,139,87,28)",
    success="#1a7f37", success_soft="rgba(26,127,55,26)",
    warning="#9a6700", warning_soft="rgba(154,103,0,26)",
    danger="#cf222e", danger_soft="rgba(207,34,46,26)",
    grid="rgba(30,90,50,16)",
    shadow="rgba(40,90,60,50)",
    node="#f9fefb",
    accents=_CATEGORY_LIGHT,
)


THEMES: dict[str, Theme] = {t.name.lower(): t for t in
                            (DARK, LIGHT, BRAND, HIGH_CONTRAST, SAND, BLUE, GREEN)}
# friendly aliases
THEMES["brand"] = BRAND


def get_theme(name: str) -> Theme:
    return THEMES.get(name.lower(), DARK)
