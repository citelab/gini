"""F3: the RouterProgram model + local packet trace (drives the Router Lab editor)."""
from gini.domain.router_modules import RouterProgram


def test_base_pipeline_and_add():
    p = RouterProgram()
    # base only: ingress, parse, route, rewrite, egress
    stages = p.stages()
    labels = [s.label for s in stages]
    assert labels == ["ingress", "Parse", "Route lookup", "Rewrite", "egress"]
    p.add("acl")
    p.add("nat")
    stages = p.stages()
    inline = [s.label for s in stages if s.kind == "inline"]
    assert inline == ["ACL / Firewall", "NAT"]


def test_reorder_and_remove():
    p = RouterProgram()
    p.add("acl"); p.add("nat")
    p.move(0, 1)                      # ACL after NAT
    assert [m.type_key for m in p.inline] == ["nat", "acl"]
    p.remove(0)
    assert [m.type_key for m in p.inline] == ["acl"]


def test_openflow_mode_inserts_front_door():
    p = RouterProgram()
    p.set_mode("openflow")
    kinds = [s.kind for s in p.stages()]
    assert "mode" in kinds                       # flow-table front door at ingress
    of = next(s for s in p.stages() if s.kind == "mode")
    assert "OpenFlow" in of.label


def test_trace_drop_on_acl():
    p = RouterProgram()
    acl = p.add("acl")
    acl.params["deny"] = "10.0.2.0/24"
    verdicts = p.trace(dst="10.0.2.10")
    # the ACL stage should DROP and downstream stages are not reached
    assert any("DROP" in v for v in verdicts)
    assert verdicts[-1] == "—"                   # egress not reached


def test_trace_pass_through():
    p = RouterProgram()
    p.add("nat")
    verdicts = p.trace(dst="10.0.9.9")
    assert "sent ✓" in verdicts[-1]
