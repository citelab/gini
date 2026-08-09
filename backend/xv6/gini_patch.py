#!/usr/bin/env python3
"""Apply GINI's xv6 kernel changes robustly (anchored edits, not a context diff).

A `git apply` patch needs exact context lines and breaks across xv6 revisions. Instead we edit
by stable anchors, and every edit is designed so the kernel STILL COMPILES (under xv6's -Werror)
even if an invasive anchor isn't found:

  • append-only (always apply): scheduler globals + gini_pick(), vmprint(), and defs.h prototypes
    — all non-static, so no -Wunused-function under -Werror;
  • anchored regex (apply if found, else warn): the time-slice quantum in trap.c.

The one edit we DON'T automate is wiring scheduler() to call gini_pick() for live policy
switching — it's a 6-line restructure best done by hand (see XV6_BACKEND.md §4). Everything else,
including the headline settable time-slice and vmprint for the Memory face, applies here.

Usage:  python3 gini_patch.py [xv6-dir]   (default: current dir)
Idempotent: re-running is a no-op.
"""
import re
import sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
MARK = "GINI-xv6"
applied, skipped = [], []


def detect_print(root: Path) -> str:
    """The kernel's formatted-print function: 'printk' in current xv6-riscv, 'printf' in older."""
    defs = root / "kernel" / "defs.h"
    txt = defs.read_text() if defs.exists() else ""
    if "printk(" in txt:
        return "printk"
    if "printf(" in txt:
        return "printf"
    for name in ("printk.c", "printf.c"):
        if (root / "kernel" / name).exists():
            return name[:-2]
    return "printk"


PRINT = detect_print(ROOT)


def _read(rel):
    p = ROOT / rel
    return p, (p.read_text() if p.exists() else None)


def append_once(rel, text, marker):
    p, src = _read(rel)
    if src is None:
        skipped.append(f"{rel}: file not found")
        return
    if marker in src:
        applied.append(f"{rel}: already present")
        return
    p.write_text(src.rstrip() + "\n\n" + text.rstrip() + "\n")
    applied.append(f"{rel}: appended {marker}")


def regex_once(rel, pattern, repl, marker):
    p, src = _read(rel)
    if src is None:
        skipped.append(f"{rel}: file not found")
        return
    if marker in src:
        applied.append(f"{rel}: {marker} already present")
        return
    new, n = re.subn(pattern, repl, src, count=1)
    if n:
        p.write_text(new)
        applied.append(f"{rel}: applied {marker}")
    else:
        skipped.append(f"{rel}: anchor for {marker} not found — apply by hand (XV6_BACKEND.md §4)")


# 1) proc.c — control globals + the policy-aware picker (non-static; unused is fine).
append_once("kernel/proc.c", """
// GINI-xv6: scheduler control knobs (the Machine Lab bridge writes these live over gdb).
//   sched_policy: 0=round-robin 1=priority 2=mlfq 3=lottery
//   sched_quantum: timer ticks per time-slice before preemption.
int sched_policy = 0;
int sched_quantum = 1;

// GINI-xv6: choose the next RUNNABLE proc per sched_policy. Round-robin matches stock xv6.
// Wire scheduler() to call this (see XV6_BACKEND.md §4) to enable live policy switching.
struct proc *
gini_pick(void)
{
  struct proc *p;
  static int rr = 0;
  for(int i = 0; i < NPROC; i++){
    p = &proc[(rr + i) % NPROC];
    if(p->state == RUNNABLE){ rr = (rr + i + 1) % NPROC; return p; }
  }
  return 0;
}
""", "GINI-xv6: scheduler control knobs")

# 2) trap.c — the settable time-slice: preempt only every sched_quantum timer ticks. The
#    counter is PER-CPU (indexed by cpuid()) — a single global would be shared across harts, so
#    on SMP every core's timer bumps it and slices come out 1/ncpu too short. Declared before
#    usertrap()/kerneltrap() use it (right after the includes).
regex_once("kernel/trap.c",
           r'(#include "defs.h"\n)',
           r'\1\n// GINI-xv6: PER-CPU time-slice counter (see the which_dev==2 guards below).\n'
           r'int gini_qticks[NCPU];\nextern int sched_quantum;\n',
           "GINI-xv6: PER-CPU time-slice counter")

