"""Rider execution — the pure command-builder + measurement-parser, and the runner wiring.

`domain.riders` is Docker-free and fully testable: it turns a rider's properties into the argv to
exec on its donor, and turns real tool output (ping / curl / tcpdump) into one gradable scalar. The
`RiderRunner` glue is checked with a fake orchestrator so the donor-resolution + parse path is
covered without a live stack.
"""
import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["GINI_HOME_DIR"] = tempfile.mkdtemp()

import pytest

from gini.domain import riders as R
from gini.domain.topology import Topology


# -- build_command ----------------------------------------------------------- #
def test_ping_command_and_missing_target_is_refused():
    assert R.build_command("ping_probe", {"Target": "M2", "Count": "3"}) == \
        ["ping", "-c", "3", "-W", "1", "M2"]
    with pytest.raises(R.RiderError):
        R.build_command("ping_probe", {"Target": ""})     # no target → prompt, don't run junk


def test_http_command_builds_a_counted_curl_loop():
    argv = R.build_command("http_probe", {"Target": "web", "Path": "status", "Count": "2"})
    assert argv[:2] == ["sh", "-lc"]
    assert "http://web/status" in argv[2] and "seq 2" in argv[2]


def test_packet_view_captures_the_overlay_by_default():
    # default interface is gini0 (the drawn network), NOT `any` (which leaks Docker-bridge traffic);
    # Count N → stop after N packets; Count 0 → capture continuously (no -c), line-buffered
    assert R.build_command("packet_view", {"Count": "10", "Filter": "icmp"}) == \
        ["tcpdump", "-n", "-l", "-i", "gini0", "-c", "10", "icmp"]
    assert R.build_command("packet_view", {"Count": "0"}) == \
        ["tcpdump", "-n", "-l", "-i", "gini0"]
    assert R.build_command("packet_view", {"Interface": "eth0"}) == \
        ["tcpdump", "-n", "-l", "-i", "eth0"]      # overridable to the bridge for advanced use


def test_iface_stats_hides_the_docker_bridge():
    dev = ("Inter-|   Receive\n face |bytes packets\n"
           " eth0: 3390 35 0 0 0 0 0 0  1622 18 0 0 0 0 0 0\n"      # Docker bridge — hidden
           " gini0: 776 10 0 0 0 0 0 0  866 11 0 0 0 0 0 0\n===\n")  # overlay — reported
    m = R.parse_measurement("iface_stats", dev)
    assert m["ok"] and m["rx_packets"] == 10 and m["tx_packets"] == 11   # gini0 only, not eth0


def test_ping_and_http_go_continuous_at_count_zero():
    assert R.build_command("ping_probe", {"Target": "M2", "Count": "0"}) == \
        ["ping", "-W", "1", "M2"]                       # no -c → pings until stopped
    argv = R.build_command("http_probe", {"Target": "web", "Count": "0"})
    assert "while true" in argv[2] and "http://web/" in argv[2]


def test_streaming_ping_lines_reduce_to_loss_and_rtt_without_a_summary():
    # continuous ping never prints the summary — derive loss/rtt from per-packet lines
    stream = ("64 bytes from 10.0.1.2: icmp_seq=0 ttl=64 time=0.10 ms\n"
              "64 bytes from 10.0.1.2: icmp_seq=1 ttl=64 time=0.20 ms\n"
              "64 bytes from 10.0.1.2: icmp_seq=3 ttl=64 time=0.30 ms\n")   # seq 2 dropped
    m = R.parse_measurement("ping_probe", stream)
    assert m["ok"] and m["received"] == 3 and m["transmitted"] == 4
    assert m["loss_pct"] == 25.0 and m["rtt_avg_ms"] == 0.2


# -- parse_measurement (real tool output) ------------------------------------ #
def test_ping_output_reduces_to_loss_and_avg_rtt():
    raw = ("PING M2 (10.0.1.2): 56 data bytes\n"
           "64 bytes from 10.0.1.2: icmp_seq=0 ttl=64 time=0.075 ms\n"
           "--- M2 ping statistics ---\n"
           "5 packets transmitted, 5 packets received, 0% packet loss\n"
           "round-trip min/avg/max/stddev = 0.075/0.101/0.140/0.025 ms\n")
    m = R.parse_measurement("ping_probe", raw)
    assert m["ok"] and m["loss_pct"] == 0.0 and m["rtt_avg_ms"] == 0.101
    assert m["transmitted"] == 5 and m["received"] == 5
    assert "0% loss" in R.summarize("ping_probe", m)


