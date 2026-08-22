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

# One student file per SUBSYSTEM, matching the `sub=` field the kernel now emits on each SHADOW
# line. Each has a pristine reference copy stashed outside the bind-mount, so "has the student
# edited THIS file?" is answered per subsystem instead of every shadow inheriting the scheduler's
# answer (which used to mark a vm shadow "present" because gini_sched.c had been touched).
SHADOWS = {
    "sched": (XV6_DIR + "/kernel/shadows/gini_sched.c", "/opt/gini_sched_ref.c"),
    "vm":    (XV6_DIR + "/kernel/shadows/gini_vm.c",    "/opt/gini_vm_ref.c"),
    "fs":    (XV6_DIR + "/kernel/shadows/gini_fs.c",    "/opt/gini_fs_ref.c"),
}

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

    def dump(self, ctrl, wait=0.35, poll=0.005):
        """Send a console control char that makes the kernel print a dump, and return just that
        dump's text — WITHOUT halting the kernel (unlike gdb). Prefers the sentinel-framed block
        captured at ingest (robust); falls back to a monotonic raw-byte window for a pre-marker
        kernel. Monotonic offset, NOT len(buf): the ring buffer evicts old bytes, so len(buf)
        saturates at maxlen and a `buf[before:]` slice would go permanently empty once full.

        THE LOCK MUST SPAN THE WHOLE EXCHANGE, not just the send.
        -------------------------------------------------------
        There is one serial line and it can carry one dump at a time, so this method has to be
        serialised end to end. It previously released the lock before waiting, which meant two
        callers could both send, both wait, and both then read `_last_dump` — and `_dump_seq >
        seq0` only proves that *a* dump arrived, never that it was YOURS. A request for /board
        would come back holding the /procs dump, which parses to nothing, and the OS HUD would
        correctly report "no board support" every other poll while the polls that won the race
        drew a perfect board. The symptom looked like an intermittent kernel; it was two readers
        on one wire.

        WAIT ONLY AS LONG AS IT TAKES.
        ------------------------------
        Serialising would be expensive with the old fixed 0.35 s sleep: the OS HUD alone asks for
        four dumps a poll, which would monopolise the line for 1.4 s and starve the Machine Lab.
        A framed dump normally lands in a few milliseconds, so poll for the sentinel and return
        the moment it arrives, keeping `wait` as a ceiling rather than a cost. Same contract,
        typically an order of magnitude less serial time held.
        """
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
            deadline = time.time() + wait
            while self._dump_seq == seq0 and time.time() < deadline:
                time.sleep(poll)
            if self._dump_seq > seq0:                     # our framed dump arrived -> use it
                return self._last_dump.decode(errors="replace")
            n = self._total - before                      # pre-marker kernel -> raw window
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


class Wedge:
    """Notices when the guest stops answering, and blames the shadow that was enabled.

    Deliberately does NOT reboot: a student who wrote a scheduler that hangs the machine should
    SEE that it hung, be told which of their shadows did it, and press Reboot themselves. We only
    report. The kernel's `gini_shadow[]` initialises enabled=0, so any reboot brings the machine
    back WITHOUT their code — no extra mechanism needed to "boot with it disabled".

    A hard wedge (panic, or a loop with interrupts off) stops the dumps, which is what this sees.
    A SOFT wedge — a picker that loops forever with interrupts on — keeps answering dumps while
    nothing ever runs; that one is detected frontend-side from the scheduling timeline.
    """
    GRACE_S = 10.0                # a busy guest can be slow; only complain after this long

    def __init__(self):
        self._lock = threading.Lock()
        self.last_ok = time.monotonic()
        self.enabled = []         # shadow names enabled at the last manifest read
        self.faults = {}          # shadow name -> how many times it wedged the machine
        self.blamed = []          # names blamed for the CURRENT wedge (cleared on reboot)

    def note_dump(self, text: str) -> None:
        """Called on every poll. A non-empty dump means the kernel is still servicing interrupts."""
        if not text:
            return
        with self._lock:
            self.last_ok = time.monotonic()
            if self.blamed:       # it answered again on its own -> not wedged after all
                self.blamed = []

    def note_manifest(self, text: str) -> None:
        with self._lock:
            self.enabled = re.findall(r"SHADOW (\S+).*?enabled=1", text or "")

    def rebooted(self) -> None:
        with self._lock:
            self.last_ok = time.monotonic()
            self.blamed = []
            self.enabled = []     # they come back disabled

    def state(self) -> dict:
        with self._lock:
            quiet = time.monotonic() - self.last_ok
            wedged = quiet > self.GRACE_S
            if wedged and not self.blamed:
                self.blamed = list(self.enabled) or ["(no shadow enabled)"]
                for n in self.blamed:                  # count the fault once per wedge
                    self.faults[n] = self.faults.get(n, 0) + 1
            return {"wedged": wedged, "quiet_s": round(quiet, 1),
                    "blamed": list(self.blamed), "faults": dict(self.faults),
                    "panic": _SERIAL.tail()[-2000:].count("panic") > 0 if wedged else False}


