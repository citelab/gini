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


def test_parse_and_apply_proc_sched():
    from gini.domain.xv6 import apply_proc_sched, parse_proc_sched
    dump = ("3 run spin 2\nPROC 3 pri 5 tk 1 lv 0 wait 0\n"
            "4 runble spin 2\nPROC 4 pri 10 tk 2 lv 1 wait 3\n")
    sched = parse_proc_sched(dump)
    assert sched[3] == {"priority": 5, "tickets": 1, "level": 0, "wait_ticks": 0}
    assert sched[4]["tickets"] == 2 and sched[4]["wait_ticks"] == 3
    procs = apply_proc_sched(parse_procdump(dump), dump)
    assert [(p.pid, p.priority, p.tickets, p.level) for p in procs] == [(3, 5, 1, 0), (4, 10, 2, 1)]
    # a build without PROC lines leaves the fields None (no crash)
    assert parse_procdump("3 run spin 2")[0].priority is None


def test_parse_policies_roster():
    from gini.domain.xv6 import parse_policies
    txt = ("SCHED policy 1 quantum 3\nPOLICY 0 round-robin\nPOLICY 1 priority\n"
           "POLICY 2 lottery\nPOLICY 3 sjf\n")
    assert parse_policies(txt) == {0: "round-robin", 1: "priority", 2: "lottery", 3: "sjf"}
    assert parse_policies("no roster here") == {}      # older build -> UI falls back to POLICY_NAMES


def test_policy_names_match_kernel_ids():
    from gini.domain.xv6 import POLICY_IDS, POLICY_NAMES, policy_name
    assert POLICY_NAMES == {0: "round-robin", 1: "priority", 2: "lottery"}
    assert POLICY_IDS["lottery"] == 2 and policy_name(1) == "priority"
    assert policy_name(9) == "policy9"      # a student-added policy id we don't have a name for


def test_demo_priority_dominates_but_aging_prevents_starvation():
    from collections import Counter
    from gini.domain.xv6 import DemoScheduler
    s = DemoScheduler(policy="priority")
    runs = [s.step().running_pid for _ in range(60)]
    c = Counter(runs)
    assert c[3] > c[4] and c[3] > c[5]        # the high-priority proc (pri 5) runs the most
    assert set(runs) == {3, 4, 5}             # but aging eventually lets the others run (no starve)


def test_demo_lottery_is_ticket_weighted():
    from collections import Counter
    from gini.domain.xv6 import DemoScheduler
    s = DemoScheduler(policy="lottery")
    c = Counter(s.step().running_pid for _ in range(400))
    assert c[5] > c[4] > c[3]                  # CPU share tracks tickets (5 has 4, 4 has 2, 3 has 1)


def test_demo_set_policy_accepts_name_or_id():
    from gini.domain.xv6 import DemoScheduler
    s = DemoScheduler()
    assert s.policy == "round-robin"
    s.set_policy("lottery"); assert s.policy == "lottery"
    s.set_policy(1); assert s.policy == "priority"      # numeric id maps to the name


def test_parse_trapcounts_and_kinds():
    from gini.domain.xv6 import parse_trapcounts, trap_kind_name
    counts = parse_trapcounts("TC 0 syscall 12\nTC 2 timer 340\nTC 1 pagefault 4\n")
    assert counts == {0: 12, 2: 340, 1: 4}
    assert trap_kind_name(0) == "syscall" and trap_kind_name(2) == "timer"
    assert trap_kind_name(1) == "pagefault" and trap_kind_name(9) == "kind9"   # unknown -> numbered


def test_parse_traptrace():
    from gini.domain.xv6 import parse_traptrace
    txt = ("TR 5 2 0x8000000000000005 0x0000000000001050 0x0\n"
           "TR 7 1 0x000000000000000f 0x0000000000001080 0x0000000000004000\n")
    evs = parse_traptrace(txt)
    assert [(e.pid, e.kind, e.tval) for e in evs] == [
        (5, 2, "0x0"), (7, 1, "0x0000000000004000")]      # timer (no addr), store fault (addr)
    assert evs[0].cause == "0x8000000000000005"           # interrupt cause keeps the top bit


def test_trap_rate_60s_window():
    from gini.domain.xv6 import TrapRate
    r = TrapRate(window=60.0)
    r.add(0.0, {0: 0, 2: 0})
    r.add(5.0, {0: 2, 2: 40})
    r.add(20.0, {0: 10, 2: 120})
    r.add(70.0, {0: 30, 2: 400})     # now=70, cutoff=10 -> baseline is the t=5 snapshot
    rates = r.rates()
    assert rates[0] == 28 and rates[2] == 360    # 30-2 syscalls, 400-40 timer, in the window


