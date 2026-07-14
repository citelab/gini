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


def test_faithful_mode_cuts_host_default_route_not_the_network():
    from gini.services.orchestrator import _compose
    t = Topology("net")
    h = t.add_device("host"); r = t.add_device("router")
    t.add_link(h.id, r.id)                       # a host only becomes a machine once linked
    cfg = RuntimeCompiler().compile(t)
    # the network is NEVER `internal` — that would block host access to web consoles
    on = _compose(cfg, auto_internet=True)
    off = _compose(cfg, auto_internet=False)
    assert "internal: true" not in on and "internal: true" not in off
    # faithful mode cuts the host's default route instead (per-host, in the shuttle)
    assert '"cut_default": true' in off
    assert '"cut_default": true' not in on


def test_published_ports_are_reachable_in_faithful_mode():
    from gini.services.orchestrator import _compose
    t = Topology("obs"); t.add_device("dashboard")     # Grafana publishes a web port
    compose = _compose(RuntimeCompiler().compile(t), auto_internet=False)
    assert "internal: true" not in compose             # host can reach localhost:<port>
    assert ":3000" in compose                          # Grafana's published port survives


def test_internet_element_is_a_nat_gateway_with_default_routes():
    t = Topology("net")
    m1 = t.add_device("host")
    r1 = t.add_device("router")
    net = t.add_device("cloud")              # the drawn Internet element
    t.add_link(m1.id, r1.id)
    t.add_link(r1.id, net.id)
    cfg = RuntimeCompiler().compile(t)

    # the Internet node compiles as a NAT-gateway machine (on the fabric), not a service
    assert net.name not in {s.name for s in cfg.services}
    gw = next(m for m in cfg.machines if m.name == net.name)
    assert gw.gateway and gw.gw is None and gw.fabric_gw      # NAT + return path via router
    # the ordinary host defaults INTO the fabric (egress via the drawn router)
    host = next(m for m in cfg.machines if m.name == m1.name)
    assert host.fabric_default and host.gw and not host.gateway
    # R1 gets a default route (0.0.0.0/0) toward the Internet node's fabric IP
    r = cfg.routers[0]
    defr = next(rt for rt in r.routes if rt["net"] == "0.0.0.0" and rt["mask"] == "0.0.0.0")
    assert defr["gw"] == gw.ifaces[0].ip.split("/")[0]


def test_internet_gateway_emits_wan_network_and_dualhomes():
    t = Topology("net")
    m1 = t.add_device("host"); r1 = t.add_device("router"); net = t.add_device("cloud")
    t.add_link(m1.id, r1.id); t.add_link(r1.id, net.id)
    cfg = RuntimeCompiler().compile(t)
    compose = _compose(cfg, auto_internet=False)
    assert "wan:" in compose and "192.168.244.0/24" in compose
    assert "networks: [gini, wan]" in compose     # the gateway is dual-homed
    assert "networks: [gini]" in compose          # ordinary hosts stay on gini only
    assert "internal: true" not in compose        # network is never isolated (consoles work)


def test_no_wan_network_without_an_internet_element():
    t = Topology("net"); t.add_device("host")
    assert "wan:" not in _compose(RuntimeCompiler().compile(t))


def test_machine_image_ships_the_gini_toolkit():
    from gini.services.orchestrator import _DOCKERFILE_MACHINE
    for tool in ("tcpdump", "tshark", "nmap", "traceroute", "iperf3", "hping3",
                 "dnsutils", "net-tools", "iptables"):
        assert tool in _DOCKERFILE_MACHINE, tool


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


def test_dashboard_has_always_on_pipeline_panels():
    # the board must never be blank: lead panels use Prometheus's own metrics, which
    # always have data even when cAdvisor is sparse (Docker Desktop)
    from gini.services.compiler import _grafana_dashboard_json
    import json
    dash = json.loads(_grafana_dashboard_json())
    exprs = [t["expr"] for p in dash["panels"] for t in p["targets"]]
    assert "up" in exprs                                 # scrape-target health (always data)
    assert "scrape_samples_scraped" in exprs             # samples per target (always data)
    # cAdvisor container panels still present (best-effort)
    assert any("container_cpu_usage_seconds_total" in e for e in exprs)


def test_lone_grafana_auto_adds_prometheus_and_home_dashboard():
    # a Dashboards element by itself must still come up showing graphs
    t = Topology("obs")
    g = t.add_device("dashboard")
    cfg = RuntimeCompiler().compile(t)
    names = {s.name for s in cfg.services}
    assert "Prometheus" in names and "cAdvisor" in names    # both auto-added
    graf = next(s for s in cfg.services if s.name == g.name)
    # home dashboard is set (so Grafana lands on it) AND its file is provisioned
    assert graf.env.get("GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH")
    assert "observability/grafana/container.json" in graf.files
    assert f"http://{_svc('Prometheus')}:9090" in graf.files["observability/grafana/ds.yml"]


def test_grafana_home_dashboard_only_set_when_provisioned():
    # with NO observability elements, a plain Grafana-less topology sets no home path
    from gini.services.cloud_catalog import CATALOG
    assert "GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH" not in CATALOG["dashboard"].env


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
    from gini.services.cloud_catalog import CATALOG
    compose = _compose(RuntimeCompiler().compile(t))
    # assert against the CATALOG, not a hardcoded tag — the tags are pinned and will be bumped,
    # and a test that has to be edited on every version bump is a test that teaches nothing
    assert f"image: {CATALOG['object_store'].image}" in compose
    assert f"image: {CATALOG['queue'].image}" in compose
    assert "networks: [gini]" in compose
    # a published console port mapping is present (host:container)
    assert ":9001" in compose and ":15672" in compose
