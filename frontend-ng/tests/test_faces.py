"""Role-specialized Router Lab faces: firewall (rules-first, pipeline under Advanced),
router (full pipeline), OVS (SDN dashboard) — one engine, different front-ends."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from gini.domain.firewall import deploy_commands, parse_rules
from gini.domain.router_modules import RouterProgram
from gini.ui.main_window import MainWindow
from gini.ui.router_lab import RouterLab
from gini.ui.theme import ThemeManager


def _app():
    return QApplication.instance() or QApplication([])


# ---- pure ACL translation ------------------------------------------------ #
def test_firewall_rules_to_acl_commands():
    txt = "deny 10.0.3.0/24\n# a comment\n10.0.9.5\nallow whatever\ndeny 10.0.4.0/24"
    assert parse_rules(txt) == [("deny", "10.0.3.0/24"), ("deny", "10.0.9.5"),
                                ("deny", "10.0.4.0/24")]
    assert deploy_commands(txt) == ["clear", "add acl 10.0.3.0/24", "add acl 10.0.9.5",
                                    "add acl 10.0.4.0/24"]


# ---- the faces ----------------------------------------------------------- #
class _Dev:
    def __init__(self, tk, name):
        self.type_key = tk
        self.name = name
        self.properties = {}


def test_firewall_face_leads_with_rules_pipeline_hidden():
    app = _app()
    lab = RouterLab(None, ThemeManager(app), _Dev("firewall", "FW1"), RouterProgram(),
                    face="firewall")
    assert lab.face == "firewall" and not lab.sdn
    assert "Firewall — FW1" in lab.windowTitle()
    assert hasattr(lab, "fw_rules") and hasattr(lab, "_adv_box")
    assert not lab._adv_box.isVisible()               # full pipeline collapsed by default


def test_firewall_deploy_sends_acl_gpipe_commands():
    app = _app()
    dev = _Dev("firewall", "FW1")
    sent = []
    lab = RouterLab(None, ThemeManager(app), dev, RouterProgram(), face="firewall",
                    command_fn=lambda c: (sent.append(c), "base: parse -> [0:acl] -> route -> rewrite")[1],
                    query_fn=lambda c: "base: parse -> [0:acl] -> route -> rewrite")
    lab.fw_rules.setPlainText("deny 10.0.3.0/24")
    lab._deploy_firewall()
    assert "clear" in sent and "add acl 10.0.3.0/24" in sent
    assert dev.properties["Rules"] == "deny 10.0.3.0/24"   # persisted onto the element


def test_router_face_shows_full_pipeline_no_firewall_panel():
    app = _app()
    lab = RouterLab(None, ThemeManager(app), _Dev("router", "R1"), RouterProgram(),
                    face="router")
    assert "Router Lab — R1" in lab.windowTitle()
    assert not hasattr(lab, "fw_rules") and hasattr(lab, "classifier_edit")


def test_ovs_face_unchanged():
    app = _app()
    lab = RouterLab(None, ThemeManager(app), _Dev("ovs", "OVS1"), RouterProgram(), sdn=True)
    assert lab.face == "ovs" and lab.sdn and hasattr(lab, "flow_table")


def test_main_window_opens_firewall_with_firewall_face():
    app = _app()
    w = MainWindow(app)
    fid = w.api.add_device("firewall")["id"]
    w._on_device_activated(fid)
    assert isinstance(w._router_lab, RouterLab) and w._router_lab.face == "firewall"
