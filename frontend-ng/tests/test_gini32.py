"""GINI32: real ESP32 boards attached to the emulated fabric.

Covers the three seams that make hardware-in-the-loop work:
  * the gbridge relay's wire format and board learning (runtime/gbridge.py),
  * the compiler emitting a board as a fabric endpoint rather than a container,
  * the orchestrator publishing the one UDP port a board can reach.
"""
import json
import socket
import threading
import time

import pytest

from gini.domain import devices as dev
from gini.domain import connection_rules as cr
from gini.domain.topology import Topology
from gini.runtime import gbridge as gb
from gini.services import orchestrator as orch
from gini.services.compiler import RuntimeCompiler, _valid_cidr


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _board_topology(mode="nat", subnet="10.0.9.0/24"):
    """The Ch.19 experiment: a real board, a router, a LAN, an emulated machine."""
    t = Topology()
    board = t.add_device("gini32", x=0, y=0)
    board.properties.update({"Mode": mode, "PhysicalSubnet": subnet})
    r1 = t.add_device("router", x=1, y=0)
    s1 = t.add_device("switch", x=2, y=0)
    m1 = t.add_device("host", x=3, y=0)
    t.add_link(board.id, r1.id)
    t.add_link(r1.id, s1.id)
    t.add_link(s1.id, m1.id)
    return t, board


# --------------------------------------------------------------- the element

def test_gini32_is_on_the_palette():
    d = dev.REGISTRY["gini32"]
    assert d.category is dev.Category.EXTERNAL
    assert d.max_links == 1                      # a board fronts exactly one segment
    assert set(d.property_choices["Mode"]) == {"nat", "routed"}
    assert "BoardID" in d.default_properties


def test_board_may_join_a_router_or_a_lan():
    assert cr.can_connect("gini32", "router")
    assert cr.can_connect("gini32", "switch")


# ------------------------------------------------------------- the wire format

@pytest.mark.parametrize("board_id", ["gini32-1", "b", "x" * 40])
@pytest.mark.parametrize("mtype", [gb.T_HELLO, gb.T_FRAME, gb.T_KEEPALIVE])
def test_header_round_trips(board_id, mtype):
    payload = b"\xde\xad\xbe\xef"
    t, bid, pay = gb.decode(gb.encode(mtype, board_id, payload))
    assert t == mtype
    assert bid == board_id[:gb.ID_LEN]           # over-long ids are truncated, not rejected
    assert pay == payload


def test_non_gini_datagrams_are_rejected():
    assert gb.decode(b"garbage") is None
    assert gb.decode(b"G32" + bytes([99]) + bytes(20)) is None      # wrong version
    assert gb.decode(b"") is None


def test_relay_shuttles_frames_and_learns_the_board():
    """A board that has never been seen is discovered from its own first datagram,
    and frames cross both ways with the fabric hop staying header-free."""
    listen, fab = _free_port(), _free_port()
    router = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    router.bind(("127.0.0.1", _free_port()))
    router.settimeout(3.0)

    relay = gb.GBridge({
        "listen_port": listen,
        "boards": [{"board_id": "gini32-1", "ip": "10.0.1.10", "mask": "255.255.255.0",
                    "gw": "10.0.1.1", "mac": "02:00:00:01:04:02",
                    "fabric": {"bind_host": "127.0.0.1", "bind_port": fab,
                               "peer_host": "127.0.0.1",
                               "peer_port": router.getsockname()[1]}}],
    })
    threading.Thread(target=relay.run, daemon=True).start()
    time.sleep(0.2)

    board = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    board.settimeout(3.0)

    # the board announces itself and is handed the identity the canvas assigned
    board.sendto(gb.encode(gb.T_HELLO, "gini32-1"), ("127.0.0.1", listen))
    mtype, _, payload = gb.decode(board.recvfrom(65535)[0])
    assert mtype == gb.T_HELLO_ACK
    cfg = dict(kv.split("=", 1) for kv in payload.decode().split())
    assert cfg["ip"] == "10.0.1.10" and cfg["gw"] == "10.0.1.1"
    assert relay.links["gini32-1"].online

    # board -> fabric: the router must receive a BARE Ethernet frame
    eth = b"\x02\x00\x00\x01\x00\x01" + b"\xaa\xbb\xcc\xdd\xee\xff" + b"\x08\x00" + b"up"
    board.sendto(gb.encode(gb.T_FRAME, "gini32-1", eth), ("127.0.0.1", listen))
    got, relay_addr = router.recvfrom(65535)
    assert got == eth

    # fabric -> board: wrapped again, delivered to the learned address
    down = b"\xaa\xbb\xcc\xdd\xee\xff" + b"\x02\x00\x00\x01\x00\x01" + b"\x08\x00" + b"dn"
    router.sendto(down, relay_addr)
    mtype, _, payload = gb.decode(board.recvfrom(65535)[0])
    assert mtype == gb.T_FRAME and payload == down


