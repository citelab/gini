"""The new fragment format (measure() output checks + rider elements) flows through the Teaching
Center: the vocabulary advertises it, and such a fragment registers cleanly."""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["GINI_HOME_DIR"] = tempfile.mkdtemp()

_TC = Path(__file__).resolve().parents[2] / "teaching-center"
sys.path.insert(0, str(_TC))

import teacher as T                                       # noqa: E402

from gini.domain import authoring as AU                   # noqa: E402
from gini.domain import fragment_yaml as FY               # noqa: E402
from gini.domain import vocabulary as V                   # noqa: E402


def test_vocabulary_advertises_measure_and_the_riders():
    voc = V.export()
    assert "measure" in voc["probes"]                     # the new output-check probe is discoverable
    keys = {e["key"] for e in voc["elements"]}
    assert {"packet_view", "ping_probe", "iperf_client"} <= keys   # riders are real elements → exported


def test_a_new_format_fragment_registers_at_the_tc():
    d = AU.build_fragment_dict(
        frag_id="cap-exp", teaches="net", summary="the capture sees traffic",
        spirit="the receiver's capture sees the pings",
        objectives=[{"id": "h", "say": "2 hosts", "check": "count(host) >= 2", "level": 1},
                    AU.output_check("packet_view", "packets", ">=", 1)])
    yaml_text = FY.to_yaml(FY.fragment_from_dict(d))
    res = T.register_fragment(yaml_text)
    assert res.get("ok"), res                             # measure() + rider ref accepted by the gate
