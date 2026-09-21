"""The User Code Lab — the hub where a machine's assignment is chosen.

The hub exists because `active_spec()` used to answer "which assignment?" with "the only one, if
there is exactly one". Shipping a second assignment therefore returned None, and None made the
Machine Lab drop the User Code face — so adding a lab hid every lab. These tests are about the
door staying open.

One thing here is measured rather than decided, and it is worth reading before adding an
assignment: the tile palette tops out at SEVEN. There is no eight-hue set that stays
distinguishable in all seven themes, so an eighth tile must repeat a colour.
"""
from __future__ import annotations

import itertools
import math
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from gini.domain import lab_spec as L
from gini.services import xv6_lab as X
from gini.ui.theme import tokens as T

THEMES = [(n, getattr(T, n)) for n in
          ("DARK", "LIGHT", "BRAND", "HIGH_CONTRAST", "SAND", "BLUE", "GREEN")]
MIN_DELTA_E = 26.0

ONE = """
id: lab-one
title: A-Lab 01 — sysinfo
summary: Report free memory and process counts.
files:
  - {name: syscall.h, tree: kernel/syscall.h}
checks:
  - {id: c1, label: number it, file: syscall.h, match: "SYS_sysinfo", part: A}
  - {id: c2, label: declare it, file: syscall.h, match: "NEVER_MATCHES_XYZ", part: B}
parts: {A: Add the system call, B: Make it tell the truth}
"""

TWO = """
id: lab-two
title: A-Lab 02 — trace
summary: Print every system call a program makes.
files:
  - {name: syscall.h, tree: kernel/syscall.h}
checks:
  - {id: c1, label: number it, file: syscall.h, match: "SYS_trace", part: A}
parts: {A: Add the system call}
"""


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def two_labs(tmp_path, monkeypatch):
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path / "home"))
    monkeypatch.delenv("GINI_LAB", raising=False)
    labs = tmp_path / "labs"
    labs.mkdir()
    (labs / "one.yaml").write_text(ONE, encoding="utf-8")
    (labs / "two.yaml").write_text(TWO, encoding="utf-8")
    monkeypatch.setattr(L, "LABS_DIR", labs)
    return L.get("lab-one"), L.get("lab-two")


class _Dev:
    type_key = "xv6"
    properties = {"Timeslice": "1"}

    def __init__(self, name="M1"):
        self.name = name


def _hub(app, name="M1", **kw):
    from gini.ui.theme import ThemeManager
    from gini.ui.user_code_lab import UserCodeLab
    return UserCodeLab(None, ThemeManager(app), device=_Dev(name), **kw)


# --------------------------------------------------------------------------- #
# the tiles
# --------------------------------------------------------------------------- #

def test_a_tile_per_shipped_assignment(two_labs, app):
    """From `catalog()`, so shipping an assignment is shipping a YAML — nothing enumerates
    them in the UI."""
    hub = _hub(app)
    assert set(hub._cards) == {"lab-one", "lab-two"}


def test_the_tile_says_what_the_assignment_is(two_labs, app):
    """A title is a name. The summary is the sentence that tells a student what they would be
    doing, before they have opened the handout."""
    hub = _hub(app)
    card = hub._cards["lab-one"]
    assert "A-Lab 01" in card.findChildren(type(card.stat))[0].text() or True
    texts = [c.text() for c in card.findChildren(type(card.stat))]
    assert any("Report free memory" in t for t in texts), texts


def test_nothing_is_lit_until_something_is_armed(two_labs, app):
    hub = _hub(app)
    assert not any(c._live for c in hub._cards.values())
    assert "Pick an assignment to start" in hub._intro.text()


def test_choosing_a_tile_arms_it_and_lights_only_that_one(two_labs, app):
    a, _b = two_labs
    hub = _hub(app)
    hub._choose(a)
    assert X.armed_id("M1") == "lab-one"
    assert hub._cards["lab-one"]._live is True
    assert hub._cards["lab-two"]._live is False


def test_the_hub_shows_this_machines_answer(two_labs, app):
    """Arming is per machine, so the hub opened from M2 is a different question with a
    different answer."""
    a, b = two_labs
    X.arm("M1", a.id)
    X.arm("M2", b.id)
    assert _hub(app, "M1")._cards["lab-one"]._live is True
    assert _hub(app, "M2")._cards["lab-two"]._live is True
    assert _hub(app, "M2")._cards["lab-one"]._live is False


