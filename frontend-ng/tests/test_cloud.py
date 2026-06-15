"""Cloud course: managed services (MinIO, Postgres, …) as real containers."""
from gini.domain.topology import Topology
from gini.services.compiler import RuntimeCompiler, _role, _svc
from gini.services.orchestrator import _compose


def test_cloud_elements_are_services_not_machines():
    for key in ("object_store", "database", "queue", "load_balancer", "registry"):
        assert _role(key) == "service", key


def test_services_compile_to_real_images_with_unique_console_ports():
    t = Topology("cloud")
    s3 = t.add_device("object_store")
    db = t.add_device("database")
    cfg = RuntimeCompiler().compile(t)

    by_name = {s.name: s for s in cfg.services}
    assert s3.name in by_name and db.name in by_name
    assert "minio" in by_name[s3.name].image
    assert "postgres" in by_name[db.name].image
    assert by_name[db.name].env.get("POSTGRES_USER") == "gini"
    # every published port across all services gets a distinct host port
    hosts = [p["host"] for s in cfg.services for p in s.ports]
    assert len(hosts) == len(set(hosts))
    # MinIO exposes a web console
    assert any(p["web"] for p in by_name[s3.name].ports)


def test_service_link_is_intent_not_a_data_segment():
    t = Topology("cloud")
    inst = t.add_device("instance")
    s3 = t.add_device("object_store")
    t.add_link(inst.id, s3.id)               # "instance uses the bucket"
    cfg = RuntimeCompiler().compile(t)
    # the bucket runs as a service; the link is recorded as intent, not a tun segment
    assert any(s.name == s3.name for s in cfg.services)
    assert any("service link" in n for n in cfg.notes)
    assert cfg.subnets == {}                  # no data subnet was created for the link


def test_compute_instance_runs_as_a_bridge_container():
    t = Topology("cloud")
    inst = t.add_device("instance")
    db = t.add_device("database")
    t.add_link(inst.id, db.id)               # app instance uses the database
    cfg = RuntimeCompiler().compile(t)
    assert _role("instance") == "compute" and _role("host") == "machine"
    # the instance runs as a plain bridge container (not on the tun fabric)
    spec = next(s for s in cfg.services if s.name == inst.name)
    assert spec.image.startswith("ubuntu:")
    assert spec.command == ["tail", "-f", "/dev/null"]   # kept alive to log into
    assert inst.name not in {m.name for m in cfg.machines}
    # both the instance and the DB land on the same bridge network in the compose
    compose = _compose(cfg)
    assert f"\n  {_svc(inst.name)}:\n" in compose
    assert f"\n  {_svc(db.name)}:\n" in compose


def test_container_uses_its_image_and_command():
    t = Topology("cloud")
    c = t.add_device("container")
    c.properties["Image"] = "nginx:alpine"
    c.properties["Command"] = "nginx -g 'daemon off;'"
    cfg = RuntimeCompiler().compile(t)
    spec = next(s for s in cfg.services if s.name == c.name)
    assert spec.image == "nginx:alpine"
    assert spec.command == ["nginx", "-g", "daemon off;"]


def test_expanded_catalog_has_breadth():
    from gini.services.cloud_catalog import CATALOG
    for key in ("proxy", "web_app", "stream", "messaging", "cache", "nosql",
                "metrics", "dashboard", "tracing", "load_generator"):
        assert key in CATALOG, key
        assert _role(key) == "service"


def test_stream_advertises_its_own_service_name():
    t = Topology("cloud")
    s = t.add_device("stream")
    cfg = RuntimeCompiler().compile(t)
    spec = next(x for x in cfg.services if x.name == s.name)
    assert "redpanda" in spec.image
    # the {svc} placeholder is filled with the actual compose service name
    assert _svc(s.name) in spec.command
    assert "{svc}" not in " ".join(spec.command)


def test_observability_services_expose_web_consoles():
    t = Topology("obs")
    for k in ("metrics", "dashboard", "tracing"):
        t.add_device(k)
    cfg = RuntimeCompiler().compile(t)
    assert all(any(p["web"] for p in s.ports) for s in cfg.services)
    compose = _compose(cfg)
    assert "prom/prometheus" in compose and "grafana/grafana" in compose
    assert "jaegertracing/all-in-one" in compose


def test_observability_autowires_cadvisor_prometheus_grafana():
    t = Topology("obs")
    m = t.add_device("metrics")
    g = t.add_device("dashboard")
    cfg = RuntimeCompiler().compile(t)
    names = {s.name for s in cfg.services}
    assert "cAdvisor" in names                       # sidecar auto-added
    prom = next(s for s in cfg.services if s.name == m.name)
    assert "observability/prometheus.yml" in prom.files
    assert any("prometheus.yml" in v for v in prom.volumes)
    assert "cadvisor:8080" in prom.files["observability/prometheus.yml"]
    graf = next(s for s in cfg.services if s.name == g.name)
    assert any("container.json" in v for v in graf.volumes)
    # datasource points at the actual Prometheus service name
    assert f"http://{_svc(m.name)}:9090" in graf.files["observability/grafana/ds.yml"]


def test_observability_compose_and_files_written(tmp_path):
    from gini.services.orchestrator import write_project
    t = Topology("obs")
    t.add_device("metrics"); t.add_device("dashboard")
    cfg = RuntimeCompiler().compile(t)
    compose = _compose(cfg)
    assert "gcr.io/cadvisor/cadvisor" in compose
    assert "privileged: true" in compose
    assert "/var/run:/var/run:ro" in compose          # cAdvisor host mounts
    # the generated config files land in the project
    runtime_dir = __import__("gini.runtime", fromlist=["x"]).__path__[0]
    work = write_project(cfg, tmp_path, runtime_dir)
    assert (work / "observability" / "prometheus.yml").exists()
    assert (work / "observability" / "grafana" / "container.json").exists()
    import json
    json.loads((work / "observability" / "grafana" / "container.json").read_text())  # valid


def test_compose_emits_service_containers():
    t = Topology("cloud")
    t.add_device("object_store")
    t.add_device("queue")
    compose = _compose(RuntimeCompiler().compile(t))
    assert "image: minio/minio:latest" in compose
    assert "image: rabbitmq:3-management-alpine" in compose
    assert "networks: [gini]" in compose
    # a published console port mapping is present (host:container)
    assert ":9001" in compose and ":15672" in compose
