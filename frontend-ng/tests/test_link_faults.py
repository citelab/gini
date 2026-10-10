"""Link failures: the clocks, and what a cut link means on the Python side (Phase 3).

The schedule is pure and seeded, so these pin it exactly -- and check the exponential model
statistically, since that is the claim the inspector makes. Then transport.Port, the one place
every Python node (machine shuttle, switch, gbridge, the simulator) honours a cut cable, and the
`link` command gBuilder sends to it.
"""
import socket
import statistics
import time

import pytest

from gini.domain import link_faults as LF
from gini.domain import link_props as LP
from gini.domain.topology import Topology
from gini.runtime.control import link_command
from gini.runtime.transport import Port


def _lab(**props_by_name):
    t = Topology("faults")
    r1, r2 = t.add_device("router", "R1"), t.add_device("router", "R2")
    s, m = t.add_device("switch", "S1"), t.add_device("host", "M1")
    links = {"rr": t.add_link(r1.id, r2.id), "rs": t.add_link(r2.id, s.id),
             "sm": t.add_link(m.id, s.id)}
    for name, props in props_by_name.items():
        for k, v in props.items():
            LP.set_prop(links[name], k, v)
    return t, links


def _events(sched, until):
    return [(round(e.at, 6), e.link_id, e.up) for e in sched.due(until)]


# ---- the schedule ---------------------------------------------------------------- #
def test_a_link_that_never_fails_has_no_clock():
    t, _ = _lab()
    sched = LF.Schedule(t, seed=1)
    assert sched.next_event() is None and sched.due(1e9) == []


def test_the_same_seed_gives_the_same_failures():
    t, _ = _lab(rr={"fail_after": 30, "repair_after": 10}, sm={"fail_after": 50})
    assert _events(LF.Schedule(t, 7), 1000) == _events(LF.Schedule(t, 7), 1000)
    assert _events(LF.Schedule(t, 7), 1000) != _events(LF.Schedule(t, 8), 1000)


def test_one_links_schedule_does_not_move_when_another_changes():
    t, links = _lab(rr={"fail_after": 30, "repair_after": 10})
    before = [e for e in _events(LF.Schedule(t, 3), 500) if e[1] == links["rr"].id]
    LP.set_prop(links["sm"], "fail_after", 5)            # another link starts failing too
    after = [e for e in _events(LF.Schedule(t, 3), 500) if e[1] == links["rr"].id]
    assert before == after


def test_a_failed_link_with_no_repair_time_stays_down():
    t, links = _lab(rr={"fail_after": 20})
    sched = LF.Schedule(t, 1)
    evs = sched.due(1e9)
    assert [(e.link_id, e.up) for e in evs] == [(links["rr"].id, False)]
    assert links["rr"].id in sched.down and sched.next_event() is None


def test_failures_and_repairs_alternate():
    t, links = _lab(rr={"fail_after": 5, "repair_after": 2})
    ups = [e.up for e in LF.Schedule(t, 11).due(500)]
    assert len(ups) > 20 and ups[0] is False
    assert all(a != b for a, b in zip(ups, ups[1:]))      # down, up, down, up, ...


def test_the_times_are_exponential_with_the_means_set():
    """Up-times average fail_after and down-times repair_after, and spread like an exponential
    (standard deviation about equal to the mean) -- the model the inspector promises."""
    t, links = _lab(rr={"fail_after": 10, "repair_after": 4})
    evs = LF.Schedule(t, 2024).due(200_000)
    ups, downs, last, state_up = [], [], 0.0, True
    for e in evs:
        (ups if state_up else downs).append(e.at - last)
        last, state_up = e.at, e.up
    assert len(ups) > 5000
    assert statistics.mean(ups) == pytest.approx(10, rel=0.05)
    assert statistics.mean(downs) == pytest.approx(4, rel=0.05)
    assert statistics.stdev(ups) == pytest.approx(statistics.mean(ups), rel=0.08)


