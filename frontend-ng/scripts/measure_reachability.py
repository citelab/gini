#!/usr/bin/env python3
"""Measure the assumptions the AOP's fast reachability strategy rests on.

Run this on a machine with Docker. It builds a multi-LAN topology headlessly (no Qt, no gBuilder),
brings it up, and answers three questions with numbers rather than estimates:

  1. What does one `docker compose exec` actually cost? Every projection in the design's §8.4 was
     arithmetic on an ESTIMATED 0.4s. If the real figure is 0.05s the batching win mostly
     evaporates; if it is 1.5s it matters more than claimed. Either way the table should not stay
     a guess.

  2. How much does a batched sweep really save? One exec running many pings inside the container,
     against one exec per ping.

  3. **Is reachability actually symmetric and transitive on a real GINI network?** This is the
     load-bearing one. The whole n(n-1) -> n-1 optimisation is sound only if reachability is an
     equivalence relation, and that is an empirical claim about gRouter forwarding, ARP, and
     routing symmetry — not something a unit test with a scripted runner can establish. A fake
     runner programmed to be transitive proves only that we are consistent with our own
     assumption.

Nothing here is mocked. It measures a real stack or it reports that it could not.

    python3 scripts/measure_reachability.py --lans 3 --hosts 3
    python3 scripts/measure_reachability.py --lans 5 --hosts 2 --keep

`--keep` leaves the topology running for poking at by hand. Otherwise it is always torn down,
including on failure — a half-up stack left behind is worse than no measurement.
"""
from __future__ import annotations

import argparse
import itertools
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from gini import runtime as _runtime                           # noqa: E402
from gini.domain import reach_strategy as _reach                # noqa: E402
from gini.domain.topology import Topology                      # noqa: E402
from gini.services.compiler import RuntimeCompiler             # noqa: E402
from gini.services.orchestrator import Orchestrator            # noqa: E402
from gini.services.probe_runner import DockerProbeRunner       # noqa: E402


# --------------------------------------------------------------------------- #
# the topology
# --------------------------------------------------------------------------- #
def build(lans: int, hosts_per_lan: int) -> tuple[Topology, list[str]]:
    """A chain of LANs joined by routers: [hosts]-S1-R1-S2-R2-S3-[hosts].

    A chain rather than a star so packets cross a varying number of routers — reachability that
    holds across one hop but not three would be invisible in a star, and that is exactly the shape
    of asymmetry that would break the transitivity assumption.

    **Names come from GINI's own auto-namer** (M1, S1, R1 …), never invented here. An earlier
    version named stations `M1_1` and none of them ever started: the name becomes a Docker Compose
    service key verbatim, and a shape the rest of the system never produces is a shape nothing was
    built or tested against. A measurement harness must exercise the ordinary path, not a novel
    one — otherwise it measures its own deviation.
    """
    t = Topology(name=f"reach-{lans}x{hosts_per_lan}")
    switches, host_names = [], []
    for i in range(lans):
        sw = t.add_device("switch", x=200.0 * i, y=200.0)
        switches.append(sw)
        for j in range(hosts_per_lan):
            h = t.add_device("host", x=200.0 * i, y=320.0 + 60 * j)
            t.add_link(h.id, sw.id)
            host_names.append(h.name)
    for i in range(lans - 1):
        r = t.add_device("router", x=200.0 * i + 100, y=100.0)
        t.add_link(switches[i].id, r.id)
        t.add_link(r.id, switches[i + 1].id)
    return t, host_names


def partition(t: Topology) -> str:
    """Cut the last router out of the chain, isolating the final LAN.

    A healthy network proves only that the strategy works where it is easy. This is the case the
    report has to get RIGHT: some hosts reachable, some not. Spanning must conclude "not all", and
    the partition it observes must name the isolated LAN rather than shrugging.
    """
    routers = sorted((d for d in t.devices.values() if d.type_key == "router"),
                     key=lambda d: d.name)
    victim = routers[-1]
    for lid in [l.id for l in t.links.values()
                if victim.id in (l.source_id, l.target_id)]:
        t.links.pop(lid, None)
    return victim.name


