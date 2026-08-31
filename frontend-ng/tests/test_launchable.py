"""The launcher menu, the agent's allow-list, and the programs that actually get built must agree.

Three lists, in three files, that nobody edits together:

    ui/machine_lab.py   _LAUNCHABLE   what the dropdown offers
    xv6/gini_agent.py   PROGRAMS      what the agent will actually run
    xv6/gini_patch.py   _UPROGS       what gets compiled into the image (plus stock xv6 programs)

A name in the menu but not in the agent is a launch that silently fails. A name in the agent but
not built is a launch that fails inside the container. Neither is visible until a student clicks
it in class, which is the worst possible time to find out.
"""
import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / "frontend-ng" / "src" / "gini" / "ui" / "machine_lab.py"
AGENT = ROOT / "backend" / "xv6" / "gini_agent.py"
PATCH = ROOT / "backend" / "xv6" / "gini_patch.py"

# xv6 ships these; GINI does not generate them, but they are legitimate to launch.
STOCK = {"forktest", "grind", "usertests", "stressfs", "zombie"}


def _list_literal(path: Path, name: str) -> list:
    """Read a module-level list of strings without importing the module."""
    if not path.exists():
        pytest.skip(f"{path} not present")
    tree = ast.parse(path.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == name:
            return [e.value for e in node.value.elts]
    pytest.fail(f"{name} not found in {path.name}")


def _uprog_names() -> set:
    if not PATCH.exists():
        pytest.skip("gini_patch.py not present")
    s = PATCH.read_text()
    blk = s[s.index("_UPROGS = {"):s.index("\ndef add_uprog")]
    return set(re.findall(r'^    "(\w+)": """', blk, re.M))


def test_the_menu_and_the_agent_offer_the_same_programs():
    """The one that bites: a student picks a program the agent refuses, and nothing happens."""
    assert _list_literal(LAB, "_LAUNCHABLE") == _list_literal(AGENT, "PROGRAMS")


def test_every_launchable_program_is_actually_built():
    built = _uprog_names() | STOCK
    missing = [p for p in _list_literal(LAB, "_LAUNCHABLE") if p not in built]
    assert not missing, f"offered in the launcher but never compiled: {missing}"


def test_the_three_new_programs_are_present():
    """walker/sgrind/mgrind carry specific rounds of the observation guide; if they vanish, those
    rounds have nothing to run."""
    built = _uprog_names()
    for name in ("walker", "sgrind", "mgrind"):
        assert name in built, f"{name} is no longer generated"
        assert name in _list_literal(LAB, "_LAUNCHABLE"), f"{name} is not in the launcher"


def test_every_program_the_menu_offers_is_described():
    """A program offered but undescribed is a program nobody picks — the invariant this has always
    protected.

    It used to read the hint LABEL, because that was where the descriptions lived: one
    non-wrapping line of prose beside the dropdown, 521 characters long. A QLabel in a horizontal
    layout hands its full single-line width to the layout as a minimum, so it asked for 3033 px on
    its own and dragged the Process Scheduler panel past the width of the screen. The descriptions
    moved to `_WHAT_IT_DOES` and are shown as the dropdown's own tooltips, which is where you want
    them anyway: this is text you read while CHOOSING a program.
    """
    src = LAB.read_text()
    described = src[src.index("_WHAT_IT_DOES = {"):]
    described = described[:described.index("\n}")]
    for name in _list_literal(LAB, "_LAUNCHABLE"):
        assert f'"{name}"' in described, f"{name} is offered in the menu but never described"


def test_the_launcher_bar_carries_no_prose_of_its_own():
    """The failure that started this, guarded where it can recur.

    Length was never the invariant — a tooltip may be as long as it likes. What cannot happen is
    a long string inside `_build_launcher`, because a QLabel in that horizontal layout hands its
    full single-line width to the layout as a MINIMUM, and one 521-character sentence asked for
    3033 px and took the whole panel past the width of the screen. Read from the source so it
    holds with no Qt and no display.
    """
    import ast
    src = LAB.read_text()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_build_launcher")
    long = [s.value for s in ast.walk(fn)
            if isinstance(s, ast.Constant) and isinstance(s.value, str)
            and len(s.value) > 120 and " " in s.value.strip()]
    assert not long, f"prose back in the launcher bar: {long[0][:70]!r}"


def test_no_uprog_uses_percent_escaping():
    """_UPROGS is written to disk verbatim — it is NOT %-formatted, unlike the kernel patch blocks
    a few hundred lines above it. A `%%` here reaches the C compiler as a literal `%%`, which is
    how walker's modulo arithmetic was briefly wrong."""
    s = PATCH.read_text()
    blk = s[s.index("_UPROGS = {"):s.index("\ndef add_uprog")]
    assert "%%" not in blk, "a %% escape leaked into _UPROGS; these entries are written verbatim"
