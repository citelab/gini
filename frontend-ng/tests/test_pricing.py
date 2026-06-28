"""GINI $ cost model + the dashboard's live meter."""
import time

from gini.domain.pricing import (
    DEFAULT_RATES, bill, category_of, rate_of,
)
from gini.domain.topology import Topology


def test_rates_rank_by_cloud_value():
    # a managed database costs more than a VM, which costs more than a switch
    assert DEFAULT_RATES["database"] > DEFAULT_RATES["instance"] > DEFAULT_RATES["switch"]
    assert category_of("instance") == "Compute"
    assert category_of("router") == "Networking"
    assert category_of("database") == "Services"
    assert category_of("dashboard") == "Observability"
    assert category_of("vpc") is None          # grouping elements are not billable


def test_bill_sums_rate_and_groups_by_category():
    t = Topology("net")
    t.add_device("host"); t.add_device("host")     # 2 × 2.0 = 4 compute
    t.add_device("router")                          # 3.0 networking
    t.add_device("database")                        # 12.0 services
    b = bill(t)
    assert b["count"] == 4
    assert b["rate_per_hr"] == 4.0 + 3.0 + 12.0
    assert b["by_category"]["Compute"] == {"rate": 4.0, "count": 2}
    assert b["by_category"]["Services"]["rate"] == 12.0
    assert "Observability" not in b["by_category"]   # empty categories are dropped


def test_k8s_topology_is_billed():
    # regression: K8s elements used to fall outside the rate table and bill $0
    t = Topology("k8s")
    t.add_device("k8s_cluster")        # 8.0
    t.add_device("instance_group")     # HPA — 2.0
    t.add_device("pod")                # 5.0 × 2 default replicas = 10.0
    b = bill(t)
    assert b["by_category"]["Kubernetes"] == {"rate": 8.0 + 2.0 + 10.0, "count": 3}
    assert b["rate_per_hr"] == 20.0


def test_pod_bills_per_replica():
    t = Topology("k8s")
    pod = t.add_device("pod")
    pod.properties["Replicas"] = "5"
    assert bill(t)["rate_per_hr"] == 5.0 * 5      # scaling the workload costs more
    pod.properties["Replicas"] = "1"
    assert bill(t)["rate_per_hr"] == 5.0


def test_serverless_topology_is_billed():
    # regression: the API Gateway had no rate and billed $0
    t = Topology("fn")
    t.add_device("function")           # 1.0 — cheap, scale-to-zero
    t.add_device("api_gateway")        # 5.0
    b = bill(t)
    assert b["by_category"]["Serverless"] == {"rate": 6.0, "count": 2}
    assert category_of("function") == "Serverless"
    assert category_of("api_gateway") == "Serverless"


def test_every_element_is_priced_or_explicitly_free():
    """Guard: every element in the domain must be either billable or in the deliberate
    FREE list. A new element that's neither would bill $0 and make the meter under-count
    (the bug that hit K8s + serverless). This keeps pricing in sync with the palette."""
    from gini.domain.devices import REGISTRY
    from gini.domain.pricing import BILLABLE, FREE
    missing = sorted(k for k in REGISTRY if k not in BILLABLE and k not in FREE)
    assert not missing, f"unpriced elements — add to a category or to FREE: {missing}"


def test_price_overrides_from_settings():
    t = Topology("net"); t.add_device("database")
    assert rate_of("database", {"database": 99}) == 99.0
    assert bill(t, {"database": 99})["rate_per_hr"] == 99.0
    # blank/invalid override falls back to default
    assert rate_of("database", {"database": "oops"}) == DEFAULT_RATES["database"]


def test_grouping_and_unknown_elements_are_free():
    t = Topology("net")
    t.add_device("vpc"); t.add_device("region")    # boundaries, not rented
    assert bill(t)["rate_per_hr"] == 0.0
    assert bill(t)["count"] == 0


def test_dashboard_accrues_then_freezes(qtbot=None):
    from PySide6.QtWidgets import QApplication
    from gini.ui.theme.manager import ThemeManager
    app = QApplication.instance() or QApplication([])
    from gini.ui.dashboard import Dashboard
    tm = ThemeManager(app, "Dark")
    d = Dashboard(tm)
    d.set_estimate(bill(_two_hosts()))
    assert d._rate == 4.0 and d._count == 2
    d.start(3600.0)                  # 3600 GINI$/hr == 1 GINI$/sec
    d._start = time.monotonic() - 2.0   # pretend 2s elapsed
    d._on_tick()
    assert d._accrued >= 1.9         # ~2 GINI $ accrued
    d.stop()                         # freezes the final session bill
    frozen = d._accrued
    time.sleep(0.05)
    d._on_tick()                     # ticking after stop must not change the frozen bill
    assert d._accrued == frozen
    assert not d._tick.isActive()


def _two_hosts():
    t = Topology("net"); t.add_device("host"); t.add_device("host")
    return t
