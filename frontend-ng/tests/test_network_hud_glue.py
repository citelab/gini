"""MainWindow's Network HUD glue: the SDN facts the routing model cannot derive.

These three helpers are the model's only source of truth about the DRAWN topology, and
`_ovs_link_peers` in particular decides the OpenFlow port numbering -- the one input that
can make the HUD draw a confident lie rather than fail visibly. They are tested by binding
the unbound methods to a stub, so no Qt and no MainWindow are involved.
"""
from types import SimpleNamespace

from gini.domain.routing_model import (
    collect_network_data, model_signature, query_failed,
)
from gini.ui.main_window import MainWindow


def _dev(did, name, type_key):
    return SimpleNamespace(id=did, name=name, type_key=type_key)


def _stub(devices, links, addressing=None):
    """Anything with `.ctx.topology`; the helpers touch nothing else on MainWindow."""
    topo = SimpleNamespace(devices={d.id: d for d in devices},
                           links={l.id: l for l in links})
    return SimpleNamespace(ctx=SimpleNamespace(topology=topo, addressing=addressing or {}))


def _link(lid, a, b, kind="link"):
    return SimpleNamespace(id=lid, source_id=a, target_id=b, kind=kind)


DEVICES = [_dev("ovs1", "OVS1", "ovs"), _dev("r1", "R1", "router"),
           _dev("r2", "R2", "router"), _dev("ofc1", "OFC1", "controller"),
           _dev("m1", "M1", "machine")]


def _peers(links):
    return MainWindow._ovs_link_peers(_stub(DEVICES, links), "ovs1")


# --- port ordering: the number that must not be wrong --------------------------------------- #
def test_peers_come_back_in_link_order():
    """Link order IS the port order (compiler.py:1032), so this list is the whole basis of the
    OpenFlow port numbering."""
    assert _peers([_link("l1", "ovs1", "r1"), _link("l2", "r2", "ovs1"),
                   _link("l3", "ovs1", "m1")]) == ["r1", "r2", "m1"]


def test_a_control_link_is_not_counted_as_a_port():
    """The compiler drops controller links from `kept` BEFORE numbering ports
    (compiler.py:726-733). Counting one here would shift every port by one and point every
    L2 hop at the wrong neighbour -- while still looking entirely plausible."""
    peers = _peers([_link("l0", "ofc1", "ovs1"), _link("l1", "ovs1", "r1"),
                    _link("l2", "ovs1", "r2")])
    assert peers == ["r1", "r2"], "the controller must not consume port 2"


def test_an_attach_link_is_not_a_cable():
    """A rider mount runs INSIDE its donor; the compiler skips it, so it is not a port."""
    assert _peers([_link("l1", "ovs1", "m1", kind="attach"),
                   _link("l2", "ovs1", "r1")]) == ["r1"]


def test_links_that_do_not_touch_this_switch_are_ignored():
    assert _peers([_link("l1", "r1", "r2"), _link("l2", "ovs1", "m1")]) == ["m1"]


# --- controller association ------------------------------------------------------------------ #
def test_the_controller_is_found_in_either_direction():
    for links in ([_link("l0", "ofc1", "ovs1")], [_link("l0", "ovs1", "ofc1")]):
        assert MainWindow._ovs_controller(_stub(DEVICES, links), "ovs1") == "ofc1"


def test_a_switch_with_no_controller_reports_none():
    assert MainWindow._ovs_controller(
        _stub(DEVICES, [_link("l1", "ovs1", "r1")]), "ovs1") is None


def test_only_a_controller_device_counts_as_the_controller():
    assert MainWindow._ovs_controller(
        _stub(DEVICES, [_link("l1", "ovs1", "m1")]), "ovs1") is None


# --- MACs ------------------------------------------------------------------------------------- #
def test_mac_of_keys_by_device_id_and_keeps_every_interface():
    """The address map is keyed by NAME while the model is keyed by id, and a router is
    multi-homed -- so this both translates and keeps the full list."""
    addressing = {"R2": {"role": "router", "interfaces": [
        {"name": "eth0", "mac": "02:00:00:01:01:02"},
        {"name": "eth1", "mac": "02:00:00:01:02:02"}]}}
    out = MainWindow._hud_mac_of(_stub(DEVICES, [], addressing))
    assert out == {"r2": ["02:00:00:01:01:02", "02:00:00:01:02:02"]}