# usertrap(): `if (which_dev == 2)\n    yield();`  (space after `if` in current xv6). Interrupts
# are off at this point, so cpuid() is safe.
regex_once("kernel/trap.c",
           r"if\s*\(which_dev == 2\)\s*\n\s*yield\(\);",
           "if (which_dev == 2 && (++gini_qticks[cpuid()] >= sched_quantum)) "
           "{ gini_qticks[cpuid()] = 0; yield(); } // GINI-xv6 quantum",
           "GINI-xv6 quantum")

# kerneltrap(): `if (which_dev == 2 && myproc() != 0[ && ...])\n    yield();` — trailing
# `&& myproc()->state == RUNNING` was dropped in current xv6, so match it optionally.
regex_once("kernel/trap.c",
           r"if\s*\(which_dev == 2 && myproc\(\) != 0"
           r"(?: && myproc\(\)->state == RUNNING)?\)\s*\n\s*yield\(\);",
           "if (which_dev == 2 && myproc() != 0 && (++gini_qticks[cpuid()] >= sched_quantum)) "
           "{ gini_qticks[cpuid()] = 0; yield(); } // GINI-xv6 quantum k",
           "GINI-xv6 quantum k")

# 2b) clockintr() — set the timer TICK to ~0.5s (QEMU virt timebase is 10 MHz -> 5,000,000).
#     Match the trailing `;` too, so the // comment doesn't swallow the statement terminator.
regex_once("kernel/trap.c",
           r"w_stimecmp\(r_time\(\) \+ 1000000\);",
           "w_stimecmp(r_time() + 5000000); // GINI-xv6: ~0.5s tick",
           "GINI-xv6: ~0.5s tick")

# 3) vm.c — vmprint() for the Memory face (non-static; also the 6.1810 lab function).
#    Uses the kernel's actual print function (printk in current xv6, printf in older).
append_once("kernel/vm.c", """
// GINI-xv6: print a page table as a tree so the Memory face can read the leaf mappings.
void
gini_vmprint_walk(pagetable_t pt, int level)
{
  for(int i = 0; i < 512; i++){
    pte_t pte = pt[i];
    if(pte & PTE_V){
      for(int j = 0; j < level; j++) %(P)s(".. ");
      %(P)s("..%%d: pte %%p pa %%p\\n", i, (void*)pte, (void*)PTE2PA(pte));
      if((pte & (PTE_R|PTE_W|PTE_X)) == 0)
        gini_vmprint_walk((pagetable_t)PTE2PA(pte), level + 1);
    }
  }
}

void
vmprint(pagetable_t pt)
{
  %(P)s("page table %%p\\n", pt);
  gini_vmprint_walk(pt, 0);
}
""" % {"P": PRINT}, "GINI-xv6: print a page table")

# 4) defs.h — prototypes for the non-static additions. The syscall counter/ring types + externs
# live here (declared BEFORE syscall() uses them; defined in syscall.c).
append_once("kernel/defs.h", """
// GINI-xv6 additions
void            vmprint(pagetable_t);
void            gini_dump(void);
void            gini_vmdump(void);
void            gini_fsdump(void);
void            gini_logdump(void);
void            gini_scdump(void);
void            gini_break(void);
struct proc*    gini_pick(void);
extern int      sched_policy;
extern int      sched_quantum;
struct gini_sc { int pid; int num; uint64 a0; uint64 ret; };
extern uint64   gini_sccount[64];
extern struct gini_sc gini_ring[64];
extern int      gini_ring_i;
""", "GINI-xv6 additions")

