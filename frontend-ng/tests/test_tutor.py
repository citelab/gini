"""Tutor polish: path tracing, the trace_path tool, and present-tool wiring."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from gini.agent.api import GiniAPI
from gini.agent.tools.registry import build_registry
from gini.app import AppContext
from gini.ui.main_window import MainWindow


def _api():
    return GiniAPI(AppContext())


def test_trace_path_crosses_routers():
    api = _api()
    r1 = api.add_device("router")["id"]; r2 = api.add_device("router")["id"]
    m1 = api.add_device("host")["id"]; m2 = api.add_device("host")["id"]
    api.connect(m1, r1); api.connect(r1, r2); api.connect(r2, m2)
    path = api.trace_path("M1", "M2")
    assert path[0] == "M1" and path[-1] == "M2"
    assert "R1" in path and "R2" in path          # goes through both routers


def test_trace_path_no_route():
    api = _api()
    api.add_device("host"); api.add_device("host")     # two unconnected hosts
    assert api.trace_path("M1", "M2") == []


def test_trace_path_tool_registered():
    api = _api()
    reg = build_registry(api)
    assert "trace_path" in reg.names()
    api.add_device("host"); api.add_device("host")
    m1 = list(api.ctx.topology.devices.values())[0].id
    m2 = list(api.ctx.topology.devices.values())[1].id
    api.connect(m1, m2)
    out = reg.execute("trace_path", {"src": "M1", "dst": "M2"})
    assert out["path"] == ["M1", "M2"]


def test_explain_mode_is_interactive():
    app = QApplication.instance() or QApplication([])
    w = MainWindow(app)
    r1 = w.api.add_device("router")["id"]
    r2 = w.api.add_device("router")["id"]
    w.api.connect(r1, r2)

    spots = []
    w.ctx.bus.present_spotlight.connect(lambda ids: spots.append(ids))

    # enter explain mode (sticky toggle)
    w.assistant._handle("explain")
    assert w.assistant.explain_mode

    # selecting R2 explains R2 — spotlight moves off the hub
    w.ctx.select(r2)
    assert spots[-1] == [r2], "spotlight should follow the selected device"

    # selecting another device moves it again (mode does NOT silently drop)
    w.ctx.select(r1)
    assert spots[-1] == [r1]
    assert w.assistant.explain_mode

    # exit only via the toggle
    w.assistant._explain_btn.setChecked(False)
    assert not w.assistant.explain_mode


def test_loop_keeps_recent_context_and_bounds_it():
    from gini.agent.llm.backend import Chunk
    from gini.agent.llm.fake import ScriptedBackend
    from gini.agent.loop import AgentLoop
    from gini.agent.tools.registry import build_registry
    api = _api()
    loop = AgentLoop(ScriptedBackend([[Chunk(text="ok")] for _ in range(40)]),
                     build_registry(api), max_history=6)
    for i in range(10):
        loop.send(f"turn {i}")
    assert len(loop.history) <= 7                     # system + <= max_history
    assert any(m.role == "user" and "turn 9" in m.content for m in loop.history)
    assert not any(m.role == "user" and "turn 0" in m.content for m in loop.history)


def test_explain_routes_through_llm_async_and_grounds_on_facts():
    app = QApplication.instance() or QApplication([])
    w = MainWindow(app)
    r1 = w.api.add_device("router")["id"]
    m3 = w.api.add_device("host")["id"]
    w.api.connect(r1, m3)

    class FakeLoop:
        def __init__(self): self.prompts = []
        def send(self, p): self.prompts.append(p); return "Authored: " + p[:12]

    fl = FakeLoop()
    w.assistant.set_loop(fl)
    got = []
    w.assistant.answer_ready.connect(lambda d, t: got.append((d, t)))

    res = w.assistant._show_device("R1")
    assert res is None                                # answer comes asynchronously
    import time
    for _ in range(100):
        app.processEvents()
        if got:
            break
        time.sleep(0.02)
    assert got and got[0][0] == "R1"                  # delivered, tagged with the device
    assert "R1" in fl.prompts[0] and "Facts:" in fl.prompts[0]   # grounded in real facts


def test_element_guide_covers_all_palette_types():
    from gini.domain.devices import all_devices
    from gini.domain.element_guide import guide_for
    missing = [d.key for d in all_devices() if not guide_for(d.key)]
    assert missing == [], f"element guide missing: {missing}"


def test_explain_element_type_when_to_use():
    api = _api()
    for key in ("router", "switch", "hub"):
        text = api.explain_element_type(key)
        assert "use" in text.lower() and len(text) > 60


def test_palette_click_explains_element_in_explain_mode():
    app = QApplication.instance() or QApplication([])
    w = MainWindow(app)
    posted = []
    w.ctx.bus.assistant_message.connect(lambda role, t: posted.append((role, t)))

    w.palette.element_selected.emit("hub")        # not in explain mode -> ignored
    assert not any("Hub" in t for _r, t in posted)

    w.assistant._handle("explain")                # enter explain mode
    w.palette.element_selected.emit("hub")        # now it explains the Hub element type
    assert any("Hub" in t for _r, t in posted)


def test_streaming_types_answer_into_pane():
    from gini.agent.llm.backend import Chunk
    from gini.agent.llm.fake import ScriptedBackend
    from gini.agent.loop import AgentLoop
    from gini.agent.tools.registry import build_registry
    app = QApplication.instance() or QApplication([])
    w = MainWindow(app)
    w.api.add_device("router")
    backend = ScriptedBackend([[Chunk(text="Hel"), Chunk(text="lo "), Chunk(text="R1.")]])
    w.assistant.set_loop(AgentLoop(backend, build_registry(w.api)))

    chunks = []
    w.assistant.answer_chunk.connect(lambda d: chunks.append(d))
    assert w.assistant._show_device("R1") is None      # async, streams in

    import time
    for _ in range(100):
        app.processEvents()
        if w.assistant._messages and w.assistant._messages[-1][1].startswith("Hello"):
            break
        time.sleep(0.02)
    assert len(chunks) >= 2                              # arrived token-by-token
    assert "Hello R1." in w.assistant.log.toPlainText() # typed live into the pane
    assert w.assistant._messages[-1] == ("GINI", "Hello R1.")   # persisted for re-render


def test_followup_chips_appear_after_explain_and_run():
    app = QApplication.instance() or QApplication([])
    w = MainWindow(app)
    w.api.add_device("router")
    w.assistant._handle("explain")                      # enter explain mode (offline)
    w.assistant.explain_selected("R1")                  # deterministic explain of a router

    labels = [w.assistant._follow_lay.itemAt(i).widget().text()
              for i in range(w.assistant._follow_lay.count())
              if w.assistant._follow_lay.itemAt(i).widget()]
    assert labels and any("R1" in l for l in labels)    # chips reference the explained device

    posted = []
    w.ctx.bus.assistant_message.connect(lambda role, t: posted.append((role, t)))
    w.assistant._run_followup(labels[0])                # clicking a chip asks the question
    assert any(role == "You" for role, _t in posted)
    # chips clear once one is used (layout emptied)
    assert not [w.assistant._follow_lay.itemAt(i).widget()
                for i in range(w.assistant._follow_lay.count())
                if w.assistant._follow_lay.itemAt(i).widget()]


def test_warning_badge_click_asks_gini():
    app = QApplication.instance() or QApplication([])
    w = MainWindow(app)
    h = w.api.add_device("host")["id"]                  # lone host -> lint flags it
    app.processEvents()

    posted = []
    w.ctx.bus.assistant_message.connect(lambda role, t: posted.append((role, t)))
    w.ctx.bus.warning_explain_requested.emit(h)         # as if the badge was clicked
    app.processEvents()
    assert any(role == "You" and "flagged" in t for role, t in posted)
    assert w.assistant._last_ref and w.assistant._last_ref[0] == "warning"


def test_assistant_path_command_animates():
    app = QApplication.instance() or QApplication([])
    w = MainWindow(app)
    r = w.api.add_device("router")["id"]
    a = w.api.add_device("host")["id"]; b = w.api.add_device("host")["id"]
    w.api.connect(a, r); w.api.connect(r, b)

    packets = []
    w.ctx.bus.present_packet.connect(lambda ids: packets.append(ids))
    reply = w.assistant._handle("how does M1 reach M2")
    assert "M1 → M2" in reply and "R1" in reply
    assert packets and len(packets[0]) == 3       # M1 -> R1 -> M2 animated