def block_at_endpoint(runner: DockerProbeRunner, victim: str, deny_cidr: str) -> str:
    """Make `victim` refuse traffic from `deny_cidr`, using iptables inside the station itself.

    Why not the in-path `vnf` firewall, which is the natural way to express this? Because splicing
    a VNF into a link re-segments it, and the compiler does not then give the routers a route to
    the network behind the VNF (see the `firewall()` note). The result is a total partition rather
    than a filter — it cannot produce a non-transitive relation, so it cannot test the thing we
    need tested.

    Filtering at the endpoint gets there with nothing in the path:

        M1 -> M4   permitted        M4 -> M7   permitted        M1 -> M7   DROPPED

    For deciding whether spanning is sound, only the SHAPE of the relation matters, not what
    produced it. A relation that is non-transitive is a counterexample however it arose. This is a
    weaker claim than "GINI's firewall breaks the assumption" and is labelled as such — it tests
    the strategy, not the firewall.
    """
    code, out = runner._exec(runner._service(victim),
                             ["iptables", "-A", "INPUT", "-s", deny_cidr, "-j", "DROP"])
    if code != 0:
        return f"FAILED to install the rule on {victim}: {' '.join(out.split())[:120]}"
    return f"{victim} drops INPUT from {deny_cidr}"


def firewall(t: Topology) -> str:
    """Splice a filtering VNF into the last LAN's uplink, so reachability stops being transitive.

    **The case that decides whether the optimisation is safe to ship.** Everything else measures
    speed; this measures soundness.

    The rule is chosen to build the counterexample deliberately rather than hope one appears. The
    firewall guards the LAST LAN and denies the FIRST LAN's subnet, which makes exactly the triangle
    the transitivity assumption forbids:

        M1 (LAN 1) -> M4 (LAN 2)   permitted
        M4 (LAN 2) -> M7 (LAN 3)   permitted
        M1 (LAN 1) -> M7 (LAN 3)   DENIED

    If spanning still reports "all reachable" here, it would hand a student a pass they did not
    earn. Being saved by which representative happened to be picked is not a guard.

    The element is `vnf` (`Kind: firewall`), NOT the `firewall` palette element — only `vnf` maps to
    the compiler's forwarding-function role. A `firewall` device here compiles to nothing at all,
    and the run would then "prove" that filtering does not break transitivity, which is the most
    dangerous wrong answer this harness could give. It is a device in the PATH, so the uplink is
    cut and the function spliced in between.
    """
    switches = sorted((d for d in t.devices.values() if d.type_key == "switch"),
                      key=lambda d: d.name)
    routers = sorted((d for d in t.devices.values() if d.type_key == "router"),
                     key=lambda d: d.name)
    last_sw, last_rt = switches[-1], routers[-1]

    for lid in [l.id for l in t.links.values()
                if {l.source_id, l.target_id} == {last_sw.id, last_rt.id}]:
        t.links.pop(lid)

    fw = t.add_device("vnf", x=last_sw.x - 60, y=140.0,
                      properties={"Kind": "firewall", "Rules": "deny 10.0.1.0/24"})
    t.add_link(last_sw.id, fw.id)
    t.add_link(fw.id, last_rt.id)
    return f"{fw.name} between {last_sw.name} and {last_rt.name}, denying 10.0.1.0/24"


# --------------------------------------------------------------------------- #
# measurements
# --------------------------------------------------------------------------- #
def addresses(config) -> dict:
    """name -> its address on the DRAWN network (gini0), from the compiled plan.

    Probing must target these, not Docker DNS names. A service name resolves to the container's
    `eth0`, which is the management bridge every container shares and which carries the fabric only
    in encapsulated form. Pinging it tests Docker, not the network the student built — two machines
    on deliberately isolated LANs would answer each other. Same trap the book warns about with
    `tcpdump -i gini0`.
    """
    out = {}
    for m in config.machines:
        for i in m.ifaces:
            out[m.name] = str(i.ip).split("/")[0]
            break
    return out


