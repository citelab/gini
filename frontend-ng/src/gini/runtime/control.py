"""Per-element control socket — the seed of the gRouter/switch console.

Each switch and gRouter, when GINI_CTRL_DIR is set, listens on its own UNIX socket
(<dir>/<name>.sock) speaking a tiny line protocol. `console.py` connects to one of
these, so a student can "log into" R1 or S1 *individually* even though they share the
fabric container. This is the first slice of the R3 control protocol.
"""
from __future__ import annotations

import os
import socket
import threading
from collections.abc import Callable


class ControlServer(threading.Thread):
    def __init__(self, path: str, handler: Callable[[str], str], banner: str = "") -> None:
        super().__init__(daemon=True)
        self.path = path
        self.handler = handler
        self.banner = banner

    def run(self) -> None:  # pragma: no cover - exercised via integration/in-process test
        try:
            os.unlink(self.path)
        except OSError:
            pass
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(self.path)
        srv.listen()
        while True:
            conn, _ = srv.accept()
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        conn.sendall((self.banner + "\ngini> ").encode())
        buf = b""
        while True:
            try:
                data = conn.recv(1024)
            except OSError:
                break
            if not data:
                break
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                cmd = line.decode(errors="replace").strip()
                if cmd in ("exit", "quit"):
                    conn.close()
                    return
                out = ""
                if cmd:
                    try:
                        out = self.handler(cmd)
                    except Exception as e:  # never kill the console on a bad command
                        out = f"error: {e}"
                conn.sendall((out + "\ngini> ").encode())
        conn.close()


def maybe_start(name: str, handler: Callable[[str], str], banner: str = "") -> ControlServer | None:
    """Start a control server iff GINI_CTRL_DIR is set (i.e., running in the fabric)."""
    ctrl_dir = os.environ.get("GINI_CTRL_DIR")
    if not ctrl_dir:
        return None
    os.makedirs(ctrl_dir, exist_ok=True)
    server = ControlServer(os.path.join(ctrl_dir, f"{name}.sock"), handler, banner)
    server.start()
    return server
