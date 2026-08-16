"""Case source for the process-classify game (instance #1 of the Diagnose engine).

Reuses the fingerprint domain: a Case's signature is the 5-axis fingerprint, its truth is the
oracle's GROUND_TRUTH class for the launched program, and its hint is the rule-classifier's own guess
(shown in practice mode)."""
from __future__ import annotations

from ..diagnose import GameSpec
from ..diagnose import Case
from ..fingerprint import CLASSES, classify, demo_features, fingerprint, true_class

PROCESS_ABBR = {"cpu-bound": "cpu", "io-bound": "io", "memory": "mem",
                "fork-heavy": "fork", "mixed": "mix"}

PROCESS_SPEC = GameSpec(
    id="process-classify",
    title="Classify the process",
    prompt="What is this process?",
    classes=CLASSES,
    abbrev=PROCESS_ABBR,
)


def _case(name: str, fp: dict, cid: str) -> Case | None:
    t = true_class(name)
    if not t:
        return None
    return Case(id=cid, signature=fp, truth=t, subtitle=name, hint=classify(fp))


def demo_cases() -> list:
    """Canned deck from the shipped programs — always available (offline fallback)."""
    out = []
    for f in demo_features():
        c = _case(f.name, fingerprint(f), f"proc-{f.name}")
        if c is not None:
            out.append(c)
    return out


def live_cases(fingerprints_by_pid: dict, names: dict) -> list:
    """Cases from live fingerprints ({pid: fp}) whose program has a known ground-truth class."""
    out = []
    for pid, fp in fingerprints_by_pid.items():
        c = _case(names.get(pid, ""), fp, f"proc-{pid}")
        if c is not None:
            out.append(c)
    return out
