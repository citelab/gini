"""Process tree widget — renders the hierarchy offscreen and fires kill for user procs."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from gini.domain.xv6 import parse_procdump

QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _theme(app):
    from gini.ui.theme import ThemeManager
    return ThemeManager(app)


def _procs():
    return parse_procdump("1 sleep init 0\n2 sleep sh 1\n4 run busy 2\n"
                          "5 runble busy 2\n7 zombie hello 2\n")


def test_tree_nests_by_parent(app):
    from gini.ui.process_tree import ProcessTree
    tree = ProcessTree(_theme(app))
    tree.set_procs(_procs(), running_pids=[4])
    assert tree.topLevelItemCount() == 1                 # init is the sole root
    init = tree.topLevelItem(0)
    assert init.text(1) == "1"
    sh = init.child(0)
    assert sh.text(1) == "2" and sh.childCount() == 3    # sh has 3 children
    tree.close()


def test_tree_kill_only_for_user_procs_when_live(app):
    from gini.ui.process_tree import ProcessTree
    tree = ProcessTree(_theme(app))
    tree.set_live(True)
    fired = []
    tree.kill_requested.connect(fired.append)
    tree.set_procs(_procs(), running_pids=[4])
    # walk to a user proc (pid 4) and click its labelled Kill button
    sh = tree.topLevelItem(0).child(0)
    busy = sh.child(0)                                    # pid 4
    btn = tree.itemWidget(busy, 3)
    assert btn is not None and btn.text() == "Kill"       # proper label, not a bare ✕
    btn.click()
    assert fired == [4]
    # init (pid 1) has no kill button
    assert tree.itemWidget(tree.topLevelItem(0), 3) is None
    tree.close()


def test_tree_kill_button_shows_pending_state(app):
    from gini.ui.process_tree import ProcessTree
    tree = ProcessTree(_theme(app))
    tree.set_live(True)
    tree.set_procs(_procs(), running_pids=[4])
    busy = tree.topLevelItem(0).child(0).child(0)         # pid 4
    tree.itemWidget(busy, 3).click()                      # request kill
    # the button flips to a disabled 'killing…' state immediately...
    b = tree.itemWidget(busy, 3)
    assert b.text() == "killing…" and not b.isEnabled()
    assert 4 in tree._killing
    # ...and the pending state SURVIVES a tree rebuild (the ~0.5s poll) while pid 4 still exists
    tree.set_procs(_procs(), running_pids=[4])
    assert tree.itemWidget(tree.topLevelItem(0).child(0).child(0), 3).text() == "killing…"
    # once the proc is gone (reaped), the pending mark is dropped
    tree.set_procs(parse_procdump("1 sleep init 0\n2 sleep sh 1\n"), running_pids=[])
    assert tree._killing == set()
    tree.close()
