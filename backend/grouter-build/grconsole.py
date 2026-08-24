#!/usr/bin/env python3
"""Console client for a running gRouter — the real CLI over its control socket.

The gRouter runs as a daemon, so its interactive CLI isn't on stdin. It instead
listens on a Unix socket (<confdir>/<name>.ctl); this client sends a command line and
prints whatever the command produced. Use it interactively (a real GINI-<r> $ prompt)
or one-shot for a single query.

  python3 grconsole.py /run/r1.ctl                 # interactive REPL
  python3 grconsole.py /run/r1.ctl --once "route"  # single command, print, exit
"""
import os
import socket
import sys

END = "__END__"


# The gRouter's own verbs, from registerCLI() in src/grouter/cli.c. Used for Tab completion and
# nothing else, so a stale entry costs a useless suggestion rather than a broken command.
COMMANDS = [
    "arp", "class", "console", "delay", "exit", "filter", "get", "gnc", "gpipe", "halt",
    "help", "ifconfig", "openflow", "ping", "qdisc", "queue", "route", "set", "source",
    "spolicy", "version", "quit", "logout",
]
# Second words worth completing. Only where there is an obvious small set — guessing wrong here
# is worse than not completing, because Tab then silently inserts something that does not exist.
SUBCOMMANDS = {
    "ifconfig": ["show", "add", "down", "up"],
    "route": ["show", "add", "del"],
    "arp": ["show", "clear"],
    "gpipe": ["list", "add", "del", "cp"],
    "queue": ["show", "add", "del", "stats"],
    "qdisc": ["add", "del", "show"],
    "delay": ["show", "clear", "ingress", "egress"],
    "spolicy": ["set", "show"],
    "openflow": ["entry", "stats", "show"],
    "class": ["add", "del", "show"],
}

HISTORY = os.path.expanduser("~/.grconsole_history")
HISTORY_LINES = 500
_readline = None


def _complete(text, state):
    """Complete the verb, or its second word when we know the set."""
    try:
        buf = _readline.get_line_buffer()[:_readline.get_endidx()]
        words = buf.split()
        first_word = len(words) == 0 or (len(words) == 1 and not buf.endswith(" "))
        pool = COMMANDS if first_word else SUBCOMMANDS.get(words[0], [])
        hits = [c + " " for c in pool if c.startswith(text)]
        return hits[state] if state < len(hits) else None
    except Exception:            # noqa: BLE001 - completion must never break the prompt
        return None


def setup_readline(name: str) -> None:
    """Give the console line editing, history and Tab completion.

    input() has none of that on its own — no Up for the previous command, no Left/Right to fix a
    typo, no Ctrl-A. Importing readline is all it takes: Python then routes input() through it and
    every editing key works, because the terminal is already sending the right escape sequences.

    Best-effort: a Python built without readline (or a minimal image) simply gets the old
    behaviour rather than a traceback at start-up.
    """
    global _readline
    try:
        import readline
    except ImportError:
        return
    _readline = readline
    try:
        readline.read_history_file(HISTORY)
    except (OSError, ValueError):
        pass                     # no history yet, or an unreadable one: start fresh
    readline.set_history_length(HISTORY_LINES)
    readline.set_completer(_complete)
    readline.set_completer_delims(" \t\n")
    # libedit (what readline resolves to on some builds) uses a different bind syntax; try both
    # rather than detect, since getting it wrong just means Tab inserts a tab.
    doc = getattr(readline, "__doc__", "") or ""
    readline.parse_and_bind("bind ^I rl_complete" if "libedit" in doc else "tab: complete")


def save_history() -> None:
    if _readline is None:
        return
    try:
        _readline.write_history_file(HISTORY)
    except OSError:
        pass                     # read-only home: history just does not persist


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
    setup_readline(name)
    print(f"Connected to gRouter '{name}'. Type CLI commands "
          f"(help, ifconfig show, route show, arp show, gpipe list). "
          f"Up/Down for history, Tab to complete. Ctrl-D to exit.")
    while True:
        try:
            line = input(f"GINI-{name} $ ").strip()
        except EOFError:
            print()
            break
        except KeyboardInterrupt:      # Ctrl-C abandons the LINE, it does not quit the console
            print("^C")
            continue
        if not line:
            continue
        if line in ("quit", "logout"):
            break
        print(query(s, line))
    save_history()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