def _compose(orch, *args, timeout: float = 30.0) -> str:
    """A raw `docker compose` call in the project's workdir, for diagnostics."""
    try:
        r = subprocess.run([*orch._dc, *args], cwd=str(orch.workdir or "."),
                           capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"<could not run: {e}>"


def _why_not_running(runner: DockerProbeRunner, svc: str) -> None:
    """Show what compose thinks, when a service we expected is absent.

    `docker compose ps` lists only *running* services, so a container that started and died is
    simply invisible — which reads as "the service was never defined" and sends you looking in the
    wrong place. `ps -a` and the logs are what distinguish "never written into the compose file"
    from "started and crashed".
    """
    orch = runner.orch
    print("\n  diagnosis")
    print("  --- docker compose ps -a (includes exited) ---")
    print("  " + "\n  ".join(_compose(orch, "ps", "-a").strip().splitlines()[:14]))
    logs = _compose(orch, "logs", "--tail", "18", svc).strip()
    print(f"  --- last log lines for {svc} ---")
    print("  " + ("\n  ".join(logs.splitlines()[:18]) if logs else "(no logs — it never started)"))

    # Translate the failures we have actually hit into what to do about them. A log line naming a
    # missing module is only obvious once you already know the cause.
    if "No module named dataplane" in logs:
        print("\n  => the station image has an EMPTY dataplane package. `write_project` copies")
        print("     <runtime_dir>/*.py into the build context; if that directory has no modules")
        print("     the build still succeeds and every station exits(1) at startup.")
        print(f"     Pass --runtime-dir pointing at gini/runtime (default: "
              f"{Path(_runtime.__file__).parent}).")


def preflight(runner: DockerProbeRunner, ips: dict) -> bool:
    """Prove the measurement apparatus works before trusting a single number from it.

    Exists because the first run of this script reported "SOUND" over a relation in which nothing
    reached anything: every probe had failed, and a relation where nothing is reachable is
    trivially symmetric and transitive. A harness that cannot fail loudly is worse than no harness.
    """
    host = min(ips)
    svc = runner._service(host)
    running = sorted(runner.orch.status() or {})
    missing = [h for h in ips if runner._service(h) not in running]
    print("preflight")
    print(f"  services running : {running}")
    print(f"  stations expected: {sorted(ips)}")
    if missing:
        # Named explicitly: "the machines are absent" is a different problem from "the machines
        # are up but cannot talk", and the two send you to opposite ends of the codebase.
        print(f"  MISSING          : {missing}")
    print(f"  probing {host} (service {svc!r})")

    code, out = runner._exec(svc, ["sh", "-c", "echo alive"])
    print(f"  exec             : rc={code} out={out.strip()!r}")
    if code != 0 or "alive" not in out:
        print("  => cannot exec into the container. Nothing below would mean anything.")
        _why_not_running(runner, svc)
        return False

    code, out = runner._exec(svc, ["sh", "-c", "ip -4 addr show gini0 2>/dev/null || ifconfig gini0"])
    got = " ".join(out.split())[:120]
    print(f"  gini0            : rc={code} {got!r}")
    if code != 0:
        print("  => the station has no gini0. The fabric never came up.")
        return False

    peer = sorted(k for k in ips if k != host)[0]
    code, out = runner._exec(svc, ["ping", "-c", "1", "-W", "2", ips[peer]])
    print(f"  ping {peer} ({ips[peer]}) : rc={code} {' '.join(out.split())[:100]!r}")
    if code != 0:
        print(f"  => a station cannot reach its own LAN neighbour. Fix the topology before")
        print(f"     measuring anything about reachability.")
        return False
    print("  => apparatus works.\n")
    return True


def measure_exec_overhead(runner: DockerProbeRunner, host: str, n: int = 8) -> float:
    """Seconds per `docker compose exec`, measured with the cheapest possible payload.

    `true` does nothing, so what is left is process spawn, the compose lookup and the attach —
    the fixed cost every probe pays before it measures anything.
    """
    svc = runner._service(host)
    samples = []
    runner._exec(svc, ["true"])                       # warm: first exec pays extra
    for _ in range(n):
        t0 = time.perf_counter()
        runner._exec(svc, ["true"])
        samples.append(time.perf_counter() - t0)
    return statistics.median(samples)


def one_probe(runner: DockerProbeRunner, src: str, dst_ip: str) -> bool:
    """One ping, on the drawn network. Deliberately NOT `runner.reach()` — see `addresses`."""
    code, _ = runner._exec(runner._service(src), ["ping", "-c", "1", "-W", "1", dst_ip])
    return code == 0


def brute_force(runner: DockerProbeRunner, hosts: list[str], ips: dict) -> tuple[dict, float]:
    """Every ordered pair, one exec each — what today's `reach_all` costs. Full relation returned.

    Every pair is probed even after a failure: `all()` short-circuits in production, but a partial
    matrix cannot answer the transitivity question, which is the point of this run.
    """
    t0 = time.perf_counter()
    rel = {(a, b): one_probe(runner, a, ips[b]) for a, b in itertools.permutations(hosts, 2)}
    return rel, time.perf_counter() - t0


def batched_sweep(runner: DockerProbeRunner, src: str, dsts: list[str],
                  ips: dict) -> tuple[dict, float]:
    """One exec, many pings — the proposed replacement for n-1 execs.

    The shell loop echoes a line per destination so results are parsed rather than inferred from an
    exit code, and an unparsed destination stays `None` rather than becoming `False` — a sweep that
    silently produced nothing must not look like a network where nothing is reachable.

    POSIX `sh` and busybox `ping` only: the default station toolkit is Alpine, so anything needing
    bash or iputils would pass here and fail in a real lab.

    **Pings run in parallel.** Sequentially, a sweep's worst case is the SUM of the per-ping
    timeouts — 9 unreachable hosts at `-W 1` is 9s, past `DockerProbeRunner`'s 8s exec cap, so the
    exec is killed and the sweep returns nothing at all. That fails exactly when the network is
    broken, which is when the measurement matters most: the same backwards behaviour as `all()`
    short-circuiting, wearing different clothes. Backgrounding each ping makes the worst case the
    MAX timeout (~1s) regardless of host count.

    Output arrives in completion order rather than argument order, which is fine — results are
    parsed by name. Fairness invariant #5 is about *which* pairs get probed, not what order their
    answers come back in, and that set is unchanged.
    """
    svc = runner._service(src)
    pairs = " ".join(f"{d}={ips[d]}" for d in dsts)
    script = (f'for p in {pairs}; do n=${{p%%=*}}; a=${{p#*=}}; '
              f'( if ping -c 1 -W 1 "$a" >/dev/null 2>&1; then echo "OK $n"; '
              f'else echo "NO $n"; fi ) & done; wait')
    t0 = time.perf_counter()
    _code, out = runner._exec(svc, ["sh", "-c", script])
    elapsed = time.perf_counter() - t0

    seen = {}
    for line in (out or "").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] in ("OK", "NO"):
            seen[parts[1]] = (parts[0] == "OK")
    return ({d: seen.get(d) for d in dsts}, elapsed)


