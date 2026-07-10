"""P5: the DomainPack seam — networking as the first pack, driving the blackboard through the pack
interface instead of hardcoded calls."""
from gini.agent import domains
from gini.agent.domains import DomainPack as Pack, NetworkingPack
from gini.agent.notifier import MissionMonitor
from gini.domain import assembly as A
from gini.domain.topology import Topology


def _lan():
    t = Topology()
    sw = t.add_device("switch", "S"); r = t.add_device("router", "R")
    for i in range(2):
        h = t.add_device("host", f"H{i}"); t.add_link(h.id, sw.id)
    t.add_link(sw.id, r.id)
    return t


def test_networking_pack_is_registered_and_conforms():
    pack = domains.get("networking")
    assert pack is not None
    assert isinstance(pack, Pack)                    # satisfies the DomainPack protocol
    assert "networking" in domains.names()


def test_pack_exposes_the_domain_pieces():
    pack = NetworkingPack()
    assert "through" in pack.predicates()            # our new chokepoint predicate is in the set
    assert pack.fragments()                          # composable content present
    assert pack.palette()                            # a buildable palette
    assert [o.id for o in pack.observers()] == ["topology"]


def test_monitor_runs_through_the_pack():
    les = A.assemble(["basic-lan"], genre="experience", lesson_id="t")
    m = MissionMonitor()
    m.load(les, pack=domains.get("networking"))      # verifiers come FROM the pack
    notes = m.on_world_change(_lan())
    assert m.bb.all_objectives_met()
    assert {"mission_complete", "objective_met"} & {n.change for n in notes}


def test_observer_snapshots_the_world():
    obs = domains.get("networking").observers()[0].observe(_lan())
    assert obs and obs[0].source == "topology" and obs[0].data