_WEDGE = Wedge()


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


def _revert(sub: str = ""):
    """Restore the shipped stub(s), then rebuild — one click back to a known-good kernel.

    `sub` names ONE subsystem (sched/vm/fs) so a student experimenting with paging can throw away
    their vm file without losing the scheduler they already got working. No `sub` reverts all,
    which is the old behaviour and still the "put everything back" button.
    """
    targets = [SHADOWS[sub]] if sub in SHADOWS else list(SHADOWS.values())
    try:
        for path, ref in targets:
            if os.path.exists(ref):
                shutil.copyfile(ref, path)
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
    # present/hash PER SUBSYSTEM: each SHADOW line carries `sub=`, so stamp it from that
    # subsystem's own file. (Pre-`sub=` kernels fall back to the scheduler file, as before.)
    stamp = {}
    for sub, (path, ref) in SHADOWS.items():
        fh = _md5(path)
        stamp[sub] = ("1" if (fh and fh != _md5(ref)) else "0", (fh or "baseline")[:8])
    faults = _WEDGE.state()["faults"]      # host-side: times this shadow wedged the machine
    out = []
    for ln in text.splitlines():
        if ln.lstrip().startswith("SHADOW "):
            msub = re.search(r"sub=(\w+)", ln)
            present, short = stamp.get(msub.group(1) if msub else "sched", stamp["sched"])
            ln = re.sub(r"hash=\S+", f"hash={short}", ln)
            ln = re.sub(r"present=\d+", f"present={present}", ln)
            # the kernel's own faults= counter is never incremented (it cannot catch its own
            # crash); substitute the agent's count, which is the honest number
            m = re.match(r"\s*SHADOW (\S+)", ln)
            if m and faults.get(m.group(1)):
                ln = re.sub(r"faults=\d+", f"faults={faults[m.group(1)]}", ln)
        out.append(ln)
    _WEDGE.note_manifest(text)
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
            # carries the wedge verdict so the Lab can tell the student to reboot (we never do)
            self._send({"ok": True, "wedge": _WEDGE.state()})
        elif path == "/programs":
            self._send({"programs": PROGRAMS})
        elif path == "/procs":                       # fast, NO-halt process table (Ctrl-P)
            txt = _SERIAL.procdump()
            _WEDGE.note_dump(txt)                    # the liveness heartbeat (~2/s)
            self._send(txt, ctype="text/plain")
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
        elif path == "/locks":                        # Ctrl-L -> gini_lockdump() (contention)
            self._send(_SERIAL.dump(b"\x0c"), ctype="text/plain")
        elif path == "/traps":
            self._send(_SERIAL.dump(b"\x12"), ctype="text/plain")   # Ctrl-R -> gini_trapdump()
        elif path == "/board":                        # Ctrl-D -> gini_boarddump(): the kernel map
            # The board is by far the largest dump — 14 subsystem lines, the call matrix, our own
            # observation matrix, a 64-entry trail and, when armed, up to 128 path hops. On a slow
            # host, or under `grind`, it does not always finish inside the 0.35 s the small dumps
            # need, and a truncated dump parses to nothing.
            #
            # Raising the ceiling costs nothing now that dump() returns the moment the sentinel
            # lands: `wait` bounds a stall, it is not a sleep.
            self._send(_SERIAL.dump(b"\x04", wait=1.5), ctype="text/plain")
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
        elif u.path == "/control/priority":       # CONTROL-PLANE: set a proc's scheduling priority
            try:
                pid = int(q.get("pid", ["0"])[0]); v = int(q.get("v", ["10"])[0])
            except ValueError:
                pid, v = 0, 10
            # Ctrl-O + "<pid> <v>" + newline -> gini_setprio() from consoleintr (no shell scheduling)
            ok = pid > 2 and _SERIAL.write(f"\x0f{pid} {v}\n")
            self._send({"ok": ok, "pid": pid, "priority": v})
        elif u.path == "/control/tickets":        # CONTROL-PLANE: set a proc's lottery ticket count
            try:
                pid = int(q.get("pid", ["0"])[0]); n = int(q.get("n", ["1"])[0])
            except ValueError:
                pid, n = 0, 1
            ok = pid > 2 and _SERIAL.write(f"\x0e{pid} {n}\n")   # Ctrl-N + "<pid> <n>" + newline
            self._send({"ok": ok, "pid": pid, "tickets": n})
        elif u.path == "/input":                      # raw console input (the in-app console)
            self._send({"ok": _SERIAL.write(self._body())})
        elif u.path in ("/interrupt", "/break"):      # Ctrl-C: kernel breaks a hung foreground
            self._send({"ok": _SERIAL.write("\x03")})  # console driver handles it, not sh
        elif u.path == "/shadow/enable":
            # Toggle a shadow BY INDEX (kernel Ctrl-G <digits><terminator>). /shadow/toggle only
            # ever reached the current scheduler policy; the harness needs to drive vm/fs shadows
            # too, and to enable exactly ONE at a time so a measurement isolates that shadow.
            i = (parse_qs(u.query).get("i", ["0"])[0] or "0").strip()
            ok = i.isdigit() and _SERIAL.write("\x07" + i + "\n")
            self._send({"ok": bool(ok), "index": i})
        elif u.path == "/shadow/toggle":              # flip the CURRENT policy's shadow on/off
            self._send({"ok": _SERIAL.write("\x0b")})  # Ctrl-K (the bridge sets policy first)
        elif u.path == "/reboot":                     # student-initiated reset: relaunch QEMU only
            # No `make` — this reboots the CURRENT kernel. A full process restart (not a QEMU
            # `system_reset`) so the kernel image in RAM is pristine even if a wedged kernel
            # scribbled over its own text. Shadows come back OFF: gini_shadow[] initialises
            # enabled=0, and nothing here re-enables them — which is exactly what a student
            # recovering from a wedge needs.
            _QEMU.restart()
            _WEDGE.rebooted()
            self._send({"ok": True, "shadows": "disabled after reboot"})
        elif u.path == "/rebuild":                    # Load: incremental make + restart QEMU
            ok, log = _rebuild()
            self._send({"ok": ok, "log": log})
        elif u.path == "/revert":                     # restore the shipped stub(s), then rebuild
            # ?sub=vm reverts just that subsystem's file; no sub reverts all of them
            sub = (parse_qs(u.query).get("sub", [""])[0] or "").strip()
            ok, log = _revert(sub)
            self._send({"ok": ok, "log": log})
        elif u.path == "/locks/reset":                # Ctrl-Z -> zero the lock counters
            self._send({"ok": bool(_SERIAL.write("\x1a"))})
        elif u.path == "/console/clear":              # blank the Screen (baseline to now)
            _SERIAL.clear_console()
            self._send({"ok": True})
        else:
            self._send({"error": "not found"})


if __name__ == "__main__":
    # Seed EVERY shadow file if the bind-mounted host folder is empty (Load loop): the mount hides
    # the image's kernel/shadows/*.c, so copy each pristine stub back in — the pre-built
    # kernel/kernel still boots, and the student's edits + Load rebuild from these files.
    # (Missing any one of them would fail the link, since all three .o are in the Makefile.)
    for _path, _ref in SHADOWS.values():
        if not os.path.exists(_path) and os.path.exists(_ref):
            try:
                os.makedirs(os.path.dirname(_path), exist_ok=True)
                shutil.copyfile(_ref, _path)
            except Exception:
                pass
    _QEMU.start()                                     # launch the kernel; the agent stays PID 1
    HTTPServer(("0.0.0.0", 5000), Handler).serve_forever()