def test_demo_scheduler_traps_grows_a_mix():
    from gini.domain.xv6 import DemoScheduler, parse_trapcounts, parse_traptrace
    sched = DemoScheduler()
    a = parse_trapcounts(sched.traps())
    b = parse_trapcounts(sched.traps())
    assert b[2] > a[2] and b[2] > b[0]                    # timer dominates and keeps growing
    evs = parse_traptrace(sched.traps())
    assert any(e.kind == 2 for e in evs) and any(e.kind == 1 for e in evs)   # timer + a page fault


def test_decode_scause_buckets_and_names():
    from gini.domain.xv6 import decode_scause
    assert decode_scause("0x8") == (0, "ecall from U-mode (syscall)")
    assert decode_scause("0xf")[0] == 1 and "store page fault" in decode_scause("0xf")[1]
    assert decode_scause("0xd") == (1, "load page fault")
    assert decode_scause("0x2") == (4, "illegal instruction")
    assert decode_scause("0x8000000000000005") == (2, "supervisor timer interrupt")
    assert decode_scause("0x8000000000000009")[0] == 3          # external (device)
    assert decode_scause("garbage") == (5, "other")


def test_parse_trapframe_from_gdb_output():
    from gini.domain.xv6 import parse_trapframe
    out = ("Temporary breakpoint 1, usertrap () at kernel/trap.c:37\n"
           "===TRAP===\n"
           "scause 0x000000000000000f\nsepc 0x0000000000001080\nstval 0x0000000000004000\n"
           "pid 5\nepc 0x0000000000001080\nra 0x0000000000001d3c\nsp 0x0000003fffff9000\n"
           "a0 0x0000000000000005\na7 0x000000000000000f\n")
    fr = parse_trapframe(out)
    assert fr.ok and fr.kind == 1 and "store page fault" in fr.kind_name   # scause 15 decoded
    assert fr.pid == 5 and fr.stval == "0x0000000000004000"                # faulting address kept
    assert fr.regs["a7"] == "0x000000000000000f" and fr.regs["sp"] == "0x0000003fffff9000"


def test_parse_trapframe_timeout_is_not_ok():
    from gini.domain.xv6 import parse_trapframe
    assert parse_trapframe("gdb-timeout").ok is False        # idle kernel -> authored fallback
    assert parse_trapframe("").ok is False


def test_demo_catch_trap_is_a_seedable_frame():
    from gini.domain.xv6 import DemoScheduler
    fr = DemoScheduler().catch_trap()
    assert fr.ok and fr.kind == 1 and fr.regs.get("a7")      # a usable frozen page fault


def test_parse_alarms_only_returns_active():
    from gini.domain.xv6 import parse_alarms
    txt = ("ALARM 1 0 0 0x0 0\n"                          # init: no alarm set -> dropped
           "ALARM 5 10 3 0x0000000000001120 0\n"          # a periodic handler, 7 ticks to fire
           "ALARM 7 4 4 0x0000000000002000 1\n")          # firing NOW (on=1)
    al = parse_alarms(txt)
    assert set(al) == {5, 7}                               # only processes with interval > 0
    assert al[5].remaining == 7 and al[5].on == 0
    assert al[7].on == 1 and al[7].handler == "0x0000000000002000"


def test_demo_alarms_counts_down():
    from gini.domain.xv6 import DemoScheduler, parse_alarms
    d = DemoScheduler()
    seen = [parse_alarms(d.alarms())[5].ticks for _ in range(4)]
    assert seen == [1, 2, 3, 4]                            # the countdown advances each read


def test_demo_catch_trap_honours_kind():
    from gini.domain.xv6 import DemoScheduler
    d = DemoScheduler()
    assert d.catch_trap("syscall").kind == 0              # ecall
    assert d.catch_trap("timer").kind == 2               # timer interrupt
    assert d.catch_trap("pagefault").kind == 1           # store fault (default)


def test_parse_cpu_lines():
    from gini.domain.xv6 import parse_cpu_lines
    txt = "1 sleep init\nSCHED policy 0 quantum 3\nCPU 0 pid 5\nCPU 1 pid 6\n"
    assert parse_cpu_lines(txt) == {0: 5, 1: 6}
    assert parse_cpu_lines("1 sleep init\n") == {}      # single-CPU -> no CPU lines


def test_parse_shadow_manifest():
    from gini.domain.xv6 import parse_shadow_manifest
    txt = ("SHADOW prio_sched present=1 enabled=1 active=1 faults=0 hash=9f3a1c\n"
           "SHADOW lottery_sched present=0 enabled=0 active=0 faults=0 hash=baseline\n"
           "SHADOW rr_sched present=1 enabled=1 active=0 faults=2 hash=aa11bb\n")
    m = parse_shadow_manifest(txt)
    assert set(m) == {"prio_sched", "lottery_sched", "rr_sched"}
    assert m["prio_sched"].is_student and m["prio_sched"].healthy       # wired + clean
    assert not m["lottery_sched"].is_student                            # still the baseline stub
    assert m["rr_sched"].is_student and not m["rr_sched"].healthy       # crashed back to primary
    assert m["rr_sched"].faults == 2