# 4b) gini_dump(): the process table PLUS the running process's saved registers (from its
# trapframe) and page table — printed to the console. This lets the Machine Lab read LIVE
# registers over the serial (via Ctrl-T) WITHOUT halting the kernel through gdb.
_GINI_DUMP = '''
// GINI-xv6: like procdump, plus the RUNNING process's registers (from its trapframe) and its
// page table root — so the Machine Lab can read live registers over the serial, no gdb halt.
void
gini_dump(void)
{
  static char *states[] = {
  [UNUSED]    "unused",
  [USED]      "used",
  [SLEEPING]  "sleep ",
  [RUNNABLE]  "runble",
  [RUNNING]   "run   ",
  [ZOMBIE]    "zombie"
  };
  struct proc *p;
  PRINTF("\\n");
  for(p = proc; p < &proc[NPROC]; p++){
    if(p->state == UNUSED)
      continue;
    int s = p->state;
    char *st = (s >= 0 && s <= ZOMBIE && states[s]) ? states[s] : "???";
    // pid state name ppid  (ppid drives the process TREE; proc[] slots are never freed so
    // p->parent is always a valid pointer — best-effort read, no wait_lock needed for a dump).
    PRINTF("%d %s %s %d\\n", p->pid, st, p->name, p->parent ? p->parent->pid : 0);
  }
  PRINTF("SCHED policy %d quantum %d\\n", sched_policy, sched_quantum);
  // per-CPU: which pid each core runs (Gantt strips) + that proc's live registers (from its
  // trapframe) — so every CPU has its own register/memory view, not just one.
  for(int ci = 0; ci < NCPU; ci++){
    struct proc *rp = cpus[ci].proc;
    if(rp){
      PRINTF("CPU %d pid %d\\n", ci, rp->pid);
      if(rp->trapframe){
        struct trapframe *tf = rp->trapframe;
        PRINTF("REGS cpu %d pid %d pc %p sp %p ra %p a0 %p a7 %p satp %p sz %p\\n",
               ci, rp->pid, (void*)tf->epc, (void*)tf->sp, (void*)tf->ra,
               (void*)tf->a0, (void*)tf->a7,
               (void*)MAKE_SATP(rp->pagetable), (void*)rp->sz);
      }
    }
  }
}
'''
append_once("kernel/proc.c", _GINI_DUMP.replace("PRINTF", PRINT),
            "GINI-xv6: like procdump, plus")

# 4b') add the ppid column to STOCK procdump too (Ctrl-P / `ps`), so the console view matches
# the tree. Matches whichever print fn this xv6 uses (printf or printk); `state` var, no \\n here.
regex_once("kernel/proc.c",
           r'(print[kf])\("%d %s %s", p->pid, state, p->name\);',
           r'\1("%d %s %s %d", p->pid, state, p->name, p->parent ? p->parent->pid : 0);',
           '"%d %s %s %d", p->pid, state')

# 4d) gini_vmdump(): the running process's page table (via vmprint) to the console — the
# Memory face reads this over the serial instead of asking gdb to walk the page table.
append_once("kernel/proc.c", """
// GINI-xv6: print the RUNNING process's page table (leaf mappings) to the console, so the
// Memory face can read it over the serial with no gdb halt.
void
gini_vmdump(void)
{
  struct proc *p;
  for(p = proc; p < &proc[NPROC]; p++){
    if(p->state == RUNNING){
      vmprint(p->pagetable);
      return;
    }
  }
}
""", "GINI-xv6: print the RUNNING process's page table")

# 4d2) gini_break(): xv6 has NO Ctrl-C / SIGINT, so a foreground program blocks the shell forever.
# Break kills the highest-pid user process (the most-recently started — usually the foreground
# child), so sh's wait() returns and the prompt comes back. Called from the console interrupt
# handler (Ctrl-C), so it must NOT take p->lock — we hold cons.lock there and would deadlock
# against it; a direct killed=1 is benign (it only ever flips 0->1). The victim exits on its next
# timer trap (usertrap checks killed).
_GINI_BREAK = '''
// GINI-xv6: interrupt a hung foreground program (there is no Ctrl-C/SIGINT in xv6). Kills the
// highest-pid RUNNING/RUNNABLE user process so a blocked sh wait() returns.
void
gini_break(void)
{
  struct proc *victim = 0;
  for(struct proc *p = proc; p < &proc[NPROC]; p++){
    if(p->pid > 2 && (p->state == RUNNING || p->state == RUNNABLE)){
      if(victim == 0 || p->pid > victim->pid)
        victim = p;
    }
  }
  if(victim){
    victim->killed = 1;              // no p->lock here (cons.lock is held) — benign single write
    PRINTF("[gini] break: killed pid %d\\n", victim->pid);
  } else {
    PRINTF("[gini] break: nothing to interrupt\\n");
  }
}
'''
append_once("kernel/proc.c", _GINI_BREAK.replace("PRINTF", PRINT),
            "GINI-xv6: interrupt a hung foreground")

# 4e) gini_fsdump()/gini_logdump(): superblock (fs.c) + write-ahead log state (log.c) to the
# console, so the Storage face reads them over the serial. Emitted in the gdb-print format the
# existing parsers already accept (`size = N`, `n = N`, `block = {..}`).
_FSDUMP = '''
// GINI-xv6: print the superblock to the console (Storage face reads this over the serial).
void
gini_fsdump(void)
{
  PRINTF("size = %d nblocks = %d ninodes = %d nlog = %d logstart = %d "
         "inodestart = %d bmapstart = %d\\n",
         sb.size, sb.nblocks, sb.ninodes, sb.nlog, sb.logstart, sb.inodestart, sb.bmapstart);
  gini_logdump();
}
'''
append_once("kernel/fs.c", _FSDUMP.replace("PRINTF", PRINT), "GINI-xv6: print the superblock")

