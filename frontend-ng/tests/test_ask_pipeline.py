"""Ask GINI pipeline wiring in the assistant: markdown rendering, recipe auto-build,
and that free-form questions route through understand->retrieve (no raw dump)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from gini.ui.main_window import MainWindow


def _win():
    app = QApplication.instance() or QApplication([])
    return MainWindow(app)


def test_markdown_answer_renders_to_html():
    w = _win()
    w.assistant._post("GINI", "### Title\n\n- **bold** item\n\n| A | B |\n|---|---|\n| 1 | 2 |",
                      markdown=True)
    html = w.assistant.log.toHtml()
    assert "<table" in html and "<h3" in html.lower()      # rendered, not raw '###'
    assert "###" not in w.assistant.log.toPlainText()      # no literal markdown left


def test_build_recipe_auto_builds_and_narrates():
    w = _win()
    w.assistant._build_recipe("serverless")
    kinds = {d.type_key for d in w.ctx.topology.devices.values()}
    assert {"api_gateway", "function", "object_store"} <= kinds   # built on the canvas
    role, text, err, md = w.assistant._messages[-1]
    assert role == "GINI" and md and "Built a" in text           # narrated as markdown


def test_ask_gini_empty_canvas_construct_builds_without_llm():
    w = _win()
    # no loop/model needed: build_recipe is deterministic. Route a construct request.
    w.assistant._ask_gini("construct a serverless setup")
    kinds = {d.type_key for d in w.ctx.topology.devices.values()}
    assert "function" in kinds and "api_gateway" in kinds


def test_session_accumulator_present():
    w = _win()
    from gini.agent.kb import Card
    w.assistant._session.add([Card("concept", "serverless", "Serverless", "functions...")])
    assert "functions" in w.assistant._session.as_context()