def test_keepalive_is_answered_so_a_live_link_is_never_declared_dead():
    """The board's only proof the relay still exists is a reply to its KEEPALIVE.

    Regression: the relay originally answered HELLO only, so a board that had come up
    (and therefore switched from HELLO to KEEPALIVE) heard nothing for 30s, decided the
    link was stale, and tore it down to re-run discovery — forever, on a 30s cycle.
    """
    listen = _free_port()
    relay = gb.GBridge({
        "listen_port": listen,
        "boards": [{"board_id": "gini32-1", "ip": "10.0.3.10", "mask": "255.255.255.0",
                    "gw": "10.0.3.1", "mac": "02:00:00:03:04:03",
                    "fabric": {"bind_host": "127.0.0.1", "bind_port": _free_port(),
                               "peer_host": "127.0.0.1", "peer_port": _free_port()}}],
    })
    threading.Thread(target=relay.run, daemon=True).start()
    time.sleep(0.2)

    board = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    board.settimeout(3.0)
    board.sendto(gb.encode(gb.T_HELLO, "gini32-1"), ("127.0.0.1", listen))
    assert gb.decode(board.recvfrom(65535)[0])[0] == gb.T_HELLO_ACK

    # now the steady state: every keepalive must draw a reply
    for _ in range(3):
        board.sendto(gb.encode(gb.T_KEEPALIVE, "gini32-1"), ("127.0.0.1", listen))
        mtype, _, payload = gb.decode(board.recvfrom(65535)[0])
        assert mtype == gb.T_HELLO_ACK, f"keepalive unanswered -> board will re-discover"
        # and the reply re-states the canvas config, so edits propagate to hardware
        assert b"ip=10.0.3.10" in payload


def test_two_boards_get_distinct_subnets_and_ssids():
    """Multi-board is only safe if nothing per-board comes from a shared default.

    Regression: every element defaulted to BoardID 'gini32-1' and PhysicalSubnet
    10.0.9.0/24, so a second board silently vanished from the relay's table and the
    routers got two routes to one network via different next hops.
    """
    t = Topology()
    r1 = t.add_device("router", x=2, y=0)
    for i in (1, 2):
        b = t.add_device("gini32", x=0, y=i)
        b.properties["BoardID"] = f"gini-{i}"        # from each board's label
        t.add_link(b.id, r1.id)
    t.add_link(r1.id, t.add_device("host", x=3, y=0).id)

    rt = RuntimeCompiler().compile(t).to_runtime(docker=True)
    subs = [b["physical_subnet"] for b in rt["gbridge"]]
    ssids = [b["ap_ssid"] for b in rt["gbridge"]]
    fabrics = [b["ip"] for b in rt["gbridge"]]
    assert len(set(subs)) == 2, f"boards share a physical subnet: {subs}"
    assert len(set(ssids)) == 2, f"boards raise the same SSID: {ssids}"
    assert len(set(fabrics)) == 2
    # and each subnet is routed to its own board
    routes = {r["net"]: r["gw"] for r in rt["routers"][0]["routes"]}
    for b in rt["gbridge"]:
        net = b["physical_subnet"].split("/")[0]
        assert routes.get(net) == b["ip"], f"{net} not routed via its own board"


def test_allocated_subnet_never_collides_with_a_topology_segment():
    t = Topology()
    r = t.add_device("router", x=0, y=0)
    for i in range(10):                       # forces segments 10.0.1 .. 10.0.11
        t.add_link(r.id, t.add_device("host", x=i + 1, y=0).id)
    b = t.add_device("gini32", x=0, y=5)
    b.properties["BoardID"] = "gini-9"
    t.add_link(r.id, b.id)

    cfg = RuntimeCompiler().compile(t)
    phys = cfg.gbridge[0].physical_subnet
    seg_octets = {c.split(".")[2] for c in cfg.subnets.values()}
    assert phys.split(".")[2] not in seg_octets, f"{phys} collides with a segment"


