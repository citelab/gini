"""Address-translation game — the one that makes live page tables playable.

Given a page table (leaf VA→PA mappings) and a target virtual address, the student computes the
physical address: PA = (PA base of the page holding VA) + (VA − VA base). Exact answer (tolerance 0),
graded against the real mapping. Live cases come straight from the kernel's `vmprint` leaves; the
demo deck synthesizes small tables.
"""
from __future__ import annotations

import random

from ..diagnose import Case, GameSpec

PGSIZE = 4096

TRANSLATE_SPEC = GameSpec(
    "addr-translate", "Translate the address",
    "Compute the physical address (PA) for the target VA — enter hex (0x…) or decimal.",
    classes=[], answer="estimate", tolerance=0, unit="PA",
)


def _case(rows, va, cid) -> Case | None:
    """rows = [(va_base, pa_base, perms)]; find the page holding `va` and compute its PA."""
    for vb, pb, _perm in rows:
        if vb <= va < vb + PGSIZE:
            pa = pb + (va - vb)
            return Case(cid, {"rows": rows, "va": va}, pa, subtitle=hex(pa),
                        hint=f"page base VA {hex(vb)} → PA {hex(pb)}, offset {hex(va - vb)}")
    return None


def demo_cases(seed: int = 0) -> list:
    rng = random.Random(seed)
    out = []
    for k in range(5):
        vpns = sorted(rng.sample(range(0, 12), 4))
        rows = [(v * PGSIZE, rng.randrange(0x80, 0x140) * PGSIZE,
                 rng.choice(["r-x u", "rw- u", "r-- u"])) for v in vpns]
        vb, _pb, _ = rng.choice(rows)
        c = _case(rows, vb + rng.randrange(0, PGSIZE), f"tr-{k}")
        if c is not None:
            out.append(c)
    return out


def live_cases(leaves, seed: int = 0, window: int = 6) -> list:
    """Cases from real vmprint leaves ([Pte with .va/.pa/.perms]). Each targets a mapped page and
    shows a small window of the table around it."""
    rows = [(p.va, p.pa, getattr(p, "perms", "----")) for p in (leaves or [])]
    if not rows:
        return []
    rng = random.Random(seed)
    out = []
    for i, (vb, pb, _perm) in enumerate(rows[:5]):
        lo = max(0, i - window // 2)
        c = _case(rows[lo:lo + window], vb + rng.randrange(0, PGSIZE), f"trl-{i}")
        if c is not None:
            out.append(c)
    return out
