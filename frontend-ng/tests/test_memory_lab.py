"""Memory Lab dialog — renders the address space offscreen and drives a page fault."""
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


def test_memory_lab_renders_regions_and_page_table(app):
    from gini.ui.memory_lab import MemoryLab
    lab = MemoryLab(None, _theme(app))
    assert len(lab._strip._regions) >= 5
    assert lab._pt_tbl.rowCount() >= 5              # leaf mappings
    assert "satp" in lab._satp.text()
    assert "used" in lab._phys_lbl.text()
    lab.close()


def test_memory_lab_simulate_fault_allocates_and_logs(app):
    from gini.ui.memory_lab import MemoryLab
    lab = MemoryLab(None, _theme(app))
    rows0 = lab._pt_tbl.rowCount()
    assert lab._fault_tbl.rowCount() == 0
    lab._on_fault()
    assert lab._fault_tbl.rowCount() == 1           # a fault was logged
    assert lab._pt_tbl.rowCount() == rows0 + 1      # a new mapping appeared
    lab.close()