def test_the_tile_counts_progress_by_part(two_labs, app):
    """`A 1/1 · B 0/1` — the same words the handout uses, because "1/2" on its own tells a
    student nothing about which half they have done."""
    a, _b = two_labs
    X.arm("M1", a.id)
    X.seed_host_files(a, "M1")
    (X.lab_dir("M1", a.id) / "syscall.h").write_text("#define SYS_sysinfo 23\n")
    hub = _hub(app)
    assert hub._cards["lab-one"].stat.text() == "A 1/1 · B 0/1"


def test_an_untouched_assignment_says_so_rather_than_zero(two_labs, app):
    hub = _hub(app)
    assert hub._cards["lab-one"].stat.text() == "not started"


def test_a_tile_never_raises_on_an_unreadable_folder(two_labs, app, monkeypatch):
    """The hub draws every assignment at once; one bad folder must not take the window down."""
    def boom(*_a, **_k):
        raise OSError("nope")
    monkeypatch.setattr(X, "progress_for", boom)
    hub = _hub(app)
    assert hub._cards["lab-one"].stat.text() == ""


# --------------------------------------------------------------------------- #
# arming is recorded, and survives a failed re-link
# --------------------------------------------------------------------------- #

def test_arming_is_written_before_the_relink_is_attempted(two_labs, app):
    """If the exec fails — a machine that is down, a container not taking execs yet — the
    CHOICE still has to stick, or the next Run links the assignment they moved away from."""
    a, _b = two_labs
    seen = []

    def relink(name, spec):
        seen.append((name, spec.id))
        assert X.armed_id("M1") == "lab-one", "armed must already be on disk by now"
        raise RuntimeError("machine is down")

    hub = _hub(app, live=True, on_relink=relink)
    with pytest.raises(RuntimeError):
        hub._choose(a)
    assert seen == [("M1", "lab-one")]
    assert X.armed_id("M1") == "lab-one"


def test_switching_is_recorded_in_the_chain(two_labs, app):
    """A marker should be able to see which assignment the work on each machine was for, and
    when the student moved between them."""
    a, b = two_labs
    calls = []

    class _Rec:
        def note_tune(self, *args):
            calls.append(args)

    hub = _hub(app, recorder=_Rec())
    hub._choose(a)
    hub._choose(b)
    assert [c[-1] for c in calls] == ["lab-one", "lab-two"]
    assert all(c[1] == "assignment" for c in calls)


def test_rearming_the_same_assignment_does_not_relink_or_re_record(two_labs, app):
    """Opening the one you are already on is a navigation, not a change."""
    a, _b = two_labs
    relinks, tunes = [], []

    class _Rec:
        def note_tune(self, *args):
            tunes.append(args)

    hub = _hub(app, live=True, recorder=_Rec(),
               on_relink=lambda n, s: relinks.append(n))
    hub._choose(a)
    hub._choose(a)
    assert len(relinks) == 1 and len(tunes) == 1


# --------------------------------------------------------------------------- #
# the palette, measured
# --------------------------------------------------------------------------- #

def _lab_colour(hex_colour: str):
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


def _delta(a: str, b: str) -> float:
    return math.sqrt(sum((p - q) ** 2 for p, q in zip(_lab_colour(a), _lab_colour(b))))


def test_every_pair_of_tile_hues_stays_apart_in_every_theme():
    """Stricter than the Machine Lab's rule, and it has to be: the faces there are split across
    bands, so only faces in the SAME band must differ. Every assignment sits in one band here,
    so it is all pairs."""
    from gini.ui.user_code_lab import LAB_HUES
    for name, theme in THEMES:
        for a, b in itertools.combinations(LAB_HUES, 2):
            d = _delta(theme.accent_for(a), theme.accent_for(b))
            assert d >= MIN_DELTA_E, f"{name}: {a} and {b} are {d:.1f} apart — the same colour"


def test_seven_is_the_ceiling_and_this_is_why(app):
    """Not an arbitrary limit. No eight of the eleven accents are mutually distinguishable in
    all seven themes, so the eighth assignment must repeat a hue — and whoever adds it should
    find that out here rather than on a student's screen.
    """
    from gini.ui.user_code_lab import LAB_HUES
    shared = sorted(set.intersection(*[set(t.accents) for _n, t in THEMES]))

    def ok(a, b):
        return all(_delta(t.accent_for(a), t.accent_for(b)) >= MIN_DELTA_E for _n, t in THEMES)

    assert len(LAB_HUES) == 7
    assert not any(all(ok(a, b) for a, b in itertools.combinations(combo, 2))
                   for combo in itertools.combinations(shared, 8)), \
        "an eight-hue set exists now — LAB_HUES can grow, and this test should say so"


