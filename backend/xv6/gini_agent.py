#!/usr/bin/env python3
"""gini-xv6 in-container agent — runs gdb-multiarch against the local QEMU stub (where the
kernel symbols live) and serves the reads as JSON/text over HTTP, so the Mac-side Machine Lab
bridge only makes plain HTTP calls (no gdb or symbols needed on the host).

Endpoints (GET unless noted):
  /snapshot  -> {"registers": <text>, "bt": <text>, "procs": <text>, "ticks": <text>}
  /vm        -> vmprint-format text (running proc's page-table leaf mappings)
  /vmall     -> all user procs' page tables (`VP pid name sz` + `VL pid va pa flags`) for COW view
  /faults    -> live page-fault ring (`FLT pid scause va epc`)
  /traps     -> trap-taxonomy counters + ring (`TC kind name count` + `TR pid kind cause epc tval`)
  /fs        -> {"sb": <text>, "log": <text>}
  /step      (POST) -> break swtch; continue; delete   (advance one context switch)
  /trapcatch (POST) -> freeze the next user trap: CSRs (scause/sepc/stval) + saved user registers
  /control   (POST ?quantum=N | ?policy=N) -> write the kernel knob over gdb

The kernel-side parsing stays on the Mac (already unit-tested); this agent just returns raw
gdb output. It talks to :1234 (gdb stub) and reads proc[] via a small gdb-python walk.
"""
import collections
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

XV6_DIR = "/opt/xv6-riscv"
SHADOW_FILE = XV6_DIR + "/kernel/shadows/gini_sched.c"
SHADOW_REF = "/opt/gini_sched_ref.c"    # pristine stub (outside the bind-mount) for present/hash

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


# --------------------------------------------------------------------------- #
# QEMU lifecycle — the agent owns QEMU so the Load loop can rebuild + relaunch it.
# --------------------------------------------------------------------------- #
def _qemu_cmd():
    cpus = os.environ.get("XV6_CPUS", "1")
    return ["qemu-system-riscv64", "-machine", "virt", "-bios", "none",
            "-kernel", "kernel/kernel", "-m", "128M", "-smp", str(cpus),
            "-display", "none", "-monitor", "none",
            "-global", "virtio-mmio.force-legacy=false",
            "-drive", "file=fs.img,if=none,format=raw,id=x0",
            "-device", "virtio-blk-device,drive=x0,bus=virtio-mmio-bus.0",
            "-serial", "tcp::4444,server,nowait", "-gdb", "tcp::1234"]


class Qemu:
    """Owns the QEMU child process. start/stop/restart; the reconnecting SerialLink + fresh gdb
    connections re-attach automatically after a restart, so a Load just cycles this."""

    def __init__(self):
        self.proc = None
        self._lock = threading.Lock()

    def start(self):
        with self._lock:
            if self.proc and self.proc.poll() is None:
                return
            self.proc = subprocess.Popen(_qemu_cmd(), cwd=XV6_DIR)

    def stop(self):
        with self._lock:
            p = self.proc
            self.proc = None
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=5)
            except Exception:
                p.kill()

    def restart(self):
        self.stop()
        time.sleep(0.3)          # let :4444/:1234 free up before QEMU re-listens
        self.start()


_QEMU = Qemu()


def _scope_errors(log: str) -> str:
    """Keep the gcc lines that name the student's shadow file (or say 'error:'), so a compile
    failure is legible instead of a wall of kernel build output."""
    keep = [ln for ln in log.splitlines() if "gini_sched" in ln or "error:" in ln]
    return "\n".join(keep[-40:]) or log[-2000:]


def _rebuild():
    """Incremental `make` (recompiles the changed shadow file + relinks), then restart QEMU with the
    new kernel. Returns (ok, log). The bind-mounted gini_sched.c is newer, so make rebuilds just it."""
    try:
        r = subprocess.run(["make", "kernel/kernel", "fs.img"], cwd=XV6_DIR,
                           capture_output=True, text=True, timeout=180)
    except Exception as e:
        return False, f"build error: {e}"
    if r.returncode != 0:
        return False, _scope_errors(r.stdout + r.stderr)
    _QEMU.restart()
    return True, "loaded"


