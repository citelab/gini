"""xv6 virtual-memory model — vmprint parsing, permission decode, and the DemoVm allocator."""
from gini.domain.xv6_vm import (
    TRAMPOLINE,
    TRAPFRAME,
    DemoVm,
    Pte,
    Region,
    accessed,
    ad_str,
    classify_faults,
    dirty,
    parse_faults,
    parse_vmall,
    parse_vmprint,
    perms,
    region_for,
    regions_from_leaves,
    shared_frames,
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


# -- live fault ring (gini_faultdump) --------------------------------------- #
def test_parse_faults_reads_the_ring():
    faults = parse_faults("FLT 4 15 0x1000 0x0000000000001abc\n"
                          "FLT 3 13 0x40000000 0x2100\nnoise\n")
    assert [(f.pid, f.scause, f.va) for f in faults] == [(4, 15, 0x1000), (3, 13, 0x40000000)]
    assert faults[0].cause == "store page fault"       # scause 15
    assert faults[1].cause == "load page fault"        # scause 13


# -- all-procs page tables (gini_vmdump_all) + PTE flags -------------------- #
VMALL = """VP 3 forktest 0x6000
VL 3 0x0 0x87001000 27
VL 3 0x1000 0x87002000 275
VL 3 0x3000 0x87003000 275
VP 4 forktest 0x6000
VL 4 0x0 0x87001000 27
VL 4 0x1000 0x87002000 275
VL 4 0x3000 0x87003000 275
"""


def test_parse_vmall_splits_by_pid_and_keeps_flags():
    procs = parse_vmall(VMALL)
    assert set(procs) == {3, 4}
    assert procs[3].name == "forktest" and procs[3].sz == 0x6000
    data = next(p for p in procs[3].leaves if p.va == 0x1000)
    assert data.flags == 275 and data.perms == "r--u"      # V|R|U|RSW8 -> read-only user
    assert data.cow is True                                # user, not writable, RSW set
    text = next(p for p in procs[3].leaves if p.va == 0x0)
    assert text.cow is False                               # r-x-u text is not a COW page
    assert procs[3].resident_pages == 3 and procs[3].virtual_pages == 6


def test_shared_frames_finds_cow_sharing_then_loses_it_on_write():
    procs = parse_vmall(VMALL)
    shared = shared_frames(procs)
    assert shared[0x87002000] == [3, 4]                    # data page shared by parent+child
    assert shared[0x87003000] == [3, 4]                    # stack page too
    # after the child copies its data page, that PA is no longer shared
    procs[4].leaves = [p for p in procs[4].leaves if p.va != 0x1000]
    from gini.domain.xv6_vm import Pte
    procs[4].leaves.append(Pte(0x1000, 0x87005000, "rw-u", True, 23))
    shared2 = shared_frames(procs)
    assert shared2.get(0x87002000) is None                 # data pa now owned by parent only
    assert shared2[0x87003000] == [3, 4]                   # stack still shared


def test_classify_faults_labels_cow_lazy_illegal():
    procs = parse_vmall(VMALL)
    faults = parse_faults("FLT 4 15 0x1000 0x1abc\n"      # store to shared read-only data -> cow
                          "FLT 3 15 0x5000 0x1de0\n"      # unmapped, below sz 0x6000 -> lazy
                          "FLT 3 13 0x40000000 0x2100\n")  # far out of range -> illegal
    classify_faults(faults, procs)
    assert [f.kind for f in faults] == ["cow-write", "lazy-alloc", "illegal"]


def test_demovm_cow_demo_shares_then_privatises():
    d = DemoVm()
    procs = d.all_procs()
    assert shared_frames(procs)[0x87002000] == [3, 4]      # parent+child share the data page
    d.simulate_cow_write()
    procs2 = d.all_procs()
    assert 0x87002000 not in shared_frames(procs2)         # child copied it -> no longer shared
    child_data = next(p for p in procs2[4].leaves if p.va == 0x1000)
    assert child_data.writable and not child_data.cow      # now private + writable


# -- S2: the vm shadow (page-fault handling) + the A/D bits it needs ---------------------------- #
def test_ad_bits_decode():
    V, A, D = 1, 64, 128
    assert ad_str(V) == "··"
    assert ad_str(V | A) == "A·" and accessed(V | A) and not dirty(V | A)
    assert ad_str(V | A | D) == "AD" and dirty(V | D)


def test_parse_vmprint_keeps_the_raw_pte():
    # regression: `flags` was never set, so A/D could not reach the UI at all
    snap = parse_vmprint(
        "page table 0x0000000087f6b000\n"
        "..0: pte 0x0000000021fd9c01 pa 0x0000000087f67000\n"
        ".. ..0: pte 0x0000000021fd9801 pa 0x0000000087f66000\n"
        ".. .. ..0: pte 0x0000000021fd94d7 pa 0x0000000087f65000\n")
    leaf = snap.leaves[0]
    assert leaf.flags == 0x21fd94d7
    assert ad_str(leaf.flags) == "AD"          # this page was read AND written


def test_vmf_telemetry_optional():
    with_vmf = parse_vmprint("page table 0x1\nVMF handled 12 fellthrough 3")
    assert (with_vmf.vmf_handled, with_vmf.vmf_fell) == (12, 3)
    older = parse_vmprint("page table 0x1")    # kernel built before the vm shadow
    assert (older.vmf_handled, older.vmf_fell) == (0, 0)


# -- B1/B4: the numbers the kernel already sent -------------------------------- #
# A full /vm dump: the vm-shadow counters, the page-allocator line, then vmprint's tree. The
# leaves are one exec'd process after two sbrk pages — note the heap sits ABOVE the stack, which
# is what growproc() does in this xv6 and what makes sz-arithmetic the wrong way to find a guard.
VMDUMP = (
    "VMF handled 3 fellthrough 412\n"
    "KA free 32000 total 32768 maxrun 30000 shadow 1\n"
    "page table 0x87f6e000\n"
    " .. .. ..0: pte 0x1b pa 0x87001000\n"     # text   r-xu
    " .. .. ..1: pte 0x17 pa 0x87002000\n"     # data   rw-u
    " .. .. ..2: pte 0x07 pa 0x87003000\n"     # guard  rw--  <- uvmclear cleared U, only here
    " .. .. ..3: pte 0x17 pa 0x87004000\n"     # stack  rw-u
    " .. .. ..4: pte 0x17 pa 0x87005000\n"     # heap   rw-u  (sbrk grew past the stack)
    " .. .. ..5: pte 0x17 pa 0x87006000\n"
)


def test_the_ka_line_fills_the_physical_bar():
    """The bug in one assertion: these numbers arrived, were parsed, and were dropped on the
    floor, so the physical bar read "0 used / 0 free of 0 pages" beside a working frag gauge."""
    vm = parse_vmprint(VMDUMP)
    assert vm.phys.total_pages == 32768 and vm.phys.free_pages == 32000
    assert vm.phys.used_pages == 768
    # and it must agree with the fragmentation gauge, which reads the same line by another route
    assert (vm.total_pages, vm.free_pages) == (vm.phys.total_pages, vm.phys.free_pages)


def test_a_kernel_without_the_allocator_line_reports_no_physical_data():
    """Absent, not zero — the caller has to be able to tell "no allocator" from "no memory"."""
    vm = parse_vmprint("page table 0x87f6e000\n .. .. ..0: pte 0x1b pa 0x87001000\n")
    assert vm.phys.total_pages == 0 and vm.phys.free_pages == 0


def test_the_vm_shadow_counters_survive_to_the_snapshot():
    vm = parse_vmprint(VMDUMP)
    assert (vm.vmf_handled, vm.vmf_fell) == (3, 412)


# -- B3: regions derived from the leaves --------------------------------------- #
def test_regions_are_derived_from_the_leaves():
    vm = parse_vmprint(VMDUMP)
    regs = {r.name: r for r in regions_from_leaves(vm.leaves)}
    assert set(regs) == {"text", "data", "guard", "stack", "heap"}
    assert (regs["text"].start, regs["text"].end) == (0x0, 0x0FFF)
    assert (regs["data"].start, regs["data"].end) == (0x1000, 0x1FFF)
    assert (regs["guard"].start, regs["guard"].end) == (0x2000, 0x2FFF)
    assert (regs["stack"].start, regs["stack"].end) == (0x3000, 0x3FFF)


def test_the_heap_is_found_ABOVE_the_stack():
    """The correction that made this derivation worth writing.

    growproc() grows p->sz upward, so sbrk pages land above the stack — not between data and it,
    as a conventional Unix layout would put them. A derivation that located the guard at
    `sz - (USERSTACK+1)*PGSIZE` would be right only until the first sbrk, i.e. it would break
    exactly when a student runs `alloc` and watches the heap grow, which is the case the panel
    exists for.
    """
    regs = {r.name: r for r in regions_from_leaves(parse_vmprint(VMDUMP).leaves)}
    assert regs["heap"].start > regs["stack"].end, "the heap grows up, past the stack"
    assert (regs["heap"].start, regs["heap"].end) == (0x4000, 0x5FFF)


def test_the_guard_page_is_found_by_its_cleared_user_bit():
    """uvmclear clears ONLY PTE_U, so the guard is the one leaf below TRAPFRAME that is valid,
    writable and not user. Give every page the U bit and there is no guard to find."""
    leaves = parse_vmprint(VMDUMP.replace("pte 0x07", "pte 0x17")).leaves
    names = [r.name for r in regions_from_leaves(leaves)]
    assert "guard" not in names and "stack" not in names


def test_a_page_table_with_no_guard_invents_no_stack():
    """A truncated dump used to be enough to manufacture a guard and a stack out of one leaf.
    Better a blank strip than a confident wrong map."""
    assert regions_from_leaves([Pte(va=0, pa=0x87001000, perms=perms(0x0b), flags=0x0b)]) == []


def test_the_kernel_pages_are_named_when_they_are_mapped():
    leaves = [Pte(va=0, pa=0x87001000, perms=perms(0x1b), flags=0x1b),
              Pte(va=0x1000, pa=0x87002000, perms=perms(0x07), flags=0x07),
              Pte(va=0x2000, pa=0x87003000, perms=perms(0x17), flags=0x17),
              Pte(va=TRAPFRAME, pa=0x87f66000, perms=perms(0x07), flags=0x07),
              Pte(va=TRAMPOLINE, pa=0x87f6f000, perms=perms(0x0b), flags=0x0b)]
    regs = regions_from_leaves(leaves)
    assert [r.name for r in regs][-2:] == ["trapframe", "trampoline"]
    assert region_for(TRAPFRAME, regs) == "trapframe"


def test_a_region_spans_an_inclusive_number_of_pages():
    """`end` is the region's LAST byte, so a two-page region is start .. start+2*PGSIZE-1. The
    old arithmetic dropped a page from every multi-page region — invisible while the only
    regions in existence were the demo's single-page ones."""
    assert Region("heap", 0x4000, 0x5FFF).pages == 2
    assert Region("text", 0x0, 0x0FFF).pages == 1
    assert Region("tf", TRAPFRAME, TRAPFRAME).pages == 1        # the demo's zero-width form


def test_the_demo_can_draw_its_own_fragmentation_gauge():
    """A demo that says "rebuild the xv6 image" is giving advice that means nothing in a mode
    whose whole point is that there is no image."""
    vm = DemoVm().snapshot()
    assert "frag" in vm.have and vm.total_pages and vm.max_free_run
