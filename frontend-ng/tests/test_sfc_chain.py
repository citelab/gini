"""Phase 1 in-router SFC: live-chain parsing + deploy commands + real-vs-illustrative VNFs."""
from gini.domain.modulechain import chain_summary, parse_chain
from gini.domain.router_modules import MODULE_BY_KEY, RouterProgram


def test_parse_live_chain():
    txt = "base: parse -> [0:acl] -> [1:nat] -> route -> rewrite"
    mods = parse_chain(txt)
    assert [(m.index, m.type) for m in mods] == [(0, "acl"), (1, "nat")]
    assert chain_summary(txt) == "parse → acl → nat → route → rewrite"
    assert "no service functions" in chain_summary("base: parse -> route -> rewrite")


def test_real_vs_illustrative_modules():
    assert MODULE_BY_KEY["acl"].real and MODULE_BY_KEY["nat"].real
    assert MODULE_BY_KEY["block"].real and MODULE_BY_KEY["rate"].real
    assert not MODULE_BY_KEY["classify"].real and not MODULE_BY_KEY["tap"].real


def test_deploy_commands_program_real_functions_in_order():
    p = RouterProgram()
    p.add("acl")                     # -> add acl 10.0.3.0/24
    p.add("tap")                     # illustrative -> skipped
    p.add("nat")                     # -> add nat 203.0.113.1
    p.add("rate")                    # -> add counter (no arg)
    cmds = p.deploy_commands()
    assert cmds[0] == "clear"
    assert "add acl 10.0.3.0/24" in cmds
    assert "add nat 203.0.113.1" in cmds
    assert "add counter" in cmds
    assert not any("tap" in c for c in cmds)          # illustrative not deployed
    # order preserved (acl before nat before counter)
    assert cmds.index("add acl 10.0.3.0/24") < cmds.index("add nat 203.0.113.1") \
        < cmds.index("add counter")
    assert [i.type_key for i in p.illustrative()] == ["tap"]


def test_classifier():
    p = RouterProgram()
    p.set_classifier("  tcp:80 ")
    assert p.classifier == "tcp:80"
