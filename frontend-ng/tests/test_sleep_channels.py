"""Sleep channels — what a blocked process is actually waiting for.

`state == sleeping` says a process is stopped. It never said WHY, or who would start it again.
In xv6 the answer is an address: `sleep_prepare(chan)` records it, `sleep()` blocks, and
`wakeup(chan)` matches on that same address and clears it. The whole mechanism is a pointer
comparison, and from outside the kernel it was completely invisible.

The kernel now names the well-known channels (each subsystem registers its own at init, because
bcache/itable/log/cons are static to their own files), so "sleeping" becomes "sleeping · a child
to exit".
"""
from gini.domain.xv6 import (
    Proc, WaitOn, apply_waits, parse_procdump, parse_waits,
)

# gini_dump as the patched kernel emits it
DUMP = """
1 sleep  init 0
PROC 1 pri 10 tk 1 lv 0 wait 0
ALARM 1 0 0 0x0 0
WAIT 1 0x80008d20 a child to exit
2 sleep  sh 1
PROC 2 pri 10 tk 1 lv 0 wait 0
ALARM 2 0 0 0x0 0
WAIT 2 0x80009a40 console input
3 run    spin 2
PROC 3 pri 10 tk 1 lv 0 wait 0
ALARM 3 0 0 0x0 0
4 sleep  cat 2
PROC 4 pri 10 tk 1 lv 0 wait 0
ALARM 4 0 0 0x0 0
WAIT 4 0x80011200 ?
"""


def test_a_named_channel_says_who_will_wake_you():
    w = parse_waits(DUMP)
    assert w[1].name == "a child to exit"
    assert w[2].name == "console input"
    assert w[1].chan == 0x80008d20
    assert w[1].label == "a child to exit"


def test_an_unnamed_channel_falls_back_to_the_address():
    """Pipes are allocated per pipe, so there is no fixed address for the kernel to claim. The
    raw channel still beats a bare "sleeping" with no object at all."""
    w = parse_waits(DUMP)
    assert w[4].name == ""            # the kernel's "?" becomes "unknown", not the literal "?"
    assert not w[4].known
    assert w[4].label == "0x80011200"


def test_a_running_process_has_no_channel():
    assert 3 not in parse_waits(DUMP)


def test_channels_attach_to_the_right_processes():
    procs = apply_waits(parse_procdump(DUMP), DUMP)
    by_pid = {p.pid: p for p in procs}
    assert by_pid[1].waiting_on.name == "a child to exit"
    assert by_pid[2].waiting_on.name == "console input"
    assert by_pid[3].waiting_on is None          # running
    assert by_pid[4].waiting_on.label.startswith("0x")


def test_an_older_kernel_reports_nothing_and_that_is_fine():
    """A build without the registry emits no WAIT lines. Every process must simply have None."""
    old = "1 sleep  init 0\n2 run    sh 1\n"
    procs = apply_waits(parse_procdump(old), old)
    assert parse_waits(old) == {}
    assert all(p.waiting_on is None for p in procs)


def test_a_channel_on_a_RUNNABLE_process_is_not_a_bug():
    """This xv6 splits sleep(chan, lk) into sleep_prepare(chan) then sleep(), so there is a window
    where a process is armed to sleep but still runnable — which is precisely the race the split
    exists to close. The kernel prints WAIT for any non-zero chan, so that window is visible."""
    txt = "5 runble waker 1\nWAIT 5 0x80008000 the log\n"
    procs = apply_waits(parse_procdump(txt), txt)
    p = procs[0]
    assert p.state == "runnable" and p.waiting_on.name == "the log"


def test_apply_waits_clears_a_stale_channel():
    """Woken processes must lose their channel, or the tree shows a wait that ended."""
    p = Proc(1, "runnable", "sh", waiting_on=WaitOn(chan=0x1234, name="the log"))
    apply_waits([p], "1 runble sh 0\n")           # no WAIT line this poll
    assert p.waiting_on is None


def test_apply_waits_returns_the_list_for_chaining():
    procs = parse_procdump(DUMP)
    assert apply_waits(procs, DUMP) is procs


# -- and it has to reach the screen ------------------------------------------------------------ #
def _tree():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import pytest
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from gini.ui.process_tree import ProcessTree
    from gini.ui.theme import ThemeManager
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    return ProcessTree(ThemeManager(app))


def test_the_tree_shows_the_channel_beside_the_state():
    tree = _tree()
    tree.set_procs(apply_waits(parse_procdump(DUMP), DUMP), running_pids=[3])

    seen = {}

    def walk(item):
        seen[item.text(1)] = (item.text(2), item.toolTip(2))
        for i in range(item.childCount()):
            walk(item.child(i))

    for i in range(tree.topLevelItemCount()):
        walk(tree.topLevelItem(i))

    assert seen["1"][0] == "sleeping · a child to exit"
    assert seen["2"][0] == "sleeping · console input"
    assert seen["3"][0] == "running"                 # no channel, no suffix
    assert seen["4"][0] == "sleeping · 0x80011200"   # unnamed falls back to the address
    assert "wakeup(0x80008d20)" in seen["1"][1]      # the tooltip names the actual mechanism
    assert "pipe" in seen["4"][1]                    # ...and explains an unnamed one


def test_a_scheduling_warning_outranks_the_channel():
    """A starving process is a problem; what it waits for is context. The problem wins the column."""
    tree = _tree()
    procs = apply_waits(parse_procdump(DUMP), DUMP)
    tree.set_procs(procs, running_pids=[3], flags={1: "starved"})
    top = tree.topLevelItem(0)
    assert "⚠ starved" in top.text(2)
