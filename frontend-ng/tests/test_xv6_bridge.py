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
# gini_vmdump_all (Ctrl-A): parent+child sharing the data page (same PA, COW-tagged RSW bit)
VMALL = ("VP 3 forktest 0x6000\nVL 3 0x0 0x87001000 27\nVL 3 0x1000 0x87002000 275\n"
         "VP 4 forktest 0x6000\nVL 4 0x0 0x87001000 27\nVL 4 0x1000 0x87002000 275\n")
# gini_faultdump (Ctrl-E): a store fault on the shared data page (the COW trigger)
FAULTS = "FLT 4 15 0x1000 0x0000000000001abc\nFLT 3 13 0x40000000 0x2100\n"


class FakeAgent:
    """Serves canned JSON/text for the agent endpoints and records posts."""

    def __init__(self):
        self.posts = []

    def get(self, url):
        if url.endswith("/snapshot"):
            return json.dumps({"registers": REGS, "bt": BT, "procs": PROCS, "ticks": "42"})
        if url.endswith("/procs"):
            return PROCDUMP
        if url.endswith("/vmall"):
            return VMALL
        if url.endswith("/faults"):
            return FAULTS
        if url.endswith("/traps"):
            return "TC 0 syscall 12\nTC 2 timer 88\nTR 5 2 0x8000000000000005 0x1050 0x0\n"
        if url.endswith("/vm"):
            return VMPRINT
        if url.endswith("/fs"):
            return FSDUMP
        return "{}"

    def post(self, url):
        self.posts.append(url)
        if url.endswith("/trapcatch"):
            return json.dumps({"out": (
                "===TRAP===\nscause 0x000000000000000f\nsepc 0x1080\nstval 0x4000\n"
                "pid 5\na7 0x000000000000000f\n")})
        return "{}"


def _bridge():
    fa = FakeAgent()
    agent = AgentClient("http://x:5000", get=fa.get, post=fa.post)
    return Xv6Bridge(agent, quantum=1), fa


def test_vm_reader_all_procs_and_faults():
    from gini.domain.xv6_vm import classify_faults, shared_frames
    br, _ = _bridge()
    procs = br.vm.all_procs()                            # gini_vmdump_all over /vmall
    assert set(procs) == {3, 4}
    assert shared_frames(procs)[0x87002000] == [3, 4]    # parent+child share the data page
    faults = br.vm.faults()                              # gini_faultdump over /faults
    classify_faults(faults, procs)
    assert faults[0].kind == "cow-write"                 # store to the shared read-only page
    assert faults[0].va == 0x1000


def test_traps_reads_the_taxonomy_ring():
    from gini.domain.xv6 import parse_trapcounts, parse_traptrace
    br, _ = _bridge()
    txt = br.traps()                                     # gini_trapdump over /traps
    assert parse_trapcounts(txt) == {0: 12, 2: 88}
    assert parse_traptrace(txt)[0].kind == 2             # a timer interrupt in the feed


def test_catch_trap_parses_a_frozen_frame():
    br, fa = _bridge()
    fr = br.catch_trap()                                 # POST /trapcatch -> gdb freeze -> TrapFrame
    assert "/trapcatch" in fa.posts[-1]
    assert fr.ok and fr.kind == 1 and fr.pid == 5        # scause 15 -> store page fault
    assert fr.stval == "0x4000" and fr.regs.get("a7") == "0x000000000000000f"


def test_real_vm_reader_never_fakes_on_empty_read():
    # REAL mode is honest: an empty read returns no-data, NOT the demo pair. The face shows an
    # error + a "switch to Demo" hint rather than silently passing off fake page tables as real.
    from gini.runtime.xv6_bridge import AgentClient, Xv6Bridge
    agent = AgentClient("http://x:5000", get=lambda u: "", post=lambda u: "{}")
    br = Xv6Bridge(agent)
    assert br.vm.all_procs() == {}                        # empty, not the demo {3,4}
    assert br.vm.faults() == []                           # empty, not demo faults
    vm = br.vm.snapshot()
    assert vm.ok is False and vm.source == "real" and vm.leaves == []


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


def test_load_and_revert():
    class Builder(FakeAgent):
        def post(self, url):
            self.posts.append(url)
            if url.endswith("/rebuild"):
                return json.dumps({"ok": True, "log": "loaded"})
            if url.endswith("/revert"):
                return json.dumps({"ok": False, "log": "kernel/shadows/gini_sched.c:22: error: x"})
            return "{}"
    fa = Builder()
    br = Xv6Bridge(AgentClient("http://x", get=fa.get, post=fa.post))
    ok, log = br.load()
    assert ok is True and log == "loaded"
    assert any(u.endswith("/rebuild") for u in fa.posts)
    ok2, log2 = br.revert()
    assert ok2 is False and "error:" in log2          # compile error surfaced to the UI


