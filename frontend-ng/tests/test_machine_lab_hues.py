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
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

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


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class _TM:
    """The theme manager as a LayerCard uses it: it is only ever asked for `.theme`."""

    def __init__(self, theme):
        self.theme = theme


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


# --------------------------------------------------------------------------- #
# A hue nobody can see is not a hue.
#
# Reported from a real screen: "no colors in the machine lab". Every face WAS carrying its
# assigned accent — as an eleven-pixel dot, plus a stat line that is empty on half the cards.
# Twelve carefully-chosen hues read as one grey wall. The assignment was right and the card was
# not wearing it.
# --------------------------------------------------------------------------- #

def _resting(card):
    """The rule that paints the card when nobody is pointing at it.

    Split off deliberately: `:hover` also names the accent, so asserting against the whole
    stylesheet passes whether or not the resting edge is lit — which is exactly the bug that
    hid the live-edge change from these tests the first time it was made."""
    return card.styleSheet().split("LayerCard:hover")[0]


def test_a_card_carries_its_accent_where_it_can_be_seen(app):
    """The edge and the wash, not a dot. Checked per theme, because both are derived from the
    theme's own accent rather than listed anywhere."""
    from gini.ui.machine_lab import LayerCard, over, shift

    for name, theme in THEMES:
        fills = set()
        for _band, f in _faces():
            card = LayerCard(_TM(theme), f.title, f.blurb, f.hue)
            card.set_live(True)                       # the edge is lit by a running kernel
            css = _resting(card)
            acc = theme.accent_for(f.hue)
            assert f"border-left:4px solid {acc}" in css, (
                f"{name}/{f.title}: no accent edge")
            fill = shift(over(theme.panel2, acc, 34 if theme.dark else 22),
                         -22 if theme.dark else -14)
            assert f"background:{fill};" in css, f"{name}/{f.title}: no accent wash"
            fills.add(fill)
        assert len(fills) > 1, f"{name}: every card came out the same colour"


def test_the_wash_is_derived_from_the_accent_and_not_a_second_table():
    """Two places to change a colour is one too many — the wash must follow the accent."""
    from gini.ui.machine_lab import over, shift
    assert over("#000000", "#4c8dff", 255) == "#4c8dff", "full alpha is the accent itself"
    assert over("#000000", "#ffffff", 128) == "#808080"
    assert over("#202020", "4c8dff", 0) == "#202020", "no alpha leaves the base alone"
    assert over("", "#4c8dff", 30) == "transparent", "a missing colour must not break the CSS"
    assert over("#nothex", "#4c8dff", 30) == "transparent"
    assert over("#202020", "#nothex", 30) == "#202020", "a bad accent falls back to the floor"
    assert over("#000000", "#ffffff", 999) == "#ffffff", "alpha is clamped to Qt's range"


def test_a_shade_is_additive_so_it_still_moves_at_the_ends_of_the_range():
    """A percentage of #0d0d0d is #0d0d0d. High Contrast is almost entirely at that end of the
    range, so the recess has to be built out of steps rather than ratios."""
    from gini.ui.machine_lab import shift
    assert shift("#0d0d0d", 20) == "#212121", "the darkest theme still gets a lighter edge"
    assert shift("#808080", -16) == "#707070"
    assert shift("#000000", -20) == "#000000", "clamped, not wrapped"
    assert shift("#ffffff", 20) == "#ffffff"
    assert shift("", 20) == "transparent"
    assert shift("#nothex", 20) == "transparent", "Qt drops a whole rule over one bad property"


def test_a_dark_theme_gets_a_stronger_wash_than_a_light_one(app):
    """The same alpha over a dark ground reads fainter; the card compensates."""
    from gini.ui.machine_lab import over, shift

    def wash_strength(theme):
        """How far the accent pulled the card away from a plain, unhued recess."""
        acc = theme.accent_for("red")
        fill = over(theme.panel2, acc, 34 if theme.dark else 22)
        return delta_e(fill, shift(theme.panel2, 0))

    assert wash_strength(T.DARK) > wash_strength(T.LIGHT)


