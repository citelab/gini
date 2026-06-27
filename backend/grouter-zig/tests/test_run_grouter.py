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


if __name__ == "__main__":
    test_cmd_uses_valid_option_spelling()
    test_cmd_options_are_accepted_by_the_binary()
    test_config_has_srcport_after_hwaddr()
    test_openflow_mode_adds_flag_and_drops_routes()
    print("test_run_grouter: ALL PASS")