def test_ready_queue_orders_by_level_then_priority():
    from gini.domain.xv6 import Proc, ready_queue
    procs = [Proc(3, "runnable", "spin", priority=10, level=1),
             Proc(4, "runnable", "spin", priority=5, level=0),
             Proc(5, "running", "primes"),                       # running -> not in the queue
             Proc(6, "runnable", "x", priority=5, level=0),
             Proc(2, "sleeping", "sh")]                          # sleeping -> not in the queue
    assert [p.pid for p in ready_queue(procs)] == [4, 6, 3]      # level 0 (by pid), then level 1


def test_timeline_shares_track_cpu_occupancy():
    from gini.domain.xv6 import SchedTimeline
    tl = SchedTimeline()
    for i, p in enumerate([5, 5, 5, 4, 4, 3]):                   # distinct ticks -> all recorded
        tl.add_run(p, i)
    sh = tl.shares()
    assert abs(sh[5] - 0.5) < 1e-9 and abs(sh[4] - 1 / 3) < 1e-9 and abs(sh[3] - 1 / 6) < 1e-9
    assert abs(sum(sh.values()) - 1.0) < 1e-9                    # idle excluded, fractions sum to 1


def test_short_pid_keeps_similar_pids_distinct():
    from gini.domain.xv6 import short_pid
    # the reported case: a centre-clipped full pid shows '61' for BOTH; last-two-digits disambiguates
    assert short_pid(3615) == "15" and short_pid(3613) == "13"
    assert short_pid(230) == "30"                             # last two digits
    assert short_pid(6) == "6" and short_pid(10) == "10" and short_pid(11) == "11"  # small: as-is


def test_parse_modetime_and_mode_split():
    from gini.domain.xv6 import mode_split, parse_modetime
    txt = "MODETIME user 700 kernel 200 idle 100\nCSR sstatus 0x2 sie 0x0 sip 0x0 stvec 0x0 " \
          "scause 0x0 sepc 0x0"
    mt = parse_modetime(txt)
    assert mt == {"user": 700, "kernel": 200, "idle": 100}
    assert parse_modetime("no such line") == {}
    # delta between two cumulative samples -> last-window fractions (summing to 1)
    s = mode_split({"user": 650, "kernel": 190, "idle": 95}, mt)
    assert round(s["user"], 2) == 0.77 and abs(sum(s.values()) - 1.0) < 1e-9
    # no prior sample -> since-boot ratio; no motion -> all zero
    assert round(mode_split(None, mt)["user"], 2) == 0.70
    assert mode_split(mt, mt) == {"user": 0.0, "kernel": 0.0, "idle": 0.0}


def test_parse_csr_and_decoders():
    from gini.domain.xv6 import interrupt_sources, parse_csr, scause_str, sstatus_flags
    txt = "CSR sstatus 0x22 sie 0x222 sip 0x20 stvec 0x80001bb4 scause 0x8000000000000005 " \
          "sepc 0x14"
    c = parse_csr(txt)
    assert c["sstatus"] == 0x22 and c["sie"] == 0x222 and c["stvec"] == 0x80001bb4
    assert parse_csr("nope") == {}
    f = sstatus_flags(c["sstatus"])
    assert f["SIE"] and f["SPIE"] and f["SPP"] == "U"          # came from user
    assert sstatus_flags(0x122)["SPP"] == "S"                 # SPP set -> came from kernel
    src = {d["name"]: d for d in interrupt_sources(c["sie"], c["sip"])}
    assert src["timer"]["enabled"] and src["timer"]["pending"]    # sie bit5 + sip bit5
    assert src["external"]["enabled"] and not src["external"]["pending"]
    assert scause_str(c["scause"]) == "timer int" and scause_str(15) == "store page fault"


def test_demo_scheduler_emits_modetime_and_csr():
    from gini.domain.xv6 import DemoScheduler, mode_split, sstatus_flags
    d = DemoScheduler(timeslice=1)
    a = d.snapshot(); b = d.step(); c = d.step()
    assert a.modetime and c.modetime["user"] > a.modetime["user"]     # advances
    split = mode_split(b.modetime, c.modetime)
    assert round(split["user"], 2) == 0.70                    # ≈70/20/10 offline
    assert sstatus_flags(b.csr["sstatus"])["SPP"] == "U"      # demo came-from user


def test_timeline_records_context_switches():
    tl = SchedTimeline()
    # tick 10: spin(3) running; tick 11: still 3; tick 12: switched to 4
    tl.add(Snapshot(procs=parse_procdump("3 run spin\n4 runble spin"), running_pid=3, ticks=10))
    tl.add(Snapshot(procs=parse_procdump("3 run spin\n4 runble spin"), running_pid=3, ticks=11))
    tl.add(Snapshot(procs=parse_procdump("3 runble spin\n4 run spin"), running_pid=4, ticks=12))
    pids = [s.pid for s in tl.recent()]
    assert pids == [3, 3, 4]                 # one advance without switch, then a switch
    assert tl.switches() == 1
