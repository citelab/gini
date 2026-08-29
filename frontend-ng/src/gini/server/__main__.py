"""Run the GINI server, or manage its flat user file.

    python -m gini.server adduser  users.json  s_jane
    python -m gini.server run  --users users.json  --base ./gini-labs  --port 10000

The server secret comes from $GINI_SERVER_SECRET (else a random one each start, which just
invalidates old tokens on restart — fine for a class).
"""
from __future__ import annotations

import getpass
import json
import os
import sys
from pathlib import Path


def _adduser(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: adduser <userfile> <username>")
        return 2
    from .auth import hash_password
    path, user = Path(argv[0]), argv[1]
    users = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    users[user] = hash_password(getpass.getpass(f"password for {user}: "))
    path.write_text(json.dumps(users, indent=2), encoding="utf-8")
    print(f"saved {user} to {path}")
    return 0


def _run(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="gini.server run")
    ap.add_argument("--users", default="users.json")
    ap.add_argument("--base", default="./gini-labs")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=10000)
    ap.add_argument("--max-cpus", type=float, default=2.0)
    a = ap.parse_args(argv)

    from .. import runtime as _rt
    from ..services.orchestrator import Orchestrator
    from .app import GiniServer, serve
    from .auth import Tokens, UserStore
    from .session import SessionManager

    runtime_dir = Path(_rt.__file__).parent
    secret = os.environ.get("GINI_SERVER_SECRET", os.urandom(32).hex()).encode()

    def factory(project: str, workdir):
        return Orchestrator(runtime_dir, project=project)

    srv = GiniServer(UserStore.from_file(a.users), Tokens(secret),
                     SessionManager(a.base), factory, max_cpus=a.max_cpus)
    serve(srv, a.host, a.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: python -m gini.server {run|adduser} ...")
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "adduser":
        return _adduser(rest)
    if cmd == "run":
        return _run(rest)
    print(f"unknown command: {cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