_LOGDUMP = '''
// GINI-xv6: print the write-ahead log's in-memory state (transaction blocks) to the console.
void
gini_logdump(void)
{
  PRINTF("LOG start = %d outstanding = %d committing = %d n = %d block = {",
         log.start, log.outstanding, log.committing, log.lh.n);
  for(int i = 0; i < log.lh.n && i < LOGBLOCKS; i++)
    PRINTF("%d, ", log.lh.block[i]);
  PRINTF("}\\n");
}
'''
append_once("kernel/log.c", _LOGDUMP.replace("PRINTF", PRINT), "GINI-xv6: print the write-ahead log")

# 4f) console.c — Ctrl-T (procs+regs), Ctrl-V (page table), Ctrl-F (fs) handlers, next to the
# stock Ctrl-P procdump. All no-halt serial dumps. Each GINI dump is BRACKETED with 0x1e/0x1f
# (printed via %c to keep backslashes out of the regex replacement) so the agent can strip these
# machine-readable dumps from the human console view — the Lab polls them ~2/s and would
# otherwise flood the Screen. xv6's own Ctrl-P procdump stays UNbracketed, so it still shows.
regex_once("kernel/console.c",
           r"(case C\('P'\):[^\n]*\n\s*procdump\(\);\n\s*break;)",
           r"\1\n"
           f"  case C('T'): {PRINT}(\"%c\",30); gini_dump(); {PRINT}(\"%c\",31); break;  "
           "// GINI: procs + registers (0x1e/0x1f-bracketed; agent hides it from the console)\n"
           f"  case C('V'): {PRINT}(\"%c\",30); gini_vmdump(); {PRINT}(\"%c\",31); break;  "
           "// GINI: running-proc page table\n"
           f"  case C('F'): {PRINT}(\"%c\",30); gini_fsdump(); {PRINT}(\"%c\",31); break;  "
           "// GINI: superblock + write-ahead log\n"
           f"  case C('S'): {PRINT}(\"%c\",30); gini_scdump(); {PRINT}(\"%c\",31); break;  "
           "// GINI: syscall counts + trace ring\n"
           r"  case C('C'): gini_break(); break;  // GINI: break a hung foreground (no SIGINT in xv6)\n"
           r"  case C(']'): if(sched_quantum < 100) sched_quantum++; break; // GINI: quantum up\n"
           r"  case C('\\\\'): sched_quantum = 1; break;  // GINI: quantum reset to 1",
           "gini_dump();")

# 4g) syscall.c — per-syscall counters (histogram) + a recent-call trace ring (strace view).
#     The definitions + gini_scdump go at end-of-file (types/externs are declared in defs.h so
#     syscall() can use them above); Ctrl-S dumps them.
_SCDUMP = '''
// GINI-xv6: per-syscall counters + recent-call trace ring (Machine Lab histogram + strace).
uint64 gini_sccount[64];
struct gini_sc gini_ring[64];
int gini_ring_i;

void
gini_scdump(void)
{
  for(int i = 0; i < 64; i++)
    if(gini_sccount[i])
      PRINTF("SC %d %d\\n", i, (int)gini_sccount[i]);
  int total = gini_ring_i;
  int start = total > 64 ? total - 64 : 0;
  for(int k = start; k < total; k++){
    struct gini_sc *e = &gini_ring[k % 64];
    PRINTF("TRACE %d %d %p %p\\n", e->pid, e->num, (void*)e->a0, (void*)e->ret);
  }
}
'''
append_once("kernel/syscall.c", _SCDUMP.replace("PRINTF", PRINT),
            "GINI-xv6: per-syscall counters")

# hook the dispatch: count the syscall + record it in the ring (arg0 captured before the call).
regex_once("kernel/syscall.c",
           r"p->trapframe->a0 = syscalls\[num\]\(\);",
           "uint64 gsc_a0 = p->trapframe->a0;                 // GINI: arg0 before dispatch\n"
           "    p->trapframe->a0 = syscalls[num]();\n"
           "    if(num >= 0 && num < 64) gini_sccount[num]++;   // GINI: histogram\n"
           "    int gsi = gini_ring_i++ % 64;                    // GINI: trace ring\n"
           "    gini_ring[gsi].pid = p->pid; gini_ring[gsi].num = num;\n"
           "    gini_ring[gsi].a0 = gsc_a0; gini_ring[gsi].ret = p->trapframe->a0;",
           "GINI: histogram")


