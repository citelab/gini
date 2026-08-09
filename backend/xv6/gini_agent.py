#!/usr/bin/env python3
"""gini-xv6 in-container agent — runs gdb-multiarch against the local QEMU stub (where the
kernel symbols live) and serves the reads as JSON/text over HTTP, so the Mac-side Machine Lab
bridge only makes plain HTTP calls (no gdb or symbols needed on the host).

Endpoints (GET unless noted):
  /snapshot  -> {"registers": <text>, "bt": <text>, "procs": <text>, "ticks": <text>}
  /vm        -> vmprint-format text (page-table leaf mappings)
  /fs        -> {"sb": <text>, "log": <text>}
  /step      (POST) -> break swtch; continue; delete   (advance one context switch)
  /control   (POST ?quantum=N | ?policy=N) -> write the kernel knob over gdb

The kernel-side parsing stays on the Mac (already unit-tested); this agent just returns raw
gdb output. It talks to :1234 (gdb stub) and reads proc[] via a small gdb-python walk.
"""
import collections
import json
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

# The kernel brackets every GINI dump (Ctrl-T/V/F, machine-readable) with these control bytes, so
# the reader can split the single serial stream into TWO logical streams at ingest time: a CLEAN
# human console (what the Terminal shows) and captured dump blocks (what the Machine Lab parses).
# xv6's own Ctrl-P procdump is NOT bracketed, so it lands in the console and shows in the Terminal.
DUMP_START, DUMP_END = 0x1e, 0x1f      # ASCII RS / US — record/unit separators, framing the dump

KERNEL = "/opt/xv6-riscv/kernel/kernel"
STUB = "localhost:1234"
SERIAL = ("127.0.0.1", 4444)
TIMEOUT = 6          # keep gdb calls short so a stuck read can't wedge the stub/UI
_LOCK = threading.Lock()   # one gdb at a time (defensive; also serialises stub access)

# long-running programs the Machine Lab offers to launch (gini_patch.py adds spin/alloc/writer;
# grind/forktest are stock). init/sh are never launchable/killable.
PROGRAMS = ["spin", "busy", "alloc", "writer", "grind", "forktest"]