def _revert():
    """Restore the shipped stub, then rebuild — one-click back to a known-good kernel."""
    try:
        shutil.copyfile(SHADOW_REF, SHADOW_FILE)
    except Exception as e:
        return False, f"revert error: {e}"
    return _rebuild()


def _md5(path: str):
    try:
        with open(path, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()
    except Exception:
        return None


def _stamp_manifest(text: str) -> str:
    """Rewrite the kernel's `hash=baseline present=0` into the agent-computed file hash + present
    (does the student's shadow file differ from the shipped stub?), so the manifest carries a real
    version. `active`/`enabled`/`faults` stay as the kernel emitted them."""
    fh = _md5(SHADOW_FILE)
    present = "1" if (fh and fh != _md5(SHADOW_REF)) else "0"
    short = (fh or "baseline")[:8]
    out = []
    for ln in text.splitlines():
        if ln.lstrip().startswith("SHADOW "):
            ln = re.sub(r"hash=\S+", f"hash={short}", ln)
            ln = re.sub(r"present=\d+", f"present={present}", ln)
        out.append(ln)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")

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


# Freeze the NEXT user trap: break at usertrap entry, then read the trap CSRs (scause/sepc/stval —
# the trap facts, live at entry) plus the user registers uservec saved into the trapframe. The
# current proc is cpus[$tp].proc ($tp = hartid in xv6). On an idle kernel with no user proc this
# times out and the frontend falls back to the authored journey. gdb_run appends `detach`.
_TF = "cpus[$tp].proc->trapframe"
# gdb breakpoint predicates to catch a trap of a SPECIFIC kind (Phase 4). "any" = no condition.
_TRAP_COND = {
    "syscall": "$scause==8",
    "pagefault": "($scause==12 || $scause==13 || $scause==15)",
    "illegal": "$scause==2",
    "timer": "$scause==0x8000000000000005",
    "device": "($scause==0x8000000000000009)",
}


def _trap_catch_cmds(kind="any"):
    cond = _TRAP_COND.get(kind)
    brk = f"tbreak usertrap if {cond}" if cond else "tbreak usertrap"
    return [brk] + _TRAP_CATCH_TAIL


_TRAP_CATCH_TAIL = [
    "continue",
    "echo ===TRAP===\\n",
    "printf \"scause %p\\n\", $scause",
    "printf \"sepc %p\\n\", $sepc",
    "printf \"stval %p\\n\", $stval",
    "printf \"pid %d\\n\", (cpus[$tp].proc ? cpus[$tp].proc->pid : -1)",
    f"printf \"epc %p\\n\", {_TF}->epc",
    f"printf \"ra %p\\n\", {_TF}->ra",
    f"printf \"sp %p\\n\", {_TF}->sp",
    f"printf \"a0 %p\\n\", {_TF}->a0",
    f"printf \"a1 %p\\n\", {_TF}->a1",
    f"printf \"a2 %p\\n\", {_TF}->a2",
    f"printf \"a7 %p\\n\", {_TF}->a7",
]


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
        elif path == "/shadows":                                    # Ctrl-W -> gini_shadowdump(),
            self._send(_stamp_manifest(_SERIAL.dump(b"\x17")), ctype="text/plain")  # hash-stamped
        elif path == "/vmall":
            self._send(_SERIAL.dump(b"\x01"), ctype="text/plain")   # Ctrl-A -> gini_vmdump_all()
        elif path == "/faults":
            self._send(_SERIAL.dump(b"\x05"), ctype="text/plain")   # Ctrl-E -> gini_faultdump()
        elif path == "/traps":
            self._send(_SERIAL.dump(b"\x12"), ctype="text/plain")   # Ctrl-R -> gini_trapdump()
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
        elif u.path == "/trapcatch":
            # freeze the next live user trap and read its CSRs + saved user registers. An optional
            # ?kind= (pagefault/syscall/timer/illegal/device) conditions the breakpoint (Phase 4);
            # default "any" catches the next trap of any kind (Phase 2).
            kind = (q.get("kind", ["any"])[0] or "any").strip()
            self._send({"out": gdb_run(_trap_catch_cmds(kind))})
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
            if "policy" in q:
                # set sched_policy over the serial: Ctrl-B resets to 0 (round-robin), then Ctrl-G
                # bumps up to the target (0=RR 1=priority 2=lottery). Same pattern as the quantum.
                try:
                    pv = max(0, min(2, int(q["policy"][0])))
                except ValueError:
                    pv = 0
                _SERIAL.write("\x02")                 # Ctrl-B  -> sched_policy = 0
                for _ in range(pv):
                    _SERIAL.write("\x07")             # Ctrl-G  -> sched_policy++
                out = (out + f" policy={pv}").strip()
            self._send({"ok": True, "out": out})
        elif u.path == "/run":                       # launch a program in the background
            prog = (q.get("prog", [""])[0] or "").strip()
            ok = bool(prog) and prog in PROGRAMS and _SERIAL.write(prog + " &\n")
            self._send({"ok": ok, "prog": prog})
        elif u.path == "/kill":                       # CONTROL-PLANE kill (init/sh guarded by UI)
            try:
                pid = int(q.get("pid", ["0"])[0])
            except ValueError:
                pid = 0
            # Ctrl-Y + <pid> + newline: the kernel's consoleintr fires gini_kill() straight from the
            # UART interrupt, so the kill lands immediately instead of waiting for the shell + `kill`
            # program to be scheduled behind the workload (the old `kill <pid>\n` path). The shell
            # `kill` still works when typed in the Terminal.
            ok = pid > 2 and _SERIAL.write(f"\x19{pid}\n")
            self._send({"ok": ok, "pid": pid})
        elif u.path == "/input":                      # raw console input (the in-app console)
            self._send({"ok": _SERIAL.write(self._body())})
        elif u.path in ("/interrupt", "/break"):      # Ctrl-C: kernel breaks a hung foreground
            self._send({"ok": _SERIAL.write("\x03")})  # console driver handles it, not sh
        elif u.path == "/shadow/toggle":              # flip the CURRENT policy's shadow on/off
            self._send({"ok": _SERIAL.write("\x0b")})  # Ctrl-K (the bridge sets policy first)
        elif u.path == "/rebuild":                    # Load: incremental make + restart QEMU
            ok, log = _rebuild()
            self._send({"ok": ok, "log": log})
        elif u.path == "/revert":                     # restore the shipped stub, then rebuild
            ok, log = _revert()
            self._send({"ok": ok, "log": log})
        elif u.path == "/console/clear":              # blank the Screen (baseline to now)
            _SERIAL.clear_console()
            self._send({"ok": True})
        else:
            self._send({"error": "not found"})


if __name__ == "__main__":
    # Seed the shadow file if the bind-mounted host folder is empty (Load loop): the mount hides the
    # image's kernel/shadows/gini_sched.c, so copy the pristine stub back in — the pre-built
    # kernel/kernel still boots, and the student's edits + Load rebuild from this file.
    if not os.path.exists(SHADOW_FILE) and os.path.exists(SHADOW_REF):
        try:
            os.makedirs(os.path.dirname(SHADOW_FILE), exist_ok=True)
            shutil.copyfile(SHADOW_REF, SHADOW_FILE)
        except Exception:
            pass
    _QEMU.start()                                     # launch the kernel; the agent stays PID 1
    HTTPServer(("0.0.0.0", 5000), Handler).serve_forever()
