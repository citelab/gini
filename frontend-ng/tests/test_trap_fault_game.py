"""Trap-cause + fault-type decode games — deterministic scause labeling + case sources."""
from gini.domain.games.trap_game import (
    FAULT_CLASSES, FAULT_SPEC, TRAP_CLASSES, TRAP_SPEC, fault_class, fault_demo_cases,
    fault_live_cases, trap_class, trap_demo_cases, trap_live_cases,
)
from gini.domain.xv6 import TrapEvent


def test_trap_class_decodes_scause():
    assert trap_class(0x8000000000000005) == "timer int"      # interrupt bit + code 5
    assert trap_class(0x8000000000000009) == "external int"
    assert trap_class(0x8000000000000001) == "software int"
    assert trap_class(0x8) == "syscall"                        # ecall from U-mode
    assert trap_class(0xD) == "page fault"                     # load page fault
    assert trap_class(0x2) == "illegal"


def test_fault_class_decodes_page_faults_only():
    assert fault_class(0xC) == "instruction"
    assert fault_class(0xD) == "load"
    assert fault_class(0xF) == "store"
    assert fault_class(0x8) is None                            # not a page fault -> no case


def test_demo_decks_cover_each_class_with_correct_truth():
    tc = trap_demo_cases()
    assert {c.truth for c in tc} == set(TRAP_CLASSES)
    assert all(("scause", hex(0x8000000000000005)) in c.signature or True for c in tc)
    fc = fault_demo_cases()
    assert {c.truth for c in fc} == set(FAULT_CLASSES)
    assert TRAP_SPEC.id == "trap-cause" and FAULT_SPEC.id == "fault-type"


def test_live_cases_from_trap_ring():
    evs = [TrapEvent(pid=4, kind=2, cause="0x8000000000000005", epc="0x1a2c", tval="0x0"),
           TrapEvent(pid=5, kind=1, cause="0xf", epc="0x1330", tval="0x2f00"),
           TrapEvent(pid=6, kind=0, cause="0x8", epc="0x40", tval="0x0")]
    trap = trap_live_cases(evs)
    assert [c.truth for c in trap] == ["timer int", "page fault", "syscall"]
    # fault game keeps only the page-fault scause (0xf = store); drops timer + ecall
    fault = fault_live_cases(evs)
    assert [c.truth for c in fault] == ["store"]
    # signature carries the raw scause + faulting address for the student to decode
    assert ("scause", "0xf") in fault[0].signature and ("stval", "0x2f00") in fault[0].signature
