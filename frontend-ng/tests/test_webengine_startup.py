"""AA_ShareOpenGLContexts must be set BEFORE the application is built.

Still required after the Terminal moved off QtWebEngine: the OS Zoo and the headful Desktop screen
both embed a QWebEngineView, and both import QtWebEngine LAZILY — inside the double-click handler,
so a normal launch never pays Chromium's start-up cost. That laziness means the import lands after
the application object exists, which is exactly the case this attribute covers. Without it,
constructing the first QWebEngineView segfaults the process:

    zsh: segmentation fault  gbuilder

The attribute has no visible effect on any launch that never opens a Zoo guest, which is what
makes it easy to delete during unrelated cleanup and not notice. It has been deleted once already.

ORDER is the whole point, so these read the AST rather than the running app: setting the attribute
AFTER the application is constructed leaves testAttribute() returning True while Qt has already
ignored it, so a runtime check would pass with the bug present.
"""
import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "gini" / "__main__.py"


def _main_body() -> list:
    tree = ast.parse(SRC.read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    return list(ast.walk(fn))


def _line_of(pred) -> int:
    return next((n.lineno for n in _main_body() if pred(n)), -1)


def _is_share_contexts(n) -> bool:
    return (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "setAttribute"
            and any(getattr(a, "attr", "") == "AA_ShareOpenGLContexts" for a in n.args))


def _is_app_construction(n) -> bool:
    """The application CONSTRUCTOR call, not QApplication.instance().

    Matches any *Application name so this keeps working as the class changes — it is currently
    GiniApplication (QApplication plus the ⌘Q guard, see ui/app.py). What matters is the ORDER,
    not which subclass gets built.
    """
    return (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id.endswith("Application"))


def test_the_attribute_is_set_at_all():
    assert _line_of(_is_share_contexts) > 0, (
        "main() no longer sets AA_ShareOpenGLContexts. Opening an OS Zoo guest or a headful "
        "Desktop screen will segfault. See the docstring in this file.")


def test_it_is_set_before_the_application_is_constructed():
    attr, app = _line_of(_is_share_contexts), _line_of(_is_app_construction)
    assert app > 0, "could not find the application construction in main()"
    assert attr < app, (
        f"AA_ShareOpenGLContexts is set on line {attr}, after the application is constructed on "
        f"line {app}. Qt ignores it at that point and QWebEngineView will segfault.")


def test_qtwebengine_is_not_imported_at_start_up():
    """The flip side: the attribute exists so the Zoo/Desktop imports can stay LAZY. An eager
    import would pull Chromium into every launch — the cost the Terminal was rewritten to avoid —
    and make PySide6-Addons mandatory for the browser-fallback path too.

    Checks IMPORT NODES, not source text: an earlier version grepped for "QtWebEngine" and failed
    on the explanatory comment, which is the one thing that should never have counted.
    """
    tree = ast.parse(SRC.read_text())
    bad = []
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and "QtWebEngine" in (n.module or ""):
            bad.append(n.module)
        elif isinstance(n, ast.Import):
            bad += [a.name for a in n.names if "QtWebEngine" in a.name]
    assert not bad, f"__main__ imports {bad}; keep it lazy"
