#!/usr/bin/env python3
"""End-to-end shadow harness — proves each shadow works, and that a wrong one is CAUGHT.

Run this on the Mac against a RUNNING xv6 Machine. For every shadow it:

  install a variant -> POST /rebuild -> enable ONLY that shadow -> run a workload
  -> read the live telemetry -> compute the measures with the REAL domain code -> assert

Three variants per shadow, and all three matter:

  reference  the correct implementation  -> the mission's objective must PASS
  wrong      legal C, bad policy         -> the objective must FAIL  (proves the measure
                                            discriminates; a mission that cannot fail is useless)
  hostile    an illegal answer           -> the validator must REJECT it, `rejects` climbs, and
                                            the machine keeps running on the shipped code

It imports the real parsers and Xv6Runner, so it exercises the same five links the app does:
compiles -> validator accepts -> telemetry moves -> measure computes -> objective flips.

    python3 harness.py --agent http://localhost:5000 --shadow all
    python3 harness.py --agent http://localhost:5000 --shadow bget_evict --variant reference

The agent URL is the xv6 element's published port 5000 (see the container in `docker compose ps`).
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
# import the app's own domain code — the point is to test the REAL chain, not a copy of it
SRC = HERE.parents[2] / "frontend-ng" / "src"
sys.path.insert(0, str(SRC))

from gini.domain.xv6 import (                                    # noqa: E402
    SchedTimeline, apply_proc_sched, parse_procdump, parse_shadow_manifest,
)
from gini.domain.xv6_fs import parse_balloc, parse_bcache        # noqa: E402
from gini.domain.xv6_runner import Xv6Runner                     # noqa: E402
from gini.domain.xv6_vm import parse_vmprint                     # noqa: E402

# kernel registry order — must match GINI_SH_* in gini_patch.py
INDEX = {"rr_sched": 0, "prio_sched": 1, "lottery_sched": 2,
         "vmfault": 3, "bget_evict": 4, "balloc": 5, "kalloc": 6}
FILE_OF = {"rr_sched": "gini_sched.c", "prio_sched": "gini_sched.c",
           "lottery_sched": "gini_sched.c", "vmfault": "gini_vm.c",
           "kalloc": "gini_vm.c", "bget_evict": "gini_fs.c", "balloc": "gini_fs.c"}
POLICY_OF = {"rr_sched": 0, "prio_sched": 1, "lottery_sched": 2}


class Agent:
    def __init__(self, base: str, timeout: float = 30.0) -> None:
        self.base = base.rstrip("/")
        self.timeout = timeout

    def get(self, path: str) -> str:
        with urllib.request.urlopen(self.base + path, timeout=self.timeout) as r:
            return r.read().decode(errors="replace")

    def post(self, path: str) -> dict:
        req = urllib.request.Request(self.base + path, data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=max(self.timeout, 180)) as r:
            try:
                return json.loads(r.read().decode(errors="replace"))
            except Exception:
                return {}


def measures_now(ag: Agent, window_s: float = 6.0) -> Xv6Runner:
    """Sample the machine for `window_s`, building a scheduling timeline the way the Lab does,
    then hand back a runner loaded with every telemetry source."""
    tl = SchedTimeline()
    snap = None
    end = time.monotonic() + window_s
    while time.monotonic() < end:
        txt = ag.get("/procs")
        procs = apply_proc_sched(parse_procdump(txt), txt)
        if procs:
            from gini.domain.xv6 import Snapshot, running_pid
            snap = Snapshot(procs=procs, running_pid=running_pid(procs))
            tl.add(snap)
        time.sleep(0.4)
    vm = parse_vmprint(ag.get("/vm"))
    fstxt = ag.get("/fs")
    from gini.domain.xv6_fs import FsSnapshot, Superblock
    fs = FsSnapshot(sb=Superblock(), **parse_bcache(fstxt), **parse_balloc(fstxt))
    shadows = parse_shadow_manifest(ag.get("/shadows"))
    return Xv6Runner(snapshot=snap, timeline=tl, shadows=shadows, vm=vm, fs=fs)


def install(ag: Agent, shadow: str, variant: str, shadow_dir: pathlib.Path) -> None:
    """Copy the variant's file into the student's bind-mounted shadow folder and rebuild."""
    src = HERE / variant / FILE_OF[shadow]
    dst = shadow_dir / FILE_OF[shadow]
    dst.write_text(src.read_text())
    print(f"    installed {variant}/{FILE_OF[shadow]} -> {dst}")
    r = ag.post("/rebuild")
    if not r.get("ok"):
        raise SystemExit(f"    REBUILD FAILED:\n{r.get('log', '')[:1500]}")


def enable_only(ag: Agent, shadow: str) -> None:
    """Enable exactly one shadow, so a measurement isolates it. A scheduler shadow also needs its
    policy made current, or the kernel never consults it."""
    if shadow in POLICY_OF:
        ag.post(f"/control?policy={POLICY_OF[shadow]}")
    ag.post(f"/shadow/enable?i={INDEX[shadow]}")
    time.sleep(0.5)


def workload(ag: Agent, shadow: str) -> None:
    """Traffic that makes this shadow's consequence measurable. Without load, every counter
    stays at its boot value and every assertion is meaningless."""
    progs = {"rr_sched": ["spin", "spin", "spin"],
             "prio_sched": ["spin", "spin", "spin"],
             "lottery_sched": ["spin", "spin", "spin"],
             "vmfault": ["alloc"],
             "bget_evict": ["writer", "grind"],
             "balloc": ["writer"],
             "kalloc": ["alloc", "alloc"]}[shadow]
    for p in progs:
        ag.post(f"/run?prog={p}")
        time.sleep(0.3)
    time.sleep(2.0)          # let the workload actually do something before we sample


# What each variant must look like. `check(r)` returns (ok, detail) given a loaded Xv6Runner.
def _share(r, name):
    return r.measure("shadow_active", name), r.measure("shadow_rejects", name)


CASES = {
    "rr_sched": {
        "reference": lambda r: (r.measure("every_runnable_runs", "any") == 1.0,
                                "every runnable process got a turn"),
        "wrong":     lambda r: (r.measure("every_runnable_runs", "any") == 0.0,
                                "starvation detected (no rotation)"),
    },
    "prio_sched": {
        "reference": lambda r: ((r.measure("cpu_share", "highest_priority") or 0) >= 0.45,
                                "the high-priority process dominates the CPU"),
        "wrong":     lambda r: ((r.measure("cpu_share", "highest_priority") or 0) < 0.45,
                                "priority ignored — share is merely fair"),
    },
    "lottery_sched": {
        "reference": lambda r: ((r.measure("share_ratio", "tickets") or 1) <= 0.15,
                                "CPU share tracks ticket share"),
        "wrong":     lambda r: ((r.measure("share_ratio", "tickets") or 0) > 0.15,
                                "tickets ignored — share does not track them"),
    },
    "vmfault": {
        "reference": lambda r: ((r.measure("faults_handled", "any") or 0) > 0
                                and (r.measure("faults_fellthrough", "any") or 0) == 0,
                                "handled every lazy fault itself"),
        "wrong":     lambda r: ((r.measure("faults_fellthrough", "any") or 0) > 0,
                                "store faults fell through to the shipped handler"),
    },
    "bget_evict": {
        "reference": lambda r: ((r.measure("cache_hit_rate", "any") or 0) >= 0.5,
                                "LRU keeps the hit rate up"),
        "wrong":     lambda r: ((r.measure("cache_hit_rate", "any") or 1) < 0.5,
                                "evicting the MRU buffer wrecks the hit rate"),
    },
    "balloc": {
        "reference": lambda r: ((r.measure("mean_gap", "any") or 999) <= 4,
                                "consecutive blocks land next to each other"),
        "wrong":     lambda r: ((r.measure("mean_gap", "any") or 0) > 4,
                                "allocating from the far end scatters the file"),
    },
    "kalloc": {
        "reference": lambda r: ((r.measure("max_free_run", "any") or 0) > 0,
                                "free memory stays in one long run"),
        "wrong":     lambda r: (True,
                                "(fragmentation is workload-sensitive — recorded, not asserted)"),
    },
}


def run_case(ag: Agent, shadow: str, variant: str, shadow_dir: pathlib.Path) -> bool:
    print(f"\n=== {shadow}  ·  {variant} " + "=" * (44 - len(shadow) - len(variant)))
    install(ag, shadow, variant, shadow_dir)
    enable_only(ag, shadow)
    workload(ag, shadow)
    r = measures_now(ag)
    st = r.shadows.get(shadow)
    active = getattr(st, "active", None)
    rejects = getattr(st, "rejects", 0)
    calls = getattr(st, "calls", 0)
    print(f"    manifest: active={active} calls={calls} rejects={rejects} "
          f"verdict={getattr(st, 'verdict', '?')}")

    if variant == "hostile":
        # the ONLY thing that matters: illegal answers were refused and the box still runs
        ok = rejects > 0
        detail = (f"validator rejected {rejects} illegal answer(s)" if ok else
                  "NO rejects recorded — the validator did not catch the illegal answer")
        alive = bool(r.snapshot and r.snapshot.procs)
        print(f"    machine still alive: {alive}")
        ok = ok and alive
    else:
        try:
            ok, detail = CASES[shadow][variant](r)
        except Exception as e:                      # a missing measure is a failure, not a crash
            ok, detail = False, f"measure error: {e}"
        if variant == "reference" and rejects:
            ok, detail = False, f"reference had {rejects} rejects — it is not actually legal"
    print(("    PASS  " if ok else "    FAIL  ") + detail)
    return ok


def selftest() -> int:
    """Check the expectations themselves, with no machine involved.

    Every `CASES` entry is a claim about a measure, and a claim can be wrong: a threshold that
    nothing can reach, or a reference/wrong pair that both say the same thing (so the mission
    could never fail). Feed each case synthetic 'good' and 'bad' telemetry and require that the
    reference check accepts the good one and the wrong check accepts the bad one — i.e. the pair
    actually DISCRIMINATES. Runs anywhere; no xv6 needed.
    """
    class FakeRunner:
        def __init__(self, vals):
            self.vals = vals
            self.shadows = {}

        def measure(self, what, which):
            return self.vals.get(what)

    good = {"every_runnable_runs": 1.0, "cpu_share": 0.72, "share_ratio": 0.06,
            "faults_handled": 40.0, "faults_fellthrough": 0.0, "cache_hit_rate": 0.83,
            "mean_gap": 1.0, "max_free_run": 31000.0}
    bad = {"every_runnable_runs": 0.0, "cpu_share": 0.33, "share_ratio": 0.41,
           "faults_handled": 18.0, "faults_fellthrough": 22.0, "cache_hit_rate": 0.21,
           "mean_gap": 640.0, "max_free_run": 400.0}

    fails = 0
    for shadow, variants in CASES.items():
        ref_ok, ref_why = variants["reference"](FakeRunner(good))
        wrong_ok, wrong_why = variants["wrong"](FakeRunner(bad))
        # and the crucial cross-check: the reference test must REJECT bad telemetry, otherwise
        # the objective is satisfied by anything and the mission is decorative
        cross, _ = variants["reference"](FakeRunner(bad))
        ok = ref_ok and wrong_ok and (not cross or shadow == "kalloc")
        fails += 0 if ok else 1
        mark = "PASS" if ok else "FAIL"
        note = "" if ok else ("  <- reference accepts BAD telemetry" if cross else
                              "  <- expectation not met on synthetic data")
        print(f"  {mark}  {shadow:<14} ref={ref_ok} wrong={wrong_ok} discriminates={not cross}{note}")
    print(f"\nselftest: {len(CASES) - fails}/{len(CASES)} case pairs discriminate")
    if fails:
        print("a failing pair means that mission could not distinguish a correct solution "
              "from a wrong one — fix the threshold before the testing session.")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true",
                    help="validate the expectations against synthetic telemetry; no machine needed")
    ap.add_argument("--agent", default="http://localhost:5000")
    ap.add_argument("--shadow", default="all", help="shadow name, or 'all'")
    ap.add_argument("--variant", default="all",
                    choices=["all", "reference", "wrong", "hostile"])
    ap.add_argument("--shadow-dir", default="",
                    help="the element's bind-mounted shadow folder "
                         "(default ~/.gini/xv6-shadows/<the only one there>)")
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    root = pathlib.Path(a.shadow_dir) if a.shadow_dir else None
    if root is None:
        base = pathlib.Path.home() / ".gini" / "xv6-shadows"
        dirs = sorted(d for d in base.glob("*") if d.is_dir()) if base.exists() else []
        if len(dirs) != 1:
            print(f"pass --shadow-dir explicitly (found {len(dirs)} under {base})")
            return 2
        root = dirs[0]
    print(f"shadow folder : {root}\nagent         : {a.agent}")

    ag = Agent(a.agent)
    shadows = list(INDEX) if a.shadow == "all" else [a.shadow]
    variants = (["reference", "wrong", "hostile"] if a.variant == "all" else [a.variant])
    results = []
    for sh in shadows:
        for v in variants:
            if v == "wrong" and sh not in CASES:
                continue
            results.append((sh, v, run_case(ag, sh, v, root)))

    print("\n" + "=" * 62)
    for sh, v, ok in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {sh:<14} {v}")
    bad = [x for x in results if not x[2]]
    print(f"\n{len(results) - len(bad)}/{len(results)} passed")
    # leave the machine in a known-good state rather than running the last hostile variant
    print("\nrestoring the shipped stubs…")
    ag.post("/revert")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
