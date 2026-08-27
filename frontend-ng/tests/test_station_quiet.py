"""Stations must not inject traffic nobody asked for.

A capture of eleven stations showed 988 packets in a few seconds, nearly all IPv6 router
solicitations to ff02::2 -- and on a GINI fabric those are UNANSWERABLE, because the
gRouter has no IPv6. Worse, they are multicast: a reactive controller floods them, a
topology with a cycle circulates them, and they fill flow tables with rules for traffic
nobody wants.
"""
import os

from gini.runtime import shuttle


def _fake_proc(tmp_path, iface="gini0", knobs=("disable_ipv6", "accept_ra", "autoconf")):
    d = tmp_path / iface
    d.mkdir(parents=True)
    for k in knobs:
        (d / k).write_text("0")
    return d


def test_ipv6_autoconfiguration_is_switched_off(tmp_path, monkeypatch):
    d = _fake_proc(tmp_path)
    monkeypatch.setattr(shuttle, "IPV6_CONF_ROOT", str(tmp_path))
    monkeypatch.delenv("GINI_FABRIC_IPV6", raising=False)
    shuttle.quiet_ipv6("gini0")
    assert (d / "disable_ipv6").read_text() == "1"
    assert (d / "accept_ra").read_text() == "0"
    assert (d / "autoconf").read_text() == "0"


def test_the_escape_hatch_leaves_ipv6_alone(monkeypatch, tmp_path):
    """An L2-only IPv6 experiment across switches is still possible, so silently having no
    IPv6 with no way back would be a worse trap than the noise."""
    d = _fake_proc(tmp_path)
    monkeypatch.setattr(shuttle, "IPV6_CONF_ROOT", str(tmp_path))
    for value in ("1", "true", "YES"):
        (d / "disable_ipv6").write_text("0")
        monkeypatch.setenv("GINI_FABRIC_IPV6", value)
        shuttle.quiet_ipv6("gini0")
        assert (d / "disable_ipv6").read_text() == "0", f"{value} should keep IPv6"


def test_a_kernel_without_ipv6_does_not_stop_the_station(monkeypatch, tmp_path):
    """The cost of failing here is noise, not a broken network — a station must still boot."""
    monkeypatch.setattr(shuttle, "IPV6_CONF_ROOT", str(tmp_path / "nonexistent"))
    monkeypatch.delenv("GINI_FABRIC_IPV6", raising=False)
    shuttle.quiet_ipv6("gini0")          # must not raise


def test_it_never_touches_the_containers_own_eth0(tmp_path, monkeypatch):
    """eth0 carries the UDP transport underneath the fabric; only the tap is quieted."""
    _fake_proc(tmp_path, "gini0")
    eth0 = _fake_proc(tmp_path, "eth0")
    monkeypatch.setattr(shuttle, "IPV6_CONF_ROOT", str(tmp_path))
    monkeypatch.delenv("GINI_FABRIC_IPV6", raising=False)
    shuttle.quiet_ipv6("gini0")
    assert (eth0 / "disable_ipv6").read_text() == "0"
