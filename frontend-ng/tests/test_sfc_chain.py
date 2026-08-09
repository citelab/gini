"""Phase 1 in-router SFC: live-chain parsing + deploy commands + real-vs-illustrative VNFs."""
from gini.domain.modulechain import chain_summary, parse_chain
from gini.domain.router_modules import MODULE_BY_KEY, RouterProgram


def test_parse_live_chain():
    txt = "base: parse -> [0:acl] -> [1:nat] -> route -> rewrite"
    mods = parse_chain(txt)
    assert [(m.index, m.type) for m in mods] == [(0, "acl"), (1, "nat")]
    assert chain_summary(txt) == "parse → acl → nat → route → rewrite"
    assert "no service functions" in chain_summary("base: parse -> route -> rewrite")


def test_every_inline_vnf_is_real_and_the_custom_ones_are_not():
    """A module is real when it names a gpipe function the gRouter actually runs.

    This drifted once already: classify and tap were the last illustrative inline
    modules, and the QoS and Tap work gave them backends without anyone revisiting
    the claim. Assert the RULE — inline means deployable, custom means not until you
    write it — rather than a list of keys that goes stale the next time one lands.
    """
    inline = [m for m in MODULE_BY_KEY.values() if m.kind == "inline"]
    assert inline, "no inline VNFs — the registry moved"
    assert all(m.real for m in inline), \
        [m.key for m in inline if not m.real]

    custom = [m for m in MODULE_BY_KEY.values() if m.kind == "custom"]
    assert custom and not any(m.real for m in custom), \
        "a custom VNF is illustrative until its code is written and compiled"


def test_deploy_commands_program_real_functions_in_order():
    p = RouterProgram()
    p.add("acl")                     # -> add acl 10.0.3.0/24
    p.add("lua")                     # custom, not written yet -> skipped
    p.add("nat")                     # -> add nat 203.0.113.1
    p.add("rate")                    # -> add rate 100/200
    cmds = p.deploy_commands()
    assert cmds[0] == "clear"
    assert "add acl 10.0.3.0/24" in cmds
    assert "add nat 203.0.113.1" in cmds
    assert "add rate 100/200" in cmds
    assert not any("lua" in c for c in cmds)          # illustrative not deployed
    # order preserved (acl before nat before rate)
    assert cmds.index("add acl 10.0.3.0/24") < cmds.index("add nat 203.0.113.1") \
        < cmds.index("add rate 100/200")
    assert [i.type_key for i in p.illustrative()] == ["lua"]


def test_classifier():
    p = RouterProgram()
    p.set_classifier("  tcp:80 ")
    assert p.classifier == "tcp:80"
