"""The Router Lab's editor shows what the router is RUNNING — and a Lua box that deploys.

Reported from use: `gpipe add lua /scripts/loss.lua` at the console said "added", the router
dropped packets, and the Router Lab showed an empty pipeline with the word "lua" in grey
underneath and "in sync" beside it. Two halves that never met: the editor drew only its own
draft, and its Lua box had no backend at all, so dropping one in deployed nothing.

Now the live chain is mirrored into the boxes while the student has no local edits; a local
edit freezes the editor as a draft until Deploy; "in sync" compares type and argument; and a
.lua file dropped on the window becomes a box that Deploy loads.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from gini.domain.router_modules import RouterProgram
from gini.ui.router_lab import RouterLab
from gini.ui.theme import ThemeManager

LIVE = "base: parse -> [0:acl:10.0.3.0/24] -> [1:lua:/scripts/loss.lua] -> route -> rewrite"
EMPTY = "base: parse -> route -> rewrite"


class _Router:
    name, type_key, properties, id = "R1", "router", {}, "r1"


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _lab(app, program=None, live=LIVE, sent=None):
    sent = sent if sent is not None else []
    return RouterLab(None, ThemeManager(app), _Router(), program or RouterProgram(), sdn=False,
                     command_fn=lambda c: (sent.append(c), live)[1],
                     query_fn=lambda c: live if c == "gpipe list" else "", face="router")


def _boxes(lab):
    return [(i.type_key, dict(i.params)) for i in lab.program.inline]


# --------------------------------------------------------------------------- #
# the editor follows the router
# --------------------------------------------------------------------------- #

def test_a_module_loaded_at_the_console_appears_as_a_box(app):
    lab = _lab(app)
    assert _boxes(lab) == [], "the premise: nothing was composed in the editor"
    lab._on_chain(LIVE)
    assert _boxes(lab) == [("acl", {"deny": "10.0.3.0/24"}), ("lua", {"path": "/scripts/loss.lua"})]
    assert lab.program.inline[1].name == "Lua VNF · loss.lua"
    assert lab.deploy_status.text() == "in sync"
    assert "lua(loss.lua)" in lab.deployed_lbl.text()


def test_the_pipeline_widgets_are_rebuilt_to_show_them(app):
    """Model and view together: a box in `program.inline` a student cannot see is no fix."""
    lab = _lab(app)
    before = len(lab._stage_widgets)
    lab._on_chain(LIVE)
    assert len(lab._stage_widgets) == before + 2


def test_in_sync_is_no_longer_claimed_for_an_empty_editor(app):
    """It used to say "in sync" with zero boxes against a router running a module."""
    lab = _lab(app)
    lab.program.add("nat")                        # a draft that differs from the router
    lab._on_chain(LIVE)
    assert lab.deploy_status.text() != "in sync"
    assert "Deploy chain" in lab.deploy_status.text()


def test_a_poll_does_not_pull_a_draft_out_from_under_the_student(app):
    """Mid-edit the editor is theirs. Mirroring over it every 2.5 seconds would make the
    palette unusable."""
    lab = _lab(app)
    lab.program.add("nat")
    lab.program.inline[0].params["ip"] = "198.51.100.7"
    lab._on_chain(LIVE)
    assert _boxes(lab) == [("nat", {"ip": "198.51.100.7"})], "the draft was overwritten"
    assert "edited" in lab.deploy_status.text()


def test_deploy_hands_the_editor_back_to_the_router(app):
    """After Deploy the editor is no longer a draft, so the poll's answer is the truth again —
    including when the deploy did not take."""
    sent = []
    lab = _lab(app, sent=sent)
    lab.program.add("nat")
    assert lab.program.dirty
    lab._deploy_chain()
    assert lab.program.dirty is False
    lab._on_chain(LIVE)                           # what the router actually reports
    assert _boxes(lab) == [("acl", {"deny": "10.0.3.0/24"}), ("lua", {"path": "/scripts/loss.lua"})]


def test_deploy_no_longer_destroys_a_hand_loaded_module(app):
    """`clear` still leads every deploy. It stopped being destructive because the mirrored
    module is in the chain being re-added."""
    sent = []
    lab = _lab(app, sent=sent)
    lab._on_chain(LIVE)                           # the console-loaded lua is mirrored in
    lab._deploy_chain()
    assert "add lua /scripts/loss.lua" in sent
    assert sent.index("clear") < sent.index("add lua /scripts/loss.lua")


def test_a_router_running_nothing_empties_a_clean_editor(app):
    lab = _lab(app)
    lab._on_chain(LIVE)
    lab._on_chain(EMPTY)
    assert _boxes(lab) == []
    assert lab.deploy_status.text() == "in sync"


def test_no_text_means_no_claim(app):
    lab = _lab(app)
    lab._on_chain("")
    assert lab.deploy_status.text() == ""


# --------------------------------------------------------------------------- #
# the Lua box deploys
# --------------------------------------------------------------------------- #

def test_typing_a_script_name_into_the_lua_box_deploys_it(app):
    sent = []
    lab = _lab(app, sent=sent, live=EMPTY)
    inst = lab.program.add("lua")
    lab._set_param(inst, "path", "loss.lua")     # what a student would type
    assert inst.params["path"] == "/scripts/loss.lua"
    assert lab.program.dirty, "a parameter edit is a local edit"
    lab._deploy_chain()
    assert sent == ["clear", "add lua /scripts/loss.lua"]


def test_a_lua_box_with_no_script_is_shown_but_not_deployed(app):
    sent = []
    lab = _lab(app, sent=sent, live=EMPTY)
    lab.program.add("lua")
    lab._deploy_chain()
    assert sent == ["clear"], "a blank path would only earn a usage error from the router"
    lab._on_chain(EMPTY)
    assert lab.program.dirty is False
    assert "shown but not deployed" in lab.deploy_status.text()


# --------------------------------------------------------------------------- #
# "drop loss.lua into it" — literally
# --------------------------------------------------------------------------- #

def test_a_dropped_lua_file_becomes_a_box_pointing_at_the_scripts_mount(app, tmp_path, monkeypatch):
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path / "home"))
    src = tmp_path / "Desktop" / "loss.lua"
    src.parent.mkdir()
    src.write_text("LOSS = 0.2\nfunction process(pkt, ctx) return CONTINUE end\n")
    lab = _lab(app, live=EMPTY)
    assert lab.add_lua_file(str(src)) == "loss.lua"
    assert _boxes(lab) == [("lua", {"path": "/scripts/loss.lua"})]
    # copied into the one folder a router can read, so Deploy will find it
    copied = tmp_path / "home" / "scripts" / "loss.lua"
    assert copied.read_text() == src.read_text()
    assert lab.program.deploy_commands() == ["clear", "add lua /scripts/loss.lua"]


def test_a_file_already_in_the_scripts_folder_is_used_in_place(app, tmp_path, monkeypatch):
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path / "home"))
    scripts = tmp_path / "home" / "scripts"
    scripts.mkdir(parents=True)
    src = scripts / "loss.lua"
    src.write_text("-- mine\n")
    lab = _lab(app, live=EMPTY)
    assert lab.add_lua_file(str(src)) == "loss.lua"
    assert src.read_text() == "-- mine\n", "copying a file onto itself must not touch it"


def test_a_different_file_of_the_same_name_is_never_overwritten(app, tmp_path, monkeypatch):
    """A student's edited script must not be replaced by a stale copy dragged in from
    somewhere else. The existing one is used, and the status says so."""
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path / "home"))
    scripts = tmp_path / "home" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "loss.lua").write_text("LOSS = 0.5\n")
    stale = tmp_path / "old" / "loss.lua"
    stale.parent.mkdir()
    stale.write_text("LOSS = 0.1\n")
    lab = _lab(app, live=EMPTY)
    lab.add_lua_file(str(stale))
    assert (scripts / "loss.lua").read_text() == "LOSS = 0.5\n"
    assert "not overwritten" in lab.deploy_status.text()


def test_something_that_is_not_a_lua_file_is_ignored(app, tmp_path, monkeypatch):
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path / "home"))
    lab = _lab(app, live=EMPTY)
    assert lab.add_lua_file(str(tmp_path / "missing.lua")) == ""
    assert _boxes(lab) == []


def test_the_window_accepts_drops_and_only_of_lua_files(app):
    from PySide6.QtCore import QMimeData, QUrl
    lab = _lab(app, live=EMPTY)
    assert lab.acceptDrops()

    class _E:
        def __init__(self, urls): self._md = QMimeData(); self._md.setUrls(urls)
        def mimeData(self): return self._md
    assert lab._dropped_lua_files(_E([QUrl.fromLocalFile("/tmp/x/loss.lua")])) == ["/tmp/x/loss.lua"]
    assert lab._dropped_lua_files(_E([QUrl.fromLocalFile("/tmp/x/notes.txt")])) == []
    assert lab._dropped_lua_files(_E([QUrl("https://example.org/loss.lua")])) == []


# --------------------------------------------------------------------------- #
# ...and the boxes have to be where a person can see them
# --------------------------------------------------------------------------- #

def test_the_pipeline_is_not_a_peephole(app):
    """Measured before this: content 637px tall in a 62px viewport at the default window size,
    because the routing table and QoS panel below carried minimum heights and the pipeline
    carried none. Mirroring the router into the editor is worthless if the boxes land below
    the fold — which is exactly where a student's loaded module would have gone."""
    lab = _lab(app)
    lab._on_chain(LIVE)
    lab.show(); app.processEvents(); app.processEvents()
    # The viewport is the scroll area less its frame, so a pixel or two under the minimum is
    # the frame, not a squeeze. The squeeze this guards against was 62 pixels.
    assert lab.pipe_scroll.viewport().height() >= RouterLab.PIPELINE_MIN_H - 4
    # the first mirrored box sits inside the visible area, not scrolled away
    first_inline = next(w for w, st in zip(lab._stage_widgets, lab.program.stages())
                        if st.kind == "inline")
    assert first_inline.y() + first_inline.height() <= lab.pipe_scroll.viewport().height(), \
        "the first service function is below the fold"


