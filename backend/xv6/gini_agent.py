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
  /step      (POST) -> break swtch; continue; read regs+bt+procs WHILE HALTED; detach
  #                    -> {"switched": bool, "registers","bt","procs","ticks"} (frozen at swtch)
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
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

# console_mux ships beside this file (in the container both are /opt/*). Add our own directory so
# `import console_mux` resolves in the container AND when a test loads gini_agent.py by path — the
# arm bytes must come from the ONE proven encoder, never a second inline copy that could drift.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import console_mux                                                              # noqa: E402

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
GINI_NPOLICY = 3     # scheduler policies 0..2 (RR/priority/lottery); mirrors defs.h GINI_NPOLICY.
#                      Bounds ?policy=N so a bad value clamps instead of arming a partial entry.
_LOCK = threading.Lock()   # one gdb at a time (defensive; also serialises stub access)

# long-running programs the Machine Lab offers to launch (gini_patch.py adds spin/alloc/writer;
# grind/forktest are stock). init/sh are never launchable/killable.
# Keep in step with _LAUNCHABLE in ui/machine_lab.py — the UI offers this list, the agent accepts
# it, and a name in one but not the other is either a dead menu entry or a launch that is refused.
PROGRAMS = ["spin", "busy", "walker", "toucher", "alloc", "writer", "sgrind", "mgrind",
            "grind", "forktest"]


def _safe_args(s: str) -> str:
    """Sanitise the argument string for a launch.

    /run writes its command into a real shell, so an unfiltered argument string is a command
    injection: `walker 48; rm -rf /` would be obeyed. Several programs are useless without their
    argument though — sgrind's whole lesson is the number K — so the arguments are allowed but
    reduced to what those programs actually take: lowercase words and numbers, nothing else. No
    shell metacharacters survive, and there is a length cap so a long string cannot wedge the
    line-buffered console.
    """
    out = [c for c in s.strip().lower() if c.isalnum() or c == " "]
    return " ".join("".join(out).split())[:32]


class SerialLink:
    """Owns the single QEMU serial connection (QEMU allows one client), so the agent can inject
    shell commands (launch/kill) AND expose the console — the human console goes through here too,
    since a second raw client would be refused. Reconnects on drop; buffers recent output."""

    #: Class-level default so an instance built by `__new__` (which the serial tests do, to get a
    #: link with no reader thread) can never reach dump() without it and raise AttributeError.
    #: False is also the SAFE default: it only ever permits the legacy raw-byte fallback.
    _ever_framed = False

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
        # Has this kernel EVER bracketed a dump? Latched, never cleared — see dump(). It decides
        # whether a timeout may fall back to raw bytes, and only a pre-marker kernel may.
        self._ever_framed = False
        self._sock = None
        self._lock = threading.Lock()
        self._closed = threading.Event()      # see close(); never set in the container
        threading.Thread(target=self._reader, daemon=True).start()

    def close(self):
        """Stop the reader thread.

        Nothing in the container calls this: the agent is its own process and the link lasts as
        long as it does. It exists for the test suite, which constructs a SerialLink to feed
        `_ingest` directly — and a reader that cannot stop kept retrying a connection nobody was
        listening for, for the rest of the pytest session. Five tests were leaving one behind
        each.
        """
        self._closed.set()
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

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
                    self._ever_framed = True     # this kernel frames; the fallback is now unsafe
                else:
                    self._cap.append(b)
            elif b == DUMP_START:
                self._in_dump = True
                self._cap = bytearray()
            else:
                self.console.append(b)
                self._console_total += 1

    def _reader(self):
        while not self._closed.is_set():
            if self._sock is None:
                self._connect()
                if self._sock is None:
                    self._closed.wait(1)      # sleep, but wake at once on close()
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
            if self._ever_framed:
                # This kernel brackets its dumps, so a timeout means the dump did NOT arrive —
                # not that it arrived unframed. Returning the raw window here hands the caller
                # the console as if it were kernel data: walker's lap lines, grind's ABAB, and
                # whatever partial frame was in flight. parse_procdump then finds fewer PROC
                # lines than there are processes and a row vanishes from the table for one poll
                # — the "process blinks out" report, exactly.
                #
                # And it fires precisely when a student is doing what the lab asked: a CPU-bound
                # program delays the console interrupt and shares the UART with the dump's ~2 KB,
                # which is what makes the frame miss its 0.35 s deadline in the first place.
                #
                # Say nothing instead. MachineState._ingest refuses an empty process list and
                # keeps the last good snapshot, so the face holds still rather than lying.
                return ""
            n = self._total - before                      # pre-marker kernel -> raw window
            if n <= 0:
                return ""
            return bytes(self.buf)[-min(n, len(self.buf)):].decode(errors="replace")

    def procdump(self, wait=0.35):
        return self.dump(b"\x14", wait)     # Ctrl-T -> gini_dump() (procs + running-proc regs)


