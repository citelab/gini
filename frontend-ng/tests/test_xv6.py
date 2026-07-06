"""xv6 state parsing + scheduling timeline (the Machine Lab's read side)."""
from gini.domain.xv6 import (
    Snapshot, SchedTimeline, parse_backtrace, parse_procdump, parse_registers, running_pid,
)

PROCDUMP = """
1 sleep  init
2 sleep  sh
3 run    spin
4 runble spin
5 unused
"""

REGS = """
ra             0x80001d3c  0x80001d3c
sp             0x3fffff9e00  0x3fffff9e00
pc             0x80001d4a  0x80001d4a
satp           0x8000000000087fff
a7             0x7  7
"""

BT = """
#0  0x0000000080001d4a in scheduler () at kernel/proc.c:451
#1  0x0000000080001abc in main () at kernel/main.c:44
"""


def test_parse_procdump_active_only():
    ps = parse_procdump(PROCDUMP)
    assert [(p.pid, p.state, p.name) for p in ps] == [
        (1, "sleeping", "init"), (2, "sleeping", "sh"),
        (3, "running", "spin"), (4, "runnable", "spin")]      # unused dropped
    assert running_pid(ps) == 3


def test_parse_registers_keeps_key_regs():
    cpu = parse_registers(REGS)
    assert cpu.key("pc") == "0x80001d4a"
    assert cpu.key("satp") == "0x8000000000087fff"
    assert cpu.key("sp").startswith("0x")


def test_parse_backtrace():
    fr = parse_backtrace(BT)
    assert [f.fn for f in fr] == ["scheduler", "main"]
    assert fr[0].loc == "kernel/proc.c:451"


def test_parse_cpu_lines():
    from gini.domain.xv6 import parse_cpu_lines
    txt = "1 sleep init\nSCHED policy 0 quantum 3\nCPU 0 pid 5\nCPU 1 pid 6\n"
    assert parse_cpu_lines(txt) == {0: 5, 1: 6}
    assert parse_cpu_lines("1 sleep init\n") == {}      # single-CPU -> no CPU lines


def test_timeline_records_context_switches():
    tl = SchedTimeline()
    # tick 10: spin(3) running; tick 11: still 3; tick 12: switched to 4
    tl.add(Snapshot(procs=parse_procdump("3 run spin\n4 runble spin"), running_pid=3, ticks=10))
    tl.add(Snapshot(procs=parse_procdump("3 run spin\n4 runble spin"), running_pid=3, ticks=11))
    tl.add(Snapshot(procs=parse_procdump("3 runble spin\n4 run spin"), running_pid=4, ticks=12))
    pids = [s.pid for s in tl.recent()]
    assert pids == [3, 3, 4]                 # one advance without switch, then a switch
    assert tl.switches() == 1
