"""The GINI server (broker): auth, policy enforcement, per-student namespacing, and the
run flow — students send a *topology*, never a Docker command. No Qt, no Docker."""
from gini.domain.topology import Topology
from gini.server import GiniServer, SessionManager, Tokens, UserStore
from gini.server.auth import hash_password
from gini.server.policy import PolicyError, default_allowed_images, enforce
from gini.services.compiler import RuntimeConfig, ServiceSpec


# --- auth -------------------------------------------------------------------- #
def test_password_hash_and_verify():
    store = UserStore({"jane": hash_password("s3cret")})
    assert store.verify("jane", "s3cret")
    assert not store.verify("jane", "wrong")
    assert not store.verify("ghost", "s3cret")


def test_tokens_roundtrip_expiry_and_tamper():
    t = Tokens(b"server-secret")
    tok = t.mint("jane")
    assert t.verify(tok) == "jane"
    assert t.verify(tok + "x") is None                 # tampered
    assert Tokens(b"other").verify(tok) is None        # wrong secret
    assert Tokens(b"server-secret", ttl=-1).mint("jane") and \
        Tokens(b"server-secret", ttl=-1).verify(Tokens(b"server-secret", ttl=-1).mint("j")) is None


# --- policy ------------------------------------------------------------------ #
def test_enforce_clamps_cpu_and_strips_published_ports():
    cfg = RuntimeConfig()
    cfg.services.append(ServiceSpec("c", "container", "alpine:latest", "",
                                    ports=[{"host": 38000, "container": 80}], cpus=8.0))
    enforce(cfg, {"alpine:latest"}, max_cpus=2.0)
    assert cfg.services[0].cpus == 2.0                 # clamped to the cap
    assert cfg.services[0].ports == []                 # never published to the host


def test_enforce_blocks_privileged_unknown_image_and_host_mounts():
    for kwargs, needle in (
        ({"privileged": True}, "privileged"),
        ({"image": "evil:latest"}, "image"),
        ({"volumes": ["/var/run/docker.sock:/x"]}, "bind mount"),
        ({"runtime": "sysbox"}, "runtime"),
    ):
        cfg = RuntimeConfig()
        cfg.services.append(ServiceSpec("x", "container",
                                        kwargs.pop("image", "alpine:latest"), "", **kwargs))
        try:
            enforce(cfg, {"alpine:latest"})
            assert False, f"expected PolicyError for {needle}"
        except PolicyError as e:
            assert needle in str(e).lower()


# --- the run flow (fake orchestrator) --------------------------------------- #
class FakeOrch:
    def __init__(self, project, workdir):
        self.project, self.workdir, self.calls = project, workdir, []

    def up(self, cfg, workdir):
        self.calls.append("up"); return True, "running"

    def down(self):
        self.calls.append("down"); return True, "stopped"

    def status(self):
        return {"k1": "running"}

    def stats_all(self):
        return {"k1": {"cpu": 1.0}}

    def startup_times(self):
        return {"k1": 1840.0}

    def runtime_available(self, name):
        return name == "kata"


def _server():
    created = {}

    def factory(project, workdir):
        created[project] = FakeOrch(project, workdir)
        return created[project]

    srv = GiniServer(UserStore({"jane": hash_password("pw")}), Tokens(b"k"),
                     SessionManager("/tmp/gini-labs"), factory)
    return srv, created


def _login(srv):
    st, body = srv.handle("POST", "/login", None, {"username": "jane", "password": "pw"})
    assert st == 200
    return body["token"]


def test_login_then_run_a_kata_topology():
    srv, created = _server()
    token = _login(srv)
    topo = Topology("vm"); topo.add_device("kinstance"); topo.add_device("database")
    st, body = srv.handle("POST", "/run", token, {"topology": topo.to_dict()})
    assert st == 200 and body["ok"]
    assert "gini-jane" in created                      # student's namespaced project ran
    assert "up" in created["gini-jane"].calls


def test_bad_login_and_missing_token_are_rejected():
    srv, _ = _server()
    assert srv.handle("POST", "/login", None, {"username": "jane", "password": "no"})[0] == 401
    assert srv.handle("POST", "/run", None, {"topology": {}})[0] == 401   # no token


def test_run_rejects_an_out_of_scope_topology():
    # a networking topology compiles to fabric machines/switches -> not allowed on this box
    srv = _server()[0]
    token = _login(srv)
    t = Topology("net")
    h, s = t.add_device("host"), t.add_device("switch")
    t.add_link(h.id, s.id)
    st, body = srv.handle("POST", "/run", token, {"topology": t.to_dict()})
    assert st == 400 and "only" in body["error"].lower()


def test_run_rejects_an_unknown_image():
    srv = _server()[0]
    token = _login(srv)
    t = Topology("vm")
    k = t.add_device("kinstance"); k.properties["Image"] = "evil:latest"
    st, body = srv.handle("POST", "/run", token, {"topology": t.to_dict()})
    assert st == 400 and "image" in body["error"].lower()


def test_metrics_and_stop_go_through_the_orchestrator():
    srv, created = _server()
    token = _login(srv)
    srv.handle("POST", "/run", token, {"topology": Topology("vm").to_dict()})  # creates session
    st, body = srv.handle("GET", "/metrics", token, {})
    assert st == 200 and "startup" in body and body["startup"]["k1"] == 1840.0
    assert srv.handle("POST", "/stop", token, {})[0] == 200
    assert "down" in created["gini-jane"].calls


def test_session_namespacing_is_per_user():
    sm = SessionManager("/labs")
    assert sm.project_name("S Jane!") == "gini-sjane"
    assert str(sm.workdir("S Jane!")).endswith("/labs/sjane")
    assert default_allowed_images() >= {"ubuntu:22.04", "alpine:latest"}