def test_routed_is_the_default_mode():
    """The design intent is bidirectional reachability; nat is the teaching exercise."""
    assert dev.REGISTRY["gini32"].default_properties["Mode"] == "routed"


def test_board_id_is_never_auto_generated():
    """BoardID names a physical object, so it must be blank until a human sets it."""
    assert dev.REGISTRY["gini32"].default_properties["BoardID"] == ""


def test_validate_flags_missing_and_duplicate_board_ids():
    from gini.services.compiler import validate
    t = Topology()
    r = t.add_device("router", x=1, y=0)
    b1 = t.add_device("gini32", x=0, y=0)
    t.add_link(b1.id, r.id)
    assert any("No BoardID" in i["message"] for i in validate(t))

    b1.properties["BoardID"] = "gini-5"
    b2 = t.add_device("gini32", x=0, y=1)
    b2.properties["BoardID"] = "gini-5"
    t.add_link(b2.id, r.id)
    assert any("also used by" in i["message"] for i in validate(t))


def test_telemetry_tracks_devices_joining_and_leaving_the_radio():
    """The board sends its FULL client list, so a lost datagram cannot strand a
    phantom device on the canvas."""
    link = gb.BoardLink({"board_id": "gini-5",
                         "fabric": {"bind_host": "127.0.0.1", "bind_port": _free_port(),
                                    "peer_host": "127.0.0.1", "peer_port": _free_port()}})
    link.note_telemetry(b"ch=6 rssi=-71 up=lab sta=2 "
                        b"c=aa:bb:cc:dd:ee:01/10.0.9.2 c=aa:bb:cc:dd:ee:02/10.0.9.3")
    assert link.channel == 6 and link.rssi == -71 and link.uplink == "lab"
    assert {c["mac"] for c in link.clients} == {"aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:02"}

    link.note_telemetry(b"ch=6 rssi=-70 up=lab sta=1 c=aa:bb:cc:dd:ee:01/10.0.9.2")
    assert [c["mac"] for c in link.clients] == ["aa:bb:cc:dd:ee:01"]

    link.note_telemetry(b"ch=6 rssi=-70 up=lab sta=0")
    assert link.clients == []

    # a keepalive with no telemetry at all must not wipe what we know
    link.note_telemetry(b"ch=6 rssi=-70 up=lab sta=1 c=aa:bb:cc:dd:ee:01/10.0.9.2")
    link.note_telemetry(b"")
    assert len(link.clients) == 1
    link.note_telemetry(b"\xff garbage rssi=nope ch=abc")     # must not raise


def test_hello_ack_carries_the_hotspot_the_canvas_chose():
    link = gb.BoardLink({"board_id": "gini-5", "ip": "10.0.1.10", "gw": "10.0.1.1",
                         "mode": "routed", "physical_subnet": "10.0.9.0/24",
                         "ap_ssid": "GINI32-GB1", "ap_pass": "gini12345",
                         "fabric": {"bind_host": "127.0.0.1", "bind_port": _free_port(),
                                    "peer_host": "127.0.0.1", "peer_port": _free_port()}})
    cfg = link.netcfg().decode()
    assert "apnet=10.0.9.0/24" in cfg
    assert "apssid=GINI32-GB1" in cfg
    assert "appass=gini12345" in cfg


# ------------------------------------------------------------------- claiming

def _relay(laptop_id, boards=()):
    r = gb.GBridge({"listen_port": _free_port(), "laptop_id": laptop_id,
                    "boards": list(boards)})
    threading.Thread(target=r.run, daemon=True).start()
    time.sleep(0.15)
    return r


def _hello(sock, relay, name, owner=""):
    sock.sendto(gb.encode(gb.T_HELLO, name, f"mac=aa:bb:cc:00:00:01 owner={owner}".encode()),
                ("127.0.0.1", relay.listen_port))
    time.sleep(0.2)


