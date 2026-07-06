"""xv6 virtual-memory model — vmprint parsing, permission decode, and the DemoVm allocator."""
from gini.domain.xv6_vm import (
    TRAMPOLINE, DemoVm, parse_vmprint, perms, region_for,
)

# xv6 vmprint() output: root, three level-2 entries, and leaf mappings at level 0.
VMPRINT = """page table 0x0000000087f6e000
 ..0: pte 0x0000000021fda801 pa 0x0000000087f6a000
 .. ..0: pte 0x0000000021fda401 pa 0x0000000087f69000
 .. .. ..0: pte 0x0000000021fdac0b pa 0x0000000087f6b000
 .. .. ..1: pte 0x0000000021fda017 pa 0x0000000087f68000
 ..255: pte 0x0000000021fdb401 pa 0x0000000087f6d000
 .. ..511: pte 0x0000000021fdb001 pa 0x0000000087f6c000
 .. .. ..511: pte 0x0000000021fdd40b pa 0x0000000087f6f000
"""


def test_perms_decodes_rwxu():
    assert perms(0x1f) == "rwxu"          # V|R|W|X|U low bits -> all set
    assert perms(0x0b) == "r-x-"          # V|R|X (0x0b = 1011): no W, no U
    assert perms(0x17) == "rw-u"          # V|R|W|U (0x17 = 10111): no X
    assert perms(0x01) == "----"          # valid only, no access bits


def test_parse_vmprint_rebuilds_va_and_perms():
    vm = parse_vmprint(VMPRINT)
    assert vm.satp == 0x87f6e000
    vas = {p.va: p for p in vm.leaves}
    assert 0x0 in vas                     # index path 0/0/0 -> va 0
    assert vas[0x0].pa == 0x87f6b000
    assert vas[0x0].perms == "r-x-"       # pte low nibble 0xb -> V|R|X (text page)
    assert 0x1000 in vas                  # 0/0/1 -> va 0x1000
    assert vas[0x1000].perms == "rw-u"    # pte low nibble 0x17 -> V|R|W|U
    # the high mapping 255/511/511 -> top of the address space (trampoline slot)
    assert any(p.va >= (255 << 30) for p in vm.leaves)


def test_region_for_classifies_addresses():
    vm = DemoVm().snapshot()
    assert region_for(0x0, vm.regions) == "text"
    assert region_for(0x1000, vm.regions) == "data"
    assert region_for(TRAMPOLINE, vm.regions) == "trampoline"


def test_demovm_snapshot_has_regions_and_phys():
    vm = DemoVm().snapshot()
    assert vm.satp != 0
    names = [r.name for r in vm.regions]
    assert "text" in names and "stack" in names and "trampoline" in names
    assert vm.phys.total_pages > vm.phys.free_pages
    assert 0.0 < vm.phys.used_frac < 1.0


def test_demovm_fault_allocates_a_page_and_logs():
    d = DemoVm()
    free0 = d.phys.free_pages
    leaves0 = len(d.snapshot().leaves)
    snap = d.simulate_fault()
    assert len(snap.faults) == 1 and "fault" in snap.faults[0].cause
    assert d.phys.free_pages == free0 - 1          # a physical page was allocated
    assert len(snap.leaves) == leaves0 + 1         # a new mapping appeared