class SerialLink:
    """Owns the single QEMU serial connection (QEMU allows one client), so the agent can inject
    shell commands (launch/kill) AND expose the console — the human console goes through here too,
    since a second raw client would be refused. Reconnects on drop; buffers recent output."""

    def __init__(self, addr):
        self.addr = addr
        self.buf = collections.deque(maxlen=20000)   # RAW bytes (all), for the dump fallback
        self._total = 0                              # monotonic raw bytes ever received
        # clean human console (dump blocks removed at ingest) + its own monotonic counter/baseline
        self.console = collections.deque(maxlen=20000)
        self._console_total = 0
        self._console_base = 0                       # Terminal view starts here (Clear screen)
        # dump capture: bytes between 0x1e..0x1f, and the last completed one (for the Lab reads)
        self._in_dump = False
        self._cap = bytearray()
        self._last_dump = b""
        self._dump_seq = 0                           # bumps each time a dump completes
        self._sock = None
        self._lock = threading.Lock()
        threading.Thread(target=self._reader, daemon=True).start()

    def _connect(self):
        try:
            s = socket.create_connection(self.addr, timeout=3)
            s.settimeout(0.5)
            self._sock = s
        except Exception:
            self._sock = None

    def _ingest(self, data: bytes):
        """Classify each byte: bytes inside a 0x1e..0x1f frame are a machine-readable dump (kept
        aside for the Lab); everything else is the human console the Terminal shows."""
        self.buf.extend(data)
        self._total += len(data)
        for b in data:
            if self._in_dump:
                if b == DUMP_END:
                    self._in_dump = False
                    self._last_dump = bytes(self._cap)
                    self._cap = bytearray()
                    self._dump_seq += 1
                else:
                    self._cap.append(b)
            elif b == DUMP_START:
                self._in_dump = True
                self._cap = bytearray()
            else:
                self.console.append(b)
                self._console_total += 1

    def _reader(self):
        while True:
            if self._sock is None:
                self._connect()
                if self._sock is None:
                    time.sleep(1)
                    continue
            try:
                data = self._sock.recv(4096)
                if not data:
                    self._sock = None
                    continue
                self._ingest(data)
            except socket.timeout:
                continue
            except Exception:
                self._sock = None
                time.sleep(0.5)

    def write(self, text):
        with self._lock:
            if self._sock is None:
                self._connect()
            if self._sock is None:
                return False
            try:
                self._sock.sendall(text.encode())
                return True
            except Exception:
                self._sock = None
                return False

    def clear_console(self):
        """Move the console view's baseline to 'now' — the Terminal's Clear. The kernel is
        untouched; only what `tail()`/`stream()` return changes."""
        self._console_base = self._console_total

    def _console_slice(self, since):
        """(text, next) of the clean console from max(since, baseline) up to the newest byte.
        Clamped to what's still in the ring buffer (old bytes are evicted)."""
        buf = bytes(self.console)
        start = self._console_total - len(buf)           # console-offset of console[0]
        base = max(self._console_base, start, int(since))
        text = buf[base - start:].decode(errors="replace")
        return text, self._console_total

    def tail(self, n=4000):
        """The whole current console view (since the last Clear) — used by the plain /console."""
        return self._console_slice(0)[0][-n:]

    def stream(self, since):
        """Append-only console delta for the Terminal: text after `since`, plus the new cursor."""
        return self._console_slice(since)

    def dump(self, ctrl, wait=0.35):
        """Send a console control char that makes the kernel print a dump, and return just that
        dump's text — WITHOUT halting the kernel (unlike gdb). Prefers the sentinel-framed block
        captured at ingest (robust); falls back to a monotonic raw-byte window for a pre-marker
        kernel. Monotonic offset, NOT len(buf): the ring buffer evicts old bytes, so len(buf)
        saturates at maxlen and a `buf[before:]` slice would go permanently empty once full."""
        with self._lock:
            if self._sock is None:
                self._connect()
            seq0 = self._dump_seq
            before = self._total
            try:
                if self._sock:
                    self._sock.sendall(ctrl)
            except Exception:
                self._sock = None
        time.sleep(wait)
        if self._dump_seq > seq0:                         # a framed dump arrived -> use it
            return self._last_dump.decode(errors="replace")
        n = self._total - before                          # pre-marker kernel -> raw window
        if n <= 0:
            return ""
        return bytes(self.buf)[-min(n, len(self.buf)):].decode(errors="replace")

    def procdump(self, wait=0.35):
        return self.dump(b"\x14", wait)     # Ctrl-T -> gini_dump() (procs + running-proc regs)


_SERIAL = SerialLink(SERIAL)

# gdb-python: walk proc[] and print `pid <state> <name>` for every active slot (the Mac's
# parse_procdump accepts these full state words).
_PROC_WALK = r"""python
import gdb
_ST = {0:"unused",1:"used",2:"sleeping",3:"runnable",4:"running",5:"zombie"}
try:
    n = int(gdb.parse_and_eval("NPROC"))
except Exception:
    n = 64
try:
    arr = gdb.parse_and_eval("proc")
    for i in range(n):
        p = arr[i]
        st = int(p["state"])
        if st == 0:
            continue
        try:
            name = p["name"].string()
        except Exception:
            name = "?"
        print("%d %s %s" % (int(p["pid"]), _ST.get(st, "used"), name))
except Exception as e:
    print("procwalk-error:", e)
end"""

# gdb-python: walk the current process's page table and print vmprint-format lines.
_VM_WALK = r"""python
import gdb
def walk(pt, level):
    for i in range(512):
        pte = int(pt[i])
        if pte & 1:  # PTE_V
            print(".. "*level + "..%d: pte 0x%x pa 0x%x" % (i, pte, (pte >> 10) << 12))
            if (pte & 0xE) == 0:  # not R/W/X -> interior node
                walk(gdb.Value((pte >> 10) << 12).cast(pt.type), level+1)
try:
    pt = gdb.parse_and_eval("myproc()->pagetable")
    print("page table 0x%x" % int(pt))
    walk(pt, 0)
except Exception as e:
    print("vmwalk-error:", e)
end"""