def test_an_unclaimed_board_is_offered_to_a_laptop():
    r = _relay("laptop-alice")
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    _hello(s, r, "GINI32-alice")
    avail = r.available()
    assert [b["name"] for b in avail] == ["GINI32-alice"]
    assert avail[0]["claimed"] is False


def test_a_claimed_board_is_invisible_to_every_other_laptop():
    """THE point of claiming: thirty students in one room cannot take each other's
    hardware, because a board owned by someone else is never even listed."""
    bob = _relay("laptop-bob")
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    _hello(s, bob, "GINI32-alice", owner="laptop-alice")     # already Alice's
    assert bob.available() == [], "another laptop's board must not be visible"
    assert bob.claim("GINI32-alice") is False, "and must not be claimable"


def test_claim_is_delivered_on_the_next_contact():
    alice = _relay("laptop-alice")
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(2.0)
    _hello(s, alice, "GINI32-alice")
    assert alice.claim("GINI32-alice") is True
    _hello(s, alice, "GINI32-alice")                # the claim rides this contact
    mtype, name, payload = gb.decode(s.recvfrom(65535)[0])
    assert mtype == gb.T_CLAIM and name == "GINI32-alice"
    assert b"owner=laptop-alice" in payload


def test_release_returns_a_board_to_the_pool():
    alice = _relay("laptop-alice")
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(2.0)
    _hello(s, alice, "GINI32-alice", owner="laptop-alice")
    assert alice.release("GINI32-alice") is True
    _hello(s, alice, "GINI32-alice", owner="laptop-alice")
    mtype, _, _ = gb.decode(s.recvfrom(65535)[0])
    assert mtype == gb.T_RELEASE
    # and a different laptop can now see it once it announces itself free
    bob = _relay("laptop-bob")
    _hello(s, bob, "GINI32-alice")
    assert [b["name"] for b in bob.available()] == ["GINI32-alice"]


def test_blink_reaches_the_board():
    """Claiming by name is useless if you cannot tell which physical object it is."""
    alice = _relay("laptop-alice")
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(2.0)
    _hello(s, alice, "GINI32-alice")
    assert alice.blink("GINI32-alice") is True
    _hello(s, alice, "GINI32-alice")
    mtype, _, _ = gb.decode(s.recvfrom(65535)[0])
    assert mtype == gb.T_BLINK


def test_an_unclaimed_board_with_no_role_is_seen_but_not_configured():
    """Visible is not the same as adopted. A board this canvas has no element for must
    never be handed a fabric address just because it said hello — it may be someone
    else's, still waiting for its owner to sit down."""
    r = _relay("laptop-alice")                       # no boards on the canvas
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(0.6)
    _hello(s, r, "GINI32-stranger")
    assert [b["name"] for b in r.available()] == ["GINI32-stranger"]   # seen
    with pytest.raises(socket.timeout):
        s.recvfrom(65535)                            # but never answered


def test_using_an_unclaimed_board_claims_it():
    """Drawing the element and pressing Run IS the intent to use that board, so it is
    claimed on use — a separate click would be ceremony for the common case."""
    fab, peer = _free_port(), _free_port()
    r = _relay("laptop-alice", boards=[
        {"board_id": "GINI32-alice", "ip": "10.0.1.10", "gw": "10.0.1.1",
         "fabric": {"bind_host": "127.0.0.1", "bind_port": fab,
                    "peer_host": "127.0.0.1", "peer_port": peer}}])
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(2.0)
    _hello(s, r, "GINI32-alice")                     # unclaimed, but we have a role
    mtype, _, payload = gb.decode(s.recvfrom(65535)[0])
    assert mtype == gb.T_CLAIM and b"owner=laptop-alice" in payload


def test_status_exposes_available_boards_and_our_identity():
    r = _relay("laptop-alice")
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    _hello(s, r, "GINI32-alice")
    st = r.status()
    assert st["laptop_id"] == "laptop-alice"
    assert [b["name"] for b in st["available"]] == ["GINI32-alice"]


def test_relay_survives_unknown_and_malformed_traffic():
    relay = gb.GBridge({"listen_port": _free_port(), "boards": []})
    relay._from_board(gb.encode(gb.T_FRAME, "ghost", b"x"), ("127.0.0.1", 1))
    relay._from_board(b"not-a-g32-datagram", ("127.0.0.1", 1))
    assert relay.unknown == 1 and relay.malformed == 1


