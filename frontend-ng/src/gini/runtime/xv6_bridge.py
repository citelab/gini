"""xv6 live bridge (Mac side) — fills MachineState from the in-container agent over HTTP.

The gdb reads happen INSIDE the gini-xv6 container (backend/xv6/gini_agent.py), where gdb and
the kernel symbols live; this bridge just makes plain HTTP calls to the agent's published port
and parses the returned text with the already-tested domain parsers. It is a drop-in for the
offline DemoScheduler: snapshot()/step()/set_timeslice()/set_policy() plus `.vm`/`.fs` readers,
so MachineState, the Machine Lab, and the Ask GINI card use it unchanged.

The HTTP calls are injectable (`get=`/`post=`) so the compose + parsing logic is unit-tested
without a running container; the VM/FS readers fall back to the demo shape on any error, so the
Memory/Storage dialogs always open even if the live read momentarily fails.
"""
from __future__ import annotations

import json
import urllib.request
from urllib.parse import quote

from ..domain.xv6 import (
    Snapshot, apply_proc_sched, apply_waits, parse_backtrace, parse_cpu_lines, parse_cpu_regs,
    parse_csr,
    parse_modetime, parse_policies, parse_procdump, parse_registers, parse_regs_line, parse_sched,
    parse_shadow_manifest, running_pid,
)
from ..domain.xv6_fs import (
    FsSnapshot, Superblock, layout, parse_balloc, parse_bcache, parse_logheader,
    parse_superblock,
)
from ..domain.xv6_vm import VmSnapshot, parse_faults, parse_vmall, parse_vmprint

# must match gini_pick() in gini_patch.py: 0=round-robin 1=priority 2=lottery. Custom student
# policies (MLFQ, stride, …) get their own ids when added via the Scheduler Builder.
POLICY_ID = {"round-robin": 0, "priority": 1, "lottery": 2}
# each shadow overrides the policy at this index (the kernel toggles the CURRENT policy's shadow).
SHADOW_POLICY = {"rr_sched": 0, "prio_sched": 1, "lottery_sched": 2}


