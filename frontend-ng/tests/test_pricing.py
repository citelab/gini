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