# --------------------------------------------------------------------------- #
# Separated from the band, and measured on the pixels rather than in the stylesheet.
#
# A card has to be distinguishable from the band behind it, and only one of those two colours
# is written down — the other is a blend. Reading it off the stylesheet would just re-run the
# arithmetic the code already did, so this paints the card into its band and looks.
#
# It is deliberately the only thing asserted here. A literal sunken card — gradient shadow
# under the top lip — was built, measured correct in all seven themes, and thrown away for
# looking like a smudge. Depth is not the invariant; separation is.
# --------------------------------------------------------------------------- #

def _band_and_fill(theme, app):
    """Paint a card inside a band of `theme`; return the two luminances as they land on screen."""
    from PySide6.QtWidgets import QVBoxLayout, QWidget

    from gini.ui.machine_lab import LayerCard
    host = QWidget(); host.resize(320, 120)
    host.setStyleSheet(f"QWidget{{background:{theme.panel2};}}")
    lay = QVBoxLayout(host); lay.setContentsMargins(12, 12, 12, 12)
    card = LayerCard(_TM(theme), "Process Scheduler", "who runs next", "red")
    lay.addWidget(card)
    host.show(); app.processEvents()
    img = host.grab().toImage()
    x = host.width() // 2                      # mid-edge, clear of the rounded corners

    def lum(X, Y):
        c = img.pixelColor(X, Y)
        return .2126 * c.red() + .7152 * c.green() + .0722 * c.blue()

    return lum(3, 3), lum(x, card.y() + card.height() // 2)


@pytest.mark.parametrize("name,theme", THEMES)
def test_a_card_sits_below_the_band_it_is_in(name, theme, app):
    """Darker than its surround — in every theme, whichever direction that theme's own accent
    happens to pull. This is why the fill is resolved to a solid colour: a translucent wash
    composites over the band, so it could only ever go lighter in a dark theme."""
    band, fill = _band_and_fill(theme, app)
    assert fill < band, f"{name}: the card is level with or above its band ({fill} vs {band})"


@pytest.mark.parametrize("name,theme", THEMES)
def test_the_accent_edge_travels_the_whole_way_round_on_hover(name, theme, app):
    """The left edge is the card's hue, and on hover it has to come with the rest of it. An
    earlier bevel left the top and bottom grey, so pointing at a card lit up its right-hand
    side and nothing else — which read as a rendering fault rather than a hover."""
    from gini.ui.machine_lab import LayerCard

    for _band, f in _faces():
        acc = theme.accent_for(f.hue)
        hover = LayerCard(_TM(theme), f.title, f.blurb, f.hue).styleSheet().split(":hover")[1]
        assert f"border:1px solid {acc};" in hover, f"{name}/{f.title}: the edge stayed behind"
        assert f"border-left:4px solid {acc};" in hover


def test_a_label_is_text_and_not_a_filled_box(app):
    """QLabel is a QFrame subclass, so a card styled with a bare `QFrame{background:...}` type
    selector paints that background behind every label inside it. Invisible on a pale panel;
    the moment the cards were tinted, every title and every line of body text appeared in its
    own darker rectangle and an empty label showed as a grey bar.

    Fixed once, in the application stylesheet, rather than at each of the call sites — a
    stylesheet on the widget itself still wins, so a label that wants a fill can ask for one.
    """
    from gini.ui.theme import ThemeManager
    tm = ThemeManager(app)
    tm.apply()
    assert "QLabel { background: transparent; }" in app.styleSheet()


def test_the_band_styles_itself_and_not_everything_inside_it(app):
    """The other half: `QFrame{...}` set on the band reaches the cards, the separators and every
    label. Naming the frame stops the cascade at its source."""
    import inspect

    from gini.ui.machine_lab import MachineLab
    src = inspect.getsource(MachineLab._layer_band)
    assert 'setObjectName("LayerBand")' in src
    assert "QFrame#LayerBand{" in src, "an unscoped QFrame rule cascades to its children"
    assert 'f"QFrame{{background' not in src


# --------------------------------------------------------------------------- #
# The accent edge is a live indicator.
#
# The Demo stand-in draws the same numbers in the same places a running kernel does — that is
# the point of it — so a student looking at a face has no way to tell which one they are
# reading. The edges are that tell, and a tell is only worth having if it is never wrong in the
# reassuring direction: dark when there may be no kernel costs nothing, lit when there is none
# is the whole failure.
# --------------------------------------------------------------------------- #

def test_an_unlit_card_shows_no_accent_edge_at_rest(app):
    """Painted in the card's own fill rather than removed: the 4px stays in the box model, so
    no title shifts sideways as a machine starts answering or stops."""
    from gini.ui.machine_lab import LayerCard

    for name, theme in THEMES:
        for _band, f in _faces():
            card = LayerCard(_TM(theme), f.title, f.blurb, f.hue)
            acc = theme.accent_for(f.hue)
            css = _resting(card)
            assert f"border-left:4px solid {acc}" not in css, (
                f"{name}/{f.title}: edge lit with nothing behind it")
            assert "border-left:4px solid" in css, f"{name}/{f.title}: the 4px must stay"


def test_a_card_starts_dark_because_nothing_has_proved_a_kernel_yet(app):
    from gini.ui.machine_lab import LayerCard
    card = LayerCard(_TM(T.DARK), "t", "d", "red")
    dark = _resting(card)
    card.set_live(True)
    assert _resting(card) != dark
    card.set_live(False)
    assert _resting(card) == dark, "going dark again must restore the resting look exactly"


def test_hover_shows_the_hue_even_on_a_dead_machine(app):
    """The hue is the card's identity — which face this is — and that is true whether or not a
    kernel is answering. Only the RESTING edge carries the liveness claim."""
    from gini.ui.machine_lab import LayerCard
    card = LayerCard(_TM(T.DARK), "t", "d", "red")           # never set live
    acc = T.DARK.accent_for("red")
    hover = card.styleSheet().split("LayerCard:hover")[1]
    assert f"border:1px solid {acc};" in hover


class _Dev:
    type_key = "xv6"
    name = "xv6-1"
    properties = {"Timeslice": "1"}


def _open_lab(app):
    from gini.ui.theme import ThemeManager
    from gini.ui.machine_lab import MachineLab
    return MachineLab(None, ThemeManager(app), _Dev(), state=None)


def test_demo_mode_never_lights_the_edges(app):
    """Demo is a full plausible feed with no kernel behind it — the one case the indicator
    exists for."""
    lab = _open_lab(app)
    assert lab.state.mode == "demo"
    assert lab.showing_live_kernel() is False
    lab._update_overview()
    assert all(not c._live for c in lab._ov_cards.values())


def test_real_mode_alone_does_not_light_the_edges(app):
    """Real is selected by a click, usually BEFORE the topology is up. Mode says what the
    student asked for; only a snapshot says what they got.

    Goes through `set_mode`, which is the only supported way in: it clears the last snapshot,
    so the demo reading the Lab opened with cannot survive the switch and pass itself off as a
    kernel's."""
    lab = _open_lab(app)
    assert lab.state.latest is not None, "the demo plane has already produced a reading"
    lab.state.set_mode("real")
    assert lab.state.latest is None
    assert lab.showing_live_kernel() is False


def test_a_machine_that_stops_answering_puts_the_edges_out(app):
    """`latest` keeps a dead kernel's final snapshot, so without the failure count the page
    would go on presenting its last words as current."""
    from gini.ui.machine_lab import READS_BEFORE_GONE
    lab = _open_lab(app)
    snap = lab.state.provider.snapshot()
    lab.state.set_mode("real")
    lab.state.latest = snap                               # stand in for a reading arriving
    lab._read_fails = 0
    assert lab.showing_live_kernel() is True

    lab._read_fails = READS_BEFORE_GONE - 1
    assert lab.showing_live_kernel() is True, "one dropped read under load is not a dead machine"
    lab._read_fails = READS_BEFORE_GONE
    assert lab.showing_live_kernel() is False

    lab._update_overview()
    assert all(not c._live for c in lab._ov_cards.values())


def test_the_edges_go_dark_at_the_same_moment_the_banner_appears(app):
    """Two renderings of one fact. They disagreed once — the banner said no live data while
    twelve edges were still lit — and a student believes the colour, not the paragraph."""
    lab = _open_lab(app)
    snap = lab.state.provider.snapshot()
    lab.state.set_mode("real")
    for latest in (None, snap):
        lab.state.latest = latest
        lab._update_banner()
        # `isHidden()` rather than `isVisible()`: nothing is shown in an offscreen test, so
        # isVisible() is False for every widget and the assertion would hold vacuously.
        showing = not lab._banner.isHidden()
        assert showing is not lab.showing_live_kernel()
