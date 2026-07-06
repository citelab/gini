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

from ..domain.xv6 import (
    Snapshot, parse_backtrace, parse_cpu_lines, parse_cpu_regs, parse_procdump, parse_registers,
    parse_regs_line, parse_sched, running_pid,
)
from ..domain.xv6_fs import DemoDisk, FsSnapshot, Superblock, layout, parse_logheader, parse_superblock
from ..domain.xv6_vm import DemoVm, PhysMem, parse_vmprint

POLICY_ID = {"round-robin": 0, "priority": 1, "mlfq": 2, "lottery": 3}


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
        base = DemoVm().snapshot()                      # regions/allocator shape (demo fallback)
        try:
            vm = parse_vmprint(self.agent.get_text("/vm"))
            if vm.leaves:                               # got real mappings -> use them
                vm.regions = base.regions
                vm.phys = PhysMem(base.phys.total_pages, base.phys.free_pages)
                return vm
        except Exception:
            pass
        return base                                     # live read failed -> demo, dialog still opens


class _FsReader:
    def __init__(self, agent: AgentClient) -> None:
        self.agent = agent

    def snapshot(self):
        try:
            txt = self.agent.get_text("/fs")            # gini_fsdump over the serial (no gdb)
            sb = parse_superblock(txt)
            if sb.size:
                log = parse_logheader(txt, start=sb.logstart, size=sb.nlog)
                return FsSnapshot(sb=sb, regions=layout(sb), inodes=[], tree=[], bufs=[], log=log)
        except Exception:
            pass
        return DemoDisk().snapshot()                    # fallback so the Storage dialog opens


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

    def snapshot(self) -> Snapshot:
        """The Run/observe read: gini_dump() over the serial gives BOTH the process table AND
        the running process's live registers in one shot — FAST and NO halt, so scheduling keeps
        running and registers (sp/satp change per process!) update live. The kernel stack (bt)
        still needs gdb, so it's only refreshed on Step; here we reuse the last one."""
        self._seq += 1
        txt = self.agent.get_text("/procs")
        procs = parse_procdump(txt)
        sched = parse_sched(txt)                     # the kernel's ACTUAL quantum (confirms slider)
        if sched:
            self.kernel_quantum = sched.get("quantum")
        cpu = parse_regs_line(txt)                  # live registers from the same no-halt dump
        if cpu.regs:
            self._last_cpu = cpu
        else:
            cpu = self._last_cpu                     # CPU idle / no REGS line -> keep last
        return Snapshot(procs=procs, running_pid=running_pid(procs), ticks=self._seq,
                        cpu=cpu, stack=self._last_stack, cpus=parse_cpu_lines(txt),
                        cpu_regs=parse_cpu_regs(txt))

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
        self.agent.post(f"/control?policy={POLICY_ID.get(policy, 0)}")

    # -- programs (launch / kill / console) via the in-container agent's serial ---- #
    def programs(self) -> list:
        return self.agent.get_json("/programs").get("programs", [])

    def run(self, prog: str) -> bool:
        try:
            return bool(json.loads(self.agent.post(f"/run?prog={prog}")).get("ok"))
        except Exception:
            return False

    def kill(self, pid: int) -> None:
        self.agent.post(f"/kill?pid={int(pid)}")

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