def test_shadows_and_control_plane_ops():
    manifest = ("SHADOW rr_sched present=0 enabled=0 active=0 faults=0 hash=baseline\n"
                "SHADOW prio_sched present=1 enabled=1 active=1 faults=0 hash=9f3a1c\n")

    class Shadowed(FakeAgent):
        def get(self, url):
            return manifest if url.endswith("/shadows") else super().get(url)
    fa = Shadowed()
    br = Xv6Bridge(AgentClient("http://x", get=fa.get, post=fa.post))
    sh = br.shadows()
    assert sh["prio_sched"].is_student and sh["prio_sched"].active
    assert not sh["rr_sched"].is_student
    br.set_shadow("prio_sched", True)         # prio shadow already enabled in the manifest...
    assert any("/control?policy=1" in u for u in fa.posts)   # ...so make priority active,
    assert not any("/shadow/toggle" in u for u in fa.posts)  # ...but no toggle (already on)
    fa.posts.clear()
    br.set_shadow("rr_sched", True)           # rr shadow is off -> switch policy + toggle it on
    assert any("/control?policy=0" in u for u in fa.posts)
    assert any("/shadow/toggle" in u for u in fa.posts)
    br.set_priority(5, 3)
    br.set_tickets(6, 8)
    assert any("/control/priority?pid=5&v=3" in u for u in fa.posts)
    assert any("/control/tickets?pid=6&n=8" in u for u in fa.posts)


def test_set_policy_posts_mapped_id():
    # switching policy posts the kernel's numeric id (round-robin=0, priority=1, lottery=2)
    br, fa = _bridge()
    br.set_policy("lottery")
    assert any("/control?policy=2" in u for u in fa.posts)
    assert br.policy == "lottery"


def test_kernel_policy_and_proc_sched_read():
    # gini_dump now carries per-proc scheduling fields (PROC lines) + the active policy id
    dump = ("\n3 run spin 2\nPROC 3 pri 5 tk 1 lv 0 wait 0\n"
            "4 runble spin 2\nPROC 4 pri 10 tk 2 lv 0 wait 6\n"
            "SCHED policy 1 quantum 3\n"
            "REGS pid 3 pc 0x80002000 sp 0x3fffff9000 satp 0x8000000000087003 sz 0x2000\n")

    class Sched(FakeAgent):
        def get(self, url):
            return dump if url.endswith("/procs") else super().get(url)
    br = Xv6Bridge(AgentClient("http://x", get=Sched().get, post=lambda u: "{}"))
    snap = br.snapshot()
    assert br.kernel_policy == 1                       # priority policy, read live
    by_pid = {p.pid: p for p in snap.procs}
    assert by_pid[3].priority == 5 and by_pid[3].tickets == 1
    assert by_pid[4].wait_ticks == 6                   # aging counter visible to the UI


def test_vm_and_fs_readers_parse_serial_dumps_no_gdb():
    br, _ = _bridge()
    fs = br.fs.snapshot()
    assert fs.sb.size == 2000 and fs.log.blocks == [45, 587]
    assert fs.log.outstanding == 1 and fs.log.phase == "building"   # real log state parsed
    assert fs.source == "real" and fs.ok and fs.have == ("layout", "log")
    assert fs.inodes == [] and fs.tree == [] and fs.bufs == []      # not dumped -> empty, NOT faked
    vm = br.vm.snapshot()
    assert vm.leaves and vm.source == "real" and vm.ok and vm.have == ("pagetable",)
    assert vm.regions == []                                         # region map not real yet -> empty


def test_demo_providers_are_labelled_demo():
    # so the UI can badge demo mode and never confuse it with real
    from gini.domain.xv6 import DemoScheduler
    from gini.domain.xv6_fs import DemoDisk
    from gini.domain.xv6_vm import DemoVm
    assert DemoScheduler().snapshot().source == "demo"
    fd = DemoDisk().snapshot()
    assert fd.source == "demo" and fd.ok and set(fd.have) >= {"inodes", "dir", "bcache"}
    vd = DemoVm().snapshot()
    assert vd.source == "demo" and vd.ok


def test_readers_are_honest_no_data_on_agent_failure():
    # a dead agent (get raises) must NOT invent demo data in REAL mode — it returns ok=False so
    # the face shows an error. (The dialogs still open; they render the error state, not fakes.)
    def boom(url):
        raise OSError("connection refused")
    agent = AgentClient("http://x:5000", get=boom, post=lambda u: "")
    br = Xv6Bridge(agent)
    fs = br.fs.snapshot()
    assert fs.ok is False and fs.source == "real" and fs.sb.size == 0    # no demo superblock
    vm = br.vm.snapshot()
    assert vm.ok is False and vm.source == "real" and vm.leaves == []
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