_SERIAL = SerialLink(SERIAL)


# --------------------------------------------------------------------------- #
# Kernel source, read-only — what the GINI Source tab browses.
#
# Serving from INSIDE the container is the whole point: /opt/xv6-riscv is the PATCHED tree this
# kernel was compiled from, so a student who double-clicks `bcache` on the board sees
#
#     bread(uint dev, uint blockno)
#     {
#       GINI_SUB(GSUB_BCACHE);  // GINI-xv6: board probe bread
#
# and can see why the board knows what it knows. Shipping pristine xv6 from the repo would be
# showing a different program than the one on screen.
#
# READ-ONLY, deliberately. Editing kernel source belongs to the shadow files, which have Load and
# Revert and a workflow around them; a second, editable path into the same tree would be a good
# way to leave a student with a kernel that will not build and no idea why.
# --------------------------------------------------------------------------- #
_SRC_ROOT = os.path.realpath(XV6_DIR)
_SRC_SUFFIXES = (".c", ".h", ".S", ".ld", ".pl")
_SRC_MAX = 512 * 1024


def _read_source(query):
    """Serve one file from the kernel tree. "Give me a file by name" is a path-traversal hole
    unless it is nailed shut, so this resolves the real path and refuses anything that escapes
    the tree — `..`, absolute paths and symlinks out all land outside _SRC_ROOT and are rejected
    by the same check rather than by three separate ones."""
    rel = (query.get("file") or [""])[0]
    if not rel:
        return "// no file requested"
    target = os.path.realpath(os.path.join(_SRC_ROOT, rel))
    if target != _SRC_ROOT and not target.startswith(_SRC_ROOT + os.sep):
        return "// refused: outside the kernel tree"
    if not target.endswith(_SRC_SUFFIXES):
        return "// refused: not a source file"
    try:
        if os.path.getsize(target) > _SRC_MAX:
            return "// refused: too large"
        with open(target, "r", errors="replace") as fh:
            return fh.read()
    except FileNotFoundError:
        return f"// not found: {rel}"
    except OSError as e:
        return f"// unreadable: {e}"


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

    def restart(self, on_down=None):
        """Cycle QEMU. `on_down` runs while it is stopped — the only moment the files QEMU holds
        open can be replaced safely. That window is what the disk-image swap below needs."""
        self.stop()
        if on_down is not None:
            on_down()
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


# A toolchain INVOCATION, which `make` echoes for every file — each about 700 characters of
# -fno-builtin flags. A tool name followed by WHITESPACE is a command; the same name followed by
# a COLON is that tool reporting a problem (`riscv64-unknown-elf-ld -z …` versus
# `riscv64-unknown-elf-ld: undefined reference …`). That distinction is the whole trick here.
_CMD = re.compile(r"^\s*(riscv64-[\w.-]+|gcc|ld|perl|sed|awk|mkfs/\w+|python3|cp|mv|rm|make\[)\s")

# What a student actually needs: the diagnostic lines, and the source echo + caret gcc prints
# underneath them (`  124 |   return notdeclared + mask;` / `      |          ^~~~~~~~~~~`), which
# are the most useful part of a gcc error and which the old filter dropped on the floor.
_DIAG = re.compile(
    r"^\S+:\d+(:\d+)?:\s"          # file:line[:col]: error|warning|note: …
    r"|^\s*\d+\s*\|"               # the source line echoed under a diagnostic
    r"|^\s*\|\s*[\^~]"             # the caret/tilde line under that
    r"|^(make|cc1|collect2|as):"    # make's "Error 1", cc1's "all warnings being treated as errors"
    r"|^\S*ld:"                     # a LINKER diagnostic — carries no "error:" anywhere
    r"|undefined reference|multiple definition|No rule to make target"
    r"|In function|At top level"
)

