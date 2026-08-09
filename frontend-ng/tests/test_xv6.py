"""xv6 state parsing + scheduling timeline (the Machine Lab's read side)."""
from gini.domain.xv6 import (
    Snapshot, SchedTimeline, build_process_tree, parse_backtrace, parse_procdump,
    parse_registers, running_pid,
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


def test_parse_procdump_captures_ppid():
    procs = parse_procdump("1 sleep init 0\n2 sleep sh 1\n5 run busy 2\n")
    assert [(p.pid, p.parent) for p in procs] == [(1, 0), (2, 1), (5, 2)]
    # stock procdump (no ppid column) still parses, parent left None
    assert parse_procdump("3 run spin")[0].parent is None


def test_build_process_tree_orders_by_parent():
    procs = parse_procdump("1 sleep init 0\n2 sleep sh 1\n4 run busy 2\n5 runble busy 2\n"
                           "7 zombie hello 2\n")
    tree = build_process_tree(procs)
    assert [(n.proc.pid, n.depth) for n in tree] == [
        (1, 0), (2, 1), (4, 2), (5, 2), (7, 2)]           # init > sh > {busy,busy,zombie}


def test_build_process_tree_survives_a_cycle():
    # a bad read where two procs point at each other must not hang or drop anyone
    procs = parse_procdump("8 run a 9\n9 run b 8\n")
    tree = build_process_tree(procs)
    assert {n.proc.pid for n in tree} == {8, 9}


def test_parse_sccounts_and_names():
    from gini.domain.xv6 import parse_sccounts, syscall_name
    counts = parse_sccounts("SC 1 12\nSC 16 340\nSC 23 4\n")
    assert counts == {1: 12, 16: 340, 23: 4}
    assert syscall_name(1) == "fork" and syscall_name(16) == "write" and syscall_name(22) == "sync"
    assert syscall_name(23, {23: "trace"}) == "trace"     # user-defined
    assert syscall_name(99) == "sys99"                    # unknown -> numbered


def test_parse_sctrace():
    from gini.domain.xv6 import parse_sctrace
    evs = parse_sctrace("TRACE 2 1 0x0 0x7\nTRACE 7 16 0x4 0x6\n")
    assert [(e.pid, e.num, e.ret) for e in evs] == [(2, 1, "0x7"), (7, 16, "0x6")]


def test_syscall_rate_60s_window():
    from gini.domain.xv6 import SyscallRate
    r = SyscallRate(window=60.0)
    r.add(0.0, {1: 0, 16: 0})
    r.add(5.0, {1: 2, 16: 40})
    r.add(20.0, {1: 10, 16: 120})
    r.add(70.0, {1: 30, 16: 400})     # now=70, cutoff=10 -> baseline is the t=5 snapshot
    rates = r.rates()
    assert rates[1] == 28 and rates[16] == 360    # 30-2, 400-40
    # a syscall with no calls in the window is dropped
    r.add(75.0, {1: 30, 16: 400})
    assert all(v > 0 for v in r.rates().values())


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
