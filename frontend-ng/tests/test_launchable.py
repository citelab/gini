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


def test_the_hint_text_describes_what_the_menu_offers():
    """The hint under the dropdown is the only in-app documentation of these programs. A program
    offered but undescribed is a program nobody picks."""
    # Anchor inside _build_launcher: several other panels have their own `hint = QLabel(...)`,
    # and the first one in the file belongs to the scheduler description.
    src = LAB.read_text()
    body = src[src.index("def _build_launcher"):]
    body = body[:body.index("\n    def ")]
    hint = body[body.index("hint = QLabel("):]
    for name in _list_literal(LAB, "_LAUNCHABLE"):
        assert name in hint, f"{name} is offered in the menu but not described in the hint"


def test_no_uprog_uses_percent_escaping():
    """_UPROGS is written to disk verbatim — it is NOT %-formatted, unlike the kernel patch blocks
    a few hundred lines above it. A `%%` here reaches the C compiler as a literal `%%`, which is
    how walker's modulo arithmetic was briefly wrong."""
    s = PATCH.read_text()
    blk = s[s.index("_UPROGS = {"):s.index("\ndef add_uprog")]
    assert "%%" not in blk, "a %% escape leaked into _UPROGS; these entries are written verbatim"
