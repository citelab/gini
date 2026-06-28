#!/usr/bin/env python3
"""End-to-end smoke test for a running GINI server (the Kata backend).

Exercises the exact gBuilder -> server protocol: log in, report capabilities, send a small
all-Kata topology to RUN, poll metrics (startup times!), then STOP. Point it at your server:

    cd frontend-ng
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
    print(f"capabilities  : {c.capabilities()}")

    # a small all-Kata experiment: a VM workload + a database, driven by a load generator
    t = Topology("kata-smoke")
    k = t.add_device("kinstance")
    db = t.add_device("database")
    lg = t.add_device("load_generator")
    t.add_link(k.id, db.id)
    t.add_link(lg.id, k.id)

    ok, msg = c.run(t)
    print(f"run           : {'ok' if ok else 'FAILED'} — {msg}")
    if not ok:
        return 1

    time.sleep(5)                                    # let the microVM(s) boot
    print(f"status        : {c.status()}")
    m = c.metrics()
    print(f"startup (ms)  : {m.get('startup')}")
    print(f"stats         : {m.get('stats')}")

    ok, msg = c.stop()
    print(f"stop          : {'ok' if ok else 'FAILED'} — {msg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
