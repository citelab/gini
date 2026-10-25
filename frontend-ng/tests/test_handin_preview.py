"""The student sees what the marker will see, before the proof is sealed.

`generate_proof` appends the submit entry and hashes the chain, so the preview has to come before
it — after that, nothing the student learns from it can be fixed.
"""
from __future__ import annotations

import sys
from pathlib import Path

from gini.domain import grader_view as G
from gini.domain import proof_events as ev

sys.path.insert(0, str(Path(__file__).parent))
from test_grader_view import QUESTIONS, _alab_chain, _report        # noqa: E402
from test_lab_grade import _strip                                   # noqa: E402


def _unsealed(proof):
    return [e for e in proof["entries"] if e["kind"] != "submit"]


def test_the_preview_counts_what_the_marker_will_count():
    """Same function, same chain: the counts a student is shown are the marker's counts."""
    proof = _alab_chain()
    mine = G.preview(_unsealed(proof), QUESTIONS)
    theirs = _report(proof, questions=QUESTIONS)["cooked"]
    for key in ("grading", "wiring", "builds", "tests"):
        assert mine[key] == theirs[key], key
    assert [a["answered"] for a in mine["answers"]] == [a["answered"] for a in theirs["answers"]]


def test_the_preview_never_carries_the_answer_key():
    mine = G.preview(_unsealed(_alab_chain()), [{"id": "q1", "prompt": "Why?"}])
    assert all("key" not in a for a in mine["answers"])


def test_the_lines_say_what_is_missing_in_the_students_words():
    from gini.ui.handin_preview import summary_lines
    lines = summary_lines(G.preview(_unsealed(_alab_chain()), QUESTIONS))
    text = "\n".join(lines)
    assert "Measured: 6/7 agreed — failed: load_responds" in text
    assert "Part C 2/4" in text
    assert "Questions answered: 1/2" in text


def test_a_console_session_shows_in_the_preview():
    from gini.ui.handin_preview import summary_lines
    es = _unsealed({"entries": [{"kind": k, "t": 0.0, "data": d} for k, d in
                                [ev.command("M1", "sharetest 10 5 1", ["x"])]]})
    assert "Console: 1 command(s) recorded" in summary_lines(G.preview(es))


def test_not_yet_hands_nothing_in():
    log = []
    app, st = _strip(log)
    type(st).confirm_handin, saved = staticmethod(lambda p, r: False), type(st).confirm_handin
    try:
        st._generate()
        assert log == []                                 # generate_proof never ran
    finally:
        type(st).confirm_handin = saved
        st.deleteLater()


def test_hand_in_goes_on_to_seal_the_proof():
    log = []
    app, st = _strip(log)
    type(st).confirm_handin, saved = staticmethod(lambda p, r: True), type(st).confirm_handin
    try:
        st._generate()
        assert log == ["generate_proof"]
    finally:
        type(st).confirm_handin = saved
        st.deleteLater()


def test_a_preview_that_breaks_never_stands_in_the_way():
    from gini.ui.handin_preview import confirm

    class _Broken:
        @property
        def _chain(self):
            raise RuntimeError("no chain")
    _strip([])                                           # a QApplication exists
    assert confirm(None, _Broken()) is True


def _told(st):
    seen = []
    saved = type(st).tell_student
    type(st).tell_student = staticmethod(lambda p, title, text: seen.append((title, text)))
    return seen, saved


def test_not_yet_says_so_in_a_dialog_not_only_in_the_strip():
    """The preview's ✕ is "Not yet". A student who closed it thinking they were done was told
    only by a strip line, and went looking for a receipt that was never made."""
    log = []
    _app, st = _strip(log)
    type(st).confirm_handin, saved = staticmethod(lambda p, r: False), type(st).confirm_handin
    seen, saved_tell = _told(st)
    try:
        st._generate()
        [(title, text)] = seen
        assert "Not handed in" in title
        assert "receipt code" in text and "Nothing has been sent" in text
    finally:
        type(st).confirm_handin = saved
        type(st).tell_student = saved_tell
        st.deleteLater()


def test_a_proof_that_could_not_be_sealed_is_a_dialog_with_the_reason():
    log = []
    _app, st = _strip(log)                                # its recorder refuses with "no code"
    type(st).confirm_handin, saved = staticmethod(lambda p, r: True), type(st).confirm_handin
    seen, saved_tell = _told(st)
    try:
        st._generate()
        [(title, text)] = seen
        assert "could not be sealed" in title
        assert "no code" in text and "no receipt code" in text
    finally:
        type(st).confirm_handin = saved
        type(st).tell_student = saved_tell
        st.deleteLater()


def test_a_sealed_proof_is_not_reported_as_a_failure():
    log = []
    _app, st = _strip(log)
    st.recorder.generate_proof = lambda objectives=None: {"ok": True, "receipt": "ABCD-1234",
                                                          "proof": {}, "path": "/p"}
    st._hand_in = lambda result, receipt: log.append(("hand_in", receipt))
    type(st).confirm_handin, saved = staticmethod(lambda p, r: True), type(st).confirm_handin
    seen, saved_tell = _told(st)
    try:
        st._generate()
        assert seen == [] and log == [("hand_in", "ABCD-1234")]
    finally:
        type(st).confirm_handin = saved
        type(st).tell_student = saved_tell
        st.deleteLater()


def test_the_preview_says_nothing_has_been_sent_yet():
    import inspect

    from gini.ui import handin_preview
    src = inspect.getsource(handin_preview.confirm)
    assert "nothing sent yet" in src and "Nothing has been handed in yet" in src
    assert "receipt code" in src


def test_telling_the_student_never_hangs_headless():
    from gini.ui.handin_preview import tell
    _strip([])
    assert tell(None, "t", "x") is None
