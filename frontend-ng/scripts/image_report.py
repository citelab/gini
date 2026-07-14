#!/usr/bin/env python3
"""image_report — what GINI actually ships, and what it costs.

Two things worth separating, because they get conflated and they have different fixes:

  * IMAGE SIZE — hurts the FIRST run on a machine (build + pull), disk, and CI. On a warm Mac with
    the layers already cached it costs comparatively little per boot.
  * BOOT TIME  — what a student actually feels. Driven by container create + whatever the entrypoint
    does before it's useful (our shuttle, a database's initdb, k3s coming up), not by megabytes.

So this prints BOTH: the size of every image the topology uses, and the wall-clock time from
`docker compose up` to each service being up. Optimise what the numbers say, not what we assume.

    python3 scripts/image_report.py              # sizes of every GINI image present
    python3 scripts/image_report.py --boot       # …plus time a cold `up` of the running project
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time

# every image GINI can instantiate, and where it comes from
GINI_BUILT = {
    "gini-machine": "Host / Machine — python:3.12-slim + the full book toolkit (apt)",
    "gini-fabric": "the L2 fabric (all switches multiplexed) — python:3.12-slim",
    "gini-grouter": "Router / OVS — debian:bookworm-slim + zig + gcc (single-stage)",
    "gini-pox": "SDN controller — python:3.9-slim + POX",
    "gini-xv6": "Machine (xv6) — debian:bookworm-slim + riscv toolchain + qemu (single-stage)",
    "gini-faas": "serverless runtime — python:3.12-slim + pika/nats/kafka",
    "gini-cloudfabric": "telemetry agent — python:3.12-slim + psycopg2",
    "gini-sg": "security group — alpine:3.20 + iptables   ← the lean one",
}
PULLED = {
    "postgres:16-alpine": "Database", "redis:7-alpine": "Cache", "nginx:alpine": "Load Balancer",
    "nats:latest": "Messaging", "nginxdemos/hello:latest": "Web App", "registry:2": "Registry",
    "traefik:v3.1": "API Gateway / Proxy", "minio/minio:latest": "Object Store",
    "rabbitmq:3-management-alpine": "Queue", "mongo:7": "NoSQL",
    "redpandadata/redpanda:latest": "Stream", "prom/prometheus:latest": "Metrics",
    "grafana/grafana:latest": "Dashboard", "jaegertracing/all-in-one:latest": "Tracing",
    "fortio/fortio:latest": "Load Generator", "rancher/k3s:v1.30.6-k3s1": "K8s Cluster",
    "gcr.io/cadvisor/cadvisor:v0.49.1": "container metrics",
}


def sh(cmd, cwd=None):
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=60)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:                                       # noqa: BLE001
        return 127, str(e)


def sizes() -> dict[str, str]:
    code, out = sh(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}\t{{.Size}}"])
    if code != 0:
        return {}
    got = {}
    for line in out.splitlines():
        if "\t" not in line:
            continue
        name, size = line.split("\t", 1)
        got[name.strip()] = size.strip()
        got.setdefault(name.split(":")[0].strip(), size.strip())   # also match untagged lookups
    return got


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--boot", action="store_true", help="also time the running project's services")
    a = ap.parse_args()

    have = sizes()
    if not have:
        print("Docker isn't answering — is it running?")
        return 1

    def table(title, entries):
        print(f"\n\033[1m{title}\033[0m")
        missing = []
        for img, what in entries.items():
            size = have.get(img) or have.get(img.split(":")[0])
            if size is None:
                missing.append(img)
                continue
            print(f"  {size:>9}   {img:<34} {what}")
        for img in missing:
            print(f"  {'—':>9}   {img:<34} (not built/pulled here yet)")

    table("BUILT BY GINI (docker build, on your machine)", GINI_BUILT)
    table("PULLED FROM UPSTREAM", PULLED)

    print("\n\033[1mNotes\033[0m")
    print("  · gini-machine is instantiated ONCE PER HOST — it's both the fattest and the most")
    print("    replicated image, so it's where any optimisation pays off the most.")
    print("  · gini-grouter and gini-xv6 are SINGLE-STAGE: the compilers (zig/gcc, riscv toolchain,")
    print("    qemu build deps) are still inside the shipped image. A multi-stage build drops them.")
    print("  · ':latest' tags are not reproducible — two students can get different bytes.")

    if a.boot:
        code, out = sh(["docker", "compose", "ls", "--format", "json"])
        rows = json.loads(out or "[]") if code == 0 else []
        running = [r for r in rows if "running" in str(r.get("Status", "")).lower()]
        if not running:
            print("\n(no running lab to time — start one, then re-run with --boot)")
            return 0
        proj = running[0]["Name"]
        wd = (running[0].get("ConfigFiles") or "").split(",")[0].rsplit("/", 1)[0]
        print(f"\n\033[1mBOOT TIME — restarting '{proj}' to measure\033[0m")
        sh(["docker", "compose", "-p", proj, "down"], cwd=wd)
        t0 = time.time()
        code, _ = sh(["docker", "compose", "-p", proj, "up", "-d"], cwd=wd)
        dt = time.time() - t0
        print(f"  compose up returned in \033[1m{dt:.1f}s\033[0m (images already cached)")
        print("  → if this is fast but the FIRST run on a fresh Mac is slow, the cost is image")
        print("    build/pull, not boot: fix it with leaner images. If THIS is slow, the cost is")
        print("    container startup, and leaner images will not help much.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
