"""MachineState bridge — state card, deltas, and the Coach event watcher (all pure)."""
from gini.agent import ask
from gini.agent.wizard import os_coach_prompt
from gini.domain.machine_state import CoachLedger, MachineState, OsEvent, StateWatcher, state_card
from gini.domain.xv6 import DemoScheduler, Proc, SchedTimeline, Snapshot


def _snap(specs, run, ticks=0):
    procs = [Proc(pid, st, nm) for pid, st, nm in specs]
    return Snapshot(procs=procs, running_pid=run, ticks=ticks)


# -- state card ------------------------------------------------------------- #
def test_card_l0_shows_scheduling_picture():
    tl = SchedTimeline()
    snap = _snap([(1, "sleeping", "init"), (3, "running", "spin"),
                  (4, "runnable", "spin")], run=3, ticks=12)
    tl.add(snap)
    card = state_card(snap, tl, meta={"policy": "round-robin", "timeslice": 5}, level=0)
    assert "running: pid 3 (spin)" in card
    assert "time-slice: 5 ticks" in card
    assert "pid 3" in card and "pid 4" in card
    assert "round-robin" in card
    # L0 must NOT include registers/stack (budget)
    assert "CPU registers" not in card


def test_card_l1_adds_registers_and_stack():
    sched = DemoScheduler()
    snap = sched.snapshot()
    card = state_card(snap, None, level=1)
    assert "CPU registers" in card and "pc=" in card
    assert "kernel stack" in card


def test_card_grounds_only_in_present_state():
    # the card explicitly frames itself as ground truth for the closed-world rule
    card = state_card(DemoScheduler().snapshot(), None, level=0)
    assert "ground truth" in card.lower()


# -- watcher ---------------------------------------------------------------- #
def test_watcher_flags_starvation_once_per_episode():
    w = StateWatcher(starve=3)
    events = []
    for _ in range(5):                      # pid 4 stays runnable, pid 3 always runs
        events += w.observe(_snap([(3, "running", "spin"), (4, "runnable", "spin")], run=3))
    kinds = [e.kind for e in events]
    assert kinds.count("starvation") == 1   # edge-triggered, not per-tick
    assert any(e.pid == 4 for e in events if e.kind == "starvation")


def test_watcher_active_flags_persist_while_true():
    # the scheduler-face badge needs the CURRENTLY-active condition, not just the one-shot event
    w = StateWatcher(starve=3)
    for _ in range(5):
        w.observe(_snap([(3, "running", "spin"), (4, "runnable", "spin")], run=3))
    assert w.active("starvation") == {4}
    # once pid 4 gets to run, the condition clears and the badge goes away
    w.observe(_snap([(3, "runnable", "spin"), (4, "running", "spin")], run=4))
    assert w.active("starvation") == set()


def test_machine_state_scheduling_flags():
    ms = MachineState(DemoScheduler(timeslice=1), device_id="d1")
    ms.watcher = StateWatcher(starve=3)
    for _ in range(5):                          # drive a starvation condition through the state
        ms._ingest(_snap([(3, "running", "spin"), (4, "runnable", "spin")], run=3))
    flags = ms.scheduling_flags()
    assert flags["starvation"] == {4}
    assert set(flags) == {"starvation", "cpu_monopoly", "zombie_leak"}


def test_watcher_flags_cpu_monopoly():
    w = StateWatcher(monopoly=3)
    ev = []
    for _ in range(4):
        ev += w.observe(_snap([(3, "running", "spin"), (4, "runnable", "sh")], run=3))
    assert any(e.kind == "cpu_monopoly" for e in ev)


def test_watcher_flags_zombie_leak_and_idle():
    w = StateWatcher(zombie=2)
    ev = []
    for _ in range(3):
        ev += w.observe(_snap([(5, "zombie", "prog"), (2, "sleeping", "sh")], run=None))
    assert any(e.kind == "zombie_leak" and e.pid == 5 for e in ev)
    assert any(e.kind == "idle" for e in ev)   # nothing runnable/running


# -- MachineState orchestration --------------------------------------------- #
def test_machine_state_steps_and_cards():
    ms = MachineState(DemoScheduler(timeslice=1), device_id="d1")
    for _ in range(6):
        ms.step()
    card = ms.card(level=0)
    assert "context switches:" in card
    assert ms.latest is not None


def test_machine_state_control_changes_become_events_and_deltas():
    ms = MachineState(DemoScheduler(timeslice=1), device_id="d1")
    ms.card()                                  # establish a baseline for deltas
    ms.set_timeslice(10)
    ms.set_policy("priority")
    ev = ms.drain_events()
    kinds = [(e.kind, e.detail) for e in ev]
    assert any(k == "control" and "time-slice" in d for k, d in kinds)
    assert any(k == "control" and "policy" in d for k, d in kinds)
    # the next card surfaces the change to the student-facing context
    card = ms.card()
    assert "changes since your last question" in card
    assert "priority" in card and "time-slice 1 -> 10" in card
    assert ms.drain_events() == []             # drained


