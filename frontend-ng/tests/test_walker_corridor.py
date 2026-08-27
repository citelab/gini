"""walker generates MACHINE CODE at runtime, so its constants are unreviewable by eye.

The program builds a corridor of NOP instructions in executable memory and jumps into it, so the
program counter itself walks a large span of addresses. Four hex constants in walker.c ARE that
corridor. If one of them drifts, nothing fails loudly: the corridor still gets built, still gets
jumped into, and either executes garbage or hangs the machine.

These tests decode the constants back to instructions arithmetically -- the same decode a
disassembler does -- so a typo cannot survive. The encodings were checked against binutils
(riscv64 objdump) when the program was written; this keeps them pinned.

The killer case is the lui immediate. `lui` SIGN-EXTENDS bit 31, so an immediate of 0x80000 or
more loads a NEGATIVE loop counter, and counting down from it takes about 2^64 iterations -- a
hang indistinguishable from a crashed machine. walker clamps to keep that impossible, and
test_no_iteration_count_can_produce_a_negative_counter is the guard.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PATCH = ROOT / "backend" / "xv6" / "gini_patch.py"


def _walker_src() -> str:
    if not PATCH.exists():
        pytest.skip("gini_patch.py not present")
    s = PATCH.read_text()
    blk = s[s.index("_UPROGS = {"):s.index("\ndef add_uprog")]
    m = re.search(r'^    "walker": """(.*?)"""', blk, re.S | re.M)
    if not m:
        pytest.fail("walker entry not found in _UPROGS")
    return m.group(1)


def _const(name: str) -> int:
    m = re.search(r"^#define %s\s+(0x[0-9A-Fa-f]+)u?\b" % name, _walker_src(), re.M)
    if not m:
        pytest.fail(f"{name} not defined in walker.c")
    return int(m.group(1), 16)


# -- the four instruction words decode to what walker claims they are ------- #

def test_nop_is_addi_zero_zero_zero():
    w = _const("NOP")
    assert w & 0x7F == 0x13, "not an OP-IMM instruction"
    assert (w >> 7) & 0x1F == 0, "rd is not x0 -- this would clobber a register per NOP"
    assert (w >> 15) & 0x1F == 0 and (w >> 12) & 7 == 0 and (w >> 20) == 0


def test_ret_is_jalr_zero_ra():
    w = _const("RET")
    assert w & 0x7F == 0x67, "not a JALR"
    assert (w >> 7) & 0x1F == 0, "rd must be x0, or ret would overwrite a register"
    assert (w >> 15) & 0x1F == 1, "must jump through ra (x1) -- the corridor's way home"
    assert (w >> 20) == 0, "offset must be 0"


def test_the_decrement_is_addi_t0_t0_minus_one():
    w = _const("ADDI_M1")
    imm = w >> 20
    imm = imm - 0x1000 if imm & 0x800 else imm            # sign-extend the 12-bit field
    assert w & 0x7F == 0x13 and (w >> 12) & 7 == 0
    assert (w >> 7) & 0x1F == 5 and (w >> 15) & 0x1F == 5, "must read and write t0 (x5)"
    assert imm == -1, f"the loop counter changes by {imm}, not -1"


def test_the_branch_goes_back_exactly_one_instruction():
    """A B-type immediate is scattered across the word. If this offset is wrong the loop either
    never terminates or falls straight through, and the dwell silently vanishes."""
    w = _const("BNE_BK")
    assert w & 0x7F == 0x63, "not a BRANCH"
    assert (w >> 12) & 7 == 1, "funct3 must be BNE"
    assert (w >> 15) & 0x1F == 5 and (w >> 20) & 0x1F == 0, "must compare t0 against x0"
    off = (((w >> 31) & 1) << 12 | ((w >> 7) & 1) << 11 |
           ((w >> 25) & 0x3F) << 5 | ((w >> 8) & 0xF) << 1)
    if off & 0x1000:
        off -= 0x2000
    assert off == -4, f"branch target is {off} bytes away, not -4 (the addi)"


# -- the hang guard -------------------------------------------------------- #

def test_no_iteration_count_can_produce_a_negative_counter():
    """lui sign-extends bit 31. A negative counter counts AWAY from zero: ~2^64 iterations."""
    cap = _const("IMM20_MAX")
    assert cap < 0x80000, "IMM20_MAX allows a negative loop counter -- walker would hang"
    for imm in (0, 1, cap, cap + 1, 0x80000, 0xFFFFF, 0x7FFFFFFF):
        clamped = min(max(imm, 1), cap)
        word = (clamped << 12) | (5 << 7) | 0x37
        assert word & 0x80000000 == 0, f"imm {imm:#x} yields a sign-extended (negative) count"
        assert word >> 12 != 0, "a zero count means 2^64 iterations, not zero"


def test_walker_clamps_rather_than_masking():
    """`imm & 0xFFFFF` would WRAP a too-large count to a small one, so a long dwell would come
    back as a short one with nothing to show for it. It has to clamp."""
    src = _walker_src()
    body = src[src.index("lui_t0(uint32 imm20)"):]
    body = body[:body.index("\n}")]
    assert "IMM20_MAX" in body and ">" in body, "lui_t0 does not clamp against IMM20_MAX"
    assert "& 0xFFFFF" not in body, "masking silently wraps an over-large count"


# -- the pieces the corridor cannot work without --------------------------- #

def test_the_corridor_is_page_aligned_before_use():
    """sbrk returns the process's OLD size, page-aligned only by luck, while uvmalloc starts
    mapping at PGROUNDUP of it -- so an unaligned base would put the corridor's first bytes in
    the previous page, which has no PTE_X and faults on the first instruction."""
    src = _walker_src()
    assert "PGSZ - 1) & ~" in src, "walker does not round its corridor base up to a page"
    assert "pages + 1" in src, "walker must over-allocate a page to have room to round up"


def test_walker_asks_for_executable_memory():
    src = _walker_src()
    assert "sbrkexec" in src, "walker uses ordinary sbrk; the corridor would not be executable"
    # Comments legitimately mention sbrk() when explaining why it is not enough, so only look at
    # lines that are actually code.
    code = [l for l in src.splitlines() if "//" not in l]
    plain = [l for l in code if re.search(r"\bsbrk\s*\(", l)]
    assert not plain, f"plain sbrk gives no PTE_X: {plain}"


def test_the_icache_is_synced_before_the_generated_code_runs():
    """Instructions written as data are not guaranteed visible to instruction fetch until stale
    copies are discarded."""
    assert "fence.i" in _walker_src(), "generated code is executed without fence.i"


def test_the_delay_is_inlined_not_called():
    """A shared delay routine would hold the PC at ITS address (so the corridor would look like
    one hot spot, exactly like spin), and `call` would clobber ra -- which is the register the
    corridor's closing `ret` needs."""
    src = _walker_src()
    body = src[src.index("fill_page"):]
    body = body[:body.index("\n}")]
    assert "ADDI_M1" in body and "BNE_BK" in body, "fill_page no longer writes the delay inline"
    assert "0x67" not in body and "RET" not in body, "fill_page emits a call/return, not a dwell"