# 5) launchable long-running user programs, so the Machine Lab can spawn real work to watch
#    the scheduler (spin = CPU loop), the Memory face (alloc = grow + touch pages -> faults),
#    and the Storage face (writer = repeated file writes -> log transactions).
_UPROGS = {
    "spin": """#include "kernel/types.h"
#include "user/user.h"
// CPU-bound work for the scheduler to show. With an argument it runs for that many SECONDS then
// exits (a GINI tick is ~0.5s, so ~2 ticks/sec); with none it spins forever. Launch from the
// Keyboard, e.g.  spin 10 &   (spin 10s in the background).
int
main(int argc, char *argv[])
{
  if(argc > 1){
    int end = uptime() + atoi(argv[1]) * 2;   // ~2 ticks per second at GINI's 0.5s tick
    while(uptime() < end)
      for(volatile int i = 0; i < 200000; i++)
        ;
    exit(0);
  }
  for(;;)
    ;
  return 0;
}
""",
    "alloc": """#include "kernel/types.h"
#include "user/user.h"
// grow the heap a page at a time with LAZY allocation, then touch each page so it faults in
// (real demand paging -> new mappings appear in the Memory face), then spin.
int
main(int argc, char *argv[])
{
  int n = (argc > 1) ? atoi(argv[1]) : 48;
  for(int i = 0; i < n; i++){
    char *p = sbrklazy(4096);
    if(p == SBRK_ERROR)
      break;
    *p = 1;               // first touch -> page fault -> allocation
    pause(1);
  }
  for(;;)
    ;
  return 0;
}
""",
    "busy": """#include "kernel/types.h"
#include "user/user.h"
// CPU-bound like spin, but runs VARIED code (a function called in a loop), so the sampled user
// PC actually MOVES across the loop body — unlike spin, whose empty loop sits on one instruction.
// With an argument it runs for that many SECONDS then exits; with none it runs forever.
__attribute__((noinline)) unsigned int step(unsigned int x){ return x*1103515245u + 12345u; }
int
main(int argc, char *argv[])
{
  volatile unsigned int x = 1;
  int timed = argc > 1;
  int end = timed ? uptime() + atoi(argv[1]) * 2 : 0;   // ~2 ticks/sec at GINI's 0.5s tick
  while(!timed || uptime() < end){
    for(int i = 0; i < 200000; i++)
      x = step(x) ^ (x >> 3);
  }
  exit(0);
}
""",
    "writer": """#include "kernel/types.h"
#include "kernel/fcntl.h"
#include "user/user.h"
// repeatedly create/write/remove a file -> a stream of write-ahead-log transactions
int
main(int argc, char *argv[])
{
  char buf[512];
  for(int i = 0; i < 512; i++)
    buf[i] = 'x';
  for(;;){
    int fd = open("giniwr", O_CREATE | O_WRONLY);
    if(fd >= 0){
      write(fd, buf, sizeof(buf));
      close(fd);
    }
    unlink("giniwr");
    pause(1);
  }
  return 0;
}
""",
}


def add_uprog(name: str, src: str) -> None:
    p = ROOT / "user" / f"{name}.c"
    if not (ROOT / "user").exists():
        skipped.append(f"user/{name}.c: no user/ dir")
        return
    if not p.exists():
        p.write_text(src)
        applied.append(f"user/{name}.c: created")
    # register in the Makefile UPROGS list (idempotent)
    mk, mksrc = (ROOT / "Makefile"), None
    if mk.exists():
        mksrc = mk.read_text()
    if mksrc is None:
        skipped.append("Makefile: not found")
        return
    entry = f"\t$U/_{name}\\\n"
    if f"$U/_{name}\\" in mksrc:
        return
    new, n = re.subn(r"(UPROGS=\\\n)", r"\1" + entry, mksrc, count=1)
    if n:
        mk.write_text(new)
        applied.append(f"Makefile: registered _{name}")
    else:
        skipped.append(f"Makefile: UPROGS anchor not found for _{name}")


for _name, _src in _UPROGS.items():
    add_uprog(_name, _src)

print("GINI xv6 patch:")
for a in applied:
    print("  applied:", a)
for s in skipped:
    print("  SKIPPED:", s)
# Never fail the build for a skipped invasive edit — the kernel still compiles + boots.
