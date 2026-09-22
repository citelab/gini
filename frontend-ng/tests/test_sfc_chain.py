"""Phase 1 in-router SFC: live-chain parsing + deploy commands + real-vs-illustrative VNFs."""
from gini.domain.modulechain import chain_summary, parse_chain
from gini.domain.router_modules import MODULE_BY_KEY, RouterProgram, lua_container_path


def test_parse_live_chain():
    txt = "base: parse -> [0:acl] -> [1:nat] -> route -> rewrite"
    mods = parse_chain(txt)
    assert [(m.index, m.type) for m in mods] == [(0, "acl"), (1, "nat")]
    assert chain_summary(txt) == "parse → acl → nat → route → rewrite"
    assert "no service functions" in chain_summary("base: parse -> route -> rewrite")


def test_a_module_carries_the_argument_it_was_added_with():
    """`gpipe list` now says WHICH acl and WHICH script. Without it a loaded loss.lua was a box
    that said "lua" — which reads as "not loaded" — and two Lua modules were the same."""
    txt = ("base: parse -> [0:acl:10.0.3.0/24] -> [1:lua:/scripts/loss.lua] -> [2:counter]"
           " -> [3:classify:10.0.3.0/24:ef] -> route -> rewrite")
    mods = parse_chain(txt)
    assert [(m.type, m.arg) for m in mods] == [
        ("acl", "10.0.3.0/24"), ("lua", "/scripts/loss.lua"), ("counter", ""),
        ("classify", "10.0.3.0/24:ef")]           # classify's own colon survives
    assert chain_summary(txt) == (
        "parse → acl(10.0.3.0/24) → lua(loss.lua) → counter → classify(10.0.3.0/24:ef)"
        " → route → rewrite")


def test_a_router_built_before_the_argument_was_recorded_still_parses():
    """Old images print the short form for everything. They must keep working unchanged."""
    mods = parse_chain("base: parse -> [0:lua] -> [1:acl] -> route -> rewrite")
    assert [(m.type, m.arg) for m in mods] == [("lua", ""), ("acl", "")]


def test_every_inline_vnf_is_real_and_lua_is_too():
    """A module is real when it names a gpipe function the gRouter actually runs.

    This drifted once already: classify and tap were the last illustrative inline
    modules, and the QoS and Tap work gave them backends without anyone revisiting
    the claim. Assert the RULE rather than a list of keys that goes stale.

    REVERSED for Lua, deliberately. The rule used to be "custom means illustrative until you
    write it" — but the router has run Lua scripts since gr_mod_lua.c landed, and the console
    command loaded them fine. Only the BOX was illustrative, so dropping it into the chain
    deployed nothing ("it goes nowhere") while the working path never showed in the editor.
    `native` is the one that is still illustrative: nothing exists to add until it is compiled in.
    """
    inline = [m for m in MODULE_BY_KEY.values() if m.kind == "inline"]
    assert inline, "no inline VNFs — the registry moved"
    assert all(m.real for m in inline), \
        [m.key for m in inline if not m.real]

    assert MODULE_BY_KEY["lua"].real, "the Lua box must deploy; the router has always run it"
    assert MODULE_BY_KEY["lua"].gpipe == ("lua", "path")
    assert not MODULE_BY_KEY["native"].real, \
        "a native VNF is illustrative until its code is compiled in"


def test_deploy_commands_program_real_functions_in_order():
    p = RouterProgram()
    p.add("acl")                     # -> add acl 10.0.3.0/24
    p.add("lua")                     # no script named yet -> skipped, and SAID to be
    p.add("nat")                     # -> add nat 203.0.113.1
    p.add("rate")                    # -> add rate 100/200
    cmds = p.deploy_commands()
    assert cmds[0] == "clear"
    assert "add acl 10.0.3.0/24" in cmds
    assert "add nat 203.0.113.1" in cmds
    assert "add rate 100/200" in cmds
    assert not any("lua" in c for c in cmds), "a blank path would only earn a usage error"
    # order preserved (acl before nat before rate)
    assert cmds.index("add acl 10.0.3.0/24") < cmds.index("add nat 203.0.113.1") \
        < cmds.index("add rate 100/200")
    assert [i.type_key for i in p.illustrative()] == ["lua"]


def test_a_lua_box_with_a_script_named_deploys_it():
    """The whole point: drop the box, name loss.lua, press Deploy, and the router loads it."""
    p = RouterProgram()
    p.add("lua").params["path"] = lua_container_path("loss.lua")
    assert p.deploy_commands() == ["clear", "add lua /scripts/loss.lua"]
    assert p.illustrative() == []


