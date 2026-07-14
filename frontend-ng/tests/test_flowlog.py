"""Flow event log: diffing successive snapshots into install/expire events."""
from gini.domain.flowlog import FlowLog


class _F:
    """Minimal flow row (duck-typed like FlowEntry)."""
    def __init__(self, match, action, packets=0):
        self._m, self._a, self.packets = match, action, packets

    def match_summary(self):
        return self._m

    def action_summary(self):
        return self._a


def test_installs_detected_on_first_snapshot():
    log = FlowLog()
    ev = log.update([_F("10.0.1.10→10.0.1.12", "output:2"),
                     _F("10.0.1.12→10.0.1.10", "output:1")], now="00:00:01")
    assert {e.kind for e in ev} == {"installed"} and len(ev) == 2


def test_expiry_and_new_install_across_snapshots():
    log = FlowLog()
    log.update([_F("A→B", "output:2"), _F("B→A", "output:1")], now="00:00:01")
    ev = log.update([_F("B→A", "output:1", packets=88),      # still present
                     _F("A→C", "output:3")], now="00:00:04")  # new
    kinds = sorted((e.kind, e.match) for e in ev)
    assert ("expired", "A→B") in kinds        # A→B gone -> expired
    assert ("installed", "A→C") in kinds      # A→C new -> installed
    assert len(ev) == 2


def test_no_change_yields_no_events():
    log = FlowLog()
    log.update([_F("A→B", "output:2")], now="t1")
    assert log.update([_F("A→B", "output:2")], now="t2") == []


def test_history_accumulates_and_recent_is_newest_first():
    log = FlowLog()
    log.update([_F("A→B", "out:2")], now="t1")
    log.update([], now="t2")                  # A→B expired
    assert len(log.events) == 2
    assert log.recent()[0].kind == "expired"  # newest first
