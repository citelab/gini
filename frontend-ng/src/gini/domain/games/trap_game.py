"""Case sources for two "decode the cause CSR" games — both grade against a deterministic scause
decode (the RISC-V privileged encoding), so ground truth is exact.

  trap-cause  — any trap's scause -> timer / external / software int, syscall, page fault, illegal.
  fault-type  — a page fault's scause -> instruction / load / store access.

The signature is the raw scause (+ stval/epc as context); the skill is knowing the encoding. Live
cases come from the trap-taxonomy ring; the demo deck synthesizes one of each.
"""
from __future__ import annotations

from ..diagnose import Case, GameSpec

# ---- trap-cause game ------------------------------------------------------ #
TRAP_CLASSES = ["timer int", "external int", "software int", "syscall", "page fault", "illegal"]
TRAP_ABBR = {"timer int": "timer", "external int": "extern", "software int": "soft",
             "syscall": "ecall", "page fault": "fault", "illegal": "illegal"}
TRAP_SPEC = GameSpec("trap-cause", "Decode the trap", "What kind of trap is this scause?",
                     TRAP_CLASSES, TRAP_ABBR)

_INT = {1: "software int", 5: "timer int", 9: "external int"}
_EXC = {8: "syscall", 12: "page fault", 13: "page fault", 15: "page fault", 2: "illegal"}


def trap_class(scause: int) -> str:
    code = scause & 0xFF
    if scause >> 63:                       # top bit set -> interrupt
        return _INT.get(code, "external int")
    return _EXC.get(code, "illegal")


# ---- fault-type game ------------------------------------------------------ #
FAULT_CLASSES = ["instruction", "load", "store"]
FAULT_ABBR = {"instruction": "insn", "load": "load", "store": "store"}
FAULT_SPEC = GameSpec("fault-type", "Decode the page fault", "Which access caused this page fault?",
                      FAULT_CLASSES, FAULT_ABBR)

_FAULT = {12: "instruction", 13: "load", 15: "store"}


def fault_class(scause: int) -> str | None:
    return _FAULT.get(scause & 0xFF)


def _event(scause: int, stval: str = "0x0", epc: str = "0x0"):
    return [("scause", hex(scause)), ("stval", stval), ("epc", epc)]


# demo scause values, one per class
_TRAP_DEMO = [(0x8000000000000005, "timer int"), (0x8000000000000009, "external int"),
              (0x8000000000000001, "software int"), (0x8, "syscall"),
              (0xD, "page fault"), (0x2, "illegal")]
_FAULT_DEMO = [(0xC, "instruction"), (0xD, "load"), (0xF, "store")]


def trap_demo_cases() -> list:
    return [Case(f"trap-{i}", _event(sc, epc="0x1a2c"), truth, subtitle=truth)
            for i, (sc, truth) in enumerate(_TRAP_DEMO)]


def fault_demo_cases() -> list:
    vas = ["0x1330", "0x2f00", "0x3ff8"]
    return [Case(f"flt-{i}", _event(sc, stval=vas[i % 3], epc="0x1a2c"), truth, subtitle=truth)
            for i, (sc, truth) in enumerate(_FAULT_DEMO)]


def _live(trap_events, labeler) -> list:
    """Build cases from trap-ring TrapEvents (pid, kind, cause, epc, tval) whose scause labels."""
    out = []
    for e in trap_events or []:
        try:
            sc = int(e.cause, 16)
        except (TypeError, ValueError):
            continue
        truth = labeler(sc)
        if truth:
            out.append(Case(f"live-{e.epc}-{sc}", _event(sc, e.tval, e.epc), truth, subtitle=truth))
    return out


def trap_live_cases(trap_events) -> list:
    return _live(trap_events, trap_class)


def fault_live_cases(trap_events) -> list:
    return _live(trap_events, fault_class)
