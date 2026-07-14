#!/usr/bin/env python3
"""probe_doctor — see exactly what a Mission's Run/Check sees.

The behavioral half of Missions (M2) is the one part that can't be proven without Docker, and when
it misbehaves the panel only says ✗ — which is useless for debugging, because ✗ could mean "the
network is genuinely broken" (correct!) or "the probe couldn't even reach the container" (a bug in
us). This tool separates those two, by driving the SAME `DockerProbeRunner` the app drives and
printing the raw `docker compose exec` command, exit code and output behind every verdict.

Run it with your lab UP (gBuilder → Run), from the repo:

    python3 scripts/probe_doctor.py                    # discover the lab, ping every pair
    python3 scripts/probe_doctor.py --backends LB1     # why is balances(...) failing?
    python3 scripts/probe_doctor.py --flow OVS1        # why is flow_installed(...) failing?
    python3 scripts/probe_doctor.py --reach M1 M3      # one specific pair
    python3 scripts/probe_doctor.py --probe 'reach(host -> host, all) == ok'   # a real objective

Stdlib only; no GINI import needed except the runner itself.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from gini.services.probe_runner import DockerProbeRunner      # noqa: E402

BOLD, DIM, RED, GRN, YEL, OFF = "\033[1m", "\033[2m", "\033[31m", "\033[32m", "\033[33m", "\033[0m"


def sh(cmd: list[str], cwd=None) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=30)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:                                    # noqa: BLE001
        return 127, str(e)


def find_lab() -> tuple[str | None, Path | None]:
    """Locate the running compose project (name, workdir) the way the app's orchestrator would."""
    code, out = sh(["docker", "compose", "ls", "--format", "json"])
    if code != 0:
        print(f"{RED}docker compose ls failed — is Docker running?{OFF}\n{out}")
        return None, None
    try:
        rows = json.loads(out or "[]")
    except json.JSONDecodeError:
        print(f"{RED}could not parse `docker compose ls` output:{OFF}\n{out}")
        return None, None
    running = [r for r in rows if "running" in str(r.get("Status", "")).lower()]
    if not running:
        print(f"{YEL}No running compose project. Deploy the topology in gBuilder first "
              f"(the ▶ Run button), then re-run this.{OFF}")
        return None, None
    if len(running) > 1:
        print(f"{YEL}Several labs are up: {[r['Name'] for r in running]} — using the first.{OFF}")
    r = running[0]
    cfg = (r.get("ConfigFiles") or "").split(",")[0]
    return r.get("Name"), Path(cfg).parent if cfg else None


class Orch:
    """The minimum the runner needs — mirrors services.orchestrator.Orchestrator's seam."""
    def __init__(self, project: str, workdir: Path) -> None:
        self.project, self.workdir = project, workdir
        self._dc = ["docker", "compose", "-p", project]

    def status(self) -> dict:
        code, out = sh([*self._dc, "ps", "--format", "json"], cwd=str(self.workdir))
        if code != 0:
            return {}
        svcs = {}
        for line in (out or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            svcs[d.get("Service") or d.get("Name", "")] = d.get("State", "?")
        return svcs


class LoudRunner(DockerProbeRunner):
    """Same runner the Mission uses — but it shows its work."""
    def _exec(self, service, argv):
        code, out = super()._exec(service, argv)
        shown = " ".join(argv if len(argv) < 4 else argv[:3] + ["…"])
        colour = GRN if code == 0 else RED
        print(f"    {DIM}$ docker compose exec {service} {shown}{OFF}")
        print(f"      {colour}exit={code}{OFF}  {DIM}{(out or '').strip()[:220]}{OFF}")
        return code, out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reach", nargs=2, metavar=("SRC", "DST"))
    ap.add_argument("--backends", metavar="LB")
    ap.add_argument("--flow", metavar="OVS")
    ap.add_argument("--probe", metavar="EXPR", help="a full probe string, type-resolved")
    ap.add_argument("--quiet", action="store_true", help="verdicts only, hide the raw commands")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="show the raw commands in the pairwise matrix too")
    a = ap.parse_args()

    project, workdir = find_lab()
    if not project:
        return 1
    orch = Orch(project, workdir or Path.cwd())
    targeted = bool(a.reach or a.backends or a.flow)
    loud = (targeted or a.verbose) and not a.quiet
    runner = (LoudRunner if loud else DockerProbeRunner)(orch)

    print(f"\n{BOLD}lab{OFF}      {project}")
    print(f"{BOLD}workdir{OFF}  {workdir}")
    svcs = orch.status()
    print(f"{BOLD}services{OFF} {', '.join(f'{k}={v}' for k, v in svcs.items()) or '(none)'}")
    print(f"{BOLD}runner available{OFF}: {GRN if runner.available() else RED}{runner.available()}{OFF}")
    if not runner.available():
        print(f"{RED}\n→ The app would mark every live objective PENDING (not failed). The stack "
              f"isn't reporting as running.{OFF}")
        return 1
    print()

    if a.backends:
        print(f"{BOLD}balances({a.backends}){OFF} — reading nginx upstreams, then probing each:")
        n = runner.backends(a.backends)
        print(f"  → {BOLD}{n}{OFF} live backend(s)"
              f"   {DIM}(0 usually means the upstream config wasn't found — look at the cat above){OFF}\n")

    if a.flow:
        print(f"{BOLD}flow_installed({a.flow}, any){OFF} — reading the live OpenFlow table:")
        ok = runner.flow(a.flow, "any")
        print(f"  → {(GRN + 'flows installed') if ok else (RED + 'NO flows')}{OFF}"
              f"   {DIM}(no flows right after boot is NORMAL — the controller installs them "
              f"reactively on the first packet. Ping between hosts, then re-check.){OFF}\n")

    if a.reach:
        s, d = a.reach
        print(f"{BOLD}reach({s} -> {d}){OFF}:")
        ok = runner.reach(s, d)
        print(f"  → {(GRN + 'ok') if ok else (RED + 'FAIL')}{OFF}\n")

    if a.probe:
        # type-resolved, exactly like a mission objective: needs the topology, which we don't have
        # here — so we resolve types by the compose service names' prefixes instead.
        print(f"{YEL}--probe resolves TYPES against the live topology, which only gBuilder has. "
              f"Use --reach/--backends/--flow here; use the app's Run/Check for the real thing.{OFF}")

    if not any((a.reach, a.backends, a.flow, a.probe)):
        names = [s for s in svcs if svcs[s].lower().startswith("running")]
        print(f"{BOLD}Pairwise reachability across every running service{OFF} "
              f"{DIM}(this is what reach(...) is built on){OFF}\n")
        width = max((len(n) for n in names), default=4) + 1
        print(" " * width + " ".join(n[:6].rjust(6) for n in names))
        for s in names:
            cells = []
            for d in names:
                if s == d:
                    cells.append("     ·")
                    continue
                ok = runner.reach(s, d)
                cells.append((GRN + "    ok" if ok else RED + "  FAIL") + OFF)
            print(s.ljust(width) + " ".join(cells))
        print(f"\n{DIM}A whole row of FAIL for one host = that host is mis-addressed or not on the "
              f"segment. FAIL everywhere (including between healthy hosts) = the probe itself can't "
              f"exec — that's our bug, not the network's.{OFF}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