# --------------------------------------------------------------------------- #
# the old image, and self-healing an empty editor
# --------------------------------------------------------------------------- #

OLD = "base: parse -> [0:lua] -> route -> rewrite"        # a router without the argument feature


def test_a_lua_loaded_on_an_old_image_shows_in_the_chain_and_stays(app):
    """The reported bug: on an un-rebuilt router the box appeared "for a moment" then the chain
    re-synced every poll and a stray click cleared it. It must simply stay put."""
    lab = _lab(app, live=OLD)
    for _ in range(3):                                    # three polls that used to churn
        lab._on_chain(OLD)
    assert [i.name for i in lab.program.inline] == ["lua"]
    assert lab.deploy_status.text() == "in sync"
    assert lab.program.dirty is False


def test_an_empty_editor_follows_the_router_even_if_flagged_dirty(app):
    """An empty chain has no draft to protect, and this is the "show my loaded module" case.
    It self-heals the stuck "chain cleared here" a misclick used to leave behind."""
    lab = _lab(app, live=LIVE)
    lab.program.add("nat")                                # make it dirty
    lab.program.remove(0)                                 # ...and empty: the stuck state
    assert lab.program.dirty and not lab.program.inline
    lab._on_chain(LIVE)
    assert [i.type_key for i in lab.program.inline] == ["acl", "lua"], "did not recover"
    assert lab.deploy_status.text() == "in sync"


def test_a_non_empty_draft_is_still_protected_from_the_poll(app):
    """The other side: a real draft with modules in it must NOT be overwritten by a poll."""
    lab = _lab(app, live=LIVE)
    lab.program.add("nat")
    lab.program.inline[0].params["ip"] = "198.51.100.7"
    lab._on_chain(LIVE)
    assert [i.type_key for i in lab.program.inline] == ["nat"], "the draft was clobbered"
    assert "edited" in lab.deploy_status.text()
