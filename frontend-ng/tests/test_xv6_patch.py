"""The xv6 kernel patcher (backend/xv6/gini_patch.py) applies cleanly and is idempotent."""
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "backend" / "xv6" / "gini_patch.py"

# current xv6-riscv spacing: `if (which_dev == 2)` and kerneltrap without the RUNNING clause
TRAP_C = """#include "types.h"
#include "proc.h"
#include "defs.h"

struct spinlock tickslock;

uint64 usertrap(void){
  struct proc *p = myproc();
  p->trapframe->epc = r_sepc();
  // give up the CPU if this is a timer interrupt.
  if (which_dev == 2)
    yield();
  return satp;
}
void kerneltrap(){
  int which_dev = 0;
  uint64 scause = r_scause();
  // give up the CPU if this is a timer interrupt.
  if (which_dev == 2 && myproc() != 0)
    yield();
}
void clockintr(){
  w_stimecmp(r_time() + 1000000);
}
"""
PROC_C = ("struct proc proc[NPROC];\nstruct proc *initproc;\n"
          "static struct proc* allocproc(void){\n  p->state = USED;\n  return p;\n}\n"
          "void scheduler(void){}\n")
PROC_H = ("struct proc {\n  int pid;\n  char name[16];   // Process name (debugging)\n};\n")
DEFS_H = "void printk(char*, ...);\nint mappages(pagetable_t, uint64);\n"
CONSOLE_C = ("void consoleintr(int c){\n  switch(c){\n"
             "  case C('P'):\n    procdump();\n    break;\n  }\n}\n")
MAKEFILE = "UPROGS=\\\n\t$U/_cat\\\n\t$U/_echo\\\n"


