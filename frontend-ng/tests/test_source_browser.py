"""The GINI Source browser widget — rendered, not just imported.

This file exists because the widget had zero runtime coverage: it was written, wired and shipped
without ever being constructed. Three Qt spellings in it (QTextEdit.ExtraSelection,
QTextFormat.FullWidthSelection, QPlainTextEdit.NoWrap) appear nowhere else in this codebase, so
nothing else would catch them being wrong.

Everything here paints for real. A widget test that never renders is how the OS HUD shipped with
a dead scrub timeline.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets")
QtGui = pytest.importorskip("PySide6.QtGui")

BIO = """// Buffer cache.
struct buf*
bread(uint dev, uint blockno)
{
  GINI_SUB(GSUB_BCACHE);  // GINI-xv6: board probe bread
  return bget(dev, blockno);
}

void
bwrite(struct buf *b)
{
  GINI_SUB(GSUB_BCACHE);  // GINI-xv6: board probe bwrite
  virtio_disk_rw(b, 1);
}
"""


class FakeAgent:
    """Stands in for the in-container agent. Records what was asked for."""

    def __init__(self, text=BIO):
        self.text = text
        self.asked = []

    def get_text(self, path):
        self.asked.append(path)
        return self.text


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(scope="module")
def theme(app):
    from gini.ui.theme import ThemeManager
    return ThemeManager(app)


def _browser(theme, agent=None):
    from gini.ui.source_browser import SourceBrowser
    b = SourceBrowser(theme, fetch_fn=(lambda: agent))
    b.resize(420, 620)
    return b


def _paint(w):
    pm = QtGui.QPixmap(w.size())
    pm.fill()
    w.render(pm)
    return pm


def _deliver(b, rel, text):
    """Skip the worker thread and hand the widget its reply directly — the fetch is plain HTTP;
    what needs testing is what the widget does with the answer."""
    b._on_loaded(rel, text)


def test_paints_before_anything_is_asked_for(app, theme):
    _paint(_browser(theme))


def test_paints_with_no_machine_running(app, theme):
    """fetch_fn returns None when there is no xv6 — say so, do not throw."""
    b = _browser(theme, agent=None)
    b.show_block("bcache")
    _paint(b)


def test_shows_a_file_and_its_entry_points(app, theme):
    ag = FakeAgent()
    b = _browser(theme, ag)
    _deliver(b, "kernel/bio.c", BIO)
    _paint(b)
    assert b._jump.count() == 2
    assert b._jump.item(0).text().startswith("bread")
    assert "2 entry points" in b._sub.text()


def test_jumping_scrolls_and_highlights_without_throwing(app, theme):
    """The guarded highlight path — QTextEdit.ExtraSelection and friends. If a spelling is wrong
    on some PySide6 build the scroll must still work, so exercise both."""
    b = _browser(theme, FakeAgent())
    _deliver(b, "kernel/bio.c", BIO)
    for i in range(b._jump.count()):
        b._jump.setCurrentRow(i)
        b._on_jump(b._jump.item(i))
        _paint(b)
    assert b._view.textCursor().blockNumber() > 0     # it really moved


def test_a_two_file_block_offers_both(app, theme):
    ag = FakeAgent()
    b = _browser(theme, ag)
    b.show_block("memory")
    assert [b._files.itemText(i) for i in range(b._files.count())] == \
        ["kernel/vm.c", "kernel/kalloc.c"]
    _paint(b)


def test_the_agents_refusals_render_as_errors(app, theme):
    """The agent shapes refusals as C comments so they are harmless if mishandled. The browser
    must still show them as errors rather than as a one-line source file."""
    b = _browser(theme, FakeAgent())
    for msg in ("// refused: outside the kernel tree", "// not found: kernel/nope.c",
                "// refused: not a source file"):
        _deliver(b, "kernel/x.c", msg)
        _paint(b)
        assert b._jump.count() == 0
        assert b._view.toPlainText() == ""
        assert "refused" in b._sub.text() or "not found" in b._sub.text()


def test_a_path_outside_the_tree_never_leaves_the_app(app, theme):
    ag = FakeAgent()
    b = _browser(theme, ag)
    b.open_path("../../etc/passwd")
    _paint(b)
    assert ag.asked == [], "a traversal attempt was sent to the agent"
    assert "not inside the kernel tree" in b._sub.text()


def test_the_request_names_the_file(app, theme):
    """Whatever else changes, the endpoint contract is /source?file=<rel>."""
    import time
    ag = FakeAgent()
    b = _browser(theme, ag)
    b.open_path("kernel/bio.c")
    for _ in range(50):                              # the fetch runs on a worker thread
        if ag.asked:
            break
        time.sleep(0.02)
    assert ag.asked == ["/source?file=kernel/bio.c"]


def test_a_file_with_no_probes_still_gets_an_outline(app, theme):
    b = _browser(theme, FakeAgent())
    _deliver(b, "kernel/trap.c", "void\nusertrap(void)\n{\n  return;\n}\n")
    _paint(b)
    assert b._jump.count() == 1
    assert "no board probes" in b._sub.text()
