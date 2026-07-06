"""Regression guard: the POX controller must actually BOOT with the GINI app and open its
OpenFlow listen socket.

The failure this catches: POX resolves the component `gini.samples.switch` by trying
`pox.gini.samples.switch` first; a naive importer aborts on that `ModuleNotFoundError`
(missing parent package `pox.gini`) instead of falling back to the bare `ext/gini`
component, so POX never finishes booting, `of_01` never listens, and every OVS sits in
fail-secure (controller idle, pings dropped at the switch). See pox/boot.py do_import2.

Run:  cd backend/sdn && python3 -m pytest test_pox_boots.py -q   (or: python3 test_pox_boots.py)
Pure stdlib; skips cleanly if POX can't be spawned.
"""
import os
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
POX_DIR = os.path.join(HERE, "pox")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _boot_and_check(app="gini.samples.switch", timeout=15.0) -> bool:
    if not os.path.isdir(POX_DIR):
        return True  # nothing to test here
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "pox.py", "openflow.of_01", f"--port={port}", app],
        cwd=POX_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if proc.poll() is not None:            # POX exited early -> boot failed
                out = proc.stdout.read() if proc.stdout else ""
                raise AssertionError(f"POX exited before listening:\n{out[-1500:]}")
            with socket.socket() as c:
                if c.connect_ex(("127.0.0.1", port)) == 0:
                    return True                    # of_01 is listening -> booted OK
            time.sleep(0.3)
        raise AssertionError(f"POX never listened on :{port} within {timeout}s")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_pox_boots_with_gini_switch_app():
    assert _boot_and_check("gini.samples.switch")


def test_pox_boots_with_gini_sfc_app():
    # the Service Function Chaining controller must also load and listen
    assert _boot_and_check("gini.samples.sfc")


if __name__ == "__main__":
    ok = _boot_and_check("gini.samples.switch")
    print("PASS: POX booted and listened" if ok else "FAIL")
