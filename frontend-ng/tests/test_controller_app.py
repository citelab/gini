"""The OpenFlow controller's App field: a POX command line, not a value from a fixed set.

What it was: a CLOSED dropdown of twelve bare module names. `gini.samples.ids` and nothing else —
so the five GINI apps that take parameters could not be given any, and nothing on screen suggested
they had any. The plumbing had supported it all along: `run-pox.sh` leaves `$POX_APP` unquoted
precisely so a module can carry its own `--flags`, and one shipped preset
(`log.level --DEBUG …`) already used one.

Two changes, and the second is what makes the first usable:

  * the dropdown is EDITABLE for this property, so a preset is a starting point;
  * each GINI app is listed WITH its parameters at their own defaults, so the list is the
    documentation. `gini.samples.ids --threshold=10 --block=false` shows a student what there is
    to change; the bare name told them nothing.

Deliberately no validation on this side. POX refuses an unknown flag by name and prints the
module's real parameters, defaults and current values — a better message than anything restated
here, and one that cannot drift from the app it describes.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gini.domain.devices import get as device_type          # noqa: E402

SAMPLES = (Path(__file__).resolve().parents[2]
           / "backend" / "sdn" / "pox" / "ext" / "gini" / "samples")


def _apps():
    return device_type("controller").property_choices["App"]


def _launch_defaults(module: str) -> dict[str, str]:
    """`launch()`'s parameter names and defaults, read with `ast`.

    Not by splitting the signature on commas: `sequence="1111,2222,3333"` has three of them
    INSIDE a default, and a naive split reports the default as "1111" — which is exactly the sort
    of near-miss this file exists to catch, so it must not be the thing doing the catching.
    """
    import ast
    tree = ast.parse((SAMPLES / (module.rsplit(".", 1)[-1] + ".py")).read_text(encoding="utf-8"))
    fn = next((n for n in tree.body
               if isinstance(n, ast.FunctionDef) and n.name == "launch"), None)
    assert fn is not None, f"{module} has no launch()"
    names = [a.arg for a in fn.args.args]
    vals = [ast.literal_eval(d) for d in fn.args.defaults]
    return dict(zip(names[len(names) - len(vals):], (str(v) for v in vals)))


# ---- the field is open ------------------------------------------------------------ #
def test_the_app_property_is_marked_open():
    assert "App" in device_type("controller").open_properties


def test_closed_choices_stay_closed():
    """Most dropdowns really are a fixed set. A typeable box on Persist (true|false) would accept
    "maybe" without a word, so editability is per property and off by default."""
    from gini.domain.devices import all_devices
    for dt in all_devices():
        if dt.key == "controller":
            continue
        assert not dt.open_properties, (
            f"{dt.key} marks {dt.open_properties} open — every other choice list here is a "
            f"genuine fixed set")


def _inspector(qtbot):
    from gini.agent.api import GiniAPI
    from gini.app import AppContext
    from gini.ui.inspector import Inspector
    from gini.ui.theme import ThemeManager
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    ctx = AppContext()
    dev = ctx.add_device("controller", x=0, y=0)
    insp = Inspector(ctx, GiniAPI(ctx), ThemeManager(app, "Light"))
    qtbot.addWidget(insp)
    ctx.bus.selection_changed.emit(dev.id)          # the inspector rebuilds on selection
    return app, ctx, dev, insp


def _app_field(insp):
    from PySide6.QtWidgets import QLineEdit
    return next(w for w in insp.findChildren(QLineEdit)
                if "gini." in w.text() or "openflow." in w.text())


def _picker(insp):
    from PySide6.QtWidgets import QToolButton
    return next(b for b in insp.findChildren(QToolButton)
                if b.menu() and any("gini.samples" in a.text() for a in b.menu().actions()))


def test_the_app_is_a_text_field_you_can_type_a_parameter_into(qtbot):
    """The whole point. It was a closed QComboBox, so a parameter could not be entered at all."""
    _app, ctx, dev, insp = _inspector(qtbot)
    field = _app_field(insp)
    field.setText("gini.samples.ids --threshold=25 --block=true")
    field.editingFinished.emit()
    assert dev.properties["App"] == "gini.samples.ids --threshold=25 --block=true"


def test_typing_does_not_rebuild_the_form_under_the_cursor(qtbot):
    """THE crash, and it took one keystroke.

    An editable QComboBox was the obvious way to do this. Its `currentTextChanged` fires per
    CHARACTER; each commit emits device_changed; the inspector rebuilds on device_changed — and
    deletes the widget being typed into. It died with "Internal C++ object (QComboBox) already
    deleted" on the first letter, and with a lab running it also restarted the POX container once
    per character.

    So the field commits on `editingFinished`, exactly like every other text property here."""
    app, _ctx, _dev, insp = _inspector(qtbot)
    rebuilds = []
    original = insp._rebuild
    insp._rebuild = lambda: (rebuilds.append(1), original())[1]
    field = _app_field(insp)
    for ch in "--threshold=25":
        field.setText(field.text() + ch)
        app.processEvents()
    assert rebuilds == [], "the form rebuilt while someone was typing in it"
    assert _app_field(insp) is field, "the field was replaced mid-edit"


def test_nothing_is_committed_until_the_field_is_left(qtbot):
    """Per-keystroke commits are what restarted the controller once per character."""
    app, _ctx, dev, insp = _inspector(qtbot)
    was = dev.properties["App"]
    field = _app_field(insp)
    field.setText("gini.samples.ids --threshold=9")
    app.processEvents()
    assert dev.properties["App"] == was, "committed mid-typing"
    field.editingFinished.emit()
    assert dev.properties["App"] == "gini.samples.ids --threshold=9"


def test_the_presets_are_reachable_from_a_visible_control(qtbot):
    """An editable combo's arrow is nearly invisible on macOS, so the presets became hard to reach
    at exactly the moment they became worth reading. A button with a menu is unmistakable."""
    _app, _ctx, _dev, insp = _inspector(qtbot)
    actions = _picker(insp).menu().actions()
    assert len(actions) == len(_apps())
    assert any("--threshold=" in a.text() for a in actions)


def test_picking_a_preset_fills_the_field(qtbot):
    app, _ctx, dev, insp = _inspector(qtbot)
    wanted = next(a for a in _picker(insp).menu().actions() if "ids" in a.text())
    text = wanted.text()          # read BEFORE triggering: the commit rebuilds the form, which
    wanted.trigger()              # destroys the menu the action belongs to
    app.processEvents()
    assert dev.properties["App"] == text
    assert "--threshold=" in dev.properties["App"]


def test_a_closed_choice_is_still_a_plain_dropdown(qtbot):
    """The picker treatment is for open properties only. Everything else keeps the dropdown that
    is right for a fixed set — there is nothing to type into Persist (true|false)."""
    from PySide6.QtWidgets import QComboBox, QLineEdit, QApplication

    from gini.agent.api import GiniAPI
    from gini.app import AppContext
    from gini.ui.inspector import Inspector
    from gini.ui.theme import ThemeManager
    app = QApplication.instance() or QApplication([])
    ctx = AppContext()
    dev = ctx.add_device("router", x=0, y=0)
    insp = Inspector(ctx, GiniAPI(ctx), ThemeManager(app, "Light"))
    qtbot.addWidget(insp)
    ctx.bus.selection_changed.emit(dev.id)
    assert not [c for c in insp.findChildren(QComboBox) if c.isEditable()]


# ---- the presets carry the real parameters ---------------------------------------- #
@pytest.mark.skipif(not SAMPLES.exists(), reason="backend/sdn not checked out")
def test_every_listed_parameter_is_one_the_app_actually_takes():
    """THE test that stops the two drifting. The dropdown restates each app's defaults, so a
    parameter renamed in the app must not leave a preset quietly passing a flag POX will reject."""
    for entry in _apps():
        mod, *rest = entry.split()
        if not mod.startswith("gini.samples."):
            continue
        takes = set(_launch_defaults(mod))
        for flag in rest:
            name = flag.lstrip("-").split("=")[0]
            assert name in takes, f"{entry!r} passes --{name}, which {mod}.launch() will refuse"


@pytest.mark.skipif(not SAMPLES.exists(), reason="backend/sdn not checked out")
def test_the_listed_values_are_the_apps_own_defaults():
    """Written out, they must be what omitting them would have given — otherwise picking a preset
    silently changes behaviour compared with the bare module name it replaced."""
    for entry in _apps():
        mod, *rest = entry.split()
        if not mod.startswith("gini.samples."):
            continue
        defaults = _launch_defaults(mod)
        for flag in rest:
            name, _, value = flag.lstrip("-").partition("=")
            assert defaults.get(name) == value, (
                f"{entry!r} says --{name}={value}, but {mod}.launch() defaults to "
                f"{defaults.get(name)!r}")


@pytest.mark.skipif(not SAMPLES.exists(), reason="backend/sdn not checked out")
def test_an_app_with_parameters_shows_them():
    """A bare module name in the list would be a parameterised app whose knobs are invisible
    again — which is the whole complaint."""
    for path in sorted(SAMPLES.glob("*.py")):
        if path.name == "__init__.py":
            continue
        sig = re.search(r"def launch\(([^)]*)\)", path.read_text(encoding="utf-8"))
        if not sig or not sig.group(1).strip():
            continue                                   # takes nothing; a bare name is right
        mod = f"gini.samples.{path.stem}"
        listed = [e for e in _apps() if e.split()[0] == mod]
        if not listed:
            continue                                   # not offered at all — see docs/design
        assert "--" in listed[0], f"{mod} takes parameters but is listed bare"


def test_no_preset_carries_a_character_that_breaks_the_compose_file():
    from gini.services.orchestrator import _yamlish
    for entry in _apps():
        assert _yamlish(entry) == entry, f"{entry!r} would need escaping; check it is intended"


# ---- what a typed value survives -------------------------------------------------- #
def test_a_quote_cannot_end_the_yaml_scalar():
    """The value is written inside single quotes in the generated compose file. Doubling is YAML's
    own escape for a quote inside them."""
    from gini.services.orchestrator import _yamlish
    assert _yamlish("a'b") == "a''b"


def test_a_dollar_is_not_eaten_by_compose_interpolation():
    """Compose substitutes $VAR before the YAML is parsed, so `--pass=$x` would silently become
    `--pass=` with a warning nobody reads. `$$` is the documented literal."""
    from gini.services.orchestrator import _yamlish
    assert _yamlish("--pass=$x") == "--pass=$$x"


def test_a_typed_app_reaches_the_container_intact():
    """End to end through the real compiler and compose writer: what is typed is what POX gets."""
    from gini.app import AppContext
    from gini.services.compiler import RuntimeCompiler
    from gini.services.orchestrator import _compose
    typed = "gini.samples.ids --threshold=25 --block=true"
    ctx = AppContext()
    dev = ctx.add_device("controller", x=0, y=0)
    dev.properties["App"] = typed
    text = _compose(RuntimeCompiler().compile(ctx.topology))
    assert f"POX_APP: '{typed}'" in text, "the typed app did not reach POX_APP"
