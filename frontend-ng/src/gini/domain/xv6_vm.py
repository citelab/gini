"""xv6 virtual-memory state model — the Memory face's read side.

xv6 on RISC-V uses Sv39 three-level page tables: `satp` points at the root, and a 39-bit virtual
address is split into three 9-bit indices (levels 2/1/0) plus a 12-bit offset. A leaf PTE carries
a physical page number and permission flags V/R/W/X/U. This module models the flattened set of
leaf mappings, the process address-space regions (text/data/heap/guard/stack/trapframe/trampoline),
the physical page allocator, and page faults — with a parser for xv6's own `vmprint()` output, so
the VA reconstruction, the flag decoding, and the DemoVm feed are all unit-tested without QEMU.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

PGSIZE = 4096
MAXVA = 1 << 38
TRAMPOLINE = MAXVA - PGSIZE            # 0x3FFFFFF000
TRAPFRAME = TRAMPOLINE - PGSIZE        # 0x3FFFFFE000

# PTE flag bits (RISC-V)
_FLAGS = (("V", 1), ("R", 2), ("W", 4), ("X", 8), ("U", 16), ("G", 32), ("A", 64), ("D", 128))


def perms(pte: int) -> str:
    """The R/W/X/U permission string of a PTE value (e.g. 'rwx-' , 'r-x u')."""
    out = ""
    for ch, bit in (("r", 2), ("w", 4), ("x", 8), ("u", 16)):
        out += ch if (pte & bit) else "-"
    return out


def ad_str(flags: int) -> str:
    """The ACCESSED and DIRTY bits — 'A' / 'D' when set, '·' when clear.

    These are the two bits page-replacement policies are built on: the hardware sets A when a page
    is read or written and D when it is written, and a clock/second-chance algorithm sweeps them
    and clears A. They ride in every PTE we already parse but were never surfaced.
    """
    return ("A" if flags & 64 else "·") + ("D" if flags & 128 else "·")


def accessed(flags: int) -> bool:
    return bool(flags & 64)


def dirty(flags: int) -> bool:
    return bool(flags & 128)


def rsw_bits(flags: int) -> int:
    """The two reserved-for-software bits (8,9) of a PTE. xv6's COW lab parks its COW flag here,
    so a non-zero RSW on a user page is our (design-agnostic) 'this page is marked by the student'
    signal — we badge it without needing to know which bit they chose."""
    return (flags >> 8) & 0x3


@dataclass
class Pte:
    va: int
    pa: int
    perms: str = "----"
    valid: bool = True
    flags: int = 0                 # raw low-10 PTE bits (V R W X U G A D + RSW8/9); 0 if unknown

    @property
    def writable(self) -> bool:
        return "w" in self.perms

    @property
    def user(self) -> bool:
        return "u" in self.perms

    @property
    def rsw(self) -> int:
        return rsw_bits(self.flags)

    @property
    def cow(self) -> bool:
        """A COW candidate: a user page that is present, NOT writable, and carries an RSW mark —
        the exact shape xv6's COW fork leaves behind (shared, write-protected, COW-tagged)."""
        return self.user and not self.writable and self.rsw != 0


