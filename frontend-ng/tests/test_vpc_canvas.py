"""Canvas VPC boxes: containers render as resizable boxes and capture elements by where
they sit (geometry -> parent_id), which the compiler turns into isolated networks."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from gini.ui.canvas import GroupItem, NodeItem
from gini.ui.main_window import MainWindow


def _win():
    app = QApplication.instance() or QApplication([])
    return app, MainWindow(app)


def test_vpc_renders_as_a_box_not_a_node():
    app, w = _win()
    vpc = w.api.add_device("vpc", x=20, y=20)["id"]
    app.processEvents()
    scene = w.canvas.scene_
    assert vpc in scene.groups and vpc not in scene.nodes
    assert isinstance(scene.groups[vpc], GroupItem)


def test_service_dropped_inside_a_vpc_joins_it():
    app, w = _win()
    vpc = w.api.add_device("vpc", x=20, y=20)["id"]        # box ≈ (20,20)-(400,280)
    db = w.api.add_device("database", x=140, y=140)["id"]  # centre well inside
    app.processEvents()
    assert isinstance(w.canvas.scene_.nodes[db], NodeItem)
    assert w.ctx.topology.devices[db].parent_id == vpc


def test_dragging_a_service_out_clears_membership():
    app, w = _win()
    vpc = w.api.add_device("vpc", x=20, y=20)["id"]
    db = w.api.add_device("database", x=140, y=140)["id"]
    app.processEvents()
    assert w.ctx.topology.devices[db].parent_id == vpc
    w.canvas.scene_.nodes[db].setPos(900, 900)            # drag far outside the box
    w.canvas.scene_.recompute_membership()
    assert w.ctx.topology.devices[db].parent_id is None


def test_moving_the_box_carries_its_contents():
    app, w = _win()
    vpc = w.api.add_device("vpc", x=20, y=20)["id"]
    db = w.api.add_device("database", x=140, y=140)["id"]
    app.processEvents()
    node = w.canvas.scene_.nodes[db]
    x0 = node.pos().x()
    w.canvas.scene_.groups[vpc].setPos(220, 20)          # shift box right by 200
    assert node.pos().x() == x0 + 200                    # the contained db moved with it
    assert w.ctx.topology.devices[db].parent_id == vpc   # still a member afterwards