# --------------------------------------------------------------------------- #
# the claims
# --------------------------------------------------------------------------- #
def check_symmetry(rel: dict) -> list[tuple]:
    """Pairs where A reaches B but B does not reach A."""
    return sorted((a, b) for (a, b), ok in rel.items() if ok and not rel.get((b, a)))


def check_transitivity(rel: dict, hosts: list[str]) -> list[tuple]:
    """Triples where A->B and B->C hold but A->C does not — counterexamples to the assumption."""
    bad = []
    for a, b, c in itertools.permutations(hosts, 3):
        if rel.get((a, b)) and rel.get((b, c)) and not rel.get((a, c)):
            bad.append((a, b, c))
    return bad


def observed_islands(rel: dict, hosts: list[str]) -> list[list[str]]:
    """Group hosts by mutual reachability — the partition a report should show.

    Union-find over the pairs that reached BOTH ways. This is the structure a teacher can act on:
    "three stations unreachable" is a result, "everything on 10.0.3.x is unreachable" points at a
    router. Sorted throughout so the same relation always renders identically.
    """
    parent = {h: h for h in hosts}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for (a, b), ok in rel.items():
        if ok and rel.get((b, a)):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)

    groups: dict = {}
    for h in hosts:
        groups.setdefault(find(h), []).append(h)
    return sorted((sorted(g) for g in groups.values()), key=lambda g: g[0])


