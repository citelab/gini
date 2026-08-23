"""AA_ShareOpenGLContexts must be set BEFORE the QApplication is built.

This is a real crash, twice over. The OS Zoo and the headful Desktop screen embed a
QWebEngineView and import QtWebEngine lazily, inside the double-click handler, so that a normal
launch never pays Chromium's start-up cost. Qt requires AA_ShareOpenGLContexts to be set before
the application object exists precisely for that case; without it, constructing the first
QWebEngineView segfaults the process — no exception, no traceback, just:

    zsh: segmentation fault  gbuilder

The attribute has no visible effect on any launch that never opens a Zoo guest, which is exactly
what makes it easy to delete during unrelated cleanup and not notice. It was deleted once. This
test is here so the next deletion fails loudly instead of silently.

ORDER is the whole point, so the test reads the AST rather than the running app: setting the
attribute AFTER QApplication() leaves testAttribute() returning True while Qt has already ignored
it, so a runtime check would pass with the bug present. A source check cannot be fooled that way.
"""
import ast

from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "gini" / "__main__.py"


def _main_body() -> list:
    tree = ast.parse(SRC.read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    return list(ast.walk(fn))


def _line_of(pred) -> int:
    """Line number of the first node in main() satisfying `pred`, or -1."""
    return next((n.lineno for n in _main_body() if pred(n)), -1)


def _is_share_contexts(n) -> bool:
    return (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "setAttribute"
            and any(getattr(a, "attr", "") == "AA_ShareOpenGLContexts" for a in n.args))


def _is_app_construction(n) -> bool:
    """The application CONSTRUCTOR call, not QApplication.instance().

    Matches any *Application name so this keeps working as the class changes — it is currently
    GiniApplication (QApplication plus the ⌘Q guard, see ui/app.py). Pinning it to the literal
    name "QApplication" made this test fail the moment that landed, which is a false alarm: what
    matters is the ORDER, not which subclass gets built.
    """
    return (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id.endswith("Application"))


def test_the_attribute_is_set_at_all():
    assert _line_of(_is_share_contexts) > 0, (
        "main() no longer sets AA_ShareOpenGLContexts. Opening an OS Zoo guest or a headful "
        "Desktop screen will segfault. See the docstring in this file.")


def test_it_is_set_before_the_application_is_constructed():
    attr, app = _line_of(_is_share_contexts), _line_of(_is_app_construction)
    assert app > 0, "could not find the QApplication(...) construction in main()"
    assert attr < app, (
        f"AA_ShareOpenGLContexts is set on line {attr}, after QApplication is constructed on line "
        f"{app}. Qt ignores it at that point and QWebEngineView will segfault.")


def test_qtwebengine_is_not_imported_at_start_up():
    """The flip side: the attribute exists so the import can stay LAZY. An eager import would
    pull Chromium into every launch and make PySide6-Addons a hard requirement, breaking the
    browser fallback that lets gBuilder run without it.

    Checks IMPORT NODES, not source text — the first version of this test grepped for the string
    "QtWebEngine" and so failed on the explanatory comment above the attribute, which is the one
    thing in the file that should never have counted.
    """
    tree = ast.parse(SRC.read_text())
    bad = []
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and "QtWebEngine" in (n.module or ""):
            bad.append(n.module)
        elif isinstance(n, ast.Import):
            bad += [a.name for a in n.names if "QtWebEngine" in a.name]
    assert not bad, (
        f"__main__ imports {bad} — that costs every launch Chromium's start-up and makes "
        f"PySide6-Addons mandatory. Keep it lazy; the attribute above is what makes that safe.")
