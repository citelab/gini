"""The new fragment format (measure() output checks + rider elements) is accepted by the gate.

This used to route through the Teaching Center's `teacher.register_fragment`. v1 removed lesson
authoring from the server, but the format itself is *not* dead — gBuilder still authors fragments
locally (`ui/author_dialog.py`, `ui/fragment_manager.py`), so the same assertion is made directly
against the domain gate that both paths use.
"""
import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["GINI_HOME_DIR"] = tempfile.mkdtemp()

from gini.domain import authoring as AU                   # noqa: E402
from gini.domain import fragment_yaml as FY               # noqa: E402
from gini.domain import vocabulary as V                   # noqa: E402


def test_vocabulary_advertises_measure_and_the_riders():
    voc = V.export()
    assert "measure" in voc["probes"]                     # the new output-check probe is discoverable
    keys = {e["key"] for e in voc["elements"]}
    assert {"packet_view", "ping_probe", "iperf_client"} <= keys   # riders are real elements → exported


def test_a_new_format_fragment_passes_the_gate():
    d = AU.build_fragment_dict(
        frag_id="cap-exp", teaches="net", summary="the capture sees traffic",
        spirit="the receiver's capture sees the pings",
        objectives=[{"id": "h", "say": "2 hosts", "check": "count(host) >= 2", "level": 1},
                    AU.output_check("packet_view", "packets", ">=", 1)])
    assert AU.validate_dict(d) == []                      # measure() + rider ref accepted
    assert FY.validate(FY.fragment_from_dict(d)) == []    # and survives the YAML round trip
