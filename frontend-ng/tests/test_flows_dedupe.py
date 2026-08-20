"""Flow HUD chip hygiene: one chip per live connection, and none per dead one.

Three things made the panel fill with look-alike chips:
  * a dual-stack listener (iperf3 -s) reports peers as [::ffff:10.0.4.10] while the client
    reports 10.0.4.10, so one connection was counted as two flows;
  * chips were labelled by IP pair only, so the several connections iperf3 opens between the
    same two stations were indistinguishable;
  * a series was never retired, so every finished transfer left its chip behind.
"""
from gini.domain.flows import FlowTracker, _norm_ip, parse_ss

_CLIENT = """ESTAB 0 0 10.0.4.10:52134 10.0.7.10:5201
     cubic wscale:7,7 rtt:24.2/1.1 mss:1448 cwnd:102 ssthresh:80 retrans:0/12 delivery_rate 54.8Mbps"""
# the SAME connection, as the dual-stack server sees it
_SERVER = """ESTAB 0 0 [::ffff:10.0.7.10]:5201 [::ffff:10.0.4.10]:52134
     cubic wscale:7,7 rtt:24.2/1.1 mss:1448 cwnd:10 retrans:0/0 delivery_rate 1.0Mbps"""
_SECOND = """ESTAB 0 0 10.0.4.10:52140 10.0.7.10:5201
     cubic rtt:24.0/1.0 mss:1448 cwnd:44 retrans:0/1 delivery_rate 20.0Mbps"""


def test_ipv4_mapped_ipv6_is_canonicalised():
    assert _norm_ip("[::ffff:10.0.7.10]") == "10.0.7.10"
    assert _norm_ip("::ffff:10.0.4.10") == "10.0.4.10"
    assert _norm_ip("10.0.4.10") == "10.0.4.10"


def test_both_endpoint_views_are_one_flow():
    t = FlowTracker()
    t.ingest(parse_ss(_CLIENT, "M1") + parse_ss(_SERVER, "M2"), 100.0)
    flows = t.active()
    assert len(flows) == 1
    assert flows[0].cwnd[-1] == 102          # the sender's view is the representative


def test_distinct_connections_get_distinct_chips():
    t = FlowTracker()
    t.ingest(parse_ss(_CLIENT, "M1") + parse_ss(_SECOND, "M1"), 100.0)
    labels = sorted(f.label for f in t.active())
    assert labels == ["10.0.4.10:52134 -> 10.0.7.10:5201",
                      "10.0.4.10:52140 -> 10.0.7.10:5201"]


def test_finished_flow_is_retired():
    t = FlowTracker()
    t.ingest(parse_ss(_CLIENT, "M1") + parse_ss(_SECOND, "M1"), 100.0)
    t.ingest(parse_ss(_SECOND, "M1"), 105.0)          # first still within IDLE_S
    assert len(t.active()) == 2
    t.ingest(parse_ss(_SECOND, "M1"), 100.0 + FlowTracker.IDLE_S + 5.0)
    labels = [f.label for f in t.active()]
    assert labels == ["10.0.4.10:52140 -> 10.0.7.10:5201"]