def test_mac_of_survives_an_address_map_for_a_different_topology():
    """`ctx.addressing` is recompiled on change and can lag a device rename; an unknown name
    must be skipped, not crash the poll and blank the HUD."""
    addressing = {"GONE": {"interfaces": [{"mac": "02:00:00:09:09:09"}]}}
    assert MainWindow._hud_mac_of(_stub(DEVICES, [], addressing)) == {}


def test_mac_of_tolerates_no_address_map_at_all():
    assert MainWindow._hud_mac_of(_stub(DEVICES, [], None)) == {}


# --- element_query reports failure as a STRING, never an exception --------------------------- #
# This is what made the HUD flicker. A timed-out poll returned "(query failed: …)", the flow
# parser turned that into an empty table, and an empty table is a real answer meaning
# "programmed for nothing" -- so the lit path went dark and the changed forwarding projection
# recorded a phantom convergence event on the timeline.
ROUTES = "Index Network Netmask Nexthop Interface\n[0] 10.0.1.0 255.255.255.0 0.0.0.0 tun1 C\n"
IFACE = "eth0 10.0.1.1/24\n"
OVS_IFCONFIG = ("Int.\tState/Mode\tDevice\tIP\tMAC\tMTU\tSock\tTID\n"
                "1\tUN\t\ttun1\t169.254.0.1\t02:00:fe:00:00:01\t1500\t/tmp/a\t7\n")
# The REAL `openflow entry all` shape: `Match:`/`Actions:` at depth 0, match fields at one
# tab, output ports at two (flowtable._split_blocks + parse). Inventing this format instead of
# reading it is how the first version of these tests passed while asserting nothing.
ENTRIES = ("Entry 0\nMatch:\n\tEthernet destination MAC address: 02:00:00:01:01:02\n"
           "Actions:\n\t\tOutput port: 2\nPriority: 100\n")
NO_RULES = "Entry 0\nEntry inactive\n"


def test_query_failed_recognises_every_sentinel_element_query_can_return():
    for s in ("(query failed: boom)", "(no output)", "(not running)", "", "   ", None):
        assert query_failed(s) is True, s
    assert query_failed(ENTRIES) is False


def _collect(entry_reply, cache=None):
    def query(name, cmd):
        if name == "OVS1":
            return OVS_IFCONFIG if "verbose" in cmd else entry_reply
        return ROUTES if "route" in cmd else IFACE
    return collect_network_data(
        routers=[("r1", "R1")], switches=[("ovs1", "OVS1", "ofc1")],
        query=query, delay_prop=lambda rid, k: "", links=[("r1", "ovs1")],
        neighbours_of=lambda rid: ["r1"], run_cache=cache)


def test_a_timed_out_switch_is_marked_unreachable_not_empty():
    m = _collect("(query failed: timed out)")
    assert m.ovs["ovs1"].reachable is False
    assert m.ovs["ovs1"].flows == []


def test_a_switch_that_answers_with_no_rules_is_reachable():
    """'It answered and has nothing' is a fact about the network. 'It did not answer' is a
    fact about us. The HUD says different things for the two."""
    m = _collect(NO_RULES)
    assert m.ovs["ovs1"].reachable is True and m.ovs["ovs1"].flows == []


def test_a_failed_poll_does_not_record_a_phantom_convergence_event():
    """The flicker's other half: a blanked flow table changed the forwarding projection, so
    RouteHistory recorded a change tick every time a poll timed out — filling the scrub
    timeline with events that never happened.

    The cache is SHARED across the two polls, exactly as the controller keeps it for the run:
    that is what lets the failed poll carry the last good rules forward."""
    cache = {}
    good = model_signature(_collect(ENTRIES, cache))
    bad = model_signature(_collect("(query failed: timed out)", cache))
    assert good == bad, "an unreadable switch must not look like a forwarding change"