def test_whatever_a_student_types_becomes_the_path_the_router_reads():
    """They know the file as loss.lua, or as the host path they just saved; the router knows
    only /scripts/<name>. All of these are the same file."""
    for typed in ("loss.lua", "scripts/loss.lua", "~/.gini/scripts/loss.lua",
                  "/Users/me/.gini/scripts/loss.lua", "/scripts/loss.lua", "  loss.lua "):
        assert lua_container_path(typed) == "/scripts/loss.lua", typed
    assert lua_container_path("") == "", "blank stays blank, so unconfigured stays visible"


# --------------------------------------------------------------------------- #
# the editor follows the router
#
# A module loaded at the console — `gpipe add lua /scripts/loss.lua` — reached the Router Lab
# only as a grey caption under an EMPTY pipeline, and the status said "in sync" without ever
# comparing anything. The live chain is now mirrored into the editor while the student has no
# local edits, so what the router runs is what the boxes show.
# --------------------------------------------------------------------------- #

LIVE = ("base: parse -> [0:acl:10.0.3.0/24] -> [1:lua:/scripts/loss.lua] -> [2:counter]"
        " -> route -> rewrite")


def test_a_fresh_program_mirrors_the_live_chain_into_boxes():
    p = RouterProgram()
    assert p.sync_from_live(parse_chain(LIVE)) is True
    assert [(i.type_key, i.params) for i in p.inline] == [
        ("acl", {"deny": "10.0.3.0/24"}),
        ("lua", {"path": "/scripts/loss.lua"}),
        ("native", {"type": "counter", "arg": ""}),   # no box for it, but it IS running
    ]
    assert p.illustrative() == [], "a module mirrored from the router is deployed by definition"
    assert p.inline[1].name == "Lua VNF · loss.lua"
    assert p.inline[2].name == "counter", "named for what it really is, not dropped"
    assert p.dirty is False


def test_a_lua_with_no_argument_mirrors_to_a_stable_box():
    """A router built before the argument was recorded reports a loaded script as a bare
    `[0:lua]`. Mirrored as an editable Lua box its empty `path` reads as unconfigured, so the
    mirror disagreed with the router on every poll and re-ran forever — a visible flicker.
    It becomes a generic box that round-trips instead."""
    live = parse_chain("base: parse -> [0:lua] -> route -> rewrite")
    p = RouterProgram()
    assert p.sync_from_live(live) is True
    assert [(i.type_key, i.name) for i in p.inline] == [("native", "lua")]
    assert p.sync_from_live(live) is False, "second poll must be a no-op — no churn"
    assert p.matches_live(live)
    assert p.illustrative() == [], "it is running, so it is not illustrative"


def test_mirroring_is_idempotent_and_round_trips_through_deploy():
    """The mirrored chain redeploys as ITSELF. That is what makes `clear` at the top of a
    deploy no longer destroy hand-loaded modules: they are in the chain being re-added."""
    p = RouterProgram()
    live = parse_chain(LIVE)
    p.sync_from_live(live)
    assert p.matches_live(live)
    assert p.sync_from_live(live) is False, "nothing to change — must not churn the editor"
    assert p.deploy_commands() == ["clear", "add acl 10.0.3.0/24", "add lua /scripts/loss.lua",
                                   "add counter"], "the module we have no box for must survive"


def test_a_local_edit_makes_the_program_dirty_and_a_deploy_clears_it():
    p = RouterProgram()
    p.sync_from_live(parse_chain(LIVE))
    assert p.dirty is False
    p.add("nat")
    assert p.dirty is True
    p.mark_deployed()
    assert p.dirty is False
    p.remove(0); assert p.dirty is True
    p.mark_deployed(); p.move(0, 1); assert p.dirty is True
    p.mark_deployed(); p.touch(); assert p.dirty is True


def test_in_sync_is_a_real_comparison():
    """It said "in sync" with zero boxes against a router running one module."""
    p = RouterProgram()
    live = parse_chain(LIVE)
    assert not p.matches_live(live), "empty editor vs a loaded router is NOT in sync"
    p.sync_from_live(live)
    assert p.matches_live(live)
    p.inline[0].params["deny"] = "10.0.9.0/24"          # same type, different argument
    assert not p.matches_live(live), "the argument counts, not just the type"


def test_an_unconfigured_lua_box_does_not_count_as_deployed():
    """A Lua box with no script is shown, skipped by deploy, and therefore must not make the
    editor claim agreement with a router that has no such module."""
    p = RouterProgram()
    p.add("lua")
    assert p.matches_live(parse_chain("base: parse -> route -> rewrite"))


def test_classifier():
    p = RouterProgram()
    p.set_classifier("  tcp:80 ")
    assert p.classifier == "tcp:80"
