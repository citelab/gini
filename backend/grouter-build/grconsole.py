#!/usr/bin/env python3
"""Console client for a running gRouter — the real CLI over its control socket.

The gRouter runs as a daemon, so its interactive CLI isn't on stdin. It instead
listens on a Unix socket (<confdir>/<name>.ctl); this client sends a command line and
prints whatever the command produced. Use it interactively (a real GINI-<r> $ prompt)
or one-shot for a single query.

  python3 grconsole.py /run/r1.ctl                 # interactive REPL
  python3 grconsole.py /run/r1.ctl --once "route"  # single command, print, exit
"""
import socket
import sys

END = "__END__"


def query(sock: socket.socket, line: str) -> str:
    sock.sendall((line + "\n").encode())
    buf = ""
    while END not in buf:
        chunk = sock.recv(8192).decode(errors="replace")
        if not chunk:
            break
        buf += chunk
    return buf.split(END)[0].rstrip("\n")


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: grconsole.py <socket> [--once <command>]", file=sys.stderr)
        return 2
    path = sys.argv[1]
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect(path)
    except OSError as e:
        print(f"cannot reach router control socket {path}: {e}", file=sys.stderr)
        return 1

    if len(sys.argv) >= 4 and sys.argv[2] == "--once":
        print(query(s, sys.argv[3]))
        return 0

    name = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    print(f"Connected to gRouter '{name}'. Type CLI commands "
          f"(help, ifconfig show, route show, arp show, gpipe list). Ctrl-D to exit.")
    while True:
        try:
            line = input(f"GINI-{name} $ ").strip()
        except EOFError:
            print()
            break
        if not line:
            continue
        if line in ("quit", "logout"):
            break
        print(query(s, line))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
