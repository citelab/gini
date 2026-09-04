"""Per-element console: each switch/router exposes its own control socket."""
import os
import socket
import tempfile
import time

from gini.runtime.grouter import Router
from gini.runtime.switch import LearningSwitch


def _port(bind, peer):
    return {"bind_host": "127.0.0.1", "bind_port": bind,
            "peer_host": "127.0.0.1", "peer_port": peer}


def _exchange(path: str, cmd: str) -> str:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(2.0)
    s.connect(path)
    time.sleep(0.1)
    s.recv(4096)                       # drain banner + first prompt
    s.sendall((cmd + "\n").encode())
    time.sleep(0.2)
    out = s.recv(8192).decode()
    s.close()
    return out


def test_router_and_switch_consoles():
    ctrl = tempfile.mkdtemp(prefix="gini-ctrl-")
    os.environ["GINI_CTRL_DIR"] = ctrl
    # Held, not discarded: constructing a node starts its control server on a thread, and
    # `ControlServer.run` blocks in `accept()` for ever. Dropped on the floor, those two threads
    # outlived the test and ran for the remainder of the session — see the leak guard in
    # conftest.py, and the random bus errors that were the symptom.
    nodes = []
    try:
        nodes.append(Router({"name": "r1", "ifaces": [
            {"ip": "10.0.1.1/24", "mac": "02:00:00:01:01:01", "port": _port(5951, 5952)},
            {"ip": "10.0.2.1/24", "mac": "02:00:00:02:01:01", "port": _port(5953, 5954)},
        ]}))
        nodes.append(
            LearningSwitch({"name": "s1", "ports": [_port(5955, 5956), _port(5957, 5958)]}))
        time.sleep(0.2)

        r_if = _exchange(os.path.join(ctrl, "r1.sock"), "interfaces")
        assert "10.0.1.1/24" in r_if and "10.0.2.1/24" in r_if
        r_routes = _exchange(os.path.join(ctrl, "r1.sock"), "routes")
        assert "10.0.1.0/24" in r_routes

        s_ports = _exchange(os.path.join(ctrl, "s1.sock"), "ports")
        assert "p0" in s_ports and "p1" in s_ports
        s_help = _exchange(os.path.join(ctrl, "s1.sock"), "help")
        assert "mactable" in s_help

        # Z2: gpipe module pipeline over the same console (Router Lab binds to this)
        sock = os.path.join(ctrl, "r1.sock")
        _exchange(sock, "gpipe clear")
        added = _exchange(sock, "gpipe add acl 10.0.3.0/24")
        assert "added acl" in added
        denied = _exchange(sock, "gpipe trace 10.0.3.10")
        assert "DROP" in denied
        allowed = _exchange(sock, "gpipe trace 10.0.9.9")
        assert "base forwarding" in allowed
    finally:
        for n in nodes:
            ctrl = getattr(n, "_ctrl", None)
            if ctrl is not None:
                ctrl.stop()
        os.environ.pop("GINI_CTRL_DIR", None)
