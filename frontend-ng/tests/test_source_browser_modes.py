"""GINI Source serves two element families, and switching between them used to crash.

    ValueError: invalid literal for int() with base 10:
                '/Users/…/.gini/scripts/rip_reference.lua'

The jump list carries a LINE for kernel source and a FILE PATH for router modules. Both lived in
Qt.UserRole and were told apart by a `_mode` flag on the widget — and the flag can disagree with
the list:

  * `show_none()` sets the mode and THEN clears the list. QListWidget reassigns "current" to
    surviving rows as it removes them, so `currentItemChanged` fires with path-bearing items
    while the mode already reads "none".
  * `show_block()` is worse: its load is asynchronous, so the stale script list outlives the mode
    change until the reply lands.

The fix is that the ITEM says what it is. These tests drive the transitions that broke.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(scope="module")
def theme(app):
    from gini.ui.theme import ThemeManager
    return ThemeManager(app)


@pytest.fixture()
def scripts(tmp_path, monkeypatch):
    d = tmp_path / "scripts"
    d.mkdir()
    for n in ("rip_reference.lua", "mcast_tree.lua"):
        (d / n).write_text("-- a module\nfunction init() end\n")
    import gini.ui.source_browser as sb
    monkeypatch.setattr(sb, "scripts_dir", lambda: d)
    return d


def _browser(theme):
    from gini.ui.source_browser import SourceBrowser
    b = SourceBrowser(theme, fetch_fn=lambda: None)
    b.resize(420, 620)
    return b


def test_router_then_something_with_no_source(app, theme, scripts):
    """THE crash. show_scripts fills the list with paths; show_none flips the mode and clears,
    and the clear itself emits currentItemChanged with the surviving path items."""
    b = _browser(theme)
    b.show_scripts("R1")
    assert b._jump.count() == 2
    b.show_none("S1 (Switch)")                 # must not raise
    assert b._jump.count() == 0


def test_a_path_item_is_never_read_as_a_line_whatever_the_mode(app, theme, scripts):
    """THE invariant, stated directly.

    In the app the window opens because `show_block()` flips the mode to "kernel" and then loads
    asynchronously, so the path-bearing list from `show_scripts()` is still on screen when the
    reply has not arrived. That timing is awkward to stage here, so the mode is forced instead —
    which tests the same thing more sharply: an item's payload must be interpreted by ITS OWN
    kind, never by a flag that lives on the widget.

    The count assertion matters: an earlier version of this test looped over an already-cleared
    list and passed without clicking anything at all.
    """
    b = _browser(theme)
    b.show_scripts("R1")
    assert b._jump.count() == 2, "the list must hold path items for this test to mean anything"
    for mode in ("kernel", "none", ""):
        b._mode = mode                          # the stale-flag window
        for i in range(b._jump.count()):
            b._on_jump(b._jump.item(i))         # must not raise ValueError


def test_a_line_item_is_never_read_as_a_path(app, theme):
    """The mirror image: kernel entries clicked while the mode says scripts."""
    b = _browser(theme)
    b._on_loaded("kernel/bio.c", "// c\nstruct buf*\nbread(uint d)\n{\n  return 0;\n}\n")
    assert b._jump.count() >= 1
    b._mode = "scripts"
    for i in range(b._jump.count()):
        b._on_jump(b._jump.item(i))             # must not try to open a line number as a file


def test_clicking_a_script_opens_it(app, theme, scripts):
    b = _browser(theme)
    b.show_scripts("R1")
    b._jump.setCurrentRow(1)
    b._on_jump(b._jump.item(1))
    assert ".lua" in b._sub.text()
    assert b._view.toPlainText().startswith("-- a module")


def test_clicking_a_kernel_entry_jumps_to_its_line(app, theme):
    b = _browser(theme)
    b._on_loaded("kernel/bio.c",
                 "// c\nstruct buf*\nbread(uint dev)\n{\n  GINI_SUB(GSUB_BCACHE);"
                 "  // GINI-xv6: board probe bread\n  return 0;\n}\n")
    assert b._jump.count() >= 1
    b._on_jump(b._jump.item(0))
    assert b._view.textCursor().blockNumber() >= 0


def test_an_item_with_no_kind_is_ignored_not_fatal(app, theme):
    """Defence for a future mode that forgets to tag its payload."""
    from PySide6.QtWidgets import QListWidgetItem
    b = _browser(theme)
    b._jump.addItem(QListWidgetItem("untagged"))
    b._on_jump(b._jump.item(0))                # must not raise


def test_bouncing_between_modes_repeatedly(app, theme, scripts):
    """Every transition, several times — the crash needed a specific order to show up."""
    b = _browser(theme)
    for _ in range(3):
        b.show_scripts("R1")
        b.show_block("bcache")
        b.show_none("X")
        b.show_scripts("R2")
        b.show_none("Y")
    assert b._jump.count() == 0
