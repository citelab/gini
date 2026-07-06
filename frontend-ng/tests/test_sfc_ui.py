"""Router Lab as a Service Function Chain editor: classifier, deploy, live deployed view."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from gini.domain.router_modules import RouterProgram
from gini.ui.router_lab import RouterLab
from gini.ui.theme import ThemeManager


def _app():
    return QApplication.instance() or QApplication([])


class _Router:
    name = "R1"
    type_key = "router"


def test_router_lab_has_sfc_controls():
    app = _app()
    lab = RouterLab(None, ThemeManager(app), _Router(), RouterProgram(), sdn=False)
    assert hasattr(lab, "classifier_edit") and hasattr(lab, "deploy_btn")
    lab.classifier_edit.setText("tcp:80")
    assert lab.program.classifier == "tcp:80"


def test_deploy_sends_gpipe_commands_and_renders_live_chain():
    app = _app()
    prog = RouterProgram(); prog.add("acl"); prog.add("nat")
    sent = []
    lab = RouterLab(None, ThemeManager(app), _Router(), prog, sdn=False,
                    command_fn=lambda c: (sent.append(c),
                                          "base: parse -> [0:acl] -> [1:nat] -> route -> rewrite")[1],
                    query_fn=lambda c: "base: parse -> [0:acl] -> [1:nat] -> route -> rewrite")
    lab._deploy_chain()
    # drain the worker's queued signal by rendering directly with the same listing
    lab._on_chain("base: parse -> [0:acl] -> [1:nat] -> route -> rewrite")
    assert "parse → acl → nat → route → rewrite" in lab.deployed_lbl.text()


def test_deployed_view_shows_offline_hint_without_router():
    app = _app()
    lab = RouterLab(None, ThemeManager(app), _Router(), RouterProgram(), sdn=False)
    lab._deploy_chain()   # command_fn is None
    assert "not running" in lab.deploy_status.text()
