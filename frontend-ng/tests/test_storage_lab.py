"""Storage Lab dialog — renders the FS offscreen and drives the write-ahead log."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _theme(app):
    from gini.ui.theme import ThemeManager
    return ThemeManager(app)


def test_storage_lab_renders_regions_inodes_and_tree(app):
    from gini.ui.storage_lab import StorageLab
    lab = StorageLab(None, _theme(app))
    assert len(lab._strip._regions) == 6            # boot..data
    assert lab._inode_tbl.rowCount() >= 3
    assert lab._tree_tbl.rowCount() >= 3
    assert lab._buf_tbl.rowCount() >= 1
    lab.close()


def test_storage_lab_simulate_write_cycles_log(app):
    from gini.ui.storage_lab import StorageLab
    lab = StorageLab(None, _theme(app))
    assert lab._log_phase.text() == "idle"
    lab._on_write(); assert lab._log_phase.text() == "building"
    lab._on_write(); assert lab._log_phase.text() == "committing"
    lab._on_write(); assert lab._log_phase.text() == "idle"
    lab.close()
