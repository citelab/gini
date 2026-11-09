"""The Flow HUD plots the experiment's traffic, not GINI's own, and follows it between runs.

Reported: with a terminal open on M2 and iperf3 running to M1, the panel plotted a flat cwnd of
10 for `172.18.0.4:7681 -> 172.18.0.1:…` and showed the iperf3 sawtooth only occasionally. Two
causes, one test group each:
  * the poll listed every socket in the container, including the in-app terminal's websocket to
    ttyd on the management NIC (eth0) — always present, present first, never closing;
  * the plotted flow was chosen once, by cwnd, and kept while it existed, so the terminal
    connection, picked before iperf3 started, was never let go.
"""
from types import SimpleNamespace

from gini.domain.flows import FlowTracker, management_ips, station_flows
from gini.ui.flow_hud import FlowHud

# What STATION_CMD prints in a GINI machine: `ip -o addr show dev eth0`, then `ss -tin`.
_IP = ("4: eth0    inet 172.18.0.2/16 brd 172.18.255.255 scope global eth0\\"
       "       valid_lft forever preferred_lft forever\n")


def _sock(local, peer, cwnd=10, acked=0):
    return (f"ESTAB 0 0 {local} {peer}\n"
            f"\t cubic wscale:7,7 rtt:3.0/2.1 mss:1448 cwnd:{cwnd} bytes_acked:{acked} "
            f"retrans:0/0 delivery_rate 3861333328bps app_limited\n")


_HDR = "State Recv-Q Send-Q Local Address:Port Peer Address:Port\n"
_TERMINAL = _sock("172.18.0.2:7681", "172.18.0.1:62102", acked=7111)


def _m2(*socks):
    return _IP + _HDR + _TERMINAL + "".join(socks)


def _iperf(acked, port=33170, cwnd=150):
    return _sock(f"10.0.2.10:{port}", "10.0.1.10:5201", cwnd=cwnd, acked=acked)


_CONTROL = _sock("10.0.2.10:33160", "10.0.1.10:5201", acked=300)


def test_the_management_nics_addresses_are_read_from_ip():
    assert management_ips(_IP) == {"172.18.0.2"}
    assert management_ips("") == set()


def test_a_terminal_connection_on_eth0_is_not_a_flow():
    labels = [s.label for s in station_flows(_m2(_iperf(10_000)), "M2")]
    assert labels == ["10.0.2.10:33170 -> 10.0.1.10:5201"]


def test_without_ip_output_every_socket_is_kept():
    assert len(station_flows(_HDR + _TERMINAL, "M2")) == 1


def test_the_sender_is_the_side_whose_bytes_were_acknowledged():
    receiver = _sock("10.0.1.10:5201", "10.0.2.10:33170", cwnd=10, acked=0)
    t = FlowTracker()
    t.ingest(station_flows(_m2(_iperf(5_000_000, cwnd=8)), "M2")
             + station_flows(receiver, "M1"), 1.0)
    [f] = t.active()
    assert f.label.startswith("10.0.2.10:33170")      # cwnd 8 < 10, but it is the sender


def _choose(hud, tracker):
    hud._selected = FlowHud._choose(hud, tracker)
    return hud._selected


def _hud():
    return SimpleNamespace(_selected=None, _pinned=False)


def test_the_plot_follows_the_flow_that_is_moving_bytes():
    t, hud = FlowTracker(), _hud()
    t.ingest(station_flows(_m2(_CONTROL), "M2"), 0.0)
    _choose(hud, t)
    for i, tick in enumerate((1.0, 2.0, 3.0), start=1):
        t.ingest(station_flows(_m2(_CONTROL, _iperf(i * 25_000_000)), "M2"), tick)
        key = _choose(hud, t)
    assert t.series[key].label == "10.0.2.10:33170 -> 10.0.1.10:5201"
    assert t.series[key].sent_bps == 200_000_000


def test_a_finished_run_stays_on_screen_and_the_next_run_takes_over():
    t, hud = FlowTracker(), _hud()
    for tick in (1.0, 2.0):
        t.ingest(station_flows(_m2(_iperf(int(tick) * 25_000_000)), "M2"), tick)
        first = _choose(hud, t)
    t.ingest(station_flows(_m2(), "M2"), 3.0)                 # run 1 ended
    assert _choose(hud, t) == first
    for tick in (4.0, 5.0):                                   # run 2, a new port
        t.ingest(station_flows(_m2(_iperf(int(tick) * 25_000_000, port=33180)), "M2"), tick)
        second = _choose(hud, t)
    assert second != first and "33180" in t.series[second].label


def test_a_clicked_chip_holds_while_its_socket_is_open_and_lets_go_when_it_closes():
    t, hud = FlowTracker(), _hud()
    t.ingest(station_flows(_m2(_CONTROL, _iperf(0)), "M2"), 1.0)
    control = next(k for k, f in t.series.items() if "33160" in f.label)
    hud._selected, hud._pinned = control, True                # the student clicked it
    t.ingest(station_flows(_m2(_CONTROL, _iperf(50_000_000)), "M2"), 2.0)
    assert _choose(hud, t) == control                         # iperf is busier; the pin holds
    t.ingest(station_flows(_m2(_iperf(100_000_000)), "M2"), 3.0)
    t.ingest(station_flows(_m2(_iperf(150_000_000)), "M2"), 3.0 + FlowTracker.LIVE_S + 1)
    assert "33170" in t.series[_choose(hud, t)].label         # control closed: follow again
    assert hud._pinned is False
