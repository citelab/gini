#!/usr/bin/env python3
"""Guards run_grouter.py's launch path — the seam the e2e tests don't exercise.

Regression for the `--confdir` bug: the gRouter binary only accepts --config /
--confpath / -p, so a wrong option name makes it exit(1) on every launch (the
container looked 'running' only because the supervisor used to hold it open).

  GROUTER_BIN=/path/to/grouter python3 test_run_grouter.py
"""
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import run_grouter


def test_cmd_uses_valid_option_spelling():
    cmd = run_grouter.grouter_cmd("/run/r1.conf", "/run", "r1")
    assert any(a.startswith("--config=") for a in cmd)
    assert any(a.startswith("--confpath=") for a in cmd)
    assert not any("confdir" in a for a in cmd), "regression: --confdir is not a real option"


def test_cmd_options_are_accepted_by_the_binary():
    """Every --long option in the launch argv must appear in `grouter --help`."""
    binary = os.environ.get("GROUTER_BIN", "/tmp/build/grouter")
    if not os.path.exists(binary):
        print("skip: no GROUTER_BIN"); return
    help_txt = subprocess.run([binary, "--help"], capture_output=True, text=True,
                              env=dict(os.environ, GINI_HOME="/tmp")).stdout
    known = set(re.findall(r"--([a-z]+)", help_txt))
    for arg in run_grouter.grouter_cmd("/run/r1.conf", "/run", "r1"):
        if arg.startswith("--"):
            opt = arg[2:].split("=")[0]
            assert opt in known, f"option --{opt} is NOT accepted by the gRouter binary"


def test_config_has_srcport_after_hwaddr():
    cfg = run_grouter.build_config({"name": "r1", "ifaces": [
        {"ip": "10.0.1.1/24", "mac": "02:00:00:01:01:01",
         "port": {"peer_host": "127.0.0.1", "peer_port": 5000, "bind_port": 5001}}]})
    line = next(l for l in cfg.splitlines() if l.startswith("ifconfig add tun1"))
    assert "-srcport 5001" in line
    assert line.index("-hwaddr") < line.index("-srcport")   # parser needs this order


def test_openflow_mode_adds_flag_and_drops_routes():
    # OVS mode: --openflow=<port> on the argv, ports come up but get NO routes
    cmd = run_grouter.grouter_cmd("/run/ovs1.conf", "/run", "ovs1", openflow_port=6633)
    assert "--openflow=6633" in cmd
    assert cmd[-1] == "ovs1"                                   # name stays last
    cfg = run_grouter.build_config({"name": "ovs1", "openflow": {"port": 6633},
        "ifaces": [
            {"ip": "169.254.0.1/16", "mac": "02:00:fe:00:00:01",
             "port": {"peer_host": "m1", "peer_port": 5000, "bind_port": 5001}}]})
    assert "ifconfig add tun1" in cfg
    assert "route add" not in cfg                              # a switch has no routes
    # and a normal router still gets its routes
    rcfg = run_grouter.build_config({"name": "r1", "ifaces": [
        {"ip": "10.0.1.1/24", "mac": "02:00:00:01:01:01",
         "port": {"peer_host": "127.0.0.1", "peer_port": 5000, "bind_port": 5001}}]})
    assert "route add" in rcfg


def _one_iface(ip, **extra):
    return run_grouter.build_config({"name": "r1", **extra, "ifaces": [
        {"ip": ip, "mac": "02:00:00:01:01:01",
         "port": {"peer_host": "127.0.0.1", "peer_port": 5000, "bind_port": 5001}}]})


def test_config_gives_the_router_its_real_netmask():
    """The router kept no mask and assumed /24 for every broadcast it worked out. -netmask is a
    trailing option, after -srcport (the fixed part of the parser ends at -hwaddr)."""
    line = next(l for l in _one_iface("10.0.1.1/24").splitlines() if l.startswith("ifconfig"))
    assert line.endswith("-netmask 255.255.255.0")
    assert line.index("-srcport") < line.index("-netmask")
    line = next(l for l in _one_iface("172.16.5.1/16").splitlines() if l.startswith("ifconfig"))
    assert line.endswith("-netmask 255.255.0.0")


def test_router_lab_delay_is_applied_at_boot():
    """Set in the Router Lab, saved on the router -- and, until now, lost on the next Run."""
    cfg = _one_iface("10.0.1.1/24", delay={"egress": [50, 5, 0.9], "ingress": [20, 0, 0]})
    assert "delay ingress 20 0 0" in cfg.splitlines()
    assert "delay egress 50 5 0.9" in cfg.splitlines()
    assert "delay " not in _one_iface("10.0.1.1/24")        # nothing set, nothing emitted


def test_config_passes_the_links_cost_to_the_router():
    """The compiler puts the drawn link's cost in port["link"]; the router learns it as the
    interface's metric, which Lua interfaces() reports as `cost`. Cost 1 adds nothing."""
    def cfg(link):
        return run_grouter.build_config({"name": "r1", "ifaces": [
            {"ip": "10.0.1.1/24", "mac": "02:00:00:01:01:01",
             "port": {"peer_host": "127.0.0.1", "peer_port": 5000, "bind_port": 5001,
                      "link": link}}]})
    line = next(l for l in cfg({"id": "link-3", "cost": 7}).splitlines()
                if l.startswith("ifconfig"))
    assert line.endswith("-metric 7")
    for plain in ({"id": "link-3", "cost": 1}, {"id": "link-3"}, {}, {"cost": 99}):
        assert "-metric" not in cfg(plain)


if __name__ == "__main__":
    test_cmd_uses_valid_option_spelling()
    test_cmd_options_are_accepted_by_the_binary()
    test_config_has_srcport_after_hwaddr()
    test_openflow_mode_adds_flag_and_drops_routes()
    test_config_gives_the_router_its_real_netmask()
    test_router_lab_delay_is_applied_at_boot()
    test_config_passes_the_links_cost_to_the_router()
    print("ok")
    test_openflow_mode_adds_flag_and_drops_routes()
    print("test_run_grouter: ALL PASS")