# Properties of how xv6 is linked and how the Makefile invokes gcc, not of the student's code.
# Both appear on EVERY failure, and a report that always carries the same two irrelevant lines
# teaches a student to skim past the ones that matter.
_BENIGN = ("has a LOAD segment with RWX permissions",
           "unrecognized command-line option '-Wno-unknown-attributes'")


def _scope_errors(log: str) -> str:
    """Reduce a failed `make` to the lines that tell the student what is wrong.

    The rule this replaced kept lines containing "gini_sched" or "error:". Building a real
    syscall lab showed it failing in both directions, and the second way is the damaging one:

    * A LINKER failure carries no "error:" at all. Forgetting `entry("sysinfo");` in user/usys.pl
      is the single most common mistake in this lab, and its whole message is
      `undefined reference to 'sysinfo'` — which was dropped.
    * With nothing kept, the fallback returned the tail of the raw log, and the kernel's link
      COMMAND names kernel/shadows/gini_sched.o. So that 580-character `ld` invocation matched
      "gini_sched" and became the entire report: the student's only feedback pointed at a file
      they had never opened, and the real error was not shown anywhere.

    So: drop the invocations, keep the diagnostics. This is deliberately generic — it names no
    lab file, so it reads a shadow build, a syscall build and anything later the same way.
    """
    keep = []
    for ln in log.splitlines():
        if not ln.strip() or _CMD.match(ln) or any(b in ln for b in _BENIGN):
            continue
        if _DIAG.search(ln):
            keep.append(ln.rstrip())
    return "\n".join(keep[-40:]) or log.strip()[-2000:]


FS_IMG = XV6_DIR + "/fs.img"       # the disk QEMU has open
FS_NEW = XV6_DIR + "/fs-new.img"   # a finished image waiting for the next restart window
FS_LIVE = XV6_DIR + "/fs-live.img"  # where the live image is parked while mkfs runs