def test_the_carried_forward_rules_are_marked_stale_not_passed_off_as_fresh():
    """Carrying the rules forward is only honest because the node says it did not answer."""
    cache = {}
    _collect(ENTRIES, cache)
    m = _collect("(query failed: timed out)", cache)
    assert m.ovs["ovs1"].reachable is False
    assert len(m.ovs["ovs1"].flows) == 1, "the last known rules are still shown"


def test_a_reachable_switch_losing_its_rules_IS_still_a_change():
    """The guard above must not go so far that real convergence stops being recorded: a switch
    that ANSWERS with an empty table really has stopped forwarding, and that is an event."""
    cache = {}
    assert model_signature(_collect(ENTRIES, cache)) != \
        model_signature(_collect(NO_RULES, cache))


# --- the port map is static, so it is read once per run -------------------------------------- #
def test_the_port_map_is_cached_instead_of_re_read_every_poll():
    """Port wiring cannot change while a topology runs. Asking every switch for it on every
    poll doubled the serial `docker compose exec` round trips — and every extra call is
    another chance to time out and blank the picture."""
    calls = []

    def query(name, cmd):
        calls.append((name, cmd))
        if name == "OVS1":
            return OVS_IFCONFIG if "verbose" in cmd else ENTRIES
        return ROUTES if "route" in cmd else IFACE

    cache = {}
    for _ in range(3):
        collect_network_data(routers=[], switches=[("ovs1", "OVS1", None)],
                             query=query, delay_prop=lambda rid, k: "", links=[],
                             neighbours_of=lambda rid: ["r1"], run_cache=cache)
    verbose_calls = [c for c in calls if "verbose" in c[1]]
    assert len(verbose_calls) == 1, "read once, not once per poll"
    assert cache["ports"]["ovs1"] == {2: "r1"}


def test_a_failed_port_read_is_not_cached_so_it_can_recover():
    """Caching a failure would strand the switch with no port map for the whole run."""
    cache = {}
    replies = ["(query failed: timed out)", OVS_IFCONFIG]

    def query(name, cmd):
        if "verbose" in cmd:
            return replies.pop(0)
        return ENTRIES if name == "OVS1" else ROUTES

    for _ in range(2):
        collect_network_data(routers=[], switches=[("ovs1", "OVS1", None)],
                             query=query, delay_prop=lambda rid, k: "", links=[],
                             neighbours_of=lambda rid: ["r1"], run_cache=cache)
    assert cache["ports"]["ovs1"] == {2: "r1"}, "the retry must be allowed to succeed"


# --- the timeline was recording only half the story ------------------------------------------ #
def test_flow_table_churn_is_recorded_even_when_forwarding_does_not_change():
    """Reported from a live run: the OVS5 flow count moved 4 -> 22 -> 4 across 22 polls and
    the timeline drew ONE tick.

    Cause: model_signature is the forwarding projection (dest MAC -> egress port), and
    l2_multi matches with `from_packet`, so twenty-two microflows to one destination out one
    port project to a single pair. That blindness is correct for the scrub -- replaying
    identical forwarding twice says nothing -- but it dropped the controller's work from the
    picture entirely."""
    from gini.domain.flowtable import FlowEntry
    from gini.domain.routing_model import RouteHistory, assemble_model, flow_activity

    MA = "02:00:00:0a:0a:0a"

    def model(n):
        m = assemble_model([], links=[("ovs1", "ovs2")],
                           ovs_infos=[("ovs1", "OVS1", "", {2: "ovs2"}, None),
                                      ("ovs2", "OVS2", "", {2: "ovs1"}, None)])
        m.ovs["ovs1"].flows = [
            FlowEntry(index=i, match={"Ethernet destination MAC address": MA,
                                      "TCP/UDP source port": str(40000 + i)},
                      actions=["output:2"], priority=100) for i in range(n)]
        return m

    curve = [4, 6, 8, 10, 14, 16, 18, 22, 20, 18, 16, 12, 8, 6, 4]
    h = RouteHistory()
    forwarding_changes = sum(h.push(model(n), i * 2.5) for i, n in enumerate(curve))

    assert forwarding_changes == 1, "forwarding genuinely never changed — one snapshot"
    assert len(h.activity) == len(set(curve)) or len(h.activity) >= 10, \
        f"every distinct flow count is an activity point — got {len(h.activity)}"
    assert [n for _, n in h.activity][:4] == [4, 6, 8, 10]


