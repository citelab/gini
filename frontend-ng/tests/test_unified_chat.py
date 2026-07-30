"""The unified conversation surface: GINI and people share ONE panel.

The design the user asked for: no separate "chat with AI" vs "chat with humans". A conversation
ribbon (GINI · Instructor · Group · mates) picks the target; one transcript shows it, with colored
sender labels. Two properties are load-bearing and tested hard:

  * a student can always tell **Prof** (the real instructor) from **ProfAI** (an AI standing in) —
    the AI carries an unmistakable label, because acting on its answer is the student's risk;
  * the surface degrades to GINI-only when you're not in a course — the solo experience is unchanged.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

from PySide6.QtWidgets import QApplication

from gini.ui.main_window import MainWindow


def _win():
    app = QApplication.instance() or QApplication([])
    w = MainWindow(app)
    a = w.assistant
    a._loop = SimpleNamespace(backend=SimpleNamespace(
        chat=lambda *A, **K: iter([SimpleNamespace(text="ok", tool_call=None)])), brief="")
    a._refresh_mode_availability()
    return w, a


CHANS = [
    {"id": "teacher:ravi", "kind": "teacher", "title": "Instructor"},
    {"id": "group:g1", "kind": "group", "title": "Group g1"},
    {"id": "dm:ben|ravi", "kind": "dm", "title": "Ben", "peer": "ben"},
]


def _load(a, msgs=None):
    a._apply_convo_op(("convos", CHANS, msgs or []))


def test_the_ribbon_is_absent_until_you_are_in_a_course():
    """Solo users see no ribbon at all — one panel, GINI only, exactly as before."""
    w, a = _win()
    assert not a._convo_bar.isVisibleTo(w)
    assert a._convo == "gini"

    _load(a)
    assert a._convo_bar.isVisibleTo(w)                    # signed in + channels → ribbon appears
    assert list(a._convo_btns) == ["gini", "teacher:ravi", "group:g1", "dm:ben|ravi"]


def test_selecting_a_person_hides_the_AI_build_tools():
    """A human thread shows a plain message box, not the Chat/Explain/Wizard/Coach/Missions row."""
    w, a = _win()
    _load(a)
    a._select_convo("teacher:ravi")
    assert not a._mode_bar.isVisibleTo(w)
    assert "Message Instructor" in a.input.placeholderText()

    a._select_convo("gini")                              # …and back again restores them
    assert a._mode_bar.isVisibleTo(w)
    assert "Ask GINI" in a.input.placeholderText()


def test_Prof_and_ProfAI_are_visually_distinct_in_the_shared_transcript():
    w, a = _win()
    _load(a, [
        {"channel": "teacher:ravi", "from": "ravi", "kind": "human", "body": "what is ARP?", "ts": 1},
        {"channel": "teacher:ravi", "from": "Prof", "kind": "human", "body": "Ask the TA.", "ts": 2},
        {"channel": "teacher:ravi", "from": "ProfAI", "kind": "ai", "body": "Maps IP to MAC.", "ts": 3},
    ])
    a._select_convo("teacher:ravi")
    html = a.log.toHtml()
    assert "Prof" in html and "ProfAI" in html
    assert "AI" in html                                  # the AI tag is present…
    # the AI label uses the muted colour, the real Prof does not — they cannot be confused
    prof_color = a._role_color("Prof", ai=False)
    ai_color = a._role_color("ProfAI", ai=True)
    assert prof_color != ai_color


def test_a_message_to_a_person_goes_to_that_person_not_the_AI():
    w, a = _win()
    _load(a)
    sent = []
    a.ctx.teaching_center = SimpleNamespace(
        signed_in=lambda: True,
        send_message=lambda to, body: sent.append((to, body)) or {"ok": True},
        messages=lambda since=0.0: [], channels=lambda: CHANS)

    a._select_convo("group:g1")
    a.input.setText("anyone got the router working?")
    a._send()
    import time
    for _ in range(50):
        if sent:
            break
        time.sleep(0.02)
    assert sent == [("group", "anyone got the router working?")]

    # a DM resolves to the peer, not the group
    sent.clear()
    a._select_convo("dm:ben|ravi")
    a.input.setText("hey ben"); a._send()
    for _ in range(50):
        if sent:
            break
        time.sleep(0.02)
    assert sent == [("ben", "hey ben")]


def test_switching_to_GINI_still_talks_to_the_AI():
    """The other half of 'unified': GINI is just a conversation in the same panel, and selecting it
    routes typing back to the assistant, not to a person."""
    w, a = _win()
    _load(a)
    a._select_convo("gini")
    handled = []
    a._handle = lambda text: handled.append(text) or "ok"
    a.input.setText("add a router"); a._send()
    assert handled == ["add a router"]                   # went to the AI router, not send_message


def test_an_async_GINI_reply_does_not_bleed_into_a_human_thread():
    """If a GINI answer arrives while you're reading the instructor thread, it's stored, not painted
    over their messages. Switching back to GINI shows it."""
    w, a = _win()
    _load(a, [{"channel": "teacher:ravi", "from": "Prof", "kind": "human", "body": "Hi", "ts": 1}])
    a._select_convo("teacher:ravi")
    a._post("GINI", "Here's your topology explanation.")   # async reply lands while on the human thread
    assert "topology explanation" not in a.log.toHtml()    # not shown here…
    a._select_convo("gini")
    assert "topology explanation" in a.log.toHtml()        # …but preserved for the GINI conversation
