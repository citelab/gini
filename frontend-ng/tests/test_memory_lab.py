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


def test_memory_lab_shows_cow_sharing_then_privatises(app):
    # the offline demo puts a parent+child sharing a data page; the sharing panel shows it,
    # and "Simulate COW write" makes the child copy it so the page is no longer shared.
    from gini.ui.memory_lab import MemoryLab
    lab = MemoryLab(None, _theme(app))
    assert set(lab._proc_meters) == {3, 4}          # a resident/virtual meter per process
    shared0 = lab._share_tbl.rowCount()
    assert shared0 >= 1                              # at least the shared data page
    cells = [lab._share_tbl.item(r, 2).text() for r in range(shared0)]
    assert "COW" in cells                           # the shared page is COW-tagged
    lab._on_cow()                                    # child writes -> copies its data page
    assert lab._share_tbl.rowCount() == shared0 - 1  # one fewer physically-shared page
    lab.close()


class _LiveVm:
    """A live-shaped VM provider: no simulate_fault (so the lab treats it as live), with an
    all-procs picture and a raw fault ring the lab must CLASSIFY."""
    def snapshot(self):
        from gini.domain.xv6_vm import DemoVm
        return DemoVm().snapshot()
    def all_procs(self):
        from gini.domain.xv6_vm import DemoVm
        return DemoVm().all_procs()
    def faults(self):
        from gini.domain.xv6_vm import PageFault
        return [PageFault(4, 15, 0x1000, 0xabc),        # store to shared read-only page -> cow
                PageFault(3, 13, 0x40000000, 0x2100)]   # far out of range -> illegal


def test_memory_lab_classifies_live_fault_ring(app):
    from gini.ui.memory_lab import MemoryLab
    lab = MemoryLab(None, _theme(app), provider=_LiveVm())
    assert lab._live is True
    assert lab._fault_tbl.rowCount() == 2
    kinds = {lab._fault_tbl.item(r, 3).text() for r in range(2)}
    assert "cow-write" in kinds and "illegal" in kinds
    lab.close()
