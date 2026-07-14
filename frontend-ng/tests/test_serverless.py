"""Serverless: Function runs in a shared faas runtime; API Gateway routes paths to it."""
from gini.domain import connection_rules as cr
from gini.domain.topology import Topology
from gini.services.compiler import RuntimeCompiler, _role, _svc
from gini.services.orchestrator import _DOCKERFILE_FAAS, _RUN_FAAS, _compose


def test_function_and_gateway_roles():
    assert _role("function") == "function"          # a handler in the shared runtime
    assert _role("api_gateway") == "service"        # a real Traefik container
    assert _role("function") != "machine"           # was the placeholder bug


def test_grammar_makes_function_a_real_node():
    assert cr.can_connect("api_gateway", "function")
    assert cr.can_connect("function", "database")
    assert cr.can_connect("function", "queue")
    assert cr.can_connect("load_generator", "api_gateway")
    assert cr.can_connect("metrics", "function")


def test_function_compiles_into_the_faas_runtime():
    t = Topology("s")
    f = t.add_device("function")
    f.properties.update({"Handler": "slow"})
    cfg = RuntimeCompiler().compile(t)
    assert len(cfg.faas) == 1
    fn = cfg.faas[0]
    assert fn["handler"] == "slow" and fn["name"] == _svc(f.name)
    # a Function is NOT a standalone service container
    assert all(s.type_key != "function" for s in cfg.services)


def test_api_gateway_routes_paths_to_connected_functions():
    t = Topology("s")
    g = t.add_device("api_gateway")
    f = t.add_device("function")
    t.add_link(g.id, f.id)
    cfg = RuntimeCompiler().compile(t)
    gw = next(s for s in cfg.services if s.type_key == "api_gateway")
    dyn = next(v for k, v in gw.files.items() if k.endswith("dynamic.yml"))
    fn = _svc(f.name)
    assert f"PathPrefix(`/{fn}`)" in dyn and "http://faas:8000" in dyn
    assert "--providers.file.directory=/etc/traefik/dynamic" in gw.command


def test_api_gateway_with_no_function_notes_it():
    t = Topology("s")
    t.add_device("api_gateway")
    cfg = RuntimeCompiler().compile(t)
    assert any("Function" in n for n in cfg.notes)


def test_compose_emits_one_faas_container_for_all_functions():
    t = Topology("s")
    t.add_device("function")
    t.add_device("function")                        # two functions -> still ONE runtime
    cfg = RuntimeCompiler().compile(t)
    comp = _compose(cfg)
    assert comp.count("\n  faas:\n") == 1
    assert "Dockerfile.faas" in comp and "FAAS_CONFIG" in comp


def test_no_faas_container_without_functions():
    t = Topology("s")
    t.add_device("web_app")
    cfg = RuntimeCompiler().compile(t)
    assert cfg.faas == []
    assert "  faas:" not in _compose(cfg)


def test_function_gets_an_event_trigger_from_a_connected_queue():
    t = Topology("s")
    f = t.add_device("function")
    q = t.add_device("queue")
    t.add_link(q.id, f.id)
    cfg = RuntimeCompiler().compile(t)
    trigs = cfg.faas[0]["triggers"]
    assert len(trigs) == 1
    assert trigs[0] == {"type": "queue", "host": _svc(q.name), "port": 5672}


def test_function_triggers_for_every_event_source():
    t = Topology("s")
    f = t.add_device("function")
    for tk in ("queue", "stream", "messaging"):
        t.add_link(t.add_device(tk).id, f.id)
    cfg = RuntimeCompiler().compile(t)
    by_type = {tr["type"]: tr["port"] for tr in cfg.faas[0]["triggers"]}
    assert by_type == {"queue": 5672, "stream": 9092, "messaging": 4222}


def test_function_with_no_event_source_has_no_triggers():
    t = Topology("s")
    t.add_link(t.add_device("api_gateway").id, t.add_device("function").id)
    cfg = RuntimeCompiler().compile(t)
    assert cfg.faas[0]["triggers"] == []     # an HTTP gateway is not an event trigger


def test_runtime_subscribes_and_clients_are_installed():
    # the runtime must define subscribers for all three sources and start them
    for token in ("def _sub_queue", "def _sub_stream", "def _sub_pubsub",
                  "start_triggers()"):
        assert token in _RUN_FAAS
    # an event message goes through the same metered invoke() as an HTTP call
    assert 'invoke(name, {"method": "EVENT"' in _RUN_FAAS
    # the image ships the broker clients
    for pkg in ("pika", "nats-py", "kafka-python-ng"):
        assert pkg in _DOCKERFILE_FAAS


def test_faas_runtime_script_is_valid_python():
    compile(_RUN_FAAS, "run_faas.py", "exec")       # the embedded runtime must parse


def test_redeploy_faas_recreates_only_the_faas_service(monkeypatch, tmp_path):
    # AWS-style 'Deploy': recreate just the faas container, leave the rest of the lab up
    from gini.services import orchestrator as o
    t = Topology("s"); t.add_device("function")
    cfg = RuntimeCompiler().compile(t)
    orch = o.Orchestrator(tmp_path)
    orch.workdir = tmp_path
    calls = []
    monkeypatch.setattr(o, "write_project", lambda *a, **k: tmp_path)
    monkeypatch.setattr(orch, "_compose", lambda *a: calls.append(a) or (True, "ok"))
    ok, _msg = orch.redeploy_faas(cfg)
    assert ok
    assert calls == [("up", "-d", "--no-deps", "--force-recreate", "--build", "faas")]


def test_redeploy_faas_requires_a_running_lab_and_functions():
    from gini.services import orchestrator as o
    orch = o.Orchestrator("/nowhere")
    cfg = RuntimeCompiler().compile(Topology("s"))   # no functions, not running
    orch.workdir = None
    assert orch.redeploy_faas(cfg)[0] is False        # lab not running
    orch.workdir = "/tmp"
    assert orch.redeploy_faas(cfg)[0] is False        # no Functions to deploy
