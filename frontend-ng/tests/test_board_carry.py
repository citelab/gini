"""Board-carry — a fragment stores its authoring board, and editing restores it onto the canvas so
steps, ports, and the derived contract always agree.
"""
import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["GINI_HOME_DIR"] = tempfile.mkdtemp()

from PySide6.QtWidgets import QApplication


def _app():
    return QApplication.instance() or QApplication([])


def test_save_captures_the_board_and_load_restores_types_and_edges():
    _app()
    from gini.ui.fragment_manager import FragmentManager
    from gini.ui.main_window import MainWindow

    w = MainWindow(QApplication.instance())
    ctx = w.ctx
    m = ctx.add_device("host", 10, 10)
    sw = ctx.add_device("switch", 60, 10)
    ctx.add_link(m.id, sw.id)
    ping = ctx.add_device("ping_probe", 100, 10)
    ctx.connect(m.id, ping.id)                                # an attach edge

    fm = FragmentManager(w, ctx, author="prof")
    fm._create()
    fm.fid.setText("brd")
    fm._steps = [{"id": "h", "say": "host", "check": "exists(host)", "level": 1}]

    d = fm._current_dict()
    stage = d.get("stage")
    assert stage and len(stage["devices"]) == 3              # the whole board is captured…
    assert any(l.get("kind") == "attach" for l in stage["links"])   # …including the rider mount

    # restore onto a cleared canvas — types + both edge kinds come back
    ctx.clear_topology()
    assert not ctx.topology.devices
    fm._load_board(stage)
    assert sorted(dv.type_key for dv in ctx.topology.devices.values()) == \
        ["host", "ping_probe", "switch"]
    assert sorted(l.kind for l in ctx.topology.links.values()) == ["attach", "link"]