@pytest.mark.skipif(not SCRIPT.exists(), reason="backend/xv6/gini_patch.py not present")
def test_patcher_applies_and_is_idempotent(tmp_path):
    k = tmp_path / "kernel"
    k.mkdir()
    (tmp_path / "user").mkdir()
    (k / "trap.c").write_text(TRAP_C)
    (k / "proc.c").write_text(PROC_C)
    (k / "proc.h").write_text(PROC_H)
    (k / "vm.c").write_text("void kvminit(){}\n")
    (k / "defs.h").write_text(DEFS_H)
    (k / "console.c").write_text(CONSOLE_C)
    (k / "fs.c").write_text("struct superblock sb;\n")
    (k / "log.c").write_text("struct log log;\n")
    (tmp_path / "Makefile").write_text(MAKEFILE)

    def run():
        return subprocess.run([sys.executable, str(SCRIPT), str(tmp_path)],
                              capture_output=True, text=True)

    r = run()
    assert r.returncode == 0, r.stderr
    proc = (k / "proc.c").read_text()
    trap = (k / "trap.c").read_text()
    assert "sched_quantum" in proc and "gini_pick" in proc
    assert trap.count("GINI-xv6 quantum") == 2          # both usertrap + kerneltrap guarded
    assert "gini_qticks[cpuid()] >= sched_quantum" in trap   # PER-CPU counter (SMP-correct)
    # mode-time accounting: counters declared before the trap fns, sampled by privilege source
    # (usertrap => user tick, kerneltrap => idle when no proc else kernel). The CPU face reads
    # the delta as a user/kernel/idle split.
    assert "uint64 gini_ut, gini_kt, gini_it;" in trap
    assert trap.index("uint64 gini_ut, gini_kt, gini_it;") < trap.index("uint64 usertrap(void)")
    assert "gini_ut++;" in trap                              # user-mode timer tick
    assert "if (myproc() == 0) gini_it++; else gini_kt++;" in trap   # idle vs kernel tick
    # gini_dump emits the counters + this hart's control CSRs (trap vector, interrupt config, cause)
    assert "MODETIME user %d kernel %d idle %d" in proc
    assert "CSR sstatus %p sie %p sip %p stvec %p scause %p sepc %p" in proc
    assert "extern uint64 gini_ut, gini_kt, gini_it;" in proc
    assert "w_stimecmp(r_time() + 5000000);" in trap    # ~0.5s tick, semicolon intact
    # the counter must be DECLARED before the functions that use it (else C won't compile)
    assert trap.index("int gini_qticks[NCPU];") < trap.index("usertrap")
    vm = (k / "vm.c").read_text()
    assert "vmprint" in vm and 'printk("page table' in vm    # uses the detected print fn
    assert "printf" not in vm                                # not the wrong (older) name
    assert "extern int      sched_quantum" in (k / "defs.h").read_text()

    # console.c: the GINI dumps are added AND bracketed with 0x1e/0x1f (via %c) so the agent can
    # hide them from the human console; the native Ctrl-P procdump stays plain. Nine bracketed
    # dumps now: procs(T), page table(V), fs(F), syscalls(S), all-procs VM(A), fault ring(E),
    # trap ring(R), shadow manifest(W), lock contention(L).
    con = (k / "console.c").read_text()
    assert "gini_dump();" in con and "gini_vmdump();" in con and "gini_fsdump();" in con
    assert "gini_vmdump_all();" in con and "gini_faultdump();" in con
    assert "case C('A')" in con and "case C('E')" in con
    assert "case C('W')" in con and "gini_shadowdump();" in con   # the shadow manifest dump
    assert "case C('L')" in con and "gini_lockdump();" in con     # lock contention (Lock Lab)
    # every bracketed dump must be BALANCED — an unmatched 30/31 would corrupt the frame the
    # agent splits on, so compare the counts to each other rather than to a magic number
    assert con.count('printk("%c",30)') == con.count('printk("%c",31)') >= 9
    assert "case C('\\\\')" in con                           # quantum reset key intact
    assert "case C('C'): gini_break();" in con              # Ctrl-C -> break a hung foreground
    assert "gini_break" in (k / "proc.c").read_text()       # the kernel-side break function
    assert "void            gini_break(void);" in (k / "defs.h").read_text()
    # control-plane kill: pid-carrying state machine in consoleintr (Ctrl-Y + digits) + the kernel
    # fn, so the Kill button fires from the interrupt instead of waiting for the shell to schedule.
    assert "gini_killpid" in con and "if(c == C('Y'))" in con and "gini_kill(gini_killpid)" in con
    assert "gini_kill(int pid)" in (k / "proc.c").read_text() and "[gini] killed pid" in proc
    assert "void            gini_kill(int);" in (k / "defs.h").read_text()
    # control-plane per-proc scheduling setters (priority/tickets) — two-number console entry so the
    # priority + lottery experiments have real differences to schedule on
    assert "gini_setprio(int pid, int v)" in proc and "gini_setticket(int pid, int n)" in proc
    assert "gini_ctl_op" in con and "if(c == C('O'))" in con and "if(c == C('N'))" in con
    assert "gini_setprio(gini_ctl_pid, gini_ctl_val)" in con
    defs2 = (k / "defs.h").read_text()
    assert "void            gini_setprio(int, int);" in defs2
    assert "void            gini_setticket(int, int);" in defs2

    # VM/paging additions: the live fault ring (trap.c) + the usertrap capture hook + the
    # all-procs page-table dump (proc.c) + their defs.h prototypes.
    assert "struct gini_flt" in trap and "gini_faultdump" in trap and "gini_fault_note" in trap
    assert "gini_fault_note(); // GINI-xv6: record page faults" in trap   # the usertrap hook fired
    assert "gini_vmdump_all" in proc and "gini_leafwalk" in proc
    defs = (k / "defs.h").read_text()
    assert "void            gini_faultdump(void);" in defs
    assert "void            gini_vmdump_all(void);" in defs

    # trap-taxonomy ring (trap.c): counters + ring + classifier + dump, the usertrap capture hook
    # (recorded at the SAME early anchor as the fault ring so fatal traps are caught too), the
    # Ctrl-R console case, and the defs.h prototypes.
    assert "gini_trapcount" in trap and "gini_traprec" in trap and "gini_trapdump" in trap
    assert "GT_TIMER" in trap and "GT_PAGEFAULT" in trap        # the scause classifier
    assert "gini_traprec(); // GINI-xv6: record the trap" in trap   # the usertrap hook fired
    # captured before the fault ring, both after the saved-PC line, before the timer-yield guard
    assert trap.index("gini_traprec();") < trap.index("gini_qticks[cpuid()]")
    assert "case C('R'): printk(\"%c\",30); gini_trapdump();" in con
    assert "void            gini_trapdump(void);" in defs
    assert "extern uint64   gini_trapcount[6];" in defs
    assert "struct gini_trap { int pid; int kind;" in defs

    # Phase 4: kerneltrap also records (device interrupts), anchored on kerneltrap's `scause`; the
    # REGS dump gains s0 (the frame pointer, for the backtrace lab).
    assert "gini_traprec(); // GINI-xv6: record kernel-mode traps" in trap
    assert "s0 %p" in proc and "(void*)tf->s0" in proc          # frame pointer in the REGS line

    # Phase 3: the sigalarm-lab fields (proc.h, GINI-owned so the dump always compiles) + their
    # allocproc defaults + the per-proc ALARM dump line.
    proch = (k / "proc.h").read_text()
    assert "gini_alarm_handler" in proch and "gini_alarm_interval" in proch
    assert "gini_alarm_on" in proch
    assert "p->gini_alarm_handler = 0;" in proc                 # zeroed in allocproc
    assert "ALARM %d %d %d %p %d" in proc                       # the dump line the strip reads

    # spin/busy take an optional seconds argument (launch via the Keyboard, e.g. `spin 10 &`)
    spin = (tmp_path / "user" / "spin.c").read_text()
    assert "argc > 1" in spin and "uptime()" in spin and "atoi(argv[1])" in spin

    run()                                                # idempotent: second run doesn't duplicate
    assert (k / "proc.c").read_text().count("gini_pick(void)") == 1
    assert (k / "console.c").read_text().count("case C('T')") == 1
    assert (k / "console.c").read_text().count("case C('R')") == 1
    assert (k / "trap.c").read_text().count("gini_traprec(void)") == 1     # ring defined once
    trap2 = (k / "trap.c").read_text()
    assert trap2.count("record the trap into the taxonomy ring") == 1      # usertrap hook once
    assert trap2.count("record kernel-mode traps") == 1                    # kerneltrap hook once
    assert (k / "proc.h").read_text().count("gini_alarm_handler;") == 1    # alarm fields once
