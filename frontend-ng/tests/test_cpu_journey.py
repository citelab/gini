"""CPU-journey stage model — the trap vs context-switch difference, as data (pure, no Qt)."""
from gini.domain.cpu_journey import JOURNEY_TITLES, JOURNEYS


def test_syscall_is_a_trap_same_process_trapframe():
    sc = JOURNEYS["syscall"]
    assert sc[0].band == "user" and sc[-1].band == "user"     # user -> kernel -> user
    assert all(s.lane == "A" for s in sc)                     # SAME process throughout
    saves = [s.save for s in sc]
    assert saves.count("trapframe") == 2                      # saved on entry, restored on exit
    assert "context" not in saves                             # a syscall never touches the context


def test_context_switch_is_swtch_different_process_context():
    cx = JOURNEYS["context"]
    assert all(s.band == "kernel" for s in cx)               # never leaves supervisor mode
    saves = [s.save for s in cx]
    assert "context" in saves and "trapframe" not in saves   # touches context, not trapframe
    assert {s.lane for s in cx} == {"A", "sched", "B"}       # A -> scheduler -> B


def test_preemption_is_both():
    saves = [s.save for s in JOURNEYS["preempt"]]
    assert "trapframe" in saves and "context" in saves        # a trap wrapping a context switch


def test_every_mode_has_a_title():
    assert set(JOURNEY_TITLES) == set(JOURNEYS)
    assert all(JOURNEYS[k] for k in JOURNEYS)                 # non-empty stage lists