def test_activity_is_not_recorded_when_the_count_holds_steady():
    """It must stay a record of change, not a sample every poll."""
    from gini.domain.flowtable import FlowEntry
    from gini.domain.routing_model import RouteHistory, assemble_model

    def model(n):
        m = assemble_model([], links=[], ovs_infos=[("ovs1", "OVS1", "", {}, None)])
        m.ovs["ovs1"].flows = [FlowEntry(index=i) for i in range(n)]
        return m

    h = RouteHistory()
    for i in range(8):
        h.push(model(5), i * 2.5)
    assert len(h.activity) == 1


def test_activity_and_forwarding_are_kept_apart():
    """They answer different questions — what the controller is DOING vs what the network
    DOES — and the renderer draws them on opposite sides of the axis for that reason."""
    from gini.domain.routing_model import RouteHistory
    h = RouteHistory()
    assert hasattr(h, "activity") and hasattr(h, "snaps")
    h.clear()
    assert h.activity == [] and h.snaps == []


# --- a switch caught mid-boot must not freeze a partial port map ------------------------------ #
def test_a_partial_port_map_is_not_cached_for_the_whole_run():
    """Reported live: a HUD left open across Run never lit, while one opened afterwards did.

    Switch containers take ~2s to bring their interfaces up. A poll landing mid-boot sees
    only some, ovs_port_peers rightly drops the unverifiable ones -- and caching that
    partial answer froze it for the entire run, so every rule egressing an unmapped port
    was permanently 'unverified'."""
    booting = ("Int. State/Mode Device IP MAC MTU Sock TID\n"
               "1 UC tun1 169.254.2.1 02:00:fe:02:00:01 1500 tun1 -1\n")   # only tun1 up
    ready = booting + "2 UC tun2 169.254.2.2 02:00:fe:02:00:02 1500 tun2 -2\n"
    replies = [booting, booting, ready]

    def query(name, cmd):
        if "verbose" in cmd:
            return replies.pop(0) if replies else ready
        return ENTRIES if name == "OVS1" else ROUTES

    cache = {}
    seen = []
    for _ in range(4):
        m = collect_network_data(
            routers=[], switches=[("ovs1", "OVS1", None)], query=query,
            delay_prop=lambda rid, k: "", links=[],
            neighbours_of=lambda rid: ["m1", "ovs2"],       # this switch has TWO links
            run_cache=cache)
        seen.append(len(m.ovs["ovs1"].port_peer))

    assert seen[0] == 1, "mid-boot: only the interface that exists is mapped"
    assert seen[-1] == 2, "once the switch is up, both ports map"
    assert cache["ports"]["ovs1"] == {2: "m1", 3: "ovs2"}, "the COMPLETE map is what sticks"


def test_a_complete_map_is_still_read_only_once():
    """The retry must not undo the caching that keeps the poll cheap."""
    calls = []
    ready = ("Int. State/Mode Device IP MAC MTU Sock TID\n"
             "1 UC tun1 169.254.2.1 02:00:fe:02:00:01 1500 tun1 -1\n"
             "2 UC tun2 169.254.2.2 02:00:fe:02:00:02 1500 tun2 -2\n")

    def query(name, cmd):
        calls.append(cmd)
        return ready if "verbose" in cmd else ENTRIES

    cache = {}
    for _ in range(5):
        collect_network_data(routers=[], switches=[("ovs1", "OVS1", None)], query=query,
                             delay_prop=lambda rid, k: "", links=[],
                             neighbours_of=lambda rid: ["m1", "ovs2"], run_cache=cache)
    assert len([c for c in calls if "verbose" in c]) == 1
