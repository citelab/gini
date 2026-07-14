"""xv6 live bridge (Mac side) — HTTP calls to the in-container agent + Snapshot composition,
with a fake agent (no container needed)."""
import json

from gini.domain.machine_state import MachineState
from gini.runtime.xv6_bridge import AgentClient, Xv6Bridge, connect

REGS = "pc  0x80001d4a  0x80001d4a\nsp  0x3fffff9e00  0x3fffff9e00\nsatp 0x8000000000087fff\n"
BT = "#0  0x0000000080001d4a in scheduler () at kernel/proc.c:451\n"
# the agent's gdb proc-walk emits full state words (parse_procdump accepts them)
PROCS = "1 sleeping init\n2 sleeping sh\n3 running spin\n4 runnable spin\n"
# gini_dump (Ctrl-T, no halt): the process table PLUS the running proc's registers (REGS line)
PROCDUMP = ("\n1 sleep  init\n2 sleep  sh\n3 runble spin\n4 run    spin\n"
            "SCHED policy 0 quantum 7\n"
            "REGS pid 4 pc 0x80002000 sp 0x3fffff9000 ra 0x80001d3c a0 0x4 a7 0x7 "
            "satp 0x8000000000087004 sz 0x4000\n")
# gini_fsdump over the serial (Ctrl-F): superblock line + write-ahead-log line, no gdb
FSDUMP = ("size = 2000 nblocks = 1954 ninodes = 200 nlog = 30 logstart = 2 "
          "inodestart = 32 bmapstart = 45\n"
          "LOG start = 2 outstanding = 1 committing = 0 n = 2 block = {45, 587, }\n")
VMPRINT = "page table 0x87f6e000\n .. .. ..0: pte 0x21fdac0b pa 0x87f6b000\n"


class FakeAgent:
    """Serves canned JSON/text for the agent endpoints and records posts."""

    def __init__(self):
        self.posts = []

    def get(self, url):
        if url.endswith("/snapshot"):
            return json.dumps({"registers": REGS, "bt": BT, "procs": PROCS, "ticks": "42"})
        if url.endswith("/procs"):
            return PROCDUMP
        if url.endswith("/vm"):
            return VMPRINT
        if url.endswith("/fs"):
            return FSDUMP
        return "{}"

    def post(self, url):
        self.posts.append(url)
        return "{}"


def _bridge():
    fa = FakeAgent()
    agent = AgentClient("http://x:5000", get=fa.get, post=fa.post)
    return Xv6Bridge(agent, quantum=1), fa


def test_run_read_gives_procs_and_live_registers_no_gdb():
    # the Run read uses gini_dump (/procs): process table + the running proc's LIVE registers,
    # with NO gdb halt. Registers (sp/satp differ per process) come straight off the serial.
    br, _ = _bridge()
    snap = br.snapshot()
    assert [p.pid for p in snap.procs] == [1, 2, 3, 4]
    assert snap.running_pid == 4                        # gini_dump says pid 4 is RUNNING
    assert snap.cpu.key("sp") == "0x3fffff9000"         # live regs from the REGS line
    assert snap.cpu.key("satp") == "0x8000000000087004"
    assert snap.ticks == 1


def test_registers_persist_when_cpu_briefly_idle():
    # if a read has no REGS line (CPU idle), keep the last registers rather than blanking
    class Idle(FakeAgent):
        def get(self, url):
            if url.endswith("/procs"):
                return "\n1 sleep init\n2 sleep sh\n"   # no running proc -> no REGS line
            return super().get(url)
    br = Xv6Bridge(AgentClient("http://x", get=FakeAgent().get, post=lambda u: "{}"))
    br.snapshot()                                       # gets registers
    br.agent = AgentClient("http://x", get=Idle().get, post=lambda u: "{}")
    snap = br.snapshot()                                # idle read
    assert snap.cpu.key("sp") == "0x3fffff9000"         # kept from the previous read