def _spanning_verdict(rel: dict, hosts: list[str], rep: str) -> bool:
    """What spanning from `rep` would conclude: does everyone reach everyone?"""
    reached = {h for h in hosts if h != rep and rel.get((rep, h)) and rel.get((h, rep))}
    return len(reached) == len(hosts) - 1


def spanning_would_agree(rel: dict, hosts: list[str]) -> tuple[bool, str]:
    """Would the proposed strategy have reached the same verdict as brute force?

    The differential check that matters. Spanning probes one representative against everyone else
    and infers the rest; here we compute what it would have concluded and compare against the
    relation actually observed. A disagreement means the optimisation would report something untrue.

    Checked for EVERY possible representative, not only the one production would pick. Testing the
    single deterministic choice can pass by luck: in a firewall-shaped relation where A->B and B->C
    hold but A->C does not, spanning from A correctly says "not all reachable" while spanning from
    B says "all reachable" and is wrong. Agreement for one representative says the chosen probe set
    happened to be adequate; agreement for all of them is evidence the relation really is an
    equivalence relation, which is the property being relied on.
    """
    observed_all = all(rel.values())
    disagreeing = [r for r in hosts if _spanning_verdict(rel, hosts, r) != observed_all]
    prod = min(hosts)                                  # deterministic: fairness invariant #5
    if not disagreeing:
        return True, (f"agree for ALL {len(hosts)} possible representatives "
                      f"(all say {'all reachable' if observed_all else 'not all reachable'})")
    lucky = "" if prod in disagreeing else (
        f" — note the production representative ({prod}) happens to agree, so this would have "
        f"passed a weaker check")
    return False, (f"DISAGREE for {len(disagreeing)}/{len(hosts)} representatives "
                   f"(e.g. {disagreeing[0]}); brute force says "
                   f"{'all' if observed_all else 'not all'} reachable{lucky}")


# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lans", type=int, default=3)
    ap.add_argument("--hosts", type=int, default=3, help="stations per LAN")
    ap.add_argument("--settle", type=float, default=15.0,
                    help="seconds to wait after `up` before probing")
    ap.add_argument("--scenario", choices=("healthy", "partitioned", "filtered", "vnf-firewall"),
                    default="healthy",
                    help="healthy: everything works (proves speed). partitioned: a router is cut "
                         "out (proves the report names WHICH hosts are unreachable). filtered: an "
                         "endpoint drops one subnet, making reachability NON-transitive (proves "
                         "the optimisation is not unsound). vnf-firewall: the in-path VNF version "
                         "— currently produces a partition, not a filter, because of the missing "
                         "route behind a forwarding VNF.")
    ap.add_argument("--keep", action="store_true", help="leave the topology running")
    ap.add_argument("--runtime-dir", default=str(Path(_runtime.__file__).parent),
                    help="directory holding the data-plane modules (shuttle.py, switch.py …)")
    args = ap.parse_args()

    # `write_project` copies `<runtime_dir>/*.py` into the image build context as the `dataplane`
    # package. Point it somewhere without those modules and the copy silently produces an EMPTY
    # package: the build succeeds, `up` reports success, and every station then dies on
    # "No module named dataplane.shuttle" — visible only in container logs. Refuse that here
    # rather than spend a lab boot discovering it. (This is how `gini.server` resolves it too.)
    rt = Path(args.runtime_dir)
    if not sorted(rt.glob("*.py")):
        print(f"FAILED: --runtime-dir {rt} contains no .py modules.")
        print("        Stations are built from these; an empty dataplane package makes every")
        print("        one of them exit(1) on startup.")
        return 1

    topo, hosts = build(args.lans, args.hosts)
    n = len(hosts)
    print(f"topology: {args.lans} LANs x {args.hosts} stations = {n} hosts, "
          f"{max(0, args.lans - 1)} routers")
    if args.scenario == "partitioned":
        print(f"scenario: PARTITIONED — cut {partition(topo)} out of the chain")
        print("          expect: not all reachable, and the report should name which LAN")
    elif args.scenario == "vnf-firewall":
        print(f"scenario: VNF-FIREWALL — {firewall(topo)}")
        print("          NOTE: known to partition rather than filter (no route behind the VNF).")
    elif args.scenario == "filtered":
        print("scenario: FILTERED — an endpoint will drop one LAN's traffic after boot")
        print("          expect: transitivity BROKEN. If spanning still says 'all reachable',")
        print("                  the optimisation is unsound and must not ship without a guard.")
    print(f"brute force would be {n * (n - 1)} probes; spanning would be {n - 1}\n")

    config = RuntimeCompiler().compile(topo)
    workdir = Path(tempfile.mkdtemp(prefix="gini-measure-"))
    orch = Orchestrator(args.runtime_dir, project=f"ginimeasure{int(time.time())}")

    print("bringing the topology up …")
    t0 = time.perf_counter()
    ok, msg = orch.up(config, workdir)
    if not ok:
        print(f"FAILED to start: {msg}")
        return 1
    print(f"up in {time.perf_counter() - t0:.1f}s; settling {args.settle:.0f}s "
          f"(routes, ARP)\n")
    time.sleep(args.settle)

    try:
        runner = DockerProbeRunner(orch)
        if not runner.available():
            print("FAILED: the stack reports nothing running")
            return 1

        ips = addresses(config)
        if not preflight(runner, ips):
            return 1

        if args.scenario == "filtered":
            # After preflight, so a failure to install the rule can never be confused with a
            # network that was broken to begin with.
            # First station on the LAST LAN. `hosts` is in build order (LAN by LAN), so index it
            # rather than sorting — sorting by name puts M10 before M2 and would silently pick the
            # wrong station on any topology with ten or more.
            victim = hosts[(args.lans - 1) * args.hosts]
            note = block_at_endpoint(runner, victim, "10.0.1.0/24")
            print(f"  filter installed : {note}\n")
            if note.startswith("FAILED"):
                return 1

        # 1 -- what an exec really costs ---------------------------------- #
        overhead = measure_exec_overhead(runner, hosts[0])
        print("=" * 72)
        print(f"1. docker compose exec overhead: {overhead * 1000:.0f} ms (median of 8)")
        print(f"   design 8.4 assumed 400 ms -> projections are "
              f"{'optimistic' if overhead > 0.4 else 'conservative'} "
              f"by {abs(overhead - 0.4) * 1000:.0f} ms per probe")

        # 2 -- brute force, in full --------------------------------------- #
        rel, brute_s = brute_force(runner, hosts, ips)
        reachable = sum(1 for v in rel.values() if v)
        print("\n" + "=" * 72)
        print(f"2. brute force: {len(rel)} probes in {brute_s:.1f}s "
              f"({brute_s / max(1, len(rel)) * 1000:.0f} ms each)")
        print(f"   {reachable}/{len(rel)} ordered pairs reachable")
        if reachable == 0:
            print("\n   STOP: nothing reached anything, so there is no relation to reason about.")
            print("   A wholly empty relation is trivially symmetric and transitive, and reporting")
            print("   it as evidence would be a false green. Fix the network, then re-run.")
            return 1

        # 3 -- batched sweep ---------------------------------------------- #
        rep = min(hosts)
        others = [h for h in hosts if h != rep]
        swept, sweep_s = batched_sweep(runner, rep, others, ips)
        unparsed = [d for d in others if swept.get(d) is None]
        got = sum(1 for v in swept.values() if v)
        print("\n" + "=" * 72)
        print(f"3. batched sweep from {rep}: {len(others)} pings in ONE exec, {sweep_s:.2f}s")
        if unparsed:
            print(f"   SWEEP FAILED: no result parsed for {', '.join(unparsed)}")
            print("   (that is the sweep producing nothing, NOT those hosts being unreachable)")
        else:
            print(f"   {got}/{len(others)} reachable")
            print(f"   speedup vs brute force: {brute_s / sweep_s:.0f}x"
                  if sweep_s else "   speedup: immeasurable")
            mismatches = [d for d in others if swept[d] != rel.get((rep, d))]
            print(f"   sweep agrees with per-pair probing: "
                  f"{'yes' if not mismatches else 'NO — ' + ', '.join(mismatches)}")

        # 3b -- the SHIPPING implementation, checked against brute force ---- #
        # The whole point of this harness: `probe_runner.sweep_all` is what production will use, so
        # it is diffed pair-by-pair against per-pair probing on the same live network. A speedup
        # that quietly disagrees is not a speedup.
        prod = DockerProbeRunner(orch, addresses=lambda: ips)
        t0 = time.perf_counter()
        relation = prod.sweep_all(hosts)
        prod_s = time.perf_counter() - t0
        execs, pair_count = _reach.probe_count(hosts)
        disagree = [(a, b) for (a, b), v in relation.pairs.items() if v is not rel.get((a, b))]
        print("\n" + "=" * 72)
        print(f"3b. probe_runner.sweep_all (the shipping path): {execs} execs, "
              f"{pair_count} pairs, {prod_s:.2f}s")
        print(f"    speedup vs per-pair probing: {brute_s / prod_s:.0f}x")
        print(f"    unreadable pairs: {len(relation.unknown)}")
        print(f"    agrees with per-pair probing on all {pair_count} pairs: "
              f"{'yes' if not disagree else 'NO — ' + str(disagree[:5])}")
        print(f"    {relation.summary()}")
        if disagree:
            print("    !! the fast path and the slow path disagree. The fast path is WRONG until")
            print("       this is explained — do not ship it.")

        # 4 -- the assumption --------------------------------------------- #
        asym = check_symmetry(rel)
        trans = check_transitivity(rel, hosts)
        agree, why = spanning_would_agree(rel, hosts)
        print("\n" + "=" * 72)
        print("4. IS REACHABILITY AN EQUIVALENCE RELATION HERE?")
        print(f"   symmetric  : {'yes' if not asym else 'NO'}"
              + ("" if not asym else f"  ({len(asym)} one-way pairs, e.g. {asym[0]})"))
        print(f"   transitive : {'yes' if not trans else 'NO'}"
              + ("" if not trans else f"  ({len(trans)} counterexamples, e.g. {trans[0]})"))
        print(f"   spanning vs brute force: {why}")
        # The observed islands — what a report would actually tell a teacher. "Three stations
        # unreachable" is a result; "everything on 10.0.3.x is unreachable" is a diagnosis.
        islands = observed_islands(rel, hosts)
        if len(islands) > 1:
            print("   observed islands:")
            for grp in islands:
                print(f"     {{{', '.join(grp)}}}  ({', '.join(ips[h] for h in grp)})")

        print()
        sound = (not asym) and (not trans) and agree
        expected_sound = args.scenario != "filtered"
        # Only a relation with BOTH reachable and unreachable pairs, or a fully reachable one,
        # carries information. Guarded above, but restated: soundness is a claim, and a claim needs
        # evidence rather than the absence of counterexamples in an empty set.
        if sound:
            print("   => the spanning strategy is SOUND on this topology.")
            print(f"      {n * (n - 1)} probes ({brute_s:.0f}s) collapse to 1 exec "
                  f"({sweep_s:.2f}s)"
                  + ("." if not unparsed else ", once the sweep itself is fixed."))
        else:
            print("   => the spanning strategy is NOT sound here. It must fall back to")
            print("      exhaustive probing whenever this shape is present.")

        if sound != expected_sound:
            print()
            if args.scenario == "filtered":
                print("   !! UNEXPECTED: a firewall did NOT break transitivity here. Either the")
                print("      filter never took effect (check it is actually dropping), or the")
                print("      denied subnet is not on any path being probed. Do not read this as")
                print("      evidence the optimisation is safe under filtering.")
            else:
                print("   !! UNEXPECTED: this scenario was meant to stay an equivalence relation.")
                print("      Investigate before trusting any spanning result.")
        print("=" * 72)
        return 0
    finally:
        if args.keep:
            print(f"\n--keep: topology left running in {workdir}")
        else:
            print("\ntearing down …")
            orch.down()


if __name__ == "__main__":
    sys.exit(main())