@dataclass
class Region:
    name: str
    start: int
    end: int
    perms: str = ""

    @property
    def pages(self) -> int:
        return max((self.end - self.start) // PGSIZE, 1)


@dataclass
class PhysMem:
    total_pages: int = 0
    free_pages: int = 0

    @property
    def used_pages(self) -> int:
        return max(self.total_pages - self.free_pages, 0)

    @property
    def used_frac(self) -> float:
        return (self.used_pages / self.total_pages) if self.total_pages else 0.0


@dataclass
class Fault:
    va: int
    cause: str
    pid: int | None = None


# scause codes for the three page-fault kinds (RISC-V privileged spec)
SCAUSE_NAMES = {12: "instruction page fault", 13: "load page fault", 15: "store page fault"}


@dataclass
class PageFault:
    """One captured page fault from the kernel's live fault ring (gini_faultdump)."""
    pid: int
    scause: int
    va: int
    epc: int
    kind: str = ""                 # filled by classify_faults: lazy / cow-write / stack / illegal

    @property
    def cause(self) -> str:
        return SCAUSE_NAMES.get(self.scause, f"scause {self.scause}")


@dataclass
class ProcVm:
    """One process's address space from the all-procs dump (gini_vmdump_all)."""
    pid: int
    name: str = ""
    sz: int = 0                    # p->sz — the *virtual* size (top of the heap/brk)
    leaves: list = field(default_factory=list)   # [Pte]

    @property
    def resident_pages(self) -> int:
        """User pages actually backed by physical frames (excludes kernel trampoline/trapframe)."""
        return sum(1 for p in self.leaves if p.user)

    @property
    def virtual_pages(self) -> int:
        return max((self.sz + PGSIZE - 1) // PGSIZE, 0)


@dataclass
class VmSnapshot:
    satp: int = 0
    leaves: list = field(default_factory=list)     # [Pte]
    regions: list = field(default_factory=list)    # [Region]
    phys: PhysMem = field(default_factory=PhysMem)
    faults: list = field(default_factory=list)     # [Fault]
    # Provenance (see FsSnapshot): "real" from the kernel vs "demo" stand-in; ok=False means a
    # real read yielded nothing (show an error, don't fake). Page-table leaves + faults are
    # dumped for real; the region map + physical allocator bar are not yet (so not in `have`).
    source: str = "real"
    ok: bool = True
    have: tuple = ("pagetable", "faults")
    # vm-shadow telemetry: faults the student's handler took vs. ones that fell through to the
    # shipped implementation. `handled == 0` while their shadow is enabled means it never answers.
    vmf_handled: int = 0
    vmf_fell: int = 0
    # S3 (page allocator): free pages and the largest CONTIGUOUS free run — the fragmentation
    # score a buddy-allocator mission is graded on
    free_pages: int = 0
    total_pages: int = 0
    max_free_run: int = 0


# -- parser: xv6 vmprint() -------------------------------------------------- #
_ROOT = re.compile(r"page table\s+(0x[0-9a-fA-F]+)")
_LINE = re.compile(r"^((?:\.\.\s*)+)(\d+):\s*pte\s+(0x[0-9a-fA-F]+)\s+pa\s+(0x[0-9a-fA-F]+)")


def parse_vmprint(text: str) -> VmSnapshot:
    """Parse xv6's `vmprint()` tree into leaf mappings. Level depth = number of `..` groups
    (1,2,3 for Sv39 levels 2,1,0); a leaf is depth 3, and its VA is rebuilt from the index path."""
    satp = 0
    path: dict[int, int] = {}
    leaves: list[Pte] = []
    for line in (text or "").splitlines():
        s = line.strip()
        mr = _ROOT.search(s)
        if mr and not leaves and satp == 0:
            satp = int(mr.group(1), 16)
            continue
        m = _LINE.match(s)
        if not m:
            continue
        depth = m.group(1).count("..")
        idx = int(m.group(2))
        pte = int(m.group(3), 16)
        pa = int(m.group(4), 16)
        path[depth] = idx
        if depth == 3:                             # leaf (level 0)
            va = (path.get(1, 0) << 30) | (path.get(2, 0) << 21) | (idx << 12)
            # keep the RAW pte: `perms` only decodes r/w/x/u, but A (accessed) and D (dirty) —
            # the bits every page-replacement algorithm runs on — live in the same word, and
            # were being dropped here, so nothing downstream could ever show them.
            leaves.append(Pte(va=va, pa=pa, perms=perms(pte), valid=bool(pte & 1), flags=pte))
    return VmSnapshot(satp=satp, leaves=leaves, **_vmf_counts(text))


_VMF_RE = re.compile(r"VMF handled (\d+) fellthrough (\d+)")
_KA_RE = re.compile(r"KA free (\d+) total (\d+) maxrun (\d+) shadow (\d+)")


def _vmf_counts(text: str) -> dict:
    """`VMF handled <n> fellthrough <n>` — how many page faults the student's vm shadow took vs.
    how many fell through to the shipped handler. Absent on older kernels -> zeros."""
    out = {}
    m = _VMF_RE.search(text or "")
    if m:
        out.update(vmf_handled=int(m.group(1)), vmf_fell=int(m.group(2)))
    k = _KA_RE.search(text or "")
    if k:
        out.update(free_pages=int(k.group(1)), total_pages=int(k.group(2)),
                   max_free_run=int(k.group(3)))
    return out


# -- parser: live fault ring (gini_faultdump) ------------------------------- #
# `FLT <pid> <scause> <va_hex> <epc_hex>` — one per captured page fault, oldest first.
_FLT = re.compile(r"^FLT\s+(-?\d+)\s+(\d+)\s+(0x[0-9a-fA-F]+)\s+(0x[0-9a-fA-F]+)")


def parse_faults(text: str) -> list:
    out: list = []
    for line in (text or "").splitlines():
        m = _FLT.match(line.strip())
        if m:
            out.append(PageFault(pid=int(m.group(1)), scause=int(m.group(2)),
                                 va=int(m.group(3), 16), epc=int(m.group(4), 16)))
    return out


# -- parser: all-procs page tables (gini_vmdump_all) ------------------------ #
# `VP <pid> <name> <sz_hex>` header, then `VL <pid> <va_hex> <pa_hex> <flags_int>` per leaf.
_VP = re.compile(r"^VP\s+(\d+)\s+(\S+)\s+(0x[0-9a-fA-F]+)")
_VL = re.compile(r"^VL\s+(\d+)\s+(0x[0-9a-fA-F]+)\s+(0x[0-9a-fA-F]+)\s+(\d+)")


def parse_vmall(text: str) -> dict:
    """Parse every user process's leaf mappings into {pid: ProcVm}. Leaves carry the raw PTE
    flag bits, so permissions AND the student's RSW/COW mark survive to the UI."""
    procs: dict = {}
    for line in (text or "").splitlines():
        s = line.strip()
        mp = _VP.match(s)
        if mp:
            pid = int(mp.group(1))
            procs[pid] = ProcVm(pid=pid, name=mp.group(2), sz=int(mp.group(3), 16))
            continue
        ml = _VL.match(s)
        if ml:
            pid = int(ml.group(1))
            flags = int(ml.group(4))
            pte = Pte(va=int(ml.group(2), 16), pa=int(ml.group(3), 16),
                      perms=perms(flags), valid=bool(flags & 1), flags=flags)
            procs.setdefault(pid, ProcVm(pid=pid)).leaves.append(pte)
    return procs


def shared_frames(vmall: dict) -> dict:
    """Physical pages mapped by MORE THAN ONE process — i.e. sharing, derived directly from the
    page tables (no kernel refcount needed). Restricted to USER pages so the trampoline/trapframe
    kernel mappings (shared by everyone by construction) don't drown out the COW signal.

    Returns {pa: sorted [pids]} for every physically-shared user frame — this is what makes
    'parent and child share pages until one writes' visible: watch a pa's pid-list shrink from
    [parent, child] to [parent] on the first write."""
    owners: dict = {}
    for pid, pv in vmall.items():
        for pte in pv.leaves:
            if pte.user and pte.valid:
                owners.setdefault(pte.pa, set()).add(pid)
    return {pa: sorted(pids) for pa, pids in owners.items() if len(pids) > 1}


def classify_faults(faults: list, vmall: dict | None = None,
                    regions: list | None = None) -> list:
    """Best-effort label for each fault, using whatever address-space picture we have at read
    time. Heuristic (the kernel stays dumb, the classification is improvable pure Python):

      • store to a present, non-writable user page   -> cow-write (the write that triggers a copy)
      • fault on an unmapped VA below p->sz           -> lazy-alloc (demand paging the heap)
      • fault on an unmapped VA inside a valid region -> lazy-alloc / stack-growth
      • otherwise                                     -> illegal (out of any region -> segfault)
    """
    for f in faults:
        pv = (vmall or {}).get(f.pid)
        page_va = f.va & ~(PGSIZE - 1)
        leaf = next((p for p in pv.leaves if p.va == page_va), None) if pv else None
        if leaf is not None and not leaf.writable and f.scause == 15:
            f.kind = "cow-write"
        elif leaf is None and pv is not None and page_va < pv.sz:
            f.kind = "lazy-alloc"
        elif leaf is None and regions and region_for(f.va, regions) not in ("?",):
            f.kind = "stack-growth" if region_for(f.va, regions) == "stack" else "lazy-alloc"
        elif leaf is None:
            f.kind = "illegal"
        else:
            f.kind = f.cause
    return faults


def memory_summary(vm: VmSnapshot) -> str:
    """Compact, LLM-facing summary of the virtual-memory state (for the Ask GINI card, L2)."""
    if vm is None:
        return ""
    lines = [f"memory (virtual, Sv39): satp={hex(vm.satp)} · {len(vm.leaves)} leaf mappings"]
    if vm.regions:
        lines.append("  regions (low→high VA): "
                     + ", ".join(f"{r.name}[{r.perms}]" for r in vm.regions))
    ph = vm.phys
    if ph and ph.total_pages:
        lines.append(f"  physical pages: {ph.used_pages} used / {ph.free_pages} free of "
                     f"{ph.total_pages} ({ph.used_frac * 100:.0f}% used)")
    if vm.faults:
        lines.append("  recent page faults: "
                     + "; ".join(f"{hex(f.va)} {f.cause}" for f in vm.faults[-3:]))
    return "\n".join(lines)


def region_for(va: int, regions) -> str:
    for r in regions:
        if r.start <= va <= r.end:          # end is the region's last byte (inclusive)
            return r.name
    if va == TRAMPOLINE:
        return "trampoline"
    if va == TRAPFRAME:
        return "trapframe"
    return "?"


# -- offline demo ----------------------------------------------------------- #
class DemoVm:
    """A deterministic small user address space + simulated growth/faults, so the Memory face is
    explorable and testable without QEMU. Faithful-shaped, not a real MMU."""

    def __init__(self) -> None:
        self.satp = 0x8000000000087f6e
        self._sp = 0x4000                          # top of a one-page user stack region
        self.phys = PhysMem(total_pages=32768, free_pages=32510)
        self._fault_log: list = []                 # simulated demand-fault log (NOT the faults() ring)
        self._leaves = [
            Pte(0x0000, 0x87f6b000, "r-x-"),       # text
            Pte(0x1000, 0x87f6a000, "rw--"),       # data
            Pte(0x2000, 0x87f69000, "rw--"),       # bss/heap
            Pte(0x3000, 0x87f68000, "----"),       # guard page (no access)
            Pte(0x4000, 0x87f67000, "rw-u"),       # user stack
            Pte(TRAPFRAME, 0x87f66000, "rw--"),    # trapframe
            Pte(TRAMPOLINE, 0x87f6f000, "r-x-"),   # trampoline (shared)
        ]

    def _regions(self):
        return [
            Region("text", 0x0000, 0x0FFF, "r-x-"),
            Region("data", 0x1000, 0x1FFF, "rw--"),
            Region("heap", 0x2000, 0x2FFF, "rw--"),
            Region("guard", 0x3000, 0x3FFF, "----"),
            Region("stack", 0x4000, self._sp - 1, "rw-u") if self._sp > 0x4000
            else Region("stack", 0x4000, 0x4FFF, "rw-u"),
            Region("trapframe", TRAPFRAME, TRAPFRAME, "rw--"),
            Region("trampoline", TRAMPOLINE, TRAMPOLINE, "r-x-"),
        ]

    def snapshot(self) -> VmSnapshot:
        return VmSnapshot(satp=self.satp, leaves=list(self._leaves), regions=self._regions(),
                          phys=self.phys, faults=list(self._fault_log), source="demo",
                          have=("pagetable", "regions", "phys", "faults"))

    def simulate_fault(self) -> VmSnapshot:
        """Grow the stack by one page on a fault (lazy/demand allocation): record the fault,
        allocate a physical page, and add the new leaf mapping."""
        new_va = 0x4000 + len(self._fault_log) * PGSIZE + PGSIZE
        self._fault_log.append(Fault(new_va, "store page fault (stack growth)", pid=3))
        if self.phys.free_pages > 0:
            self.phys.free_pages -= 1
            self._sp = new_va + PGSIZE
            self._leaves.insert(5, Pte(new_va, 0x87f60000 - len(self._fault_log) * PGSIZE, "rw-u"))
        return self.snapshot()

    # -- COW / all-procs demo (offline) ------------------------------------- #
    _COW = 1 | 2 | 16 | 0x100       # V R U + RSW8 : present, read-only, user, COW-tagged
    _RW = 1 | 2 | 4 | 16            # V R W U       : present, writable, user (private after copy)
    _RX = 1 | 2 | 8 | 16            # V R X U       : text

    def __post_cow(self):
        if not hasattr(self, "_cow_written"):
            self._cow_written = False

    def all_procs(self) -> dict:
        """A parent+child pair right after fork(): they SHARE the data/stack physical pages,
        write-protected and COW-tagged, until `simulate_cow_write()` copies the child's data page.
        This is the observable heart of the COW assignment."""
        self.__post_cow()
        text = Pte(0x0000, 0x87001000, perms(self._RX), True, self._RX)
        # shared, COW-marked pages (same PA in both procs)
        data_shared = Pte(0x1000, 0x87002000, perms(self._COW), True, self._COW)
        stack_shared = Pte(0x3000, 0x87003000, perms(self._COW), True, self._COW)
        parent = ProcVm(3, "forktest", 0x6000, [text, data_shared, stack_shared,
                        Pte(TRAPFRAME, 0x87f10000, perms(self._RW & ~16), True, self._RW & ~16)])
        child_data = (Pte(0x1000, 0x87005000, perms(self._RW), True, self._RW)   # copied -> private
                      if self._cow_written else data_shared)
        child = ProcVm(4, "forktest", 0x6000, [text, child_data, stack_shared,
                       Pte(TRAPFRAME, 0x87f11000, perms(self._RW & ~16), True, self._RW & ~16)])
        return {3: parent, 4: child}

    def simulate_cow_write(self) -> dict:
        """The child writes its shared data page -> the store page-fault copies it: a fresh
        physical frame, now writable and no longer COW-tagged. The shared PA's owner list shrinks
        from [parent, child] to [parent]."""
        self.__post_cow()
        self._cow_written = True
        if self.phys.free_pages > 0:
            self.phys.free_pages -= 1
        return self.all_procs()

    def faults(self) -> list:
        """A representative fault stream: a COW write, a lazy heap fault, and an illegal access."""
        return [
            PageFault(4, 15, 0x1000, 0x0000000000001abc),      # store to the shared data page -> cow
            PageFault(3, 15, 0x5000, 0x0000000000001de0),      # unmapped, below sz -> lazy-alloc
            PageFault(3, 13, 0x40000000, 0x0000000000002100),  # way out of range -> illegal
        ]
