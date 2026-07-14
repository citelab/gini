"""Serverless authoring: the Lambda-style handle(event, context) contract, the inspector
Invoke panel's runner script, and the Load Generator reaching functions."""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# --- runtime contract (exec the embedded faas runtime; no Qt needed) --------- #
def _runtime(funcs):
    from gini.services.orchestrator import _RUN_FAAS
    os.environ["FAAS_CONFIG"] = json.dumps({"functions": funcs})
    ns = {"__name__": "faastest"}
    exec(compile(_RUN_FAAS, "run_faas.py", "exec"), ns)
    return ns


def _http(body="", method="POST"):
    return {"method": method, "path": "/x", "query": {}, "headers": {},
            "body": body, "source": "http"}


def test_custom_handler_gets_event_and_context_and_statuscode():
    code = ("def handle(event, context):\n"
            "    return {'statusCode': 201, 'body': event['body'] + '@' + context.function_name}")
    ns = _runtime([{"name": "fn", "handler": "custom", "code": code}])
    status, out = ns["invoke"]("fn", _http("hi"))
    assert status == 201 and out == "hi@fn"          # {statusCode, body} drives the response


def test_one_arg_handler_is_tolerated():
    code = "def handle(event):\n    return {'n': len(event.get('body', ''))}"
    ns = _runtime([{"name": "fn", "handler": "custom", "code": code}])
    status, out = ns["invoke"]("fn", _http("abcd"))
    assert status == 200 and out == {"function": "fn", "result": {"n": 4}}


def test_http_call_is_not_metered_as_an_event():
    ns = _runtime([{"name": "e", "handler": "echo"}])
    ns["invoke"]("e", _http("x"))
    assert ns["STATS"]["e"]["events"] == 0           # only queue/stream/pubsub count as events


def test_invoke_runner_script_is_valid_python():
    from gini.ui.main_window import _FAAS_INVOKE
    compile(_FAAS_INVOKE, "invoke.py", "exec")        # runs inside the faas container


def test_invoke_runner_sends_the_body_on_any_method():
    # regression: the body was only sent on POST, so a GET silently dropped the input
    from gini.ui.main_window import _FAAS_INVOKE
    assert "if body else None" in _FAAS_INVOKE
    assert "method=='POST'" not in _FAAS_INVOKE


def test_first_call_pays_a_cold_start():
    import time
    ns = _runtime([{"name": "e", "handler": "echo"}])
    t = time.time(); ns["invoke"]("e", _http("x", "GET")); cold = time.time() - t
    t = time.time(); ns["invoke"]("e", _http("x", "GET")); warm = time.time() - t
    assert cold > warm + 0.1                          # cold start adds a visible init delay


# --- Load Generator actually targets functions / gateways -------------------- #
def _win():
    from PySide6.QtWidgets import QApplication
    from gini.ui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    return app, MainWindow(app)


def test_loadgen_hits_a_connected_function_directly():
    from gini.services.compiler import _svc
    app, w = _win()
    fn = w.api.add_device("function")["id"]
    lg = w.api.add_device("load_generator")["id"]
    w.ctx.topology.add_link(fn, lg)
    fn_name = w.ctx.topology.devices[fn].name
    assert w._loadgen_target(lg) == f"http://faas:8000/{_svc(fn_name)}"


def test_loadgen_hits_a_function_through_the_api_gateway():
    from gini.services.compiler import _svc
    app, w = _win()
    gw = w.api.add_device("api_gateway")["id"]
    fn = w.api.add_device("function")["id"]
    lg = w.api.add_device("load_generator")["id"]
    w.ctx.topology.add_link(gw, fn)          # gateway routes the function
    w.ctx.topology.add_link(gw, lg)          # load generator fires at the gateway
    gw_name = w.ctx.topology.devices[gw].name
    fn_name = w.ctx.topology.devices[fn].name
    assert w._loadgen_target(lg) == f"http://{_svc(gw_name)}/{_svc(fn_name)}"


def test_inspector_builds_the_invoke_panel_for_a_function():
    app, w = _win()
    fid = w.api.add_device("function")["id"]
    w.ctx.select(fid)
    app.processEvents()
    assert w.inspector._invoke_result is not None     # Invoke (Test) panel is present


def test_deploy_button_gated_and_emits_request():
    app, w = _win()
    fid = w.api.add_device("function")["id"]
    w.ctx.select(fid)
    app.processEvents()
    assert w.inspector._deploy_btn is not None
    assert not w.inspector._deploy_btn.isEnabled()    # not running -> greyed out
    w.inspector.set_live_running(True)
    assert w.inspector._deploy_btn.isEnabled()
    fired = []
    w.ctx.bus.function_deploy_requested.connect(lambda: fired.append(1))
    w.inspector._deploy_btn.click()
    assert fired == [1]                               # Deploy asks main_window to redeploy


def test_invoke_button_is_disabled_until_the_lab_is_running():
    app, w = _win()
    fid = w.api.add_device("function")["id"]
    w.ctx.select(fid)
    app.processEvents()
    assert w.inspector._invoke_btn is not None
    assert not w.inspector._invoke_btn.isEnabled()    # not running -> greyed out
    w.inspector.set_live_running(True)                # docker compose up succeeded
    assert w.inspector._invoke_btn.isEnabled()        # enables in place, no reselection
    w.inspector.set_live_running(False)               # stopped
    assert not w.inspector._invoke_btn.isEnabled()
