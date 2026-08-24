"""A query thread must not crash when its dialog is destroyed underneath it.

Introduced by the fix for the leaked pollers: _retire_lab closes and DELETES the previous Router
Lab, but a query can be sitting in `docker compose exec` for up to 12 seconds. When it finishes
and emits into the destroyed dialog:

    Exception in thread Thread-176 (work):
      File ".../ui/router_lab.py", line 490, in work
        self.routes_ready.emit(rows)
    RuntimeError: Signal source has been deleted

An unhandled traceback on the console, and the worker dies without finishing its round — so the
in-flight counter is never released and polling stops for good on that dialog.

There is no reliable way to ask from another thread whether a QObject is still alive: checking and
then emitting is a race, because the deletion can land between the two. So the emit itself is the
check, and its failure is a normal outcome rather than an error.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class _Dev:
    name = "r1"
    id = "d1"
    type_key = "router"


def _lab(app):
    from gini.domain.router_modules import RouterProgram
    from gini.ui.router_lab import RouterLab
    from gini.ui.theme import ThemeManager
    if not hasattr(_lab, "_t"):
        _lab._t = ThemeManager(app)
    return RouterLab(None, _lab._t, _Dev(), RouterProgram(), query_fn=lambda c: "")


def test_emitting_into_a_deleted_dialog_is_not_fatal(app):
    """The reported crash, reproduced exactly: destroy the dialog, then emit as a late worker
    would."""
    from shiboken6 import delete
    lab = _lab(app)
    sig = lab.routes_ready
    emit = lab._emit
    delete(lab)
    assert emit(sig, []) is False, "a late emit into a deleted dialog must report failure"


def test_a_live_dialog_still_receives_the_result(app):
    """The guard must not swallow real results — that would leave every table empty."""
    lab = _lab(app)
    got = []
    lab.routes_ready.connect(lambda rows: got.append(rows))
    assert lab._emit(lab.routes_ready, []) is True
    assert got == [[]], "the result never reached the dialog"
    lab.close()


def test_a_worker_stops_early_when_its_dialog_has_gone(app):
    """_emit returns False so a worker can abandon the rest of its queries. Carrying on would run
    a 12s docker exec for a window nobody can see — the very waste the retire fix removed."""
    import inspect

    from gini.ui.router_lab import RouterLab
    src = inspect.getsource(RouterLab._refresh_routes)
    assert "if not self._emit(" in src and "return" in src, (
        "the routes worker does not bail out when the dialog is gone; it will run its second "
        "query for a destroyed window")


def test_every_worker_emit_goes_through_the_guard(app):
    """One raw .emit() left in a worker reintroduces the crash on exactly the path nobody tests."""
    import re
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "src" / "gini" / "ui" / "router_lab.py"
    raw = []
    for i, line in enumerate(src.read_text().splitlines(), 1):
        m = re.search(r"self\.(\w*_ready|worker_done)\.emit\(", line)
        if m and "_emit(" not in line:
            raw.append(f"{i}: {line.strip()}")
    assert not raw, "raw signal emits from worker threads:\n  " + "\n  ".join(raw)
