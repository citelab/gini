"""The parked-capability registry, and the two ways it could rot.

Parking rather than deleting is only safe if the parked code stays real. Commented-out code stops
compiling, stops being refactored, and stops being tested; a flag keeps all three. What a flag does
NOT give you for free is a guarantee that the notes still describe the code, or that a door someone
shut stays shut. That is what this file is for.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from gini.app import features

SRC = Path(__file__).resolve().parents[1] / "src" / "gini"


def test_every_parked_entry_says_what_it_needs_to_come_back():
    """The `needs` list IS the specification for a future Teaching Center release. An entry without
    one is a note that something is off, which is the part everybody already knows."""
    for name, p in features.PARKED.items():
        assert p.what and p.why, name
        assert p.needs, f"{name} does not say what would bring it back"
        assert p.code, f"{name} does not say where the code still lives"


def test_the_code_a_parked_entry_names_still_exists():
    """THE rot check. A parked capability whose code was renamed or removed underneath it is worse
    than a deleted one: the registry claims it is waiting, and it is not there."""
    missing = []
    for name, p in features.PARKED.items():
        for ref in p.code:
            path = ref.split(":")[0].strip()
            if not path.endswith(".py"):
                continue
            f = SRC / path
            if not f.exists():
                missing.append(f"{name}: {path}")
                continue
            for symbol in [s.strip() for s in ref.split(":", 1)[1].split(",")] if ":" in ref else []:
                if symbol and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", symbol):
                    assert symbol in f.read_text(encoding="utf-8"), \
                        f"{name}: {path} no longer defines {symbol}"
    assert not missing, f"parked entries point at files that are gone: {missing}"


def test_every_gate_in_the_source_names_a_registered_capability():
    """`features.on()` fails closed, so a typo hides something that works rather than exposing
    something that does not — but it would hide it SILENTLY. This is what catches the typo."""
    known = set(features.PARKED) | set(features.LIVE)
    used = set()
    for f in SRC.rglob("*.py"):
        for m in re.finditer(r'features\.on\(\s*["\']([^"\']+)["\']', f.read_text(encoding="utf-8")):
            used.add(m.group(1))
    assert used, "no gates found — did features.on move?"
    assert used <= known, f"gates naming unregistered capabilities: {sorted(used - known)}"


def test_nothing_parked_is_reachable_from_a_menu():
    """A parked capability that is still wired to a menu item does nothing when clicked, which is
    the exact failure parking exists to remove. Checked in the source, because the alternative is
    building every menu in every state."""
    mw = (SRC / "ui" / "main_window.py").read_text(encoding="utf-8")
    tree = ast.parse(mw)
    # every addAction("…") whose handler is one of the parked entry points
    parked_handlers = {"_open_messages", "_set_photo", "_ai_proxy_consent", "_issue_codes",
                       "_add_mission_items", "_add_group_items", "_playtest_experiment"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        src = ast.unparse(node)
        for h in parked_handlers:
            if f"self.{h}" not in src:
                continue
            # the call must sit inside a `features.on(...)` guard somewhere in its function
            fn = _enclosing_function(tree, node)
            assert fn is not None and "features.on(" in ast.unparse(fn), \
                f"{h} is wired up without a features.on() guard"


def _enclosing_function(tree, target):
    best = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.lineno <= target.lineno:
            if getattr(node, "end_lineno", 0) >= target.lineno:
                if best is None or node.lineno > best.lineno:
                    best = node
    return best


def test_local_missions_is_not_parked():
    """The one that must never be parked by accident. Missions is a headline feature; only the
    SERVER-delivered half is off, and the practice catalog has never needed a server."""
    assert features.on("missions.local")
    assert "missions.local" not in features.PARKED


def test_the_v1_student_path_is_live():
    """Proof-of-activity hand-in is the whole point of the v1 Teaching Center."""
    assert features.on("proof.submit") and features.on("proof.verify")
    assert features.on("staff.signin")


@pytest.mark.parametrize("name", sorted(features.PARKED))
def test_a_parked_capability_reads_as_off(name):
    assert features.on(name) is False
    assert features.explain(name)
