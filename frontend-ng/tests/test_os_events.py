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