# -- agent context seam ----------------------------------------------------- #
def test_grounded_context_appends_machine_card():
    card = state_card(DemoScheduler().snapshot(), None, level=0)
    ctx = ask.grounded_context("ALWAYS", "", None, "CANVAS", None, machine_card=card)
    assert "CANVAS" in ctx and "xv6 Machine — live kernel state" in ctx
    # absent when there's no xv6 focus
    ctx2 = ask.grounded_context("ALWAYS", "", None, "CANVAS", None, machine_card="")
    assert "xv6 Machine — live kernel state" not in ctx2


def test_machine_card_level_scales_with_question():
    assert ask.machine_card_level("why does pid 3 keep running?") == 0
    assert ask.machine_card_level("show me the registers and the kernel stack") == 1
    assert ask.machine_card_level("walk the page table / satp for this process") == 2


def test_card_l2_appends_memory_and_fs_summaries():
    ms = MachineState(DemoScheduler(), device_id="d1")   # vm/fs default to demo providers
    l0 = ms.card(level=0)
    assert "memory (virtual" not in l0 and "file system:" not in l0
    l2 = ms.card(level=2)
    assert "memory (virtual, Sv39)" in l2 and "satp=" in l2       # VM grounding
    assert "file system:" in l2 and "write-ahead log:" in l2      # FS grounding
    assert "context switches:" in l2                             # still includes the scheduler card


# -- measured help (budget + log) ------------------------------------------- #
def test_coach_ledger_budgets_and_logs():
    led = CoachLedger(budget=2)
    assert led.can_help()
    led.record([OsEvent("starvation", "pid 4 starving", 4)], hint="asked about fairness")
    assert led.remaining() == 1 and led.can_help()
    led.record([])
    assert led.remaining() == 0 and not led.can_help()   # budget exhausted
    assert len(led.log) == 2                              # every hint is logged
    assert led.log[0]["events"][0][0] == "starvation"


def test_machine_state_carries_a_ledger():
    ms = MachineState(DemoScheduler(), device_id="d1")
    assert isinstance(ms.ledger, CoachLedger)


def test_empty_read_keeps_last_good_snapshot():
    # an empty proc list = failed read (init+sh always exist) -> keep the last good state,
    # so the process table never blanks on a transient gdb timeout.
    class Flaky:
        def __init__(self):
            self.n = 0
        def snapshot(self):
            self.n += 1
            if self.n == 1:
                return Snapshot(procs=[Proc(1, "sleeping", "init"),
                                       Proc(5, "running", "spin")], running_pid=5, ticks=1)
            return Snapshot(procs=[], running_pid=None, ticks=2)   # failed read
    ms = MachineState(Flaky(), device_id="d1", vm=object(), fs=object())
    assert ms.latest is not None and len(ms.latest.procs) == 2   # first (good) read
    ms.refresh()                                                 # empty read
    assert ms.latest is not None and len(ms.latest.procs) == 2   # kept the good one


def test_os_coach_prompt_is_socratic_and_grounded():
    card = state_card(DemoScheduler().snapshot(), None, level=1)
    p = os_coach_prompt([OsEvent("cpu_monopoly", "pid 3 hogging", 3)], card, remaining=3)
    assert "Socratic" in p or "SOCRATIC" in p
    assert "do NOT dump" in p.lower() or "one nudge" in p.lower()
    assert "pid 3 hogging" in p          # grounded in the detected event
    assert "3 Coach hint" in p           # budget surfaced


# -- Real/Demo mode (explicit user action, never auto-fallback) ------------- #
class _FakeBridge:
    """A live-shaped provider: real Snapshots + vm/fs readers, so it can be the Real plane."""
    timeslice = 1
    source = "real"
    def __init__(self):
        self.vm = object()
        self.fs = object()
    def snapshot(self):
        return Snapshot(procs=[Proc(1, "sleeping", "init"), Proc(2, "sleeping", "sh")],
                        running_pid=1, ticks=1, source="real")
    def step(self):
        return self.snapshot()


def test_mode_toggle_swaps_real_and_demo_planes():
    real = _FakeBridge()
    ms = MachineState(real, device_id="d", mode="real", vm=real.vm, fs=real.fs)
    assert ms.has_real() and ms.mode == "real"
    assert ms.latest is not None and ms.latest.source == "real"
    ms.set_mode("demo")                                   # user flips to Demo
    assert ms.mode == "demo"
    assert isinstance(ms.provider, DemoScheduler) and ms.latest.source == "demo"
    ms.set_mode("real")                                   # ...and back
    assert ms.provider is real and ms.latest.source == "real"


