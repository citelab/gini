"""Fabric supervisor.

Launches the switch and each gRouter as their *own* processes (per the plan:
process-per-gRouter for independent control + crash isolation) and restarts any
gRouter that dies — demonstrating "restart on reconfig / crash" without taking the
rest of the fabric down. Configs come from env vars set by docker-compose.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

PY = sys.executable


def spawn(module: str, env_key: str) -> subprocess.Popen | None:
    if env_key not in os.environ:
        return None
    env = dict(os.environ)
    return subprocess.Popen([PY, "-m", module], env=env, cwd="/app")


def main() -> None:
    switch = spawn("dataplane.switch", "SWITCH_CONFIG")
    routers = {"ROUTER_CONFIG": spawn("dataplane.grouter", "ROUTER_CONFIG")}
    # also support ROUTER_CONFIG_2, _3 ... for multi-router fabrics
    for key in sorted(k for k in os.environ if k.startswith("ROUTER_CONFIG_")):
        routers[key] = spawn_named("dataplane.grouter", key)

    print(f"[fabric] up: switch + {sum(1 for r in routers.values() if r)} gRouter(s)",
          file=sys.stderr)
    while True:
        time.sleep(1.0)
        # supervise routers: restart any that exited
        for key, proc in list(routers.items()):
            if proc is not None and proc.poll() is not None:
                print(f"[fabric] {key} exited ({proc.returncode}); restarting", file=sys.stderr)
                routers[key] = spawn_named("dataplane.grouter", key)
        if switch is not None and switch.poll() is not None:
            print("[fabric] switch exited; restarting", file=sys.stderr)
            switch = spawn("dataplane.switch", "SWITCH_CONFIG")


def spawn_named(module: str, env_key: str) -> subprocess.Popen:
    """Launch a router whose config lives in env_key, exposed to it as ROUTER_CONFIG."""
    env = dict(os.environ)
    env["ROUTER_CONFIG"] = os.environ[env_key]
    return subprocess.Popen([PY, "-m", module], env=env, cwd="/app")


if __name__ == "__main__":
    main()
