"""Compiling a topology with an OS Zoo element emits a gini-oszoo container with a noVNC
web port. The screen the Zoo Lab embeds is the `screen` port's web URL; the guest is picked
by ZOO_OS. Bring-your-own guests carry the emulator/arch and bind-mount the student's image."""
from gini.domain.topology import Topology
from gini.services.compiler import RuntimeCompiler, _role, OSZOO_KEYS


def test_oszoo_role_is_standalone():
    for k in OSZOO_KEYS:
        assert _role(k) == "oszoo"                 # not "machine" -> no fabric addressing (v1)


def test_full_os_emits_novnc_web_port():
    t = Topology("zoo")
    t.add_device("freedos")
    cfg = RuntimeCompiler().compile(t)
    svc = next(s for s in cfg.services if s.type_key == "freedos")
    assert svc.image == "gini-oszoo:latest"
    assert svc.env["ZOO_OS"] == "freedos"
    assert svc.env["ZOO_PERSIST"] == "0"           # ephemeral by default
    screen = next(p for p in svc.ports if p["label"] == "screen")
    assert screen["container"] == 6080 and screen["web"] is True   # the embedded noVNC console
    assert "vnc.html" in screen["path"]
    assert isinstance(screen["host"], int)


def test_persist_flag_flows_through():
    t = Topology("zoo")
    d = t.add_device("kolibri")
    d.properties["Persist"] = "true"
    cfg = RuntimeCompiler().compile(t)
    svc = next(s for s in cfg.services if s.type_key == "kolibri")
    assert svc.env["ZOO_OS"] == "kolibri" and svc.env["ZOO_PERSIST"] == "1"


def test_byo_carries_emulator_and_mounts_image():
    t = Topology("zoo")
    d = t.add_device("oszoo_byo")
    d.properties.update({"Emulator": "basilisk", "Arch": "68k", "Image": "/imgs/sys7.img"})
    cfg = RuntimeCompiler().compile(t)
    svc = next(s for s in cfg.services if s.type_key == "oszoo_byo")
    assert svc.env["ZOO_OS"] == "byo"              # generic guest; details come from props
    assert svc.env["ZOO_EMULATOR"] == "basilisk" and svc.env["ZOO_ARCH"] == "68k"
    assert svc.env["ZOO_IMAGE"] == "/imgs/sys7.img"
    # the student's image is bind-mounted read-only (GINI hosts nothing; it only runs it)
    assert any("/imgs/sys7.img:" in v and v.endswith(":ro") for v in svc.volumes)


def test_byo_mac_passes_rom_and_mounts_it():
    t = Topology("zoo")
    d = t.add_device("oszoo_byo")
    d.properties.update({"Emulator": "basilisk", "Arch": "68k",
                         "Image": "/imgs/sys7.img", "Rom": "/imgs/quadra.rom"})
    cfg = RuntimeCompiler().compile(t)
    svc = next(s for s in cfg.services if s.type_key == "oszoo_byo")
    # local paths pass through as the env value AND get bind-mounted to the container mount points
    assert svc.env["ZOO_EMULATOR"] == "basilisk" and svc.env["ZOO_ROM"] == "/imgs/quadra.rom"
    assert any(v.startswith("/imgs/quadra.rom:") and v.endswith("/zoo/rom:ro") for v in svc.volumes)
    assert any("/imgs/sys7.img:" in v for v in svc.volumes)


def test_byo_accepts_urls_without_bind_mounts():
    t = Topology("zoo")
    d = t.add_device("oszoo_byo")
    d.properties.update({"Emulator": "basilisk", "Arch": "68k",
                         "Image": "https://example.org/System753.dsk",
                         "Rom": "https://example.org/quadra.rom"})
    cfg = RuntimeCompiler().compile(t)
    svc = next(s for s in cfg.services if s.type_key == "oszoo_byo")
    # a URL is passed through for the container to download — never a Docker bind-mount
    assert svc.env["ZOO_IMAGE"].startswith("http") and svc.env["ZOO_ROM"].startswith("http")
    assert all("/zoo/byo.img" not in v and "/zoo/rom" not in v for v in svc.volumes)


def test_presets_prefill_urls_and_route_through_byo():
    # Mac System 7 / Windows 3.11 are the BYO element with a download URL pre-filled: they compile
    # to ZOO_OS=byo with the right emulator, carry the URL as env, and never bind-mount (it's a URL).
    for tk, emu, priv, needs_rom in (("mac7", "basilisk", True, True),
                                     ("win31", "dosbox", False, False)):
        t = Topology("z"); t.add_device(tk)
        s = next(x for x in RuntimeCompiler().compile(t).services if x.type_key == tk)
        assert s.env["ZOO_OS"] == "byo" and s.env["ZOO_EMULATOR"] == emu
        assert s.env["ZOO_IMAGE"].startswith("https://")
        assert s.privileged is priv                       # basilisk needs RT scheduling, dosbox not
        assert ("ZOO_ROM" in s.env) is needs_rom
        assert all("/zoo/byo.img" not in v and "/zoo/rom" not in v for v in s.volumes)


def test_two_zoo_elements_get_distinct_host_ports():
    t = Topology("zoo")
    t.add_device("freedos")
    t.add_device("kolibri")
    cfg = RuntimeCompiler().compile(t)
    hosts = [p["host"] for s in cfg.services if s.type_key in OSZOO_KEYS for p in s.ports]
    assert len(hosts) == len(set(hosts))           # no published-port collisions
