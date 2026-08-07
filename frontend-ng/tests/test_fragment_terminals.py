"""Terminals vs non-terminals, and the guard that stops a validation instance overwriting a pattern.

A fragment with no open slots is a TERMINAL (a value — it can fill someone else's slot). One with
slots/peerings is a NON-TERMINAL (a template that must be filled before it's a real network). The
list marks them, and Save refuses while a Validate ×N instance is on the canvas.
"""
import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["GINI_HOME_DIR"] = tempfile.mkdtemp()

from PySide6.QtWidgets import QApplication

from gini.domain import fragment_yaml as FY


def _app():
    return QApplication.instance() or QApplication([])


def _fm():
    from gini.ui.fragment_manager import FragmentManager
    from gini.ui.main_window import MainWindow
    w = MainWindow(QApplication.instance())
    return FragmentManager(w, w.ctx, author="prof"), w


def test_terminal_vs_non_terminal_classification():
    _app()
    fm, _ = _fm()

    terminal = FY.fragment_from_dict({
        "id": "cap-lan", "layer": "core",
        "objectives": [{"id": "h", "say": "host", "check": "exists(host)", "level": 1}]})
    assert fm._open_slots(terminal) == []
    assert fm._is_terminal(terminal) is True

    non_terminal = FY.fragment_from_dict({
        "id": "hub-router", "layer": "core",
        "objectives": [{"id": "r", "say": "router", "check": "exists(router)", "level": 1}],
        "slots": [{"name": "lans", "role": "l2-fabric", "min": 2, "max": 0}]})
    assert fm._open_slots(non_terminal) == ["lans ×2+"]      # named + cardinality, for the list row
    assert fm._is_terminal(non_terminal) is False

    peered = FY.fragment_from_dict({
        "id": "mesh", "layer": "core",
        "objectives": [{"id": "r", "say": "router", "check": "exists(router)", "level": 1}],
        "peerings": [{"name": "sites", "role": "l2-fabric", "min": 3, "max": 0,
                      "topology": "mesh"}]})
    assert peered and fm._open_slots(peered) == ["sites ×3+ mesh"]
    assert fm._is_terminal(peered) is False


def test_a_fixed_leg_reads_as_a_single_slot_not_repeatable():
    _app()
    fm, _ = _fm()
    fixed = FY.fragment_from_dict({
        "id": "one-leg", "layer": "core",
        "objectives": [{"id": "r", "say": "router", "check": "exists(router)", "level": 1}],
        "slots": [{"name": "A", "role": "l2-fabric", "min": 1, "max": 1}]})
    assert fm._open_slots(fixed) == ["A ×1"]                 # no "+" — it binds exactly one


def test_reopening_a_certified_fragment_keeps_its_stamp_on_resave(monkeypatch):
    """The ✓ must survive reopen→save. Save stamps `certified` only when the content hash matches
    the last green grade; a freshly-opened editor used to start with no hash, so re-saving an
    already-certified fragment silently ERASED its ✓ (which is what reopening between Validate ×N
    runs does). Reopening must seed the hash from the stored fragment."""
    _app()
    from PySide6.QtWidgets import QMessageBox
    from gini.domain import fragments as F
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.Yes))
    fm, w = _fm()

    # author + "certify" a fragment (simulate the green grade by seeding the hash the way
    # _on_cert_ready does), then save it
    fm._create()
    fm.fid.setText("cert-keep")
    fm._steps = [{"id": "h", "say": "host", "check": "exists(host)", "level": 1}]
    w.ctx.add_device("host", 10, 10)
    d = fm._current_dict()
    fm._certified_hash = fm._dict_hash(d)
    fm._finalize()
    F.reload()
    assert F.get("cert-keep").certified is True            # saved with the stamp

    # …now REOPEN it and save again without touching anything
    fm._open_editor("cert-keep")
    assert fm._certified_hash is not None                  # the stamp's hash came back
    fm._finalize()
    F.reload()
    assert F.get("cert-keep").certified is True            # ✓ survived the round-trip

    # …but a real edit must still drop it
    fm._open_editor("cert-keep")
    fm._steps.append({"id": "r", "say": "router", "check": "exists(router)", "level": 1})
    fm._finalize()
    F.reload()
    assert F.get("cert-keep").certified is False           # content changed → re-certify required


