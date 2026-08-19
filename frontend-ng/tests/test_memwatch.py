"""MemWatch — the fleet memory watchdog behind the Dashboard's MEMORY gauge.

Born from a real failure: a wedged control socket leaked ~15 MiB per poll into a
container for 20 minutes until the Docker VM's OOM killer swept the lab (exit 137,
no warning). The leak had a clean linear slope; the watchdog's job is to name the
container while there's still time to act.
"""
from gini.domain.memwatch import MemWatch, estimate_need_mib


def _feed(w, svc, points):
    """points: [(t, mib)] — one service, ingested one snapshot at a time."""
    for t, mib in points:
        w.ingest({svc: {"mem_used": mib}}, t)


def test_flat_memory_is_not_a_runaway():
    w = MemWatch()
    _feed(w, "r3", [(i * 5.0, 40.0) for i in range(60)])       # 5 min flat at 40 MiB
    assert w.runaways() == []
    assert w.total_mib() == 40.0


def test_linear_leak_is_flagged_with_slope():
    w = MemWatch()
    # the real incident, scaled: +15 MiB every 15 s  (~60 MiB/min)
    _feed(w, "r3", [(i * 15.0, 50.0 + 15.0 * i) for i in range(20)])   # ~5 min
    runs = w.runaways()
    assert len(runs) == 1 and runs[0].svc == "r3"
    assert 50.0 <= runs[0].slope_mib_per_min <= 70.0
    assert runs[0].growth_mib > MemWatch.MIN_GROWTH_MIB


def test_short_history_is_never_accused():
    w = MemWatch()
    _feed(w, "m1", [(0.0, 10.0), (30.0, 200.0), (60.0, 500.0)])  # steep but < MIN_SPAN_S
    assert w.runaways() == []


def test_spike_without_sustained_growth_not_flagged():
    w = MemWatch()
    # iperf-style burst: up then back down — net growth ~0, slope small
    pts = [(i * 10.0, 60.0) for i in range(12)]
    pts += [(120.0 + i * 10.0, 60.0 + (200.0 if i == 2 else 0.0)) for i in range(12)]
    _feed(w, "m2", pts)
    assert w.runaways() == []


def test_vanished_service_is_forgotten_and_total_sums():
    w = MemWatch()
    w.ingest({"a": {"mem_used": 100.0}, "b": {"mem_used": 50.0}}, 0.0)
    assert w.total_mib() == 150.0
    w.ingest({"a": {"mem_used": 110.0}}, 5.0)                   # b's container stopped
    assert "b" not in w.series
    assert w.total_mib() == 110.0


def test_window_ring_trims_old_samples():
    w = MemWatch()
    _feed(w, "a", [(i * 10.0, 30.0) for i in range(100)])       # 1000 s of samples
    s = w.series["a"]
    assert s.t[0] >= s.t[-1] - MemWatch.WINDOW_S


def test_estimate_need_scales_with_containers():
    assert estimate_need_mib(1) < estimate_need_mib(10) < estimate_need_mib(30)
    assert estimate_need_mib(10) > 2800.0                        # 10 containers need > 2.8 GB
