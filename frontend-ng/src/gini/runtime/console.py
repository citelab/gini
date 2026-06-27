"""Interactive console client for one network element.

Runs inside the fabric container:  python -m dataplane.console R1
Connects to that element's control socket and relays stdin<->socket, giving the
student an interactive prompt scoped to *that* router/switch.
"""
from __future__ import annotations

import os
import select
import socket
import sys
import time


def main() -> int:  # pragma: no cover - interactive / needs container
    if len(sys.argv) < 2:
        print("usage: python -m dataplane.console <element-name> [command]")
        return 2
    name = sys.argv[1]
    oneshot = " ".join(sys.argv[2:]).strip() if len(sys.argv) > 2 else None
    ctrl_dir = os.environ.get("GINI_CTRL_DIR", "/run/gini")
    path = os.path.join(ctrl_dir, f"{name}.sock")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect(path)
    except OSError as e:
        print(f"cannot connect to {name} ({path}): {e}")
        return 1

    if oneshot is not None:                         # non-interactive: one command, print, exit
        try:
            s.recv(4096)                            # drain banner + prompt
            s.sendall((oneshot + "\n").encode())
            time.sleep(0.2)
            s.settimeout(0.6)
            data = b""
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                data += chunk
                if data.rstrip().endswith(b"gini>"):
                    break
        except OSError:
            pass
        print(data.decode(errors="replace").replace("gini>", "").strip())
        return 0

    print(f"-- GINI console: {name} (type 'help', 'exit') --")
    while True:
        r, _, _ = select.select([s, sys.stdin], [], [])
        if s in r:
            data = s.recv(4096)
            if not data:
                break
            sys.stdout.write(data.decode(errors="replace"))
            sys.stdout.flush()
        if sys.stdin in r:
            line = sys.stdin.readline()
            if not line:
                break
            s.sendall(line.encode())
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