def test_step_takes_full_detail_after_swtch():
    br, fa = _bridge()
    br.snapshot()
    fa.posts.clear()
    snap = br.step()
    assert any(u.endswith("/step") for u in fa.posts)  # halted at a context switch
    assert snap.cpu.key("pc") == "0x80001d4a"          # fresh gdb detail at the switch


def test_controls_post_to_agent():
    br, fa = _bridge()
    br.set_timeslice(10)
    br.step()
    assert any("/control?quantum=10" in u for u in fa.posts)
    assert any(u.endswith("/step") for u in fa.posts)


def test_kernel_quantum_read_from_sched_line():
    # the bridge surfaces the kernel's ACTUAL quantum (from gini_dump's SCHED line), so the UI
    # can confirm the slider took effect
    br, _ = _bridge()
    br.snapshot()
    assert br.kernel_quantum == 7


def test_vm_and_fs_readers_parse_serial_dumps_no_gdb():
    br, _ = _bridge()
    fs = br.fs.snapshot()
    assert fs.sb.size == 2000 and fs.log.blocks == [45, 587]
    assert fs.log.outstanding == 1 and fs.log.phase == "building"   # real log state parsed
    vm = br.vm.snapshot()
    assert vm.leaves and vm.regions and vm.phys.total_pages > 0


def test_readers_fall_back_to_demo_on_agent_failure():
    # a dead agent (get raises) must NOT break the Memory/Storage dialogs
    def boom(url):
        raise OSError("connection refused")
    agent = AgentClient("http://x:5000", get=boom, post=lambda u: "")
    br = Xv6Bridge(agent)
    assert br.fs.snapshot().sb.size > 0               # demo fallback
    assert br.vm.snapshot().regions                   # demo fallback
    assert br.snapshot().procs == []                  # scheduler honestly empty when agent down


def test_bridge_is_a_drop_in_for_machinestate():
    br, _ = _bridge()
    ms = MachineState(br, device_id="d1", vm=br.vm, fs=br.fs)
    for _ in range(3):
        ms.step()
    card = ms.card(level=2)
    assert "running: pid 3" in card and "file system:" in card


def test_connect_builds_a_bridge():
    br = connect(38000, quantum=5)
    assert isinstance(br, Xv6Bridge) and br.timeslice == 5


def test_program_launch_kill_and_console():
    posts = []

    def get(url):
        if url.endswith("/programs"):
            return json.dumps({"programs": ["spin", "alloc"]})
        if url.endswith("/console"):
            return "$ spin &\n"
        return "{}"

    def post(url, body=""):
        posts.append((url, body))
        if "/run" in url:
            return json.dumps({"ok": True, "prog": "spin"})
        return json.dumps({"ok": True})

    br = Xv6Bridge(AgentClient("http://x:5000", get=get, post=post))
    assert br.programs() == ["spin", "alloc"]
    assert br.run("spin") is True
    br.kill(7)
    br.send_input("ls\n")
    assert "spin &" in br.console()
    assert any("/run?prog=spin" in u for u, _ in posts)
    assert any("/kill?pid=7" in u for u, _ in posts)
    assert any(u.endswith("/input") and b == "ls\n" for u, b in posts)


def test_clear_console_posts_to_agent():
    br, fa = _bridge()
    br.clear_console()
    assert any(u.endswith("/console/clear") for u in fa.posts)


def test_interrupt_posts_to_agent():
    br, fa = _bridge()
    br.interrupt()
    assert any(u.endswith("/interrupt") for u in fa.posts)


def test_console_since_streams_delta():
    def get(url):
        if "/console/stream" in url:
            return json.dumps({"text": "README cat\n", "next": 42})
        return "{}"
    br = Xv6Bridge(AgentClient("http://x:5000", get=get, post=lambda u: "{}"))
    text, nxt = br.console_since(10)
    assert text == "README cat\n" and nxt == 42
