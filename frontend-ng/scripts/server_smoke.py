#!/usr/bin/env python3
"""End-to-end smoke test for a running GINI server (the Kata backend).

Exercises the exact gBuilder -> server protocol: log in, report capabilities, send a small
all-Kata topology to RUN, poll metrics (startup times!), then STOP. Point it at your server:

    cd gbuilder
    python scripts/server_smoke.py  http://YOUR_HOST:10000  USERNAME  PASSWORD

It uses GINI's own RemoteClient, so a green run here means gBuilder will work against this
server. (Add the user first on the server:  python -m gini.server adduser users.json USERNAME)
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gini.domain.topology import Topology          # noqa: E402
from gini.services.remote import RemoteClient       # noqa: E402


def main() -> int:
    if len(sys.argv) < 4:
        print(__doc__)
        return 2
    url, user, pw = sys.argv[1], sys.argv[2], sys.argv[3]
    c = RemoteClient(url)

    ok, err = c.login(user, pw)
    print(f"login         : {'ok' if ok else 'FAILED — ' + err}")
    if not ok:
        return 1
    caps = c.capabilities()
    print(f"capabilities  : {caps}")

    # a tiny one-element lab. Use a Kata Instance (VM) if the box has Kata, else a normal
    # container — so this validates the pipeline even before Kata is installed.
    use_kata = bool(caps.get("kata"))
    t = Topology("smoke")
    t.add_device("kinstance" if use_kata else "container")
    print(f"topology      : a single {'Kata Instance (VM)' if use_kata else 'container'}")

    ok, msg = c.run(t)
    print(f"run (accepted): {'ok' if ok else 'FAILED'} — {msg}")
    if not ok:
        return 1
    print("launching     : pulling images / booting … (first run can take a minute)")
    ok, msg = c.wait_until_running()                 # poll the async launch
    print(f"up            : {'ok' if ok else 'FAILED'} — {msg}")
    if ok:
        m = c.metrics()
        print(f"startup (ms)  : {m.get('startup')}")

    ok, msg = c.stop()
    print(f"stop          : {'ok' if ok else 'FAILED'} — {msg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
