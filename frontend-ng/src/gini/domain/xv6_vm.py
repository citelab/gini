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


@dataclass
class Pte:
    va: int
    pa: int
    perms: str = "----"
    valid: bool = True


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


@dataclass
class VmSnapshot:
    satp: int = 0
    leaves: list = field(default_factory=list)     # [Pte]
    regions: list = field(default_factory=list)    # [Region]
    phys: PhysMem = field(default_factory=PhysMem)
    faults: list = field(default_factory=list)     # [Fault]


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
            leaves.append(Pte(va=va, pa=pa, perms=perms(pte), valid=bool(pte & 1)))
    return VmSnapshot(satp=satp, leaves=leaves)


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
        self.faults: list = []
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
                          phys=self.phys, faults=list(self.faults))

    def simulate_fault(self) -> VmSnapshot:
        """Grow the stack by one page on a fault (lazy/demand allocation): record the fault,
        allocate a physical page, and add the new leaf mapping."""
        new_va = 0x4000 + len(self.faults) * PGSIZE + PGSIZE
        self.faults.append(Fault(new_va, "store page fault (stack growth)", pid=3))
        if self.phys.free_pages > 0:
            self.phys.free_pages -= 1
            self._sp = new_va + PGSIZE
            self._leaves.insert(5, Pte(new_va, 0x87f60000 - len(self.faults) * PGSIZE, "rw-u"))
        return self.snapshot()