def test_real_mode_with_no_running_kernel_is_empty_not_demo():
    # started in Demo (nothing running); switching to Real shows NO data, never fake data
    ms = MachineState(DemoScheduler(), device_id="d", mode="demo")
    assert not ms.has_real()
    ms.set_mode("real")
    assert ms.provider is None and ms.latest is None      # honest emptiness, not a demo swap
    assert ms.refresh() is None


def test_attach_real_brings_an_open_lab_live():
    ms = MachineState(DemoScheduler(), device_id="d", mode="demo")   # opened while stopped
    ms.set_mode("real")                                   # user asks for Real -> no data yet
    assert ms.provider is None and ms.latest is None
    real = _FakeBridge()
    ms.attach_real(real, vm=real.vm, fs=real.fs)          # the topology starts
    assert ms.has_real() and ms.provider is real          # in Real mode -> goes live immediately
    assert ms.latest is not None and ms.latest.source == "real"


def test_real_read_failure_never_becomes_demo():
    class _Dead:
        timeslice = 1
        def snapshot(self):
            return Snapshot(procs=[], source="real")      # empty = failed read
        def step(self):
            return self.snapshot()
    ms = MachineState(_Dead(), device_id="d", mode="real", vm=object(), fs=object())
    assert ms.mode == "real" and ms.latest is None        # empty, and NOT swapped to demo
    assert not isinstance(ms.provider, DemoScheduler)


# -- a fresh kernel is a fresh episode (Stop/Run must not carry the old Gantt) ---------------- #
class _FakeProvider:
    """Minimal provider: snapshot() returns whatever we hand it."""
    def __init__(self, snap=None):
        self._snap = snap
        self.timeslice = 1

    def snapshot(self):
        return self._snap

    def step(self):
        return self._snap


def test_attach_real_starts_a_new_episode():
    # boot 1: a couple of samples land on the Gantt
    first = _FakeProvider(_snap([(1, "sleeping", "init"), (3, "running", "spin")], run=3, ticks=4))
    ms = MachineState(provider=first, mode="real")
    ms.refresh(); ms.refresh()
    assert len(ms.timeline.slots) > 0                   # boot 1 left slots behind
    assert ms.latest is not None

    # Stop + Run: a brand-new kernel attaches. The old boot's slices must be GONE — its pids
    # belong to a kernel that no longer exists (pid numbers get reused).
    second = _FakeProvider(_snap([(1, "sleeping", "init"), (2, "running", "sh")], run=2, ticks=1))
    ms.attach_real(second)
    names = {s.name for s in ms.timeline.slots}
    assert "spin" not in names                          # nothing from the previous run
    assert ms.timeline.slots == [] or names <= {"sh", "init"}


def test_new_episode_clears_watcher_edges_but_keeps_thresholds():
    ms = MachineState(provider=_FakeProvider(), mode="real", watcher=StateWatcher(starve=2))
    # arm the watcher: one runnable proc starved while another hogs the CPU
    for _ in range(4):
        ms.watcher.observe(_snap([(3, "running", "busy"), (4, "runnable", "spin")], run=3))
    assert ms.watcher.active()                          # something is armed
    ms.new_episode()
    assert not ms.watcher.active()                      # edges forgotten
    assert ms.watcher.starve == 2                       # configured threshold preserved


# -- soft wedge: dumps keep working, but nothing is ever scheduled ------------------------------ #
def test_stall_reports_when_runnable_procs_never_run():
    # a picker that never returns a process: procs are RUNNABLE, none RUNNING, Gantt frozen
    stuck = _snap([(1, "sleeping", "init"), (3, "runnable", "spin"),
                   (4, "runnable", "spin")], run=None, ticks=7)
    ms = MachineState(provider=_FakeProvider(stuck), mode="real")
    ms.refresh()
    assert ms.stall(1000.0) == ""              # first observation only arms the timer
    assert ms.stall(1003.0) == ""              # still inside the grace period
    msg = ms.stall(1012.0)                     # past grace with no progress
    assert "No process has run" in msg and "Reboot" in msg


def test_stall_silent_while_something_runs():
    ms = MachineState(provider=_FakeProvider(
        _snap([(1, "sleeping", "init"), (3, "running", "spin")], run=3, ticks=4)), mode="real")
    ms.refresh()
    assert ms.stall(1000.0) == ""
    assert ms.stall(1030.0) == ""              # a RUNNING proc is progress, however long we wait


def test_stall_silent_when_nothing_is_runnable():
    # an idle box is not a wedge
    ms = MachineState(provider=_FakeProvider(
        _snap([(1, "sleeping", "init"), (2, "sleeping", "sh")], run=None, ticks=9)), mode="real")
    ms.refresh()
    assert ms.stall(1000.0) == ""
    assert ms.stall(1099.0) == ""