def test_frames_for_a_board_that_never_checked_in_are_dropped():
    relay = gb.GBridge({
        "listen_port": _free_port(),
        "boards": [{"board_id": "absent",
                    "fabric": {"bind_host": "127.0.0.1", "bind_port": _free_port(),
                               "peer_host": "127.0.0.1", "peer_port": _free_port()}}],
    })
    link = relay.links["absent"]
    relay._from_fabric(link, b"\x00" * 60)
    assert link.dropped == 1 and not link.online


# ---------------------------------------------------------------- the compiler

def test_board_compiles_to_an_endpoint_not_a_container():
    t, _ = _board_topology()
    rt = RuntimeCompiler().compile(t).to_runtime(docker=True)

    assert len(rt["gbridge"]) == 1
    board = rt["gbridge"][0]
    # BoardID is blank by default (it names a physical object), so the compiler falls
    # back to the element's own name rather than inventing a shared default that two
    # boards would collide on.
    assert board["board_id"] == "gb1"
    assert board["ip"].startswith("10.0.")
    assert board["gw"], "the board needs the router as its gateway"
    # it is NOT a machine: no container is created for real hardware
    assert not any(m["name"].startswith("gb") for m in rt["machines"])
    # and its fabric peer is the router container
    assert board["fabric"]["peer_host"] == "r1"


def test_router_talks_to_the_relay_not_the_hardware():
    """The gRouter must be unaware that hardware is involved — its peer is a service."""
    t, _ = _board_topology()
    rt = RuntimeCompiler().compile(t).to_runtime(docker=True)
    peers = {i["port"]["peer_host"] for i in rt["routers"][0]["ifaces"]}
    assert "gbridge" in peers


def test_routed_mode_gets_a_route_to_the_physical_subnet():
    t, _ = _board_topology(mode="routed", subnet="10.0.9.0/24")
    rt = RuntimeCompiler().compile(t).to_runtime(docker=True)
    board = rt["gbridge"][0]
    assert board["mode"] == "routed"
    routes = rt["routers"][0]["routes"]
    phys = [r for r in routes if r["net"] == "10.0.9.0"]
    assert phys, f"no route to the physical subnet: {routes}"
    assert phys[0]["gw"] == board["ip"]


def test_nat_mode_serves_a_subnet_but_publishes_no_route():
    """The board ALWAYS runs a hotspot subnet — `mode` only decides whether the
    emulated side gets a route to it, or the devices stay hidden behind NAT."""
    t, _ = _board_topology(mode="nat")
    rt = RuntimeCompiler().compile(t).to_runtime(docker=True)
    assert rt["gbridge"][0]["physical_subnet"], "the board still needs a subnet to serve"
    assert not [r for r in rt["routers"][0]["routes"] if r["net"] == "10.0.9.0"], \
        "nat mode must NOT advertise the physical subnet into the topology"


def test_invalid_physical_subnet_is_replaced_not_fatal():
    """A typo should cost you a note, not silently downgrade the board to NAT."""
    t, _ = _board_topology(mode="routed", subnet="not-a-cidr")
    cfg = RuntimeCompiler().compile(t)
    assert cfg.gbridge[0].mode == "routed", "a typo must not change the chosen mode"
    assert _valid_cidr(cfg.gbridge[0].physical_subnet), "should be given a usable subnet"
    assert any("PhysicalSubnet" in n for n in cfg.notes), "and the student is told"


def test_two_boards_share_one_relay():
    t = Topology()
    r1 = t.add_device("router", x=1, y=0)
    for i in (1, 2):
        b = t.add_device("gini32", x=0, y=i)
        b.properties["BoardID"] = f"gini32-{i}"
        t.add_link(b.id, r1.id)
    rt = RuntimeCompiler().compile(t).to_runtime(docker=True)
    assert {b["board_id"] for b in rt["gbridge"]} == {"gini32-1", "gini32-2"}
    # distinct fabric ports, one shared relay
    ports = {b["fabric"]["bind_port"] for b in rt["gbridge"]}
    assert len(ports) == 2


# ------------------------------------------------------------- the orchestrator

