"""A3 tests: the `present` tools emit on the event bus and resolve names to ids."""
from gini.agent.api import GiniAPI
from gini.agent.tools.registry import build_registry
from gini.app import AppContext


def make():
    ctx = AppContext()
    api = GiniAPI(ctx)
    return ctx, api, build_registry(api)


def test_present_tools_registered():
    _, _, reg = make()
    present = {t.name for t in reg.specs() if t.group == "present"}
    assert {"spotlight", "highlight", "callout", "narrate",
            "animate_packet", "clear_stage"} <= present


def test_spotlight_emits_resolved_ids():
    ctx, api, reg = make()
    r1 = api.add_device("router", name="R1")
    got = []
    ctx.bus.present_spotlight.connect(lambda ids: got.append(ids))
    reg.execute("spotlight", {"targets": ["R1"]})
    assert got == [[r1["id"]]]


def test_callout_and_narrate_emit():
    ctx, api, reg = make()
    api.add_device("switch", name="S1")
    calls, narr = [], []
    ctx.bus.present_callout.connect(lambda d, t: calls.append((d, t)))
    ctx.bus.present_narrate.connect(lambda t: narr.append(t))
    reg.execute("callout", {"device": "S1", "text": "this is a switch"})
    reg.execute("narrate", {"text": "watch the traffic"})
    assert calls and calls[0][1] == "this is a switch"
    assert narr == ["watch the traffic"]


def test_animate_packet_path_resolves():
    ctx, api, reg = make()
    a = api.add_device("host", name="H1")
    b = api.add_device("router", name="R1")
    paths = []
    ctx.bus.present_packet.connect(lambda ids: paths.append(ids))
    reg.execute("animate_packet", {"path": ["H1", "R1"]})
    assert paths == [[a["id"], b["id"]]]
