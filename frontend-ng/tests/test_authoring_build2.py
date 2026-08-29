"""Build 2 — authoring: objectives by demonstration, teacher-mode gating, the composer dialog.

The heart is reading the canvas into gradable objectives: the derived checks must be the SAME
type-based predicates the student is graded on, so a fragment is honest by construction.
"""
import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["GINI_HOME_DIR"] = tempfile.mkdtemp()

from types import SimpleNamespace

from PySide6.QtWidgets import QApplication

from gini.domain import authoring as AU
from gini.domain import fragments as F
from gini.domain.topology import Topology


def _app():
    return QApplication.instance() or QApplication([])


# -- derivation (by demonstration) ------------------------------------------- #
def test_placement_connection_containment_are_read_off_the_board():
    t = Topology()
    vpc = t.add_device("vpc", "V"); web = t.add_device("web_app", "W"); db = t.add_device("database", "D")
    web.parent_id = vpc.id; db.parent_id = vpc.id
    t.add_link(web.id, db.id)
    objs = {o["check"]: o["level"] for o in AU.derive_objectives(t)}

    assert objs["exists(vpc)"] == 1 and objs["exists(web_app)"] == 1     # L1 placement
    assert objs["link(database, web_app)"] == 2                          # L2 connection (type pair)
    assert objs["contains_type(vpc, web_app)"] == 3                      # L3 containment
    assert objs["contains_type(vpc, database)"] == 3


def test_multiple_of_a_type_becomes_a_count():
    t = Topology()
    for n in ("H1", "H2", "H3"):
        t.add_device("host", n)
    checks = {o["check"] for o in AU.derive_objectives(t)}
    assert "count(host) >= 3" in checks and "exists(host)" not in checks


def test_an_empty_canvas_derives_nothing():
    assert AU.derive_objectives(Topology()) == []


# -- build + save (blessed to the user layer) -------------------------------- #
def test_a_derived_fragment_validates_and_saves_and_loads():
    t = Topology()
    t.add_device("switch", "S1"); t.add_device("host", "H1"); t.add_device("host", "H2")
    d = AU.build_fragment_dict(frag_id="t-authored", teaches="networking-basics",
                               summary="a LAN", spirit="hosts on a switch",
                               objectives=AU.derive_objectives(t), author="prof")
    assert AU.validate_dict(d) == []
    assert d["engine_version"]                         # stamped for version-gating
    path = AU.save_fragment(d)
    assert path.endswith("t-authored.yaml")
    F.reload()
    assert F.get("t-authored") is not None and F.get("t-authored").author == "prof"


def test_an_ungradable_fragment_is_never_written():
    import pytest
    d = AU.build_fragment_dict(frag_id="bad", teaches="", summary="", spirit="",
                               objectives=[{"id": "x", "say": "x", "check": "nonsense(("}])
    assert AU.validate_dict(d)                          # caught
    with pytest.raises(ValueError):
        AU.save_fragment(d)                             # …and refused at the disk boundary


def test_a_fork_is_carried_into_the_saved_fragment():
    d = AU.build_fragment_dict(
        frag_id="t-forked", teaches="networking-basics", summary="x", spirit="x",
        objectives=[{"id": "c", "say": "Place a switch", "check": "exists(switch)"}],
        forks=[{"id": "hard", "label": "add a router", "difficulty": 2, "kind": "converge",
                "objectives": [{"id": "fork-r", "say": "Add a router", "check": "exists(router)"}]}])
    assert AU.validate_dict(d) == []
    AU.save_fragment(d); F.reload()
    f = F.get("t-forked")
    assert len(f.forks) == 1 and f.forks[0].kind == "converge"


# -- teacher-mode gating ----------------------------------------------------- #
def test_author_action_appears_only_for_a_teacher():
    """The author tool is gated on the TC session role. Tested via the extracted helper so we don't
    have to build (and offscreen-crash on) the whole user menu."""
    from PySide6.QtWidgets import QMenu
    from gini.ui.main_window import MainWindow
    app = _app()
    w = MainWindow(app)

    student_tc = SimpleNamespace(is_teacher=lambda: False)
    m1 = QMenu()
    w._add_teacher_items(m1, student_tc)
    assert not any("Fragment Manager" in a.text() for a in m1.actions())    # student: nothing

    teacher_tc = SimpleNamespace(is_teacher=lambda: True)
    m2 = QMenu()
    w._add_teacher_items(m2, teacher_tc)
    assert any("Fragment Manager" in a.text() for a in m2.actions())        # teacher: it's there


def test_the_client_tracks_teacher_role_from_login(tmp_path):
    from gini.agent import teaching_center as TC

    def as_teacher(method, path, body=None):
        if path == "/auth/login":
            return 200, {"ok": True, "session": "S", "role": "teacher"}
        return 200, []
    c = TC.TeachingCenterClient("https://localhost:8443", course="c1", student_id="prof",
                                cache_dir=tmp_path, transport=as_teacher)
    assert not c.is_teacher()                            # not until signed in
    c.login("pw")
    assert c.is_teacher()
    # role persists across a fresh client (cached session)
    c2 = TC.TeachingCenterClient("https://localhost:8443", course="c1", student_id="prof",
                                 cache_dir=tmp_path, transport=as_teacher)
    assert c2.is_teacher()