def _make(targets, timeout=180):
    """Run one `make` in the xv6 tree. Returns (ok, scoped log)."""
    try:
        r = subprocess.run(["make", *targets], cwd=XV6_DIR,
                           capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        return False, f"build error: {e}"
    if r.returncode != 0:
        return False, _scope_errors(r.stdout + r.stderr)
    return True, ""


def _fs_is_stale() -> bool:
    """Would `make` rebuild fs.img? `make -q` answers without building anything.

    Asked FIRST, because the answer is usually no. A Load that only touched a kernel file must
    not regenerate the disk — that would silently delete any file the student created inside
    xv6 during this session, which is not what "rebuild my kernel" means.
    """
    try:
        r = subprocess.run(["make", "-q", "fs.img"], cwd=XV6_DIR,
                           capture_output=True, text=True, timeout=60)
    except Exception:
        return True                      # cannot tell -> rebuild, the safe direction
    return r.returncode != 0             # 0 = up to date; 1 = out of date; 2 = error


def _stage_fs():
    """Build the new disk image WITHOUT writing to the one the running QEMU has open.

    `mkfs` opens fs.img O_TRUNC and writes two megabytes into it. QEMU has that same file open
    read-write and may write its own dirty blocks back at any moment, so `make fs.img` under a
    live QEMU is two writers on one inode — and the loser is the disk the student is about to
    boot. It survived every test run, which is exactly what makes it worth removing: a race that
    usually looks like it worked is the kind that fails in a lab, once, unreproducibly.

    The fix uses the same distinction as the rest of this system: an open file is an INODE, a
    build target is a PATH. Renaming the live image leaves QEMU's open file exactly where it was
    — same inode, still its disk — while `make` creates a brand-new file at the path. The two
    writers are now two different files and cannot meet. The finished image is parked as
    fs-new.img and swapped in while QEMU is stopped.

    Returns (ok, log, staged) — `staged` says whether there is an image waiting to be swapped.
    """
    if not _fs_is_stale():
        return True, "", False
    parked = False
    try:
        if os.path.exists(FS_IMG):
            os.replace(FS_IMG, FS_LIVE)          # QEMU keeps the inode; the PATH is now free
            parked = True
    except Exception as e:
        return False, f"could not park the live disk image: {e}", False
    ok, log = _make(["fs.img"])
    try:
        if ok:
            os.replace(FS_IMG, FS_NEW)           # the new image waits for the restart
        elif os.path.exists(FS_IMG):
            os.remove(FS_IMG)                    # a half-written image is worse than none
        if parked:
            os.replace(FS_LIVE, FS_IMG)          # the tree is consistent again either way
    except Exception as e:
        return False, f"could not stage the new disk image: {e}", False
    return ok, log, ok


def _swap_fs():
    """Move the staged image into place. Only ever called with QEMU stopped."""
    try:
        if os.path.exists(FS_NEW):
            os.replace(FS_NEW, FS_IMG)
    except Exception:
        pass                                     # QEMU boots the old disk; the kernel is new


def _recover_disk_image():
    """Put the disk back together after an agent that died mid-swap.

    Both intermediate names are unambiguous, which is what makes this safe to do unattended:
    fs-live.img exists only while mkfs is running, and fs-new.img only ever holds a COMPLETED
    image (it is named on success, never before). So: restore the live disk if the path is
    empty, then apply a build that never reached its restart. Without this the container could
    come up with no fs.img at all and QEMU would fail to open its drive.
    """
    try:
        if not os.path.exists(FS_IMG) and os.path.exists(FS_LIVE):
            os.replace(FS_LIVE, FS_IMG)
        if os.path.exists(FS_NEW):
            os.replace(FS_NEW, FS_IMG)
    except Exception:
        pass


def _touch_sources():
    """Make the container the last writer of every file the student can edit.

    A bind mount caches file attributes, and on at least one engine (Docker Desktop on macOS,
    virtiofs) a file just written on the HOST still reports its OLD mtime inside the container
    for a moment afterwards. `make` compares that stale timestamp against the .o, decides it is
    up to date, and builds nothing — so /rebuild restarts QEMU on the PREVIOUS kernel while
    reporting {"ok": true, "log": "loaded"}.

    That is the worst failure this loop can produce. It is silent, it survives a retry often
    enough to look like the student's fault, and the symptom it produces — `unknown sys call 23`
    from a syscall they can see in their own syscall.h — sends them looking in exactly the wrong
    place. Reproduced with backend/xv6/lab_feasibility.sh, which now prints it as
    "make: 'kernel/kernel' is up to date." at the moment the student pressed Load.

    Touching from INSIDE the container updates that cache as a side effect of the write, so the
    timestamp make reads is the one we just set. The cost is recompiling the handful of files
    the student owns on every Load — about a second — and in exchange `make` can no longer
    conclude that a Load has nothing to do.
    """
    for path, _ref in SHADOWS.values():
        try:
            if os.path.exists(path):
                os.utime(path, None)
        except Exception:
            pass                     # a read-only file just keeps make's own answer


def _rebuild():
    """Incremental `make`, then restart QEMU on the result. Returns (ok, log).

    Two makes rather than one, because the kernel and the disk image fail differently and are
    replaced differently. The kernel is read once at boot, so overwriting kernel/kernel under a
    live QEMU is harmless; the disk is held open for the life of the machine, so it is built
    out of place and swapped in during the restart (see _stage_fs). On any failure QEMU is left
    alone — a student whose code does not compile keeps the machine they had.
    """
    _touch_sources()
    ok, log = _make(["kernel/kernel"])
    if not ok:
        return False, log
    ok, log, staged = _stage_fs()
    if not ok:
        return False, log
    _QEMU.restart(on_down=_swap_fs if staged else None)
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


# One-shot trap capture, kernel-side (see gini_traprec + the CATCH dump line). The Traps face ARMS
# a kind through the console mux; the kernel copies the next matching trap into gini_catch; we poll
# the trapdump (Ctrl-R, non-perturbing) until CATCH is ready and its kind matches. This replaced a
# gdb conditional breakpoint at usertrap that could only see USER-mode traps and, by halting the
# guest to test its condition, drove the very timer it hunted into kernel mode (uncatchable). The
# capture sees both modes and does not perturb what it measures. See docs/design/xv6-rebuild-batch
# §11.
_CATCH_KIND = {"syscall": 0, "pagefault": 1, "timer": 2, "device": 3,
               "illegal": 4, "other": 5, "any": 9}     # 9 = "any" on the wire (kernel -> ANY)

_CATCH_RE = re.compile(
    r"^CATCH (\d+) (\d+) (\d+) "                                             # ready kind from_user
    r"(0x[0-9a-fA-F]+) (0x[0-9a-fA-F]+) (0x[0-9a-fA-F]+) (-?\d+) "           # cause epc tval pid
    r"(0x[0-9a-fA-F]+) (0x[0-9a-fA-F]+) (0x[0-9a-fA-F]+) (0x[0-9a-fA-F]+) "  # tf: epc ra sp a0
    r"(0x[0-9a-fA-F]+) (0x[0-9a-fA-F]+) (0x[0-9a-fA-F]+) "                   # tf: a1 a2 a7
    r"(-?\d+) (-?\d+) h(\d+)", re.M)                                         # qticks quantum hart


def _catch_to_trapframe(m):
    """A matched CATCH line -> the ===TRAP=== text `parse_trapframe` already accepts, plus the new
    keys (from_user/hart/qticks/quantum). Keeps the frontend parser untouched and skew-safe."""
    g = m.groups()
    return ("===TRAP===\n"
            f"scause {g[3]}\nsepc {g[4]}\nstval {g[5]}\npid {g[6]}\n"
            f"from_user {g[2]}\nhart {g[16]}\nqticks {g[14]}\nquantum {g[15]}\n"
            f"epc {g[7]}\nra {g[8]}\nsp {g[9]}\n"
            f"a0 {g[10]}\na1 {g[11]}\na2 {g[12]}\na7 {g[13]}\n")


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
        elif path == "/shadows":              # Ctrl-W Ctrl-W -> gini_shadowdump() (mux self-escape;
            #                                   bare Ctrl-W is now the command-mux prefix — §4f5)
            self._send(_stamp_manifest(_SERIAL.dump(b"\x17\x17")), ctype="text/plain")  # hash-stamped
        elif path == "/vmall":
            self._send(_SERIAL.dump(b"\x01"), ctype="text/plain")   # Ctrl-A -> gini_vmdump_all()
        elif path == "/faults":
            self._send(_SERIAL.dump(b"\x05"), ctype="text/plain")   # Ctrl-E -> gini_faultdump()
        elif path == "/locks":                        # Ctrl-L -> gini_lockdump() (contention)
            self._send(_SERIAL.dump(b"\x0c"), ctype="text/plain")
        elif path == "/programs":
            # What THIS agent will accept. The agent ships inside the image, so after a program is
            # added to the source the running container still holds the old list until the image is
            # rebuilt — and a launch then fails for a reason nothing on screen explains. This
            # endpoint makes that mismatch visible instead of mysterious.
            self._send({"programs": PROGRAMS})
        elif path == "/source":
            self._send(_read_source(parse_qs(urlparse(self.path).query)), ctype="text/plain")
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
            # ONE gdb session does BOTH halves: halt at swtch, then read registers + kernel
            # backtrace + the proc walk WHILE STILL HALTED, then detach. The old code ran two
            # sessions — `tbreak swtch; continue` here and a separate `/snapshot` afterwards —
            # but gdb_run always appends `detach`, so the first session resumed the guest before
            # the second could read, and the read landed on an arbitrary later instant instead
            # of the switch. This is known issue #11: "Step does not freeze what it shows." A
            # single session keeps the kernel stopped across the read, so the detail is frozen at
            # the switch. The temporary breakpoint auto-deletes on hit.
            #
            # Timeout scales with the quantum: at the default slice TIMEOUT is fine, but the UI
            # offers a 10-tick slice where gdb spawn + symbol load + waiting a whole slice for the
            # next switch can exceed 6 s (the "second squeeze" in the writeup). Floor at TIMEOUT so
            # the common case is unchanged; a truly idle kernel (no swtch at all) still times out.
            try:
                qt = int(q.get("quantum", ["1"])[0] or "1")
            except ValueError:
                qt = 1
            step_timeout = min(20.0, TIMEOUT + 0.6 * max(0, qt - 1))
            out = gdb_run(["tbreak swtch", "continue",
                           "info registers", "echo ===BT===\\n", "bt",
                           "echo ===PROCS===\\n", _PROC_WALK,
                           "echo ===TICKS===\\n", "printf \"%d\\n\", ticks"],
                          timeout=step_timeout)
            # A switch was actually caught iff the read ran — a timeout returns the bare
            # "gdb-timeout" string with none of the section markers. Reporting switched=False
            # (rather than a snapshot that looks like a result) lets the UI say "no switch
            # happened" instead of presenting the idle scheduler stack as a captured switch.
            switched = "===BT===" in out
            regs, _, rest = out.partition("===BT===")
            bt, _, rest = rest.partition("===PROCS===")
            procs, _, ticks = rest.partition("===TICKS===")
            self._send({"switched": switched, "registers": regs, "bt": bt,
                        "procs": procs, "ticks": ticks.strip()})
        elif u.path == "/trapcatch":
            # Arm the kernel-side capture for ?kind= (default any), then poll the trapdump until a
            # matching trap is caught or ?wait= seconds elapse. No gdb: instant arm, no guest stall.
            name = (q.get("kind", ["any"])[0] or "any").strip().lower()
            if name not in _CATCH_KIND:
                self._send({"ok": False, "error": "unknown kind %r; use one of %s"
                            % (name, ", ".join(sorted(_CATCH_KIND)))})
                return
            want = _CATCH_KIND[name]
            try:
                wait = max(1.0, min(30.0, float(q.get("wait", ["10"])[0])))
            except ValueError:
                wait = 10.0
            # ARM via the ONE proven encoder (console_mux). bytes are <0x80, so latin-1 round-trips
            # through SerialLink.write's .encode().
            _SERIAL.write(console_mux.encode("arm_trap", want).decode("latin-1"))
            print(f"[trapcatch] armed {name} (wire {want}), wait {wait:.0f}s", flush=True)
            deadline = time.time() + wait
            while time.time() < deadline:
                # Ctrl-R -> gini_trapdump: non-perturbing, and non-destructive (ready stays set).
                m = _CATCH_RE.search(_SERIAL.dump(b"\x12") or "")
                # ready AND the captured kind matches what we armed — the kind-match closes the mild
                # re-arm race (a stale capture of another kind is ignored, not returned).
                if m and m.group(1) == "1" and (want == 9 or int(m.group(2)) == want):
                    print("[trapcatch] caught kind %s pid %s h%s"
                          % (m.group(2), m.group(7), m.group(17)), flush=True)
                    self._send({"ok": True, "out": _catch_to_trapframe(m)})
                    return
                time.sleep(0.25)
            reason = (f"armed for {name}; no matching trap in {wait:.0f}s — the machine may be idle, "
                      f"or the ticks are landing on another hart")
            print(f"[trapcatch] timeout: {reason}", flush=True)
            self._send({"ok": False, "error": reason})
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
                # set sched_policy over the serial: Ctrl-B then the digits then newline — ONE
                # terminated entry the kernel's gini_polidx machine consumes atomically (0=RR
                # 1=priority 2=lottery). The old scheme (Ctrl-B then N×Ctrl-G) drove policy up
                # with Ctrl-G, but Ctrl-G is the shadow-index prefix, so every policy above 0
                # silently toggled a shadow instead of switching policy (known issue #1). No
                # pending state is left on the wire, so a following /procs poll cannot finish it.
                try:
                    pv = max(0, min(GINI_NPOLICY - 1, int(q["policy"][0])))
                except ValueError:
                    pv = 0
                _SERIAL.write("\x02" + str(pv) + "\n")   # Ctrl-B <digits> newline
                out = (out + f" policy={pv}").strip()
            self._send({"ok": True, "out": out})
        elif u.path == "/run":                       # launch a program in the background
            prog = (q.get("prog", [""])[0] or "").strip()
            args = _safe_args(q.get("args", [""])[0] or "")
            cmd = (prog + (" " + args if args else "")) + " &\n"
            if not prog:
                self._send({"ok": False, "prog": prog, "error": "no program named"})
            elif prog not in PROGRAMS:
                # Almost always a stale container: the name was added to the source but this image
                # predates it. Say so, rather than returning a bare false that looks like a hang.
                self._send({"ok": False, "prog": prog, "error":
                            f"this xv6 image does not know '{prog}' — it was built before the "
                            f"program was added. Rebuild the image. Known: "
                            f"{', '.join(PROGRAMS)}"})
            else:
                self._send({"ok": bool(_SERIAL.write(cmd)), "prog": prog, "args": args})
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
    _recover_disk_image()
    _QEMU.start()                                     # launch the kernel; the agent stays PID 1
    HTTPServer(("0.0.0.0", 5000), Handler).serve_forever()
