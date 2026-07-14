"""Move legality & relevance: supersets of the requirement are welcome (reasoning, not
pattern-matching), genuinely off-domain elements are flagged, grammar-illegal links and
`forbid:` violations are caught — all deterministically."""
from gini.domain import legality as LG, lesson as L, objectives as O
from gini.domain.topology import Topology


def _lan():
    return L.from_archetype("basic-lan", {}, id="lan")


def test_superset_is_welcome_off_domain_is_flagged():
    les = _lan()
    t = Topology()
    for _ in range(3):
        t.add_device("host")                     # more than the 2 required
    t.add_device("switch"); t.add_device("switch"); t.add_device("router"); t.add_device("firewall")
    assert LG.off_task_devices(les, t) == []      # all networking → in-family, no flags

    k = t.add_device("k8s_cluster"); fn = t.add_device("function")
    off = set(LG.off_task_devices(les, t))
    assert off == {k.id, fn.id}                   # only the genuinely off-domain elements


def test_containers_are_never_off_task():
    les = _lan()
    t = Topology(); vpc = t.add_device("vpc")
    assert vpc.id not in LG.off_task_devices(les, t)   # grouping boxes are scaffolding


def test_illegal_links_flag_grammar_violations():
    t = Topology()
    a = t.add_device("host"); b = t.add_device("host")
    t.add_link(a.id, b.id)                         # two hosts wired directly — grammar forbids
    assert len(LG.illegal_links(t)) == 1
    # a legal link (host↔switch) is not flagged
    t2 = Topology(); h = t2.add_device("host"); s = t2.add_device("switch"); t2.add_link(h.id, s.id)
    assert LG.illegal_links(t2) == []


def test_forbid_violation_detected_only_when_tripped():
    les = L.from_dict({"id": "x", "intent": {"concept": "vpc-networking", "spirit": "x"},
                       "objectives": [{"id": "o", "kind": "structural", "check": "exists(database)"}],
                       "forbid": [{"say": "DB must not touch the Internet",
                                   "check": "link(database, cloud)"}]})
    t = Topology(); db = t.add_device("database"); net = t.add_device("cloud")
    assert LG.forbid_violations(les, O.TopologyWorld(t)) == []      # not wired yet
    t.add_link(db.id, net.id)
    hit = LG.forbid_violations(les, O.TopologyWorld(t))
    assert len(hit) == 1 and "Internet" in hit[0].say


def test_flags_bundle():
    les = L.from_dict({"id": "x", "intent": {"concept": "networking-basics", "spirit": "lan"},
                       "objectives": [{"id": "o", "kind": "structural", "check": "exists(switch)"}],
                       "forbid": []})
    t = Topology(); t.add_device("switch"); k = t.add_device("k8s_cluster")
    f = LG.flags(les, t)
    assert k.id in f["devices"] and f["links"] == [] and f["forbid"] == []