def test_compose_publishes_the_relay_port():
    t, _ = _board_topology()
    compose = orch._compose(RuntimeCompiler().compile(t))
    assert "  gbridge:" in compose
    assert f"{orch.GBRIDGE_HOST_PORT}:{orch.GBRIDGE_PORT}/udp" in compose
    assert "dataplane.gbridge" in compose


def test_no_relay_when_no_board_is_drawn():
    t = Topology()
    r1 = t.add_device("router", x=0, y=0)
    m1 = t.add_device("host", x=1, y=0)
    t.add_link(r1.id, m1.id)
    cfg = RuntimeCompiler().compile(t)
    assert cfg.gbridge == []
    assert "  gbridge:" not in orch._compose(cfg)


def test_relay_config_in_compose_is_valid_json_the_relay_accepts():
    """The compose env is the relay's actual input — it must construct a GBridge."""
    t, board = _board_topology()
    # A BoardID names a physical object (the sticker), so a human sets it — same as every other
    # board test. Asserting on the compiler's blank-BoardID fallback would just pin the auto-name.
    board.properties["BoardID"] = "gini32-1"
    compose = orch._compose(RuntimeCompiler().compile(t))
    raw = compose.split("GBRIDGE_CONFIG: '")[1].split("'\n")[0]
    cfg = json.loads(raw)
    cfg["listen_port"] = _free_port()
    for b in cfg["boards"]:                      # rebind off the container ports
        b["fabric"]["bind_host"] = "127.0.0.1"
        b["fabric"]["bind_port"] = _free_port()
    relay = gb.GBridge(cfg)
    assert [l.board_id for l in relay.links.values()] == ["gini32-1"]
    assert b"ip=10.0.1.10" in relay.links["gini32-1"].netcfg()


# --------------------------------------------------- the id-mismatch trap

def test_a_board_announcing_under_another_id_is_still_offered():
    """The failure that cost a whole bring-up round.

    An element asking for a BoardID no board carries looks, from every angle, like a
    board that has not arrived: the relay turns the board away, the board keeps
    searching, and neither end can see the other's expectation. What makes it
    recoverable is that the real board is STILL listed as available — that list is
    what lets the Inspector say "no board called X is here, but these are".
    """
    r = _relay("laptop-alice", boards=[])
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    _hello(s, r, "gini-5")                       # the hardware's actual label

    names = [b["name"] for b in r.available()]
    assert names == ["gini-5"]                   # offered, despite matching no element
    assert "gini32-1" not in r.links             # and the wrong id matched nothing


def test_path_health_counters_separate_a_quiet_board_from_a_moving_one():
    """Flakiness has several candidate causes; these counters tell them apart.

    A long silence points at the radio or power; a changed source address points at the
    Docker port translation re-mapping us, which breaks the RETURN path only — a fault
    the board cannot detect, because its own transmits keep succeeding.
    """
    link = gb.BoardLink.__new__(gb.BoardLink)
    link.addr = None
    link.last_seen = 0.0
    link.worst_gap_s, link.addr_changes, link.late = 0.0, 0, 0

    here, moved_to = ("192.168.0.51", 1234), ("192.168.0.51", 5678)
    assert link.seen(here) is False               # first contact is not a gap
    assert (link.worst_gap_s, link.late, link.addr_changes) == (0.0, 0, 0)

    link.last_seen = time.time() - 12             # a silence, at >2x the 5s keepalive
    assert link.seen(here) is False
    assert link.late == 1 and link.worst_gap_s >= 12 and link.addr_changes == 0

    assert link.seen(moved_to) is True            # same board, translated port moved
    assert link.addr_changes == 1


def test_a_board_claimed_by_a_vanished_laptop_is_reported_not_hidden():
    """An orphaned board must not look like a dead one.

    If this install's laptop_id changes — a wiped config, a reinstall — every board it
    claimed keeps announcing an owner that no longer exists. The relay is right to
    ignore those boards, but silently ignoring them makes working hardware look broken,
    and the only cure (USB `unpair`) is the one nobody thinks to try.
    """
    r = _relay("laptop-NEW")                      # this install, with a fresh identity
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    _hello(s, r, "gini-5", owner="laptop-OLD")    # the board still names the old one

    st = r.status()
    assert st["available"] == []                  # correctly not offered
    assert [f["board_id"] for f in st["foreign"]] == ["gini-5"]
    assert st["foreign"][0]["owner"] == "laptop-OLD"