def gdb_run(commands, timeout=TIMEOUT):
    """Run one batch gdb session against the stub, returning combined stdout. Serialised (one
    gdb at a time) and time-bounded, and it always ends with `detach` so QEMU resumes the guest
    — so a client that dies mid-read can't leave the kernel halted."""
    args = ["gdb-multiarch", "--batch", "-nx", KERNEL, "-ex", f"target remote {STUB}"]
    for c in commands:
        args += ["-ex", c]
    args += ["-ex", "detach"]                       # resume the guest before gdb exits
    with _LOCK:
        try:
            return subprocess.run(args, capture_output=True, text=True,
                                  timeout=timeout).stdout
        except subprocess.TimeoutExpired:
            return "gdb-timeout"
        except Exception as e:
            return f"gdb-error: {e}"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj, ctype="application/json"):
        body = (json.dumps(obj) if ctype == "application/json" else obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            self._send({"ok": True})
        elif path == "/programs":
            self._send({"programs": PROGRAMS})
        elif path == "/procs":                       # fast, NO-halt process table (Ctrl-P)
            self._send(_SERIAL.procdump(), ctype="text/plain")
        elif path == "/console":
            self._send(_SERIAL.tail(), ctype="text/plain")
        elif path == "/console/stream":               # append-only delta for the Terminal
            q = parse_qs(urlparse(self.path).query)
            try:
                since = int(q.get("since", ["0"])[0])
            except ValueError:
                since = 0
            text, nxt = _SERIAL.stream(since)
            self._send({"text": text, "next": nxt})
        elif path == "/snapshot":
            out = gdb_run(["info registers", "echo ===BT===\\n", "bt",
                           "echo ===PROCS===\\n", _PROC_WALK,
                           "echo ===TICKS===\\n", "printf \"%d\\n\", ticks"])
            regs, _, rest = out.partition("===BT===")
            bt, _, rest = rest.partition("===PROCS===")
            procs, _, ticks = rest.partition("===TICKS===")
            self._send({"registers": regs, "bt": bt, "procs": procs, "ticks": ticks.strip()})
        elif path == "/vm":
            self._send(_SERIAL.dump(b"\x16"), ctype="text/plain")   # Ctrl-V -> gini_vmdump()
        elif path == "/fs":
            self._send(_SERIAL.dump(b"\x06"), ctype="text/plain")   # Ctrl-F -> gini_fsdump()
        elif path == "/sc":
            self._send(_SERIAL.dump(b"\x13"), ctype="text/plain")   # Ctrl-S -> gini_scdump()
        else:
            self._send({"error": "not found"})

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            return self.rfile.read(n).decode(errors="replace") if n else ""
        except Exception:
            return ""

    def do_POST(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/step":
            # temporary breakpoint auto-deletes on hit; if the kernel is idle (no context
            # switch) this times out harmlessly and the next read resumes the guest.
            self._send({"out": gdb_run(["tbreak swtch", "continue"])})
        elif u.path == "/control":
            # set the time-slice quantum over the SERIAL (no gdb): Ctrl-\ resets it to 1, then
            # Ctrl-] bumps it up to the target. Reliable console input, unlike the gdb write.
            out = ""
            if "quantum" in q:
                try:
                    n = max(1, min(100, int(q["quantum"][0])))
                except ValueError:
                    n = 1
                _SERIAL.write("\x1c")                 # Ctrl-\  -> sched_quantum = 1
                for _ in range(n - 1):
                    _SERIAL.write("\x1d")             # Ctrl-]  -> sched_quantum++
                out = f"quantum={n}"
            self._send({"ok": True, "out": out})
        elif u.path == "/run":                       # launch a program in the background
            prog = (q.get("prog", [""])[0] or "").strip()
            ok = bool(prog) and prog in PROGRAMS and _SERIAL.write(prog + " &\n")
            self._send({"ok": ok, "prog": prog})
        elif u.path == "/kill":                       # xv6 `kill <pid>` (init/sh guarded by UI)
            try:
                pid = int(q.get("pid", ["0"])[0])
            except ValueError:
                pid = 0
            ok = pid > 2 and _SERIAL.write(f"kill {pid}\n")
            self._send({"ok": ok, "pid": pid})
        elif u.path == "/input":                      # raw console input (the in-app console)
            self._send({"ok": _SERIAL.write(self._body())})
        elif u.path in ("/interrupt", "/break"):      # Ctrl-C: kernel breaks a hung foreground
            self._send({"ok": _SERIAL.write("\x03")})  # console driver handles it, not sh
        elif u.path == "/console/clear":              # blank the Screen (baseline to now)
            _SERIAL.clear_console()
            self._send({"ok": True})
        else:
            self._send({"error": "not found"})


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 5000), Handler).serve_forever()