def test_validate_n_is_read_only_and_keeps_the_certification(monkeypatch):
    """Validate ×N is a TEST. It installs the in-editor draft in the registry so the composer can
    resolve it by id — but the draft carries no `certified` flag (that's stamped at Save), so a
    plain install would overwrite the loaded certified entry and the list would go ○."""
    _app()
    from PySide6.QtWidgets import QInputDialog, QMessageBox
    from gini.domain import fragments as F
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    fm, w = _fm()

    fm._create()
    fm.fid.setText("keep-cert")
    fm._steps = [{"id": "r", "say": "router", "check": "exists(router)", "level": 1}]
    fm._slots = [{"name": "nets", "role": "l2-fabric", "min": 2, "max": 0, "distinct": True}]
    w.ctx.add_device("router", 10, 10)
    d = fm._current_dict()
    fm._certified_hash = fm._dict_hash(d)
    fm._finalize()
    F.reload()
    assert F.get("keep-cert").certified is True

    fm._open_editor("keep-cert")
    # cancel at the first binding dialog — the registry must still be pristine afterwards
    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: ("", False)))
    fm._compose_validate()
    assert F.get("keep-cert").certified is True          # ✓ survived a Validate ×N round-trip


def test_multi_slot_scaffolds_get_separate_layout_bands():
    """R_net(X, Y, Z): each slot's scaffold must occupy its own column band. The band offset used to
    come only from the representative index, so every slot restarted at the same x and the three
    groups piled on top of each other."""
    _app()
    fm, w = _fm()
    fm._create()

    # slot X already on the board (2 devices), then a second slot arrives
    a = w.ctx.add_device("switch", 10, 10)
    w.ctx.topology.devices[a.id].slot = "X"
    assert fm._slot_band("X") == 0                    # the first slot starts at band 0
    assert fm._slot_band("Y") == 2                    # …the next one clears it

    b = w.ctx.add_device("switch", 10, 10)
    w.ctx.topology.devices[b.id].slot = "Y"
    assert fm._slot_band("Z") == 4                    # …and a third clears both


def test_materialize_honours_a_layout_offset():
    """The scaffold builder used to start every call at the same x, stacking representatives."""
    _app()
    from gini.domain import authoring as AU
    fm, w = _fm()
    objs = [{"check": "exists(switch)"}]
    first = AU.materialize(w.ctx, objs, x0=60)
    second = AU.materialize(w.ctx, objs, x0=660)
    x1 = w.ctx.topology.devices[first[0]].x
    x2 = w.ctx.topology.devices[second[0]].x
    assert x1 == 60 and x2 == 660 and x1 != x2        # side by side, not stacked


def test_slot_tagged_nodes_are_grouped_into_labelled_hulls():
    """The canvas must show which elements are scaffolding (parameters) vs the authored delta."""
    _app()
    from gini.ui.canvas import CanvasScene
    from gini.ui.theme.manager import get_theme
    from gini.app.context import AppContext

    ctx = AppContext()
    scene = CanvasScene(ctx, get_theme("dark"))
    d1 = ctx.add_device("switch", 60, 60)
    d2 = ctx.add_device("host", 160, 60)
    ctx.add_device("router", 400, 60)                  # the delta — no slot
    ctx.topology.devices[d1.id].slot = "nets"
    ctx.topology.devices[d2.id].slot = "nets"

    groups: dict = {}
    for n in scene.nodes.values():
        s = getattr(n.inst, "slot", "")
        if s:
            groups.setdefault(s, []).append(n)
    assert list(groups) == ["nets"] and len(groups["nets"]) == 2   # scaffold grouped…
    assert len(scene.nodes) == 3                                   # …delta excluded
    assert "nets" in scene._slot_label("nets", 2)                  # …and the hull is labelled


def test_slot_hull_label_names_what_fills_the_slot():
    """`nets · cap-lan · 3 element(s)` — a count alone doesn't say what the parameter IS."""
    _app()
    from gini.ui.canvas import CanvasScene
    from gini.ui.theme.manager import get_theme
    from gini.app.context import AppContext
    scene = CanvasScene(AppContext(), get_theme("dark"))

    lbl = scene._slot_label("nets", 3, "cap-lan")
    assert "nets" in lbl and "cap-lan" in lbl and "3" in lbl
    # the composer labels members root_lans0/1/2 — show the slot, not the member index
    assert scene._slot_label("root_lans2", 3, "cap-lan").startswith("lans · cap-lan")
    assert scene._slot_label("nets", 2) == "nets   ·   2 element(s)"       # no source → just the slot


def test_slot_source_survives_composition_and_reload():
    """Provenance has to ride along with the device, or a composed board can't label its groups."""
    _app()
    from gini.domain.topology import Topology
    t = Topology()
    d = t.add_device("switch", "S1")
    d.slot, d.slot_source = "root_lans0", "cap-lan"
    back = Topology.from_dict(t.to_dict())
    got = next(iter(back.devices.values()))
    assert got.slot == "root_lans0" and got.slot_source == "cap-lan"