class AgentClient:
    """HTTP client for the in-container agent. `get`/`post` are injectable for tests."""

    # longer than the agent's own 6s gdb timeout, so a slow proc-walk under CPU load returns a
    # result (even an empty one we can ignore) instead of the client abandoning it mid-read.
    def __init__(self, base_url: str, get=None, post=None, timeout: float = 10.0) -> None:
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self._get = get or self._http_get
        self._post = post or self._http_post

    def _http_get(self, url: str) -> str:  # pragma: no cover - real network
        with urllib.request.urlopen(url, timeout=self.timeout) as r:
            return r.read().decode(errors="replace")

    def _http_post(self, url: str, body: str = "") -> str:  # pragma: no cover - real network
        req = urllib.request.Request(url, data=body.encode() if body else None, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return r.read().decode(errors="replace")

    def get_json(self, path: str) -> dict:
        try:
            return json.loads(self._get(self.base + path))
        except Exception:
            return {}

    def get_text(self, path: str) -> str:
        try:
            return self._get(self.base + path)
        except Exception:
            return ""

    def post(self, path: str, body: str = "") -> str:
        try:
            return self._post(self.base + path, body) if body else self._post(self.base + path)
        except TypeError:
            return self._post(self.base + path)          # injected posts without a body arg
        except Exception:
            return ""


class _VmReader:
    def __init__(self, agent: AgentClient) -> None:
        self.agent = agent

    def snapshot(self):
        # REAL only: return the live page table, or an explicit no-data VmSnapshot. Never fake.
        # The region map + physical-allocator bar aren't dumped for real yet, so they're left
        # empty (not in `have`) rather than borrowed from the demo.
        try:
            vm = parse_vmprint(self.agent.get_text("/vm"))
            if vm.leaves:                               # got real mappings
                vm.source, vm.ok, vm.have = "real", True, ("pagetable",)
                return vm
        except Exception:
            pass
        return VmSnapshot(source="real", ok=False, have=())

    def all_procs(self):
        """Every user process's page table (gini_vmdump_all) -> {pid: ProcVm}, for the two-proc
        COW / sharing view. REAL only: empty dict if the read fails (the face shows an error)."""
        try:
            procs = parse_vmall(self.agent.get_text("/vmall"))
            if procs:
                return procs
        except Exception:
            pass
        return {}

    def faults(self):
        """The live page-fault ring (gini_faultdump) -> [PageFault]. REAL only: empty list if the
        read fails. Classification happens in the Memory face against the all-procs picture."""
        try:
            f = parse_faults(self.agent.get_text("/faults"))
            if f:
                return f
        except Exception:
            pass
        return []


class _FsReader:
    def __init__(self, agent: AgentClient) -> None:
        self.agent = agent

    def snapshot(self):
        # REAL only: superblock + log are dumped for real today; inodes/dir/bcache are NOT, so
        # they stay empty and out of `have` (the face marks them "not available", never fakes).
        # A failed read returns ok=False so the face shows an error instead of demo data.
        try:
            txt = self.agent.get_text("/fs")            # gini_fsdump over the serial (no gdb)
            sb = parse_superblock(txt)
            if sb.size:
                log = parse_logheader(txt, start=sb.logstart, size=sb.nlog)
                # the buffer cache is only dumped by kernels built with the bcache telemetry;
                # when it IS there, declare it in `have` so the panel switches from "not
                # available (real)" to live data
                bc = parse_bcache(txt)
                ba = parse_balloc(txt)          # allocator counters + the free/used block map
                have = ("layout", "log") + (("bcache",) if bc else ()) \
                       + (("blockmap",) if ba else ())
                return FsSnapshot(sb=sb, regions=layout(sb), log=log,
                                  source="real", ok=True, have=have, **bc, **ba)
        except Exception:
            pass
        return FsSnapshot(sb=Superblock(), source="real", ok=False, have=())


class Xv6Bridge:
    """The live provider (talks HTTP to the in-container agent)."""

    def __init__(self, agent: AgentClient, quantum: int = 1) -> None:
        self.agent = agent
        self.timeslice = int(quantum)
        self.policy = "round-robin"
        self.vm = _VmReader(agent)
        self.fs = _FsReader(agent)
        self._seq = 0
        self._last_cpu = None       # register/stack detail (from gdb) — reused between fast reads
        self._last_stack: list = []
        self.kernel_quantum = None  # the kernel's ACTUAL sched_quantum (from the SCHED line)
        self.kernel_policy = None   # the kernel's ACTUAL sched_policy id (from the SCHED line)
        self.kernel_policies = {}   # {id: name} roster (POLICY lines) — drives the UI selector

    def snapshot(self) -> Snapshot:
        """The Run/observe read: gini_dump() over the serial gives BOTH the process table AND
        the running process's live registers in one shot — FAST and NO halt, so scheduling keeps
        running and registers (sp/satp change per process!) update live. The kernel stack (bt)
        still needs gdb, so it's only refreshed on Step; here we reuse the last one."""
        self._seq += 1
        txt = self.agent.get_text("/procs")
        procs = apply_proc_sched(parse_procdump(txt), txt)   # procs + priority/tickets/level/aging
        apply_waits(procs, txt)                              # ...and what each blocked one waits for
        sched = parse_sched(txt)                     # the kernel's ACTUAL quantum + policy
        if sched:
            self.kernel_quantum = sched.get("quantum")
            self.kernel_policy = sched.get("policy")
        roster = parse_policies(txt)                  # {id: name}; drives the UI policy selector
        if roster:
            self.kernel_policies = roster
        cpu = parse_regs_line(txt)                  # live registers from the same no-halt dump
        if cpu.regs:
            self._last_cpu = cpu
        else:
            cpu = self._last_cpu                     # CPU idle / no REGS line -> keep last
        return Snapshot(procs=procs, running_pid=running_pid(procs), ticks=self._seq,
                        cpu=cpu, stack=self._last_stack, cpus=parse_cpu_lines(txt),
                        cpu_regs=parse_cpu_regs(txt),
                        modetime=parse_modetime(txt), csr=parse_csr(txt))

    def _detail_snapshot(self) -> Snapshot:
        """A full gdb read (halts the guest briefly): registers + kernel stack + a proc walk.
        Used on open and on Step, where a momentary halt is expected."""
        d = self.agent.get_json("/snapshot")
        self._last_cpu = parse_registers(d.get("registers", ""))
        self._last_stack = parse_backtrace(d.get("bt", ""))
        procs = parse_procdump(d.get("procs", ""))
        return Snapshot(procs=procs, running_pid=running_pid(procs), ticks=self._seq,
                        cpu=self._last_cpu, stack=self._last_stack)

    def step(self) -> Snapshot:
        self.agent.post("/step")
        self._seq += 1
        return self._detail_snapshot()              # halted at swtch -> full frozen detail

    def set_timeslice(self, ticks: int) -> None:
        self.timeslice = int(ticks)
        self.agent.post(f"/control?quantum={int(ticks)}")

    def set_policy(self, policy: str) -> None:
        self.policy = policy
        # map the display name -> id via the live kernel roster (so a NEW policy works), falling
        # back to the built-in ids for the shipped three.
        by_name = {name: pid for pid, name in self.kernel_policies.items()}
        pid = by_name.get(policy, POLICY_ID.get(policy, 0))
        self.agent.post(f"/control?policy={pid}")

    # -- shadows + control-plane operations (NOT system calls) --------------------- #
    def shadows(self) -> dict:
        """The shadow manifest -> {name: ShadowStatus}: which student shadows are present, enabled,
        active, and fault-free. The liveness signal the assignment oracle checks first."""
        return parse_shadow_manifest(self.agent.get_text("/shadows"))

    def set_shadow(self, name: str, on: bool) -> None:
        """Enable/disable a shadow. The kernel only knows how to toggle the CURRENT policy's shadow,
        so we make that policy active, then toggle iff its state doesn't already match — all the
        naming/targeting logic lives here (testable) and the kernel stays dumb."""
        idx = SHADOW_POLICY.get(name)
        if idx is None:
            return
        self.agent.post(f"/control?policy={idx}")            # make that policy active
        cur = self.shadows().get(name)
        if cur is None or bool(cur.enabled) != bool(on):
            self.agent.post("/shadow/toggle")                # flip the current policy's shadow

    def set_priority(self, pid: int, value: int) -> None:
        """Control-plane op (not a syscall): set a process's scheduling priority so the workload
        has real differences to schedule on."""
        self.agent.post(f"/control/priority?pid={int(pid)}&v={int(value)}")

    def set_tickets(self, pid: int, n: int) -> None:
        """Control-plane op (not a syscall): set a process's lottery ticket count."""
        self.agent.post(f"/control/tickets?pid={int(pid)}&n={int(n)}")

    def load(self) -> tuple[bool, str]:
        """The Load loop: rebuild the kernel in the container (incremental make) and restart QEMU
        with the new kernel. Returns (ok, log) — log carries compile errors scoped to the student's
        shadow file on failure."""
        try:
            d = json.loads(self.agent.post("/rebuild"))
            return bool(d.get("ok")), str(d.get("log", ""))
        except Exception as e:
            return False, f"load failed: {e}"

    def revert(self) -> tuple[bool, str]:
        """Restore the shipped stub shadow and rebuild — one click back to a known-good kernel."""
        try:
            d = json.loads(self.agent.post("/revert"))
            return bool(d.get("ok")), str(d.get("log", ""))
        except Exception as e:
            return False, f"revert failed: {e}"

    def reboot(self) -> tuple[bool, str]:
        """Reset the machine: relaunch QEMU on the CURRENT kernel (no rebuild). Shadows come back
        DISABLED — the kernel initialises them off — so this is the way out of a wedge caused by a
        student's shadow, and the way to start an experiment from a clean boot."""
        try:
            d = json.loads(self.agent.post("/reboot"))
            return bool(d.get("ok")), "rebooted — shadows are off until you enable them again"
        except Exception as e:
            return False, f"reboot failed: {e}"

    def wedge(self) -> dict:
        """Agent's liveness verdict: {wedged, quiet_s, blamed[], faults{}, panic}. `wedged` means
        the kernel stopped answering dumps — GINI reports it and the student presses Reboot; we
        never reboot on their behalf."""
        try:
            return self.agent.get_json("/health").get("wedge", {}) or {}
        except Exception:
            return {}

    # -- programs (launch / kill / console) via the in-container agent's serial ---- #
    def programs(self) -> list:
        return self.agent.get_json("/programs").get("programs", [])

    def run(self, prog: str, args: str = "") -> bool:
        """Launch a program in the background. `args` is optional and is sanitised agent-side —
        several programs are useless without it (sgrind's whole lesson is the number K).

        A refusal is recorded in `last_run_error` rather than thrown away. The agent refuses a
        program its image was built before (it carries its own allow-list), and with nothing on
        screen that reads exactly as the program simply not launching."""
        self.last_run_error = ""
        try:
            q = f"/run?prog={quote(prog)}" + (f"&args={quote(args)}" if args else "")
            r = json.loads(self.agent.post(q))
            if not r.get("ok"):
                self.last_run_error = r.get("error") or f"could not launch {prog}"
            return bool(r.get("ok"))
        except Exception as e:
            self.last_run_error = f"could not reach the xv6 agent: {e}"
            return False

    def kill(self, pid: int) -> None:
        self.agent.post(f"/kill?pid={int(pid)}")

    def sc(self) -> str:
        """Raw gini_scdump text (SC counts + TRACE ring) for the histogram + strace panels."""
        return self.agent.get_text("/sc")

    def traps(self) -> str:
        """Raw gini_trapdump text (TC per-kind counters + TR trap ring) for the Traps face."""
        return self.agent.get_text("/traps")

    def catch_trap(self, kind: str = "any"):
        """Freeze the next live user trap (gdb /trapcatch) and parse it into a TrapFrame — the
        real scause/sepc/stval + saved user registers that seed the CPU journey. `kind` conditions
        the breakpoint (pagefault/syscall/timer/illegal/device). Returns a not-ok TrapFrame on
        timeout/idle, so the journey falls back to its authored captions."""
        from ..domain.xv6 import parse_trapframe
        raw = self.agent.post(f"/trapcatch?kind={kind}")
        txt = ""
        try:
            txt = json.loads(raw).get("out", "")
        except Exception:
            txt = raw or ""
        return parse_trapframe(txt)

    def alarms(self) -> str:
        """Raw gini_dump text (contains the per-proc `ALARM …` lines) for the sigalarm-lab strip."""
        return self.agent.get_text("/procs")

    def console(self) -> str:
        return self.agent.get_text("/console")

    def console_since(self, since: int = 0) -> tuple[str, int]:
        """Append-only console delta for the Terminal: (new text after `since`, new cursor). The
        Terminal keeps the cursor and appends, so output streams in instead of re-rendering."""
        d = self.agent.get_json(f"/console/stream?since={int(since)}")
        return d.get("text", ""), int(d.get("next", since))

    def clear_console(self) -> None:
        """Blank the Screen: tell the agent to move its console baseline to 'now', so the tail
        starts fresh (the kernel keeps running; this only affects what the console view shows)."""
        self.agent.post("/console/clear")

    def send_input(self, text: str) -> None:
        self.agent.post("/input", body=text)

    def interrupt(self) -> None:
        """Break a hung foreground program (xv6 has no Ctrl-C/SIGINT). Sends the kernel's break
        control-char over the serial; the console driver handles it directly (not sh), so it works
        even while a foreground process has the shell blocked in wait()."""
        self.agent.post("/interrupt")


def connect(agent_port: int, host: str = "127.0.0.1", quantum: int = 1) -> Xv6Bridge:
    """Build a live bridge to a running gini-xv6 container's agent (published `agent_port`)."""
    return Xv6Bridge(AgentClient(f"http://{host}:{agent_port}"), quantum=quantum)
