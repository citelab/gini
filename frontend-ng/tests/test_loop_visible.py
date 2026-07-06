"""Chat must show the model's prose, never raw tool-call syntax. `visible_text` strips
the tool JSON/tags (the loop still executes them) and keeps callout/narrate text."""
from gini.agent.loop import visible_text


def test_strips_tagged_tool_calls_keeps_callout_text():
    raw = ('<JSON>{"tool":"callout","args":{"text":"Let\'s start building your IP network!"}}</JSON>'
           '<tool_call>{"tool":"add","args":{"name":"R1","type":"router"}}</tool_call>')
    out = visible_text(raw)
    assert "Let's start building your IP network!" in out
    assert "{" not in out and "tool" not in out and "R1" not in out


def test_strips_bare_tool_json_objects():
    raw = 'Here is your router. {"tool":"add","args":{"name":"R1","type":"router"}}'
    assert visible_text(raw) == "Here is your router."


def test_keeps_plain_prose_untouched():
    s = "A router forwards packets at Layer 3 between subnets."
    assert visible_text(s) == s


def test_mixes_prose_and_callout_drops_other_tools():
    raw = ('Sure thing! <tool_call>{"tool":"spotlight","args":{"device":"R1"}}</tool_call>'
           '<tool_call>{"tool":"callout","args":{"text":"R1 is the gateway."}}</tool_call>')
    out = visible_text(raw)
    assert "Sure thing!" in out and "R1 is the gateway." in out
    assert "spotlight" not in out and "{" not in out


def test_pure_action_turn_has_no_leftover_json():
    raw = '<tool_call>{"tool":"add","args":{"name":"M1","type":"host"}}</tool_call>'
    out = visible_text(raw)
    assert "{" not in out and "tool" not in out          # nothing to say -> empty, caller adds "Done."


def test_json_action_aliases_map_to_real_tools():
    # a local model follows the prose prompt's short names — they must normalize to the
    # registry's real tool names + arg names, else the call silently does nothing.
    from gini.agent.llm.fake import ScriptedBackend
    from gini.agent.loop import AgentLoop

    class _Reg:
        def names(self):
            return {"add_device", "connect_devices", "remove_device"}

        def openai_tools(self):
            return []

    loop = AgentLoop(ScriptedBackend([]), _Reg())
    calls = loop._parse_json_actions('{"tool":"add","args":{"name":"R1","type":"router"}}')
    assert len(calls) == 1 and calls[0].name == "add_device"
    assert calls[0].arguments == {"name": "R1", "type_key": "router"}
    assert loop._parse_json_actions('{"tool":"connect","args":{"a":"R1","b":"S1"}}')[0].name \
        == "connect_devices"
