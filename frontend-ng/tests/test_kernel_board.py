"""The kernel board: cumulative counters in, per-window rates out.

Most of these tests are about the one defect that makes a live view lie — rendering a since-boot
total under a "last 10 s" caption. The Mode lane shipped with exactly that bug, so it gets the
most coverage here.
"""
from gini.domain.kernel_board import (
    DEVICE_BLOCKS, DOORS, SUBSYSTEMS, Frame, Sample, Window, parse, signature,
)

# a dump as the kernel emits it: indices are GSUB_* values, all counters cumulative since boot
D1 = """BOARDN 14
BSUB 0 user 700
BSUB 1 trap 20
BSUB 2 syscall 10
BSUB 9 bcache 20
BSUB 10 disk 120
BEDGE 1 2 1000
BEDGE 2 5 300
BEDGE 5 7 260
BEDGE 7 9 600
BEDGE 9 10 12
BDOOR 1000 3 120
BUSER 210000 1123
"""
D2 = """BOARDN 14
BSUB 0 user 1400
BSUB 1 trap 40
BSUB 2 syscall 20
BSUB 9 bcache 40
BSUB 10 disk 240
BEDGE 1 2 2284
BEDGE 2 5 612
BEDGE 5 7 528
BEDGE 7 9 1201
BEDGE 9 10 24
BDOOR 2284 6 240
BUSER 420000 2246
"""


def test_parse_maps_indices_to_names():
    s = parse(D1)
    assert s.ok
    assert s.resid["user"] == 700 and s.resid["disk"] == 120
    assert s.edges[("bcache", "disk")] == 12
    assert s.doors == (1000, 3, 120)
    assert (s.user_kinstr, s.user_entries) == (210000, 1123)


def test_a_kernel_without_the_board_is_not_a_quiet_machine():
    """An older image answers /board with nothing. That must read as "no data", not as a board of
    zeros — which would look like a perfectly idle kernel and send a student hunting a ghost."""
    assert not parse("").ok
    assert not parse("some unrelated console noise\n").ok


def test_first_sample_renders_nothing():
    """The baseline sample holds since-boot totals. Drawing it under a per-window caption is the
    exact lie this module exists to prevent."""
    f = Window().add(parse(D1), 100.0)
    assert f.blocks == {} and f.resid == {} and f.doors == (0, 0, 0)
    assert f.instr_per_entry == 0.0


def test_second_sample_is_a_difference_not_a_total():
    w = Window()
    w.add(parse(D1), 100.0)
    f = w.add(parse(D2), 110.0)
    assert f.edges[("trap", "syscall")] == 1284          # 2284 - 1000, not 2284
    assert f.edges[("bcache", "disk")] == 12
    assert f.resid["user"] == 700
    assert f.doors == (1284, 3, 120)
    assert f.span_s == 10.0


def test_block_totals_are_the_calls_received():
    w = Window()
    w.add(parse(D1), 100.0)
    f = w.add(parse(D2), 110.0)
    assert f.blocks["syscall"] == 1284
    assert f.blocks["bcache"] == 601                     # 1201 - 600
    assert f.blocks["disk"] == 12
    assert "user" not in f.blocks                        # user is not a block you can call into


def test_frequency_and_cost_disagree_and_both_survive():
    """The whole reason the board draws calls and time in separate channels. If this test ever
    fails because someone shaded blocks by call count, the lesson is gone."""
    w = Window()
    w.add(parse(D1), 100.0)
    f = w.add(parse(D2), 110.0)
    assert f.blocks["bcache"] > 40 * f.blocks["disk"]    # asked ~50x more often
    assert f.share("disk") > 5 * f.share("bcache")       # yet holds far more time
    assert f.busiest == "user"                           # and most time is not in the kernel


def test_instructions_per_kernel_entry():
    w = Window()
    w.add(parse(D1), 100.0)
    f = w.add(parse(D2), 110.0)
    # 210,000 k-instructions over 1,123 entries
    assert round(f.instr_per_entry) == round(210000 * 1000 / 1123)
    assert f.instr_per_entry > 100_000                   # the no-kernel path dwarfs everything


def test_no_entries_is_zero_not_infinity():
    assert Frame(user_kinstr=5, user_entries=0).instr_per_entry == 0.0
    assert Frame().kernel_entries == 0


def test_reboot_does_not_produce_negative_rates():
    """Counters restart at zero on reboot. Subtracting the old baseline would paint a huge
    negative rate; the new value is taken as-is instead, which self-corrects next poll."""
    w = Window()
    w.add(parse(D2), 100.0)                              # high baseline
    f = w.add(parse(D1), 110.0)                          # ... then the kernel restarts
    assert all(v >= 0 for v in f.edges.values())
    assert all(v >= 0 for v in f.resid.values())
    assert all(d >= 0 for d in f.doors)
    assert f.user_kinstr >= 0 and f.instr_per_entry >= 0
    assert f.edges[("trap", "syscall")] == 1000          # took the post-reboot value


def test_share_is_a_fraction_and_safe_when_empty():
    f = Frame(resid={"user": 3, "disk": 1})
    assert f.share("user") == 0.75 and f.share("nope") == 0.0
    assert Frame().share("user") == 0.0                  # no divide-by-zero on an idle window


def test_quiet_machine_records_one_snapshot():
    """signature() drives HudHistory dedupe: unchanged counters must produce an identical
    signature so the scrub timeline's ticks each mean a real change."""
    w = Window()
    w.add(parse(D1), 100.0)
    a = w.add(parse(D2), 110.0)
    w2 = Window()
    w2.add(parse(D1), 100.0)
    b = w2.add(parse(D2), 110.0)
    assert signature(a) == signature(b)
    assert signature(a) != signature(Frame())


def test_wire_order_matches_the_kernel():
    """SUBSYSTEMS is the wire format: index IS the GSUB_* constant. Reordering it silently
    mislabels every block, so pin the ones the kernel hardcodes."""
    assert SUBSYSTEMS[0] == "user" and SUBSYSTEMS[1] == "trap"
    assert SUBSYSTEMS.index("bcache") == 9 and SUBSYSTEMS.index("disk") == 10
    assert len(SUBSYSTEMS) == 14
    assert DOORS == ("asked", "couldn't", "seized")
    assert set(DEVICE_BLOCKS) <= set(SUBSYSTEMS)


def test_window_reports_whether_the_kernel_supports_the_board():
    """The HUD asks this to decide between "quiet machine" and "rebuild your image" — two very
    different messages that a board of zeros cannot tell apart."""
    w = Window()
    assert w.board_supported and not w.has_baseline      # optimistic before any sample
    w.add(parse(D1), 100.0)
    assert w.board_supported and w.has_baseline
    old = Window()
    old.add(parse("boot messages, no board here\n"), 100.0)
    assert not old.board_supported


def test_hottest_edge_and_empty_safety():
    w = Window()
    w.add(parse(D1), 100.0)
    f = w.add(parse(D2), 110.0)
    assert f.hottest_edge()[0] == ("trap", "syscall")
    assert Frame().hottest_edge() == ((None, None), 0)
    assert Frame().busiest == ""