def test_deleting_a_fragment_also_deletes_it_on_the_teaching_center(monkeypatch):
    """The teacher owns both sides. A local-only delete is undone by the next sign-in's sync, so
    deleting locally must delete centrally too — and an unreachable Center must not block it."""
    _app()
    import types
    fm, w = _fm()

    called = {}

    def _del(fid):
        called["id"] = fid
        return {"ok": True}

    w.ctx.teaching_center = types.SimpleNamespace(is_teacher=lambda: True, delete_fragment=_del)
    note = fm._delete_on_center("cap-lan")
    assert called["id"] == "cap-lan" and "Teaching Center" in note

    # a student (not a teacher) touches nothing central
    w.ctx.teaching_center = types.SimpleNamespace(is_teacher=lambda: False)
    assert fm._delete_on_center("cap-lan") == ""

    # an unreachable Center warns but never raises
    w.ctx.teaching_center = types.SimpleNamespace(
        is_teacher=lambda: True,
        delete_fragment=lambda fid: {"ok": False, "error": "Can't reach the course server."})
    warned = fm._delete_on_center("cap-lan")
    assert "re-sync" in warned

    # never published there → no scary warning
    w.ctx.teaching_center = types.SimpleNamespace(
        is_teacher=lambda: True,
        delete_fragment=lambda fid: {"ok": False, "error": "No such fragment"})
    assert fm._delete_on_center("cap-lan") == ""


def test_published_fragments_are_marked_and_unknown_stays_silent():
    """A teacher needs to see, per fragment, whether it's on the Teaching Center. When we CAN'T know
    (offline / not a teacher) the row must say nothing rather than imply 'not published'."""
    _app()
    fm, _ = _fm()

    fm._tc_ids = None                                     # unknown → silent
    assert fm._tc_mark("cap-lan") == ("", "")

    fm._tc_ids = {"cap-lan"}
    suffix, tip = fm._tc_mark("cap-lan")
    assert suffix.strip() == "↑" and "Published" in tip   # published → marked

    suffix, tip = fm._tc_mark("router-net")
    assert suffix == "" and "Local only" in tip           # known-absent → explained in the tooltip


def test_deleting_drops_the_published_mark_without_a_refetch(monkeypatch):
    _app()
    import types
    fm, w = _fm()
    fm._tc_ids = {"cap-lan", "router-net"}
    w.ctx.teaching_center = types.SimpleNamespace(
        is_teacher=lambda: True, delete_fragment=lambda fid: {"ok": True})
    fm._delete_on_center("cap-lan")
    assert fm._tc_ids == {"router-net"}                   # the ↑ goes immediately


def test_help_panel_covers_the_core_concepts():
    """The manager is concept-dense; the in-app Help has to actually name those concepts."""
    _app()
    fm, _ = _fm()
    h = fm.HELP.lower()
    for term in ("terminal", "non-terminal", "slot", "certif", "validate ×n",
                 "composer", "ports", "star"):
        assert term in h, term


def test_a_fragment_can_never_be_bound_into_its_own_slot(monkeypatch):
    """No self-reference and no cycles: a block that contains itself is a grammar with no base case,
    and `_resolve_binding` → `_pick_members` → `_resolve_binding` would recurse forever."""
    _app()
    from PySide6.QtWidgets import QInputDialog, QMessageBox
    from gini.domain import fragments as F
    fm, _ = _fm()

    # a fragment whose OWN provides satisfy its OWN slot role — the self-reference trap
    self_ref = FY.fragment_from_dict({
        "id": "loopy", "layer": "core", "certified": True,
        "objectives": [{"id": "r", "say": "router", "check": "exists(router)", "level": 1}],
        "provides": ["l2-fabric"],
        "slots": [{"name": "nets", "role": "l2-fabric", "min": 2, "max": 0}],
        "stage": {"devices": [{"id": "d1", "type_key": "switch", "name": "S1"}], "links": []}})
    monkeypatch.setattr(F, "all_fragments", lambda: [self_ref])

    shown = {"msg": ""}
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: shown.__setitem__("msg", str(a))))
    monkeypatch.setattr(QInputDialog, "getItem",
                        staticmethod(lambda *a, **k: (None, False)))   # must never be reached

    assert fm._resolve_binding(self_ref) is None          # refused, not an infinite dialog loop
    assert "loopy" in shown["msg"]                        # …and it says why


def test_save_is_refused_while_a_validation_instance_is_on_the_canvas(monkeypatch):
    """Save snapshots the canvas as the fragment's authoring board, so saving on top of a
    Validate ×N instance would replace the authored pattern with the scaled copy."""
    _app()
    from PySide6.QtWidgets import QMessageBox
    fm, w = _fm()
    fm._create()
    fm.fid.setText("hub-router")
    fm._steps = [{"id": "r", "say": "router", "check": "exists(router)", "level": 1}]

    told = {"n": 0}
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: told.__setitem__("n", told["n"] + 1)))

    fm._composed_objectives = ["pretend a composition is showing"]
    fm._finalize()
    assert told["n"] == 1                                    # explained, not silently saved
    assert "hub-router" not in fm._authored_ids()            # …and nothing was written

    fm._composed_objectives = None                           # back to the authoring board
    fm._finalize()
    assert "hub-router" in fm._authored_ids()                # now it saves
