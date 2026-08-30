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


# ---- teaching a tool call, versus attempting one ------------------------------- #
# Nothing executes a prose-style call: `_parse_json_actions` reads only JSON objects with a "tool"
# key. So stripping them is purely about what a student reads — the model tried to act, nothing
# happened, and showing the raw attempt implies something did.
#
# The old rule matched a call ANYWHERE, which meant it ate the tutor's own explanation of the API,
# and because its argument list stopped at a comma it left the wreckage `, name='M1'` on screen.
# Guessing intent from the words cannot work. The LAYOUT is the model's own signal: attempting an
# action puts the call on a line of its own; teaching one writes it inside a sentence.
def test_a_call_the_tutor_is_teaching_survives_intact():
    """The bug that started this, seen in a real screenshot: GINI explaining how to add a machine,
    rendered as `` ` , name='M1'` `` — a fragment of its own lesson."""
    raw = "For example, you could use `add_device type_key='Machine', name='M1'` to make one."
    assert visible_text(raw) == raw


def test_a_call_on_a_line_of_its_own_is_removed():
    """The case the rule exists for, and it still works."""
    raw = "Right, adding one.\nadd_device type_key='host' name='F1'\nThere it is."
    out = visible_text(raw)
    assert "add_device" not in out
    assert "Right, adding one." in out and "There it is." in out


def test_a_removed_call_leaves_no_fragment():
    """A rule that stops mid-call is worse than one that does not fire: a fragment is unreadable
    where an unstripped call is merely noise. The comma form is what the old regex choked on."""
    out = visible_text("add_device type_key='Machine', name='M1', x=3\nDone.")
    assert "name=" not in out and "M1" not in out and out.strip() == "Done."


def test_naming_a_tool_in_a_sentence_is_never_stripped():
    s = "The add_device tool is what places an element on the canvas."
    assert visible_text(s) == s


def test_a_sentence_that_merely_starts_with_a_tool_name_survives():
    """Line-anchored, but a line still has to BE a call — arguments and nothing else."""
    s = "add_device is the tool you want here, and it takes a type_key."
    assert visible_text(s) == s


# ---- a fence is a lesson unless it holds an action ----------------------------- #
def test_a_command_in_a_code_block_reaches_the_student():
    """Every fence used to be deleted, so GINI could not write a command down — a networking tutor
    that cannot show `ping 10.0.0.2` is missing something it needs."""
    raw = "Try this on M1:\n```\nping 10.0.0.2\n```\nYou should see replies."
    out = visible_text(raw)
    assert "ping 10.0.0.2" in out and "```" in out


def test_a_fenced_tool_action_is_still_hidden():
    """Models do wrap their JSON actions in fences, and that IS worth hiding."""
    raw = 'Adding it.\n```json\n{"tool":"add_device","args":{"type_key":"Router"}}\n```\nDone.'
    out = visible_text(raw)
    assert "add_device" not in out and "{" not in out
    assert "Adding it." in out and "Done." in out


def test_an_empty_fence_is_noise_whatever_produced_it():
    assert "```" not in visible_text("Here:\n```\n```\nthat was nothing.")


def test_fences_are_judged_before_the_json_stripper_runs():
    """Order is load-bearing. Stripping the JSON first empties an action fence, and an emptied
    fence looks exactly like a lesson — it was kept, and the student read bare backticks."""
    raw = '```\n{"tool":"add_device","args":{}}\n```'
    assert visible_text(raw).strip() == ""