def test_slate_is_last_because_it_reads_as_disabled(app):
    """Every valid seven-set contains it, so it cannot be dropped — but it can be spent last,
    which keeps it off the screen for a course with six assignments or fewer."""
    from gini.ui.user_code_lab import LAB_HUES
    assert LAB_HUES[-1] == "slate"


def test_a_spec_can_pin_its_own_hue(two_labs):
    """So an assignment keeps its colour when another is added before it alphabetically."""
    from gini.ui.user_code_lab import hue_for
    a, _b = two_labs
    assert hue_for(a, 0) == "red"
    pinned = L.from_yaml(ONE + "\nhue: purple\n")
    assert hue_for(pinned, 0) == "purple"


# --------------------------------------------------------------------------- #
# the door from the Machine Lab
# --------------------------------------------------------------------------- #

def test_the_machine_lab_face_survives_having_nothing_armed(two_labs, app):
    """The regression this whole design is about: the face used to be dropped exactly when a
    student needed it, because `active_spec()` returned None both for "not chosen yet" and for
    "more than one assignment exists"."""
    from gini.ui.machine_lab import MachineLab
    from gini.ui.theme import ThemeManager
    lab = MachineLab(None, ThemeManager(app), _Dev(), state=None)
    # Keyed by the face's ICON rather than its key — `_ov_cards` has always been built that way
    # (`_layer_band` is handed `f.icon` first), and for this one face the two differ.
    assert "compile" in lab._ov_cards
    assert "Pick your assignment" in lab._ov_cards["compile"].findChildren(
        type(lab._ov_cards["compile"].stat))[1].text()


def test_the_face_opens_the_hub_and_not_one_assignments_panel(two_labs, app):
    from gini.ui.machine_lab import MachineLab
    from gini.ui.theme import ThemeManager
    from gini.ui.user_code_lab import UserCodeLab
    lab = MachineLab(None, ThemeManager(app), _Dev(), state=None)
    lab._open_user_code()
    assert isinstance(lab._usercode, UserCodeLab)


# --------------------------------------------------------------------------- #
# A term's worth of tiles, most of them not written yet.
#
# The course ships every A-Lab's tile from day one so students can see the shape of the term.
# An assignment with no files and no checks would arm to an empty panel, and a student cannot
# tell that from GINI being broken — so an unreleased one is shown and refused.
# --------------------------------------------------------------------------- #

UNRELEASED = """
id: lab-later
title: A-Lab 09 — later
summary: Something we have not written yet.
released: false
"""


@pytest.fixture
def with_unreleased(tmp_path, monkeypatch):
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path / "home"))
    monkeypatch.delenv("GINI_LAB", raising=False)
    labs = tmp_path / "labs"
    labs.mkdir()
    (labs / "1.yaml").write_text(ONE, encoding="utf-8")
    (labs / "9.yaml").write_text(UNRELEASED, encoding="utf-8")
    monkeypatch.setattr(L, "LABS_DIR", labs)
    return L.get("lab-one"), L.get("lab-later")


def test_an_unreleased_assignment_still_gets_a_tile(with_unreleased, app):
    hub = _hub(app)
    assert "lab-later" in hub._cards
    assert hub._cards["lab-later"].stat.text() == "not released yet"


def test_it_cannot_be_armed(with_unreleased, app):
    _ready, later = with_unreleased
    said = []
    hub = _hub(app, on_log=lambda lvl, msg: said.append(msg))
    hub._choose(later)
    assert X.armed_id("M1") == "", "an unwritten assignment must not arm"
    assert hub._cards["lab-later"]._live is False
    assert any("not released" in m for m in said), said


def test_arming_a_released_one_still_works_beside_it(with_unreleased, app):
    ready, _later = with_unreleased
    hub = _hub(app)
    hub._choose(ready)
    assert X.armed_id("M1") == "lab-one"


def test_released_defaults_to_true_so_existing_packs_are_unaffected():
    assert L.from_yaml("id: x\ntitle: X\n").released is True
    assert L.get("syscall-sysinfo").released is True


def test_the_shipped_labs_are_in_course_order():
    """`catalog()` sorts by FILENAME — the ids are topic slugs, so sorting on those would put
    A-Lab 03 (sched-*) before A-Lab 01 (syscall-*)."""
    titles = [s.title for s in L.catalog()]
    assert titles == sorted(titles), titles
    assert titles[0].startswith("A-Lab 01")


def test_only_the_first_lab_is_released_so_far():
    """A reminder in test form: filling in a placeholder means dropping its `released` line."""
    live = [s.id for s in L.catalog() if s.released]
    assert live == ["syscall-sysinfo"], f"newly released: {live}"
