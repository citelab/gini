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


# -- B4/B5: what a panel says when it has nothing to say ------------------------ #
class _Sparse:
    """A live reader on a kernel that dumps the page table and nothing else — the shape every
    student had until the KA line landed, and the shape any older image still has."""
    def __init__(self, **kw):
        self.kw = kw

    def snapshot(self):
        from gini.domain.xv6_vm import Pte, VmSnapshot, perms
        kw = {"have": ("pagetable",), **self.kw}          # the caller may widen `have`
        return VmSnapshot(satp=0x87f6e000,
                          leaves=[Pte(0, 0x87001000, perms(0x1b), True, 0x1b)],
                          source="real", ok=True, **kw)

    def all_procs(self):
        return {}

    def faults(self):
        return []


def test_real_mode_never_paints_a_zero_physical_bar(app):
    """The rule: a panel with no data says so, it never paints a zero. "0 used / 0 free of 0
    pages" is not an empty allocator, it is an unread one — and it was on screen for months."""
    from gini.ui.memory_lab import MemoryLab
    lab = MemoryLab(None, _theme(app), provider=_Sparse())
    assert "0 used" not in lab._phys_lbl.text()
    assert "not reported by this kernel build" in lab._phys_lbl.text()
    assert "not reported by this kernel build" in lab._frag_lbl.text()
    lab.close()


def test_a_blank_region_strip_says_why_it_is_blank(app):
    from gini.ui.memory_lab import MemoryLab
    lab = MemoryLab(None, _theme(app), provider=_Sparse())
    assert lab._strip._regions == []
    assert "not reported by this kernel build" in lab._strip._note
    lab.close()


def test_an_offline_machine_is_not_confused_with_an_old_kernel(app):
    """Two different causes, two different sentences. Telling somebody to rebuild an image when
    their container is simply not answering sends them to fix the wrong thing."""
    from gini.domain.xv6_vm import VmSnapshot
    from gini.ui.memory_lab import MemoryLab

    class Down(_Sparse):
        def snapshot(self):
            return VmSnapshot(source="real", ok=False, have=())
    lab = MemoryLab(None, _theme(app), provider=Down())
    assert "container is not answering" in lab._phys_lbl.text()
    assert "rebuild" not in lab._phys_lbl.text()
    lab.close()


def test_a_derived_region_map_says_it_is_derived(app):
    """The distinction between what the kernel reported and what GINI worked out is the one this
    course teaches; the title carries it rather than passing an inference off as a measurement."""
    from gini.domain.xv6_vm import Region
    from gini.ui.memory_lab import MemoryLab

    class Derived(_Sparse):
        def snapshot(self):
            s = _Sparse.snapshot(self)
            s.regions = [Region("text", 0, 0xFFF, "r-xu")]
            s.have, s.derived = ("pagetable", "regions"), ("regions",)
            return s
    lab = MemoryLab(None, _theme(app), provider=Derived())
    assert "(derived)" in lab._strip_panel.title_label.text()
    assert lab._strip._regions, "and it still draws the map"
    lab.close()


def test_the_vm_shadow_scoreboard_reaches_the_screen(app):
    """`handled == 0` with faults falling through is THE failure state of a student's page-fault
    handler, and the counters for it were parsed but never rendered anywhere."""
    from gini.ui.memory_lab import MemoryLab
    lab = MemoryLab(None, _theme(app),
                    provider=_Sparse(vmf_handled=0, vmf_fell=412,
                                     have=("pagetable", "vmfault")))
    assert "0 handled" in lab._vmf_lbl.text() and "412 fell through" in lab._vmf_lbl.text()
    lab.close()