def test_http_output_reduces_to_ok_percentage():
    raw = "200 0.012\n200 0.010\n500 0.004\n"
    m = R.parse_measurement("http_probe", raw)
    assert m["requests"] == 3 and m["ok_count"] == 2 and m["ok_pct"] == 66.7


def test_tcpdump_output_reduces_to_a_packet_count():
    raw = ("12:00:01.123456 IP 10.0.1.1 > 10.0.1.2: ICMP echo request\n"
           "20 packets captured\n20 packets received by filter\n")
    assert R.parse_measurement("packet_view", raw)["packets"] == 20


def test_idle_capture_is_zero_packets_not_a_failure():
    # tcpdump ran (printed its banner) but the link was quiet — that's a real reading of 0
    idle = "tcpdump: verbose output suppressed\nlistening on any, link-type LINUX_SLL2\n"
    m = R.parse_measurement("packet_view", idle)
    assert m["ok"] is True and m["packets"] == 0
    # a genuine failure (no tool / no perms) is NOT ok
    assert R.parse_measurement("packet_view", "sh: tcpdump: not found")["ok"] is False


def test_unparseable_output_is_a_no_reading_not_a_crash():
    assert R.parse_measurement("ping_probe", "garbage")["ok"] is False
    assert R.summarize("ping_probe", {"ok": False}) == "no reading"


# -- RiderRunner wiring (fake orchestrator, no Docker) ----------------------- #
class _FakeOrch:
    _dc = ["docker", "compose"]
    workdir = None

    def status(self):
        return {"m2": "running"}


def test_runner_resolves_the_donor_and_returns_a_measurement():
    from gini.services.rider_runner import RiderRunner

    t = Topology()
    m2 = t.add_device("host", "M2")
    ping = t.add_device("ping_probe", "PING1", properties={"Target": "M2", "Count": "3"})
    t.add_attach(ping.id, m2.id)

    sample = ("5 packets transmitted, 5 packets received, 0% packet loss\n"
              "round-trip min/avg/max = 0.1/0.2/0.3 ms\n")

    rr = RiderRunner(_FakeOrch())
    rr._exec = lambda service, argv: (0, sample) if service == "m2" else (1, "")  # type: ignore
    res = rr.run(t, ping.id)
    assert res["ok"] and res["donor"] == "M2"
    assert res["measurement"]["loss_pct"] == 0.0
    assert "loss" in res["summary"]


def test_runner_infers_a_target_when_none_is_set():
    from gini.services.rider_runner import RiderRunner

    t = Topology()
    m1 = t.add_device("host", "M1")
    m2 = t.add_device("host", "M2")
    t.add_link(m1.id, m2.id)
    ping = t.add_device("ping_probe", "PING1")          # NO Target set
    t.add_attach(ping.id, m1.id)                         # rides M1, wired to M2

    captured = {}
    rr = RiderRunner(_FakeOrch())
    def fake_exec(service, argv):
        captured["argv"] = argv
        return (0, "1 packets transmitted, 1 packets received, 0% packet loss\n")
    rr._exec = fake_exec                                 # type: ignore
    res = rr.run(t, ping.id)
    assert res["inferred_target"] == "M2"               # auto-picked the wired neighbour
    assert captured["argv"][-1] == "M2"                  # …and pinged it


def test_empty_output_becomes_a_diagnostic_not_a_blank():
    from gini.services.rider_runner import RiderRunner
    t = Topology()
    m = t.add_device("host", "M1")
    ping = t.add_device("ping_probe", "PING1", properties={"Target": "M2"})
    t.add_attach(ping.id, m.id)
    rr = RiderRunner(_FakeOrch())
    rr._exec = lambda service, argv: (127, "")          # tool ran, produced nothing
    res = rr.run(t, ping.id)
    assert res["ok"] and "no output" in res["raw"] and "exit 127" in res["raw"]
    assert "ping" in res["raw"]                          # the exact command is shown


