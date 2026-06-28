"""gBuilder's RemoteClient talking to the in-process GiniServer — the full Phase-3
protocol, no sockets and no Docker (the orchestrator is faked)."""
from gini.domain.topology import Topology
from gini.server import GiniServer, SessionManager, Tokens, UserStore
from gini.server.auth import hash_password
from gini.services.remote import RemoteClient


class FakeOrch:
    def __init__(self, project, workdir):
        self.project, self.workdir, self.calls = project, workdir, []

    def up(self, cfg, wd):
        self.calls.append("up"); return True, "running"

    def down(self):
        self.calls.append("down"); return True, "stopped"

    def status(self):
        return {"k1": "running"}

    def stats_all(self):
        return {"k1": {"cpu": 2.0}}

    def startup_times(self):
        return {"k1": 1840.0}

    def runtime_available(self, name):
        return name == "kata"


def _client():
    created = {}

    def factory(project, workdir):
        created[project] = FakeOrch(project, workdir)
        return created[project]

    srv = GiniServer(UserStore({"jane": hash_password("pw")}), Tokens(b"k"),
                     SessionManager("/tmp/labs"), factory)
    client = RemoteClient(transport=lambda m, p, t, b: srv.handle(m, p, t, b))
    return client, created


def test_login_run_metrics_stop_round_trip():
    client, created = _client()
    assert client.login("jane", "pw") == (True, "")
    assert client.token
    t = Topology("vm"); t.add_device("kinstance")
    assert client.run(t) == (True, "running")
    assert "up" in created["gini-jane"].calls           # ran in the user's namespaced project
    assert client.metrics()["startup"]["k1"] == 1840.0
    assert client.kata_available() is True
    assert client.stop()[0] and "down" in created["gini-jane"].calls


def test_bad_login_and_unauthenticated_run_are_rejected():
    client, _ = _client()
    assert client.login("jane", "nope")[0] is False
    assert client.token is None
    assert client.run(Topology("vm"))[0] is False        # no token -> 401


def test_policy_rejection_surfaces_to_the_client():
    client, _ = _client()
    client.login("jane", "pw")
    t = Topology("net")
    h, s = t.add_device("host"), t.add_device("switch")
    t.add_link(h.id, s.id)                               # compiles to fabric machines -> denied
    ok, msg = client.run(t)
    assert ok is False and "only" in msg.lower()
