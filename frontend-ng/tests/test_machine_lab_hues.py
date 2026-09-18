"""The Machine Lab's sub-labs must stay visually distinct — in every theme, not just the one we
happened to be looking at.

There are eleven category accents and they are NOT eleven distinguishable colours. Measured below
as CIE76 ΔE across all seven themes, `teal` and `cyan` come within 6.1 of each other in Brand and
`blue` and `indigo` within 10.0 in Dark. A face wearing one of those pairs is the same colour as
its neighbour for anyone who switched theme — and nothing would ever have told us.

So the rule the face table follows, and these tests enforce: hues come from the set that stays
apart in EVERY theme, no two faces in a band share one, and no hue appears in adjacent bands.
"""
from __future__ import annotations

import itertools
import math

import pytest

from gini.ui.machine_lab import LAYERS
from gini.ui.theme import tokens as T

THEMES = [(n, getattr(T, n)) for n in
          ("DARK", "LIGHT", "BRAND", "HIGH_CONTRAST", "SAND", "BLUE", "GREEN")]

#: Below this two accents read as the same colour on screen. 26 is where the palette's own
#: usable set tops out; going higher would leave fewer hues than the biggest band has faces.
MIN_DELTA_E = 26.0


def _lab(hex_colour: str):
    h = hex_colour.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))

    def lin(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = lin(r), lin(g), lin(b)
    x = (r * .4124 + g * .3576 + b * .1805) / .9505
    y = r * .2126 + g * .7152 + b * .0722
    z = (r * .0193 + g * .1192 + b * .9505) / 1.089

    def f(t):
        return t ** (1 / 3) if t > .008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def delta_e(a: str, b: str) -> float:
    return math.sqrt(sum((p - q) ** 2 for p, q in zip(_lab(a), _lab(b))))


def _faces():
    return [(b, f) for b in LAYERS for f in b.faces]


def test_every_theme_actually_defines_every_hue_a_face_uses():
    """`accent_for` falls back to the theme's primary accent for a key it does not have — so a
    missing hue does not raise, it silently collapses several faces onto one colour."""
    used = {f.hue for _b, f in _faces()}
    for name, theme in THEMES:
        for hue in sorted(used):
            assert hue in theme.accents, f"{name} has no '{hue}'; those faces would share a colour"


def test_no_two_faces_in_a_band_share_a_hue():
    """A band is what a student navigates by, so this is the one that has to hold absolutely."""
    for band in LAYERS:
        hues = [f.hue for f in band.faces]
        assert len(hues) == len(set(hues)), f"{band.name} repeats a hue: {hues}"


def test_no_hue_appears_in_adjacent_bands():
    """Repeats are unavoidable — eight usable hues, twelve faces — but never as neighbours."""
    for upper, lower in zip(LAYERS, LAYERS[1:]):
        shared = {f.hue for f in upper.faces} & {f.hue for f in lower.faces}
        assert not shared, f"{upper.name} and {lower.name} both use {shared}"


@pytest.mark.parametrize("theme_name", [n for n, _t in THEMES])
def test_faces_in_a_band_are_distinguishable_in_this_theme(theme_name):
    theme = dict(THEMES)[theme_name]
    for band in LAYERS:
        for a, b in itertools.combinations(band.faces, 2):
            d = delta_e(theme.accent_for(a.hue), theme.accent_for(b.hue))
            assert d >= MIN_DELTA_E, (
                f"{theme_name}: {a.title} ({a.hue}) and {b.title} ({b.hue}) are {d:.1f} apart")


def test_the_palette_pairs_we_know_are_too_close_are_not_both_in_use():
    """teal/cyan collapse in Brand, blue/indigo in Dark. Documented here so that anyone adding a
    face meets the reason rather than rediscovering it."""
    used = {f.hue for _b, f in _faces()}
    for pair in (("teal", "cyan"), ("blue", "indigo"), ("purple", "indigo")):
        assert not set(pair) <= used, f"{pair} are indistinguishable in at least one theme"


def test_every_face_opens_something_that_exists():
    from gini.ui.machine_lab import MachineLab
    for _band, f in _faces():
        assert hasattr(MachineLab, f.opens), f"{f.title} opens {f.opens}, which does not exist"


def test_the_assignment_face_is_first_and_alone():
    """A student's own code is not a layer of the machine; it is an overlay on all of them."""
    first = LAYERS[0]
    assert first.name == "THIS ASSIGNMENT"
    assert [f.key for f in first.faces] == ["usercode"]
    assert not first.boundary, "nothing above it to be separated from"


def test_the_layers_read_from_user_space_down_to_hardware():
    assert [b.name for b in LAYERS] == [
        "THIS ASSIGNMENT", "USER SPACE", "SYSTEM-CALL INTERFACE", "KERNEL", "HARDWARE"]
    assert all(b.boundary for b in LAYERS[2:]), "each kernel-ward step is marked by a rule"
