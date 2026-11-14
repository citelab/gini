#!/usr/bin/env python3
"""Guards run_grouter.py's launch path — the seam the e2e tests don't exercise.

Regression for the `--confdir` bug: the gRouter binary only accepts --config /
--confpath / -p, so a wrong option name makes it exit(1) on every launch (the
container looked 'running' only because the supervisor used to hold it open).

  GROUTER_BIN=/path/to/grouter python3 test_run_grouter.py
"""
import io
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


def test_an_unresolved_peer_becomes_a_placeholder_not_a_hostname():
    """resolve() used to try for ten seconds and then return the HOSTNAME, which `ifconfig add
    -dstip` fed to sscanf: the destination was stack garbage, the interface still received, and
    the router heard every neighbour that started after it and was heard by none of them. The
    placeholder is one the router can recover from (tun.c learns the peer from its first
    datagram, see peer_learn_test.py)."""
    assert run_grouter.resolve("no-such-peer.invalid", deadline_s=0) == "0.0.0.0"
    assert run_grouter.resolve("127.0.0.1", deadline_s=0) == "127.0.0.1"
    saved = run_grouter.RESOLVE_S
    run_grouter.RESOLVE_S = 0
    try:
        cfg = run_grouter.build_config({"name": "r1", "ifaces": [
            {"ip": "10.0.1.1/24", "mac": "02:00:00:01:01:01",
             "port": {"peer_host": "no-such-peer.invalid", "peer_port": 5000,
                      "bind_port": 5001}}]})
    finally:
        run_grouter.RESOLVE_S = saved
    line = next(l for l in cfg.splitlines() if l.startswith("ifconfig add tun1"))
    assert "-dstip 0.0.0.0 " in line and "no-such-peer" not in line


if __name__ == "__main__":
    test_cmd_uses_valid_option_spelling()
    test_cmd_options_are_accepted_by_the_binary()
    test_config_has_srcport_after_hwaddr()
    test_openflow_mode_adds_flag_and_drops_routes()
    test_config_gives_the_router_its_real_netmask()
    test_router_lab_delay_is_applied_at_boot()
    test_config_passes_the_links_cost_to_the_router()
    test_an_unresolved_peer_becomes_a_placeholder_not_a_hostname()
    print("ok")
    test_openflow_mode_adds_flag_and_drops_routes()
    print("test_run_grouter: ALL PASS")


# --- a crash leaves a report and a capped log where Stop cannot delete them -------------------

def test_only_the_newest_crash_reports_are_kept(tmp_path):
    for i in range(8):
        p = tmp_path / f"r1-crash-{1000 + i}.txt"
        p.write_text("x")
        os.utime(p, (1000 + i, 1000 + i))
    (tmp_path / "r2-crash-1.txt").write_text("another router's report is not ours to delete")
    run_grouter.keep_newest_reports(str(tmp_path), "r1", keep=5)
    left = sorted(p.name for p in tmp_path.glob("r1-crash-*.txt"))
    assert left == [f"r1-crash-{1000 + i}.txt" for i in range(3, 8)]
    assert (tmp_path / "r2-crash-1.txt").exists()


def test_the_router_log_rolls_over_at_its_cap_and_keeps_one_old_file(tmp_path):
    out = io.BytesIO()
    tee = run_grouter.RotatingTee(str(tmp_path), "r1", limit=100, out=out)
    for i in range(30):
        tee.write(f"line {i:02d}\n".encode())          # 8 bytes each: 240 in all
    tee.close()
    assert out.getvalue().count(b"\n") == 30             # the container log still sees everything
    files = sorted(p.name for p in tmp_path.iterdir())
    assert files == ["r1.log", "r1.log.1"]
    assert all(p.stat().st_size <= 100 + 8 for p in tmp_path.iterdir())
    assert (tmp_path / "r1.log").read_bytes().endswith(b"line 29\n")


def test_an_exit_by_signal_is_called_a_crash():
    assert run_grouter.exit_reason(-11) == "crashed (SIGSEGV)"
    assert run_grouter.exit_reason(-6) == "crashed (SIGABRT)"
    assert run_grouter.exit_reason(1) == "exited (1)"


def test_no_writable_captures_folder_means_no_report_file(tmp_path, monkeypatch):
    monkeypatch.setattr(run_grouter, "CRASH_DIR", str(tmp_path / "missing"))
    assert run_grouter.crash_dir() is None
    monkeypatch.setattr(run_grouter, "CRASH_DIR", str(tmp_path))
    assert run_grouter.crash_dir() == str(tmp_path)
