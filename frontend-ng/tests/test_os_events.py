"""The OS HUD's X-ray: four kernel rings merged into one ordered story.

The whole value of the HUD is that a program launch becomes a single visible sequence instead of
five disconnected lab views, so these tests are mostly about ORDER and about what gets left out.
"""
from gini.domain.os_events import (
    Episode, LANES, episodes, fault_events, launch_of, merge, syscall_events, trap_events,
)

# one launch as the three rings report it — deliberately interleaved, so a naive concatenation
# would tell the story in the wrong order
SC = """TRACE 2 1 0x0 0x7 100
TRACE 7 7 0x1030 0x0 104
TRACE 7 15 0x2000 0x3 106
TRACE 7 5 0x3 0x400 107
TRACE 7 16 0x2010 0x400 112"""
FLT = """FLT 7 15 0x0000000000004000 0x0000000000000072 109
FLT 7 13 0x0000000000005000 0x0000000000000080 110"""
TR = """TR 7 3 0x8000000000000009 0x72 0x0 0x20 0x222 0x20 108
TR 7 4 0x0000000000000002 0x72 0x0 0x20 0x222 0x20 108
TR 7 2 0x8000000000000005 0x72 0x0 0x20 0x222 0x20 111"""


def _all():
    return merge(syscall_events(SC), fault_events(FLT), trap_events(TR))


def test_rings_merge_into_kernel_order():
    evs = _all()
    assert [e.seq for e in evs] == sorted(e.seq for e in evs)
    # the story reads correctly across subsystems
    assert [(e.lane, e.kind) for e in evs][:4] == [
        ("proc", "fork"), ("proc", "exec"), ("fs", "open"), ("fs", "read")]


def test_syscalls_split_into_lanes():
    evs = syscall_events(SC)
    lanes = {e.kind: e.lane for e in evs}
    assert lanes["fork"] == "proc" and lanes["exec"] == "proc"   # shape of the process world
    assert lanes["open"] == "fs" and lanes["write"] == "fs"


def test_timer_traps_are_excluded_by_default():
    # timer interrupts outnumber everything and would bury a launch
    assert not any(e.kind == "timer" for e in _all())
    assert any(e.kind == "timer" for e in trap_events(TR, include_timer=True))


def test_syscall_and_fault_traps_are_not_double_reported():
    # the syscall and fault rings already tell those stories in richer form
    kinds = {e.kind for e in trap_events(TR)}
    assert kinds, "fixture must contain a trap that survives filtering, or this is vacuous"
    assert "syscall" not in kinds and "pagefault" not in kinds


def test_unstamped_events_are_dropped():
    # a kernel built before the event clock cannot be ordered, so it contributes nothing to the
    # X-ray (its per-ring views keep working elsewhere)
    assert syscall_events("TRACE 2 1 0x0 0x7") == []
    assert fault_events("FLT 7 15 0x4000 0x72") == []
    assert trap_events("TR 7 3 0x9 0x72 0x0") == []


def test_merge_filters_by_pid():
    assert {e.pid for e in merge(syscall_events(SC), pid=7)} == {7}


def test_launch_story_starts_at_exec():
    ep = launch_of(_all(), 7)
    assert ep.events[0].kind == "exec"
    assert "exec" in ep.summary() and "page fault" in ep.summary()


def test_episodes_group_by_pid_newest_first():
    eps = episodes(_all())
    assert [e.pid for e in eps] == [7, 2]        # pid 7's story starts later, so it leads


def test_episode_lanes_cover_the_swimlanes():
    ep = launch_of(_all(), 7)
    assert set(ep.lanes) >= set(LANES)
    assert len(ep.lanes["memory"]) == 2          # both page faults landed in the memory lane


def test_empty_is_safe():
    assert merge() == [] and episodes([]) == []
    assert Episode(pid=1).span == (0, 0)


# -- the window: the kernel keeps re-reporting, so the HUD must age events out ------------------ #
def _ev(seq):
    from gini.domain.os_events import OsEvent
    return OsEvent(seq=seq, pid=7, lane="fs", kind="read")


def _cumulative(n):
    """What a poll actually returns: the kernel ring's whole contents, so consecutive polls
    overlap almost entirely."""
    return [_ev(i) for i in range(1, n + 1)]


def test_window_ages_events_out():
    from gini.domain.os_events import EventWindow
    w = EventWindow(window_s=10.0)
    assert [e.seq for e in w.add(_cumulative(3), 100.0)] == [1, 2, 3]
    assert [e.seq for e in w.add(_cumulative(5), 105.0)] == [1, 2, 3, 4, 5]
    assert [e.seq for e in w.add(_cumulative(5), 111.0)] == [4, 5]     # 1..3 older than 10s


def test_retired_events_never_come_back():
    """The bug this class exists for: a re-reported event that has already aged out must NOT be
    treated as new, or it gets a fresh timestamp and lives on screen forever."""
    from gini.domain.os_events import EventWindow
    w = EventWindow(window_s=10.0)
    w.add(_cumulative(5), 100.0)
    assert w.add(_cumulative(5), 120.0) == []
    assert w.add(_cumulative(5), 130.0) == []                          # and stays gone
    assert [e.seq for e in w.add(_cumulative(7), 131.0)] == [6, 7]     # new ones still land


def test_window_caps_a_burst():
    from gini.domain.os_events import EventWindow
    w = EventWindow(window_s=999)
    out = w.add([_ev(i) for i in range(1000)], 0.0)
    assert len(out) == EventWindow.MAX_EVENTS and out[-1].seq == 999   # newest kept


def test_device_traps_excluded_by_default():
    """Device traps are mostly OUR OWN polling: each dump request writes a control byte to the
    serial, raising a UART interrupt the kernel records as a device trap. Left on, the trap lane
    shows the measurement rather than the machine."""
    from gini.domain.os_events import trap_events
    tr = ("TR 7 3 0x8000000000000009 0x72 0x0 0x20 0x222 0x20 108\n"     # device = our poll
          "TR 7 4 0x2 0x90 0x0 0x20 0x222 0x20 115\n")                   # illegal = real
    assert [e.kind for e in trap_events(tr)] == ["illegal"]
    assert [e.kind for e in trap_events(tr, include_device=True)] == ["device", "illegal"]


def test_the_hart_field_does_not_break_the_anchored_trap_parser():
    """THE hazard this change had to avoid. There are two TR parsers: the one in `xv6.py` is
    unanchored and ignores an unknown trailing field, but THIS one ends in `\\s*$` — so a field
    appended to the wire format does not go unread, it makes the whole line stop matching and the
    X-ray's trap lane goes quietly empty. The CPU Lab would have looked perfect while a different
    panel lost a lane."""
    from gini.domain.os_events import trap_events
    # kind 4 = illegal instruction. Not device (excluded by default: mostly our own polling),
    # not timer (excluded: it would bury the lane), and not syscall or page fault — those have
    # lanes of their own. Illegal is what this lane is actually for.
    line = "TR 7 4 0x0000000000000002 0x72 0x0 0x20 0x222 0x20 108 h1\n"
    evs = trap_events(line)
    assert evs, "a TR line carrying a hart produced no events"
    assert evs[0].seq == 108


def test_a_trap_line_without_a_hart_still_parses():
    from gini.domain.os_events import trap_events
    assert trap_events("TR 7 4 0x0000000000000002 0x72 0x0 0x20 0x222 0x20 108\n")
