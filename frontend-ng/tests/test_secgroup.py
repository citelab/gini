"""Security Groups: a stateful default-deny firewall, realized as a per-member iptables
sidecar (shares the member's netns). Rules open ports from a CIDR or from another SG's
members — the classic web -> app -> db least-privilege."""
from gini.domain.topology import Topology
from gini.services.compiler import RuntimeCompiler, _parse_ingress, _role, _svc
from gini.services.orchestrator import _compose


def _fw(cfg, member):
    return next((f for f in cfg.firewalls if f["member"] == _svc(member)), None)


def test_sg_makes_member_default_deny_and_opens_the_listed_port():
    t = Topology("c")
    db = t.add_device("database")
    sg = t.add_device("security_group", name="db-sg")
    sg.properties["Ingress"] = "5432 from anywhere"
    t.add_link(sg.id, db.id)
    fw = _fw(RuntimeCompiler().compile(t), db.name)
    assert fw is not None
    s = fw["script"]
    assert "iptables -P INPUT DROP" in s                         # default-deny inbound
    assert "ESTABLISHED,RELATED" in s                            # stateful
    assert "--dport 5432 -s 0.0.0.0/0 -j ACCEPT" in s            # the one opened rule
    assert "getent hosts cloudfabric" in s                       # telemetry agent allowed


def test_sg_to_sg_rule_resolves_to_the_source_sg_members():
    # the db's SG allows 5432 only from the app SG -> the db firewall references the app box
    t = Topology("c")
    app = t.add_device("instance")
    db = t.add_device("database")
    app_sg = t.add_device("security_group", name="app-sg")
    t.add_link(app_sg.id, app.id)
    db_sg = t.add_device("security_group", name="db-sg")
    db_sg.properties["Ingress"] = "5432 from app-sg"
    t.add_link(db_sg.id, db.id)
    s = _fw(RuntimeCompiler().compile(t), db.name)["script"]
    assert f"getent hosts {_svc(app.name)}" in s and "--dport 5432" in s
    assert "5432 -s 0.0.0.0/0" not in s                          # NOT open to the world


def test_compose_emits_a_firewall_sidecar_in_the_member_netns():
    t = Topology("c")
    db = t.add_device("database")
    sg = t.add_device("security_group", name="db-sg")
    sg.properties["Ingress"] = "5432 from anywhere"
    t.add_link(sg.id, db.id)
    comp = _compose(RuntimeCompiler().compile(t))
    member = _svc(db.name)
    assert f"\n  {member}_fw:\n" in comp
    assert f'network_mode: "service:{member}"' in comp
    assert "cap_add: [NET_ADMIN]" in comp
    assert f"./sg/{member}.sh:/sg/run.sh" in comp


def test_security_group_emits_no_container():
    assert _role("security_group") == "secgroup"
    t = Topology("c"); t.add_device("security_group")
    cfg = RuntimeCompiler().compile(t)
    assert all(s.type_key != "security_group" for s in cfg.services)
    assert cfg.firewalls == []                                   # no members -> no firewall


def test_no_security_group_means_no_firewalls():
    t = Topology("c"); t.add_device("database")
    assert RuntimeCompiler().compile(t).firewalls == []


def test_parse_ingress_forms():
    rules = _parse_ingress("80 from anywhere; 5432 from app-sg\nfrom 10.0.0.0/8; 443",
                           {"app-sg": ["i1"]})
    assert rules[0] == {"port": 80, "cidrs": ["0.0.0.0/0"], "svcs": []}
    assert rules[1] == {"port": 5432, "cidrs": [], "svcs": ["i1"]}
    assert rules[2] == {"port": None, "cidrs": ["10.0.0.0/8"], "svcs": []}
    assert rules[3] == {"port": 443, "cidrs": ["0.0.0.0/0"], "svcs": []}
