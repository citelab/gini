"""A function-scope import must reach every place that uses the name.

This guards a bug that shipped twice. `ui/main_window.py` imported `compose_error` inside an
`if not dc:` branch and then used it from a nested `work()` — so on any engine where the
orchestrator already knew its compose CLI the branch never ran, the closure cell was empty, and
Python raised `cannot access free variable 'compose_error' where it is not associated with a
value in enclosing scope`.

What made it expensive was where it landed. The use sat inside `except Exception` — deliberate at
a UI boundary, see CLAUDE.md — so the NameError was caught and reported as *the device's* error.
Every machine on a Podman lab failed with "cannot access free variable 'compose_error'", and
whatever had actually gone wrong with the exec was never shown to anyone.

The two shapes below are the ones that bite. Deliberately NOT flagged: `try: import X / except
ImportError: return`, where the failure path leaves before the use — that pattern is all over this
codebase and is correct.
"""
from __future__ import annotations

import ast
import builtins
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "gini"
PACKAGES = ("ui", "services", "agent", "runtime", "setup", "server", "app")
FUNC = (ast.FunctionDef, ast.AsyncFunctionDef)
COND = (ast.If, ast.Try, ast.While, ast.For, ast.AsyncFor, ast.ExceptHandler)
BUILTINS = set(dir(builtins))


def _parents(tree):
    p = {}
    for node in ast.walk(tree):
        for kid in ast.iter_child_nodes(node):
            p[kid] = node
    return p


def _chain(node, parents):
    out = []
    while node in parents:
        node = parents[node]
        out.append(node)
    return out


def _bound_in(func) -> set:
    """Names this function binds directly: imports, assignments, args, nested defs."""
    names = {a.arg for a in getattr(func.args, "args", [])}
    names |= {a.arg for a in getattr(func.args, "kwonlyargs", [])}
    for node in ast.walk(func):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names |= {(a.asname or a.name).split(".")[0] for a in node.names}
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, FUNC + (ast.ClassDef,)) and node is not func:
            names.add(node.name)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
    return names


def _module_names(tree) -> set:
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names |= {(a.asname or a.name).split(".")[0] for a in node.names}
        elif isinstance(node, FUNC + (ast.ClassDef,)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names |= {t.id for t in ast.walk(node)
                      if isinstance(t, ast.Name) and isinstance(t.ctx, ast.Store)}
    return names


def scope_problems(source: str, label: str = "<src>") -> list[str]:
    tree = ast.parse(source)
    parents = _parents(tree)
    module_names = _module_names(tree)

    def owner(node):
        for a in _chain(node, parents):
            if isinstance(a, FUNC):
                return a
        return None

    # every function-scope import: name -> (function, node, enclosing conditional or None)
    imports: dict[str, list] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        f = owner(node)
        if f is None:
            continue
        cond = None
        for a in _chain(node, parents):
            if a is f:
                break
            if isinstance(a, COND):
                cond = a
                break
        for al in node.names:
            nm = (al.asname or al.name).split(".")[0]
            imports.setdefault(nm, []).append((f, node, cond))

    problems = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)):
            continue
        nm = node.id
        if nm not in imports or nm in module_names or nm in BUILTINS:
            continue
        ancestors = _chain(node, parents)
        funcs = [a for a in ancestors if isinstance(a, FUNC)]
        if not funcs:
            continue
        # Shape A — imported conditionally in an ENCLOSING function, used from a nested one.
        # The nested function can run whether or not the branch was taken, so the cell may be
        # empty. (A conditional import used in the SAME function body is the try/except-return
        # idiom and is fine; that is why this requires a nested function.)
        for f, imp, cond in imports[nm]:
            if cond is not None and f in funcs[1:] and cond not in ancestors:
                problems.append(
                    f"{label}:{node.lineno}: {nm!r} is used inside {funcs[0].name}(), nested in "
                    f"{f.name}(), but only imported inside a conditional at line {imp.lineno} — "
                    f"the closure cell is empty whenever that branch does not run")
                break
        else:
            # Shape B — imported in one function, used in an unrelated one. Never resolves.
            if any(nm in _bound_in(f) for f in funcs):
                continue
            where = ", ".join(f"{f.name}():{imp.lineno}" for f, imp, _ in imports[nm])
            problems.append(
                f"{label}:{node.lineno}: {nm!r} is used in {funcs[0].name}() but imported only "
                f"in a different function ({where}) — this raises NameError")
    return problems


def _modules():
    for pkg in PACKAGES:
        d = SRC / pkg
        if d.is_dir():
            yield from sorted(d.rglob("*.py"))


@pytest.mark.parametrize("path", list(_modules()), ids=lambda p: p.name)
def test_every_function_scope_import_reaches_its_uses(path):
    problems = scope_problems(path.read_text(), str(path.relative_to(SRC.parent.parent)))
    assert not problems, "\n" + "\n".join(problems)


def test_the_checker_catches_the_bug_it_was_written_for():
    """The exact shape from main_window._populate_overlay_hosts, before the fix."""
    bad = '''
def outer(orch):
    dc = list(getattr(orch, "_dc", None) or [])
    if not dc:
        from ..setup.runtime import compose_cli, compose_error
        dc = list(compose_cli())

    def work():
        return compose_error(b"boom")
    return work
'''
    problems = scope_problems(bad)
    assert len(problems) == 1
    assert "compose_error" in problems[0]
    assert "closure cell is empty" in problems[0]


def test_the_checker_catches_a_name_imported_in_another_method():
    bad = '''
class C:
    def a(self):
        from ..setup.runtime import compose_error
        return compose_error

    def b(self, r):
        return compose_error(r.stderr)
'''
    problems = scope_problems(bad)
    assert len(problems) == 1
    assert "raises NameError" in problems[0]


def test_the_checker_allows_the_guarded_optional_import_idiom():
    """`try: import X / except ImportError: return` is correct and must not be flagged."""
    fine = '''
def open_socket(self):
    try:
        from PySide6.QtWebSockets import QWebSocket
    except ImportError as e:
        self.failed.emit(str(e))
        return
    self._sock = QWebSocket()
'''
    assert scope_problems(fine) == []


def test_the_checker_does_not_flag_a_plain_local_assignment():
    """A name that happens to be imported elsewhere, but is assigned locally here."""
    fine = '''
def a():
    from x import catalog
    return catalog

def b():
    catalog, names = build()

    def work():
        return catalog, names
    return work
'''
    assert scope_problems(fine) == []
