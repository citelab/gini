"""Fragment editor — the Ports panel + output checks.

Attaching a Source/Sink while authoring makes it an In/Out port; a Sink's measurement becomes a
gradable output via a `measure(...)` objective, and saving a fragment with an unchecked Sink is
gated. This locks the authoring-side wiring (the domain probe is covered in test_riders/probes).
"""
import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["GINI_HOME_DIR"] = tempfile.mkdtemp()

from PySide6.QtWidgets import QApplication

from gini.domain import authoring as AU
from gini.domain import probes as P


def _app():
    return QApplication.instance() or QApplication([])


# -- domain: the measure() probe is a first-class, gradable behavioral check -- #
def test_measure_probe_parses_and_grades_against_a_runner():
    assert P.probe_ok("measure(packet_view, packets) >= 1")
    fr = P.FakeRunner({("measure", "packet_view", "packets"): 12,
                       ("measure", "ping_probe", "loss_pct"): 0.0})
    assert P.evaluate("measure(packet_view, packets) >= 1", fr) is True
    assert P.evaluate("measure(packet_view, packets) >= 50", fr) is False
    assert P.evaluate("measure(ping_probe, loss_pct) <= 5", fr) is True
    assert P.evaluate("measure(iperf_client, mbps) >= 100", P.FakeRunner({})) is False  # no reading


def test_output_check_is_a_valid_saveable_objective():
    o = AU.output_check("iperf_client", "mbps", ">=", 100)
    assert o["kind"] == "behavioral" and o["probe"] == "measure(iperf_client, mbps) >= 100"
    d = AU.build_fragment_dict(frag_id="t-iperf", teaches="net", summary="throughput", spirit="",
                               objectives=[{"id": "h", "say": "place host",
                                            "check": "exists(host)", "level": 1}, o])
    assert AU.validate_dict(d) == []


# -- editor: attached riders become In/Out ports, checks track them ----------- #
def test_ports_panel_reads_attached_riders_and_tracks_checks():
    _app()
    from gini.ui.main_window import MainWindow
    from gini.ui.fragment_manager import FragmentManager

    w = MainWindow(QApplication.instance())
    ctx = w.ctx
    m = ctx.add_device("host", 10, 10)
    src = ctx.add_device("http_probe", 40, 10)      # a Source
    snk = ctx.add_device("packet_view", 70, 10)     # a Sink
    ctx.connect(m.id, src.id)
    ctx.connect(m.id, snk.id)
    unattached = ctx.add_device("iperf_server", 200, 200)   # NOT attached → not a port

    fm = FragmentManager(w, ctx, author="prof")
    fm._create()
    fm._render_ports()                              # must not raise

    sources, sinks = fm._canvas_riders()
    assert [d.id for d in sources] == [src.id]      # only the attached source
    assert [d.id for d in sinks] == [snk.id]        # only the attached sink
    assert unattached.id not in [d.id for d in sources + sinks]

    # no check yet, then add one → tracked by the panel
    assert not fm._has_check("packet_view")
    fm._steps.append(AU.output_check("packet_view", "packets", ">=", 1))
    assert fm._has_check("packet_view")
    fm._render_ports()                              # re-render with the ✓ must not raise


def test_editor_certify_builds_a_report_from_current_state():
    _app()
    from gini.domain import certify as C
    from gini.ui.main_window import MainWindow
    from gini.ui.fragment_manager import FragmentManager

    w = MainWindow(QApplication.instance())
    fm = FragmentManager(w, w.ctx, author="prof")
    fm._create()
    fm.fid.setText("cap-lan")
    fm.spirit.setText("")                                # deliberately blank → a soft warning
    fm._steps = [{"id": "h", "say": "place host", "check": "exists(host)", "level": 1}]

    d = fm._current_dict()
    assert d is not None and d["id"] == "cap-lan"
    rep = C.certify(d, library=[])
    assert rep.certified                                 # nothing blocks
    assert any(i.code == "no-spirit" for i in rep.of(C.WARN))   # the blank spirit is flagged, softly


def test_certify_green_stamps_the_save_and_edits_revoke_it(monkeypatch):
    _app()
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    from gini.domain import certify as C
    from gini.domain import fragments as F
    from gini.ui.main_window import MainWindow
    from gini.ui.fragment_manager import FragmentManager

    w = MainWindow(QApplication.instance())
    fm = FragmentManager(w, w.ctx, author="prof")
    fm._create()
    fm.fid.setText("struct-cert")
    fm.spirit.setText("the hosts exist")
    fm._steps = [{"id": "h", "say": "2 hosts", "check": "count(host) >= 2", "level": 1}]

    d = fm._current_dict()
    rt = fm._runtime_grade(d)                          # no live stack → available=False
    assert rt.available is False
    rep = C.certify(d, library=[], runtime=rt)         # structural-only ⇒ certifies with no runtime
    assert rep.certified
    fm._on_cert_ready(rep, d)
    assert fm._certified_hash is not None

    fm._finalize()
    F.reload()
    assert F.get("struct-cert").certified is True      # the stamp was saved

    # editing the content revokes it: a changed board no longer matches the certified hash
    fm._steps.append({"id": "s", "say": "switch", "check": "exists(switch)", "level": 1})
    assert fm._dict_hash(fm._current_dict()) != fm._certified_hash


def test_saving_with_an_unchecked_sink_is_gated(monkeypatch):
    _app()
    from PySide6.QtWidgets import QMessageBox
    from gini.ui.main_window import MainWindow
    from gini.ui.fragment_manager import FragmentManager

    w = MainWindow(QApplication.instance())
    ctx = w.ctx
    m = ctx.add_device("host", 10, 10)
    snk = ctx.add_device("packet_view", 70, 10)
    ctx.connect(m.id, snk.id)

    fm = FragmentManager(w, ctx, author="prof")
    fm._create()
    fm.fid.setText("exp-unchecked")
    fm._steps = [{"id": "h", "say": "place host", "check": "exists(host)", "level": 1}]

    asked = {"n": 0}
    def fake_q(*a, **k):
        asked["n"] += 1
        return QMessageBox.No                        # decline "save anyway"
    monkeypatch.setattr(QMessageBox, "question", staticmethod(fake_q))
    fm._finalize()
    assert asked["n"] == 1                            # the dangling-output gate fired
    assert "exp-unchecked" not in fm._authored_ids()  # …and nothing was saved