def test_new_network_rider_commands():
    assert R.build_command("traceroute_probe", {"Target": "cloud"}) == \
        ["traceroute", "-n", "-w", "2", "cloud"]
    assert R.build_command("iperf_client", {"Target": "M2", "Seconds": "5"}) == \
        ["iperf3", "-c", "M2", "-t", "5"]
    assert R.build_command("iperf_server", {}) == ["iperf3", "-s"]
    dns = R.build_command("dns_probe", {"Target": "db", "Count": "0"})
    # getent (respects /etc/hosts → overlay), not dig (which bypasses hosts to Docker's bridge DNS)
    assert dns[:2] == ["sh", "-lc"] and "getent hosts db" in dns[2] and "while true" in dns[2]
    assert "%T" in dns[2] and "dig" not in dns[2]
    # old saved probes used 'Query' — still honoured as a fallback
    assert "web" in R.build_command("dns_probe", {"Query": "web"})[2]


def test_overlay_hosts_maps_names_to_gini0_ips():
    from gini.services.compiler import overlay_hosts
    addressing = {"M1": {"interfaces": [{"ip": "10.0.1.10/24"}]},
                  "M2": {"interfaces": [{"ip": "10.0.1.11/24"}]},
                  "S1": {"interfaces": []}}          # a switch has no addressed interface → skipped
    assert overlay_hosts(addressing) == {"M1": "10.0.1.10", "M2": "10.0.1.11"}


def test_iperf_client_caps_the_rate_by_default_and_can_opt_out():
    assert R.build_command("iperf_client", {"Target": "M2", "Seconds": "5", "Bitrate": "100M"}) == \
        ["iperf3", "-c", "M2", "-t", "5", "-b", "100M"]
    assert R.build_command("iperf_client", {"Target": "M2", "Bitrate": "0"}) == \
        ["iperf3", "-c", "M2", "-t", "10"]              # unlimited opts out of the cap


def test_new_network_rider_parsers_and_summaries():
    m = R.parse_measurement("iperf_client", "[ 5]  0-10 sec  1.10 GBytes  942 Mbits/sec receiver\n")
    assert m["mbps"] == 942.0 and "Mbit/s" in R.summarize("iperf_client", m)
    m = R.parse_measurement("traceroute_probe", " 1  10.0.1.1  0.1 ms\n 2  10.0.2.1  0.2 ms\n")
    assert m["hops"] == 2 and "hops" in R.summarize("traceroute_probe", m)
    m = R.parse_measurement("dns_probe", "12:00:01 10.0.1.5\n12:00:02 \n")
    assert m["resolved"] == 1 and m["queries"] == 2


def test_xv6_shell_and_workload_commands():
    assert R.xv6_command("xv6_shell", {"Command": "ls"}) == "ls"
    assert R.xv6_command("xv6_workload",
                         {"Program": "spin", "Args": "10", "Background": "true"}) == "spin 10 &"
    assert R.xv6_command("xv6_workload",
                         {"Program": "forktest", "Background": "false"}) == "forktest"
    with __import__("pytest").raises(R.RiderError):
        R.xv6_command("xv6_shell", {"Command": ""})     # needs a command
    # xv6 riders are not docker commands
    with __import__("pytest").raises(R.RiderError):
        R.build_command("xv6_shell", {"Command": "ls"})


def test_runtime_runner_measure_reads_the_live_rider_snapshot():
    from gini.domain.probes import TypeRunner
    from gini.services import probe_runner as PR

    t = Topology()
    m2 = t.add_device("host", "M2")
    pv = t.add_device("packet_view", "PCAP1")
    t.add_attach(pv.id, m2.id)
    results = {pv.id: {"measurement": {"ok": True, "packets": 7}}}   # a live streaming snapshot

    class _Orch:
        _dc = ["docker", "compose"]
        workdir = None

    rr = PR.RuntimeRunner(_Orch(), lambda: t, lambda: results)
    assert rr.measure("packet_view", "packets") == 7               # reads the streamed reading
    assert TypeRunner(rr, lambda: t).measure("packet_view", "packets") == 7   # via the type runner
    pv.slot = "A"
    assert rr.measure("packet_view@A", "packets") == 7             # slot-scoped resolves
    assert rr.measure("packet_view@B", "packets") is None          # no rider in slot B
    assert PR.RuntimeRunner(_Orch(), lambda: t, lambda: {}).measure(  # no snapshot yet → no reading
        "packet_view", "packets") is None


def test_runner_reports_an_unattached_rider_instead_of_running():
    from gini.services.rider_runner import RiderRunner
    t = Topology()
    ping = t.add_device("ping_probe", "PING1", properties={"Target": "M2"})
    res = RiderRunner(_FakeOrch()).run(t, ping.id)      # no attach edge
    assert res["ok"] is False and "attached" in res["error"]