# -- features whose ABSENCE is silent --------------------------------------- #
#
# These exist because the corridor was once shipped from a stale copy: the encodings were all
# correct, so every test above passed, while the multi-block dwell and the self-timing had been
# left behind in a scratch file. Nothing failed -- walker just quietly walked faster than asked.
# Each check below covers a feature that goes wrong without complaining.

def test_a_long_dwell_is_not_silently_shortened():
    """One lui tops out near 2.1 billion iterations, only a few ticks on a fast host. Without
    several blocks per page, asking for a longer dwell used to clamp and say nothing."""
    src = _walker_src()
    assert re.search(r"^#define MAXBLK\s+\d+", src, re.M), "no per-page block limit is defined"
    assert "blocks_for" in src, "walker cannot tell whether the requested dwell fits"
    # fill_page must actually emit a VARIABLE number of blocks, not one.
    fp = src[src.index("fill_page(uint32 *w"):]
    fp = fp[:fp.index("\n}")]
    assert re.search(r"for\(int b = 0; b < nblk", fp), "fill_page emits a single delay block"
    assert "3*b" in fp, "the blocks are not laid out consecutively"
    body = src[src.index("if(blocks_for("):]
    assert "printf" in body[:400], "an unachievable dwell is not reported to the student"


def test_walker_times_its_own_walk():
    """pages x dwell is a prediction the student can check -- but only if walker measures it."""
    src = _walker_src()
    assert "walked in %d ticks" in src, "walker never reports how long the walk took"
    assert "expected ~%d" in src, "walker reports a time with nothing to compare it against"


def test_walker_keeps_lapping_by_default():
    """A single pass is over in well under a minute, which is shorter than it takes to set up an
    observation. Default has to be 'until killed', as spin is."""
    src = _walker_src()
    assert "laps" in src, "no lap control"
    assert "laps == 0" in src, "walker exits after one pass instead of lapping"
    m = re.search(r"int laps\s+= \(argc > 3\) \? atoi\(argv\[3\]\) : (\d+)", src)
    assert m and m.group(1) == "0", "the default is a finite number of laps, not 'until killed'"


def test_kernel_side_support_is_present():
    """The corridor cannot run without an executable-heap path in the kernel."""
    s = PATCH.read_text()
    assert "SBRK_EXEC" in s, "no SBRK_EXEC flag"
    assert "growproc_exec" in s, "no executable growth path"
    assert "PTE_W | PTE_X" in s, "growproc_exec does not map pages executable"
