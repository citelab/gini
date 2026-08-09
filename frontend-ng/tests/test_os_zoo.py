"""OS Zoo catalog — the pure data model for the historical-OS palette."""
from gini.domain import os_zoo


def test_catalog_has_full_and_skeletal_tiers():
    full = {o.id for o in os_zoo.full_set()}
    skel = {o.id for o in os_zoo.skeletal_set()}
    assert {"freedos", "kolibri", "menuet"} <= full       # the shipped, out-of-the-box set
    assert {"win95", "mac7"} <= skel                      # proprietary -> bring your own


def test_full_boots_out_of_box_skeletal_needs_image():
    assert os_zoo.get("freedos").full is True
    assert os_zoo.get("freedos").byo is False
    win = os_zoo.get("win95")
    assert win.byo is True and win.source                 # skeletal carries a reference link
    # we never bundle a proprietary image: skeletal entries only point, never host
    for o in os_zoo.skeletal_set():
        assert o.source, f"{o.id} skeletal must document where to get the image"


def test_classic_mac_uses_the_68k_emulator():
    mac = os_zoo.get("mac7")
    assert mac.emulator == os_zoo.BASILISK and mac.arch == "68k"
    # x86 guests use qemu
    assert os_zoo.get("kolibri").emulator == os_zoo.QEMU


def test_every_os_has_a_nic_for_v2_networking():
    for o in os_zoo.CATALOG:
        assert o.nic                                      # per-OS NIC default (v2 fabric wiring)
    # old DOS/9x guests get an old NIC they have a driver for
    assert os_zoo.get("freedos").nic == "ne2k_pci"