def test_fail_now_and_restore():
    t, links = _lab(rr={"fail_after": 1000, "repair_after": 3})
    sched = LF.Schedule(t, 5)
    sched.fail_now(links["rr"].id, now=10)
    assert links["rr"].id in sched.down
    nxt = sched.next_event()
    assert nxt.up and nxt.at > 10                         # its own repair time, drawn as usual
    sched.restore(links["rr"].id, now=11)
    assert links["rr"].id not in sched.down
    assert sched.next_event().up is False                 # and its next failure, afresh


def test_a_link_with_no_model_can_still_be_failed_by_hand():
    t, links = _lab()
    sched = LF.Schedule(t, 5)
    sched.fail_now(links["sm"].id, now=1)
    assert links["sm"].id in sched.down and sched.next_event() is None
    sched.restore(links["sm"].id, now=2)
    assert not sched.down


# ---- who acts at each end ------------------------------------------------------ #
def test_every_end_of_a_link_is_told():
    from gini.services.compiler import RuntimeCompiler
    t, links = _lab()
    cfg = RuntimeCompiler().compile(t)
    assert LF.commands(cfg, links["rr"].id, up=False) == [
        ("router", "R1", "ifconfig down tun1"), ("router", "R2", "ifconfig down tun1")]
    assert LF.commands(cfg, links["rs"].id, up=True) == [
        ("router", "R2", "ifconfig up tun2"), ("fabric", "S1", f"link {links['rs'].id} up")]
    assert LF.commands(cfg, links["sm"].id, up=False) == [
        ("fabric", "S1", f"link {links['sm'].id} down"),
        ("machine", "M1", f"link {links['sm'].id} down")]


# ---- a cut cable on the Python side -------------------------------------------- #
def _pair():
    a = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); a.bind(("127.0.0.1", 0))
    b = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); b.bind(("127.0.0.1", 0))
    pa, pb = a.getsockname()[1], b.getsockname()[1]
    a.close(); b.close()
    x = Port.from_cfg({"bind_host": "127.0.0.1", "bind_port": pa, "peer_host": "127.0.0.1",
                       "peer_port": pb, "link": {"id": "link-9"}}, name="x")
    y = Port.from_cfg({"bind_host": "127.0.0.1", "bind_port": pb, "peer_host": "127.0.0.1",
                       "peer_port": pa, "link": {"id": "link-9"}}, name="y")
    return x, y


def _recv(port, wait=0.2):
    end = time.time() + wait
    while time.time() < end:
        f = port.recv()
        if f is not None:
            return f
        time.sleep(0.01)
    return None


def test_a_port_learns_which_link_it_is():
    x, y = _pair()
    assert x.link_id == y.link_id == "link-9" and not x.down
    x.sock.close(); y.sock.close()


def test_a_down_port_sends_nothing_and_receives_nothing():
    x, y = _pair()
    try:
        x.send(b"before"); assert _recv(y) == b"before"
        x.down = True
        x.send(b"while down")                              # dropped at the sender
        assert _recv(y) is None
        y.send(b"to a down port")                          # drained and discarded at the receiver
        assert _recv(x) is None
        x.down = False
        assert _recv(x) is None                            # no stale burst on the way back up
        x.send(b"after"); assert _recv(y) == b"after"
    finally:
        x.sock.close(); y.sock.close()


def test_the_link_command():
    x, y = _pair()
    try:
        changes = []
        out = link_command([x], "link link-9 down", on_change=lambda p, up: changes.append(up))
        assert out == "link link-9 down" and x.down and changes == [False]
        assert "link-9  down" in link_command([x], "links")
        assert link_command([x], "link link-9 up") == "link link-9 up" and not x.down
        assert link_command([x], "link link-1 down") == "no port on link link-1"
        assert link_command([x], "link link-9 sideways").startswith("usage")
        assert link_command([x], "mactable") is None       # not ours: the element handles it
    finally:
        x.sock.close(); y.sock.close()
