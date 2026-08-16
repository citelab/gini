"""Process fingerprints — the pure feature/classifier/confusion core (no Qt)."""
from gini.domain.fingerprint import (
    CLASSES, FEATURE_AXES, FingerprintAccumulator, accuracy, classify, confusion_matrix,
    demo_features, fingerprint, scatter_xy, similarity, true_class,
)
from gini.domain.xv6 import Proc, SyscallEvent, TrapEvent


def test_fingerprint_axes_are_bounded_and_named():
    fp = fingerprint(demo_features()[0])
    assert set(fp) == set(FEATURE_AXES)
    assert all(0.0 <= v <= 1.0 for v in fp.values())


def test_classifier_matches_ground_truth_on_the_shipped_programs():
    # each shipped program's fingerprint classifies to its true class (the game's baseline)
    for f in demo_features():
        assert classify(fingerprint(f)) == true_class(f.name), f.name


def test_spin_is_cpu_bound_writer_is_io_bound():
    fps = {f.name: fingerprint(f) for f in demo_features()}
    assert fps["spin"]["cpu"] > 0.9 and fps["spin"]["io_wait"] < 0.1
    assert fps["writer"]["io_wait"] > 0.5 and fps["writer"]["syscalls"] > 0.3
    assert fps["alloc"]["faults"] > 0.3                    # page-fault heavy
    assert fps["forktest"]["forks"] > 0.5


def test_similarity_and_scatter_separate_the_classes():
    fps = {f.name: fingerprint(f) for f in demo_features()}
    # IO/kernel-heavy programs resemble each other more than a CPU-bound one
    assert similarity(fps["writer"], fps["grind"]) > similarity(fps["writer"], fps["spin"])
    # spin sits at the CPU-bound/compute corner; writer pulls toward IO
    assert scatter_xy(fps["spin"])[0] > scatter_xy(fps["writer"])[0]


def test_confusion_matrix_counts_and_accuracy():
    pairs = [("cpu-bound", "cpu-bound"), ("io-bound", "io-bound"),
             ("memory", "io-bound"), ("cpu-bound", "cpu-bound")]
    m = confusion_matrix(pairs)
    assert m[("cpu-bound", "cpu-bound")] == 2
    assert m[("memory", "io-bound")] == 1                  # the off-diagonal confusion
    assert m[("io-bound", "io-bound")] == 1
    assert round(accuracy(pairs), 2) == 0.75
    assert accuracy([]) == 0.0
    assert set(k[0] for k in m) == set(CLASSES)            # full matrix over the class set


def test_accumulator_builds_features_from_telemetry():
    acc = FingerprintAccumulator()
    running = Proc(pid=5, state="running", name="spin")
    sleeper = Proc(pid=7, state="sleeping", name="writer")
    for _ in range(4):                                     # spin runs, writer blocks
        acc.observe([running, sleeper],
                    sc_events=[SyscallEvent(7, 16), SyscallEvent(7, 1)],   # writer: write + fork
                    trap_events=[TrapEvent(5, 1)],                          # spin: a page fault
                    dt=1.0)
    fps = acc.fingerprints()
    assert fps[5]["cpu"] == 1.0 and fps[5]["faults"] > 0        # spin ran + faulted
    assert fps[7]["io_wait"] == 1.0 and fps[7]["forks"] > 0     # writer slept + forked
    assert acc.feats[7].syscalls == 8                           # 2 events × 4 polls


def test_accumulator_hides_procs_with_too_few_samples():
    acc = FingerprintAccumulator()
    acc.observe([Proc(pid=5, state="running", name="spin")], dt=1.0)   # 1 sample only
    assert acc.fingerprints(min_samples=3) == {}              # 'settling' until enough samples
