#!/usr/bin/env python3
"""Apply GINI's xv6 kernel changes robustly (anchored edits, not a context diff).

A `git apply` patch needs exact context lines and breaks across xv6 revisions. Instead we edit
by stable anchors, and every edit is designed so the kernel STILL COMPILES (under xv6's -Werror)
even if an invasive anchor isn't found:

  • append-only (always apply): scheduler globals + gini_pick(), vmprint(), and defs.h prototypes
    — all non-static, so no -Wunused-function under -Werror;
  • anchored regex (apply if found, else warn): the time-slice quantum in trap.c.

This includes wiring scheduler() to call gini_pick() (edit 1b) — done by an anchored regex that
matches both the classic and newer (found/wfi) xv6 scheduler loops; if the anchor isn't found the
kernel still builds and falls back to stock round-robin. The settable time-slice, the policy-aware
picker, per-proc scheduling fields, and vmprint for the Memory face all apply here.

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
#    scheduler() is wired to call gini_pick() in edit 1b below, so switching sched_policy
#    changes scheduling LIVE (no rebuild). RR/priority/lottery ship built-in; students add
#    MLFQ/stride/etc. via the Scheduler Builder (a new sched_policy case + any per-proc fields).
append_once("kernel/proc.c", """
// GINI-xv6: scheduler control knobs (the Machine Lab bridge writes these live over the serial).
//   sched_policy: 0=round-robin  1=priority (lower number = higher, with aging)  2=lottery
//   sched_quantum: timer ticks per time-slice before preemption.
int sched_policy = 0;
int sched_quantum = 1;

// GINI-xv6 SHADOW registry — one entry per shadowable policy. The `pick_*` code below is the
// (deliberately imperfect) PRIMARY; `pick_*_shadow` (in kernel/shadows/gini_sched.c, the ONE file
// students edit) is the SHADOW. Boots enabled=0 -> runs the primary; a control op toggles the
// current policy's shadow. `active` = the shadow was actually used on the last decision.
struct gini_shadow gini_shadow[3] = {
  { "rr_sched",      pick_rr_shadow,      0, 0, 0 },
  { "prio_sched",    pick_prio_shadow,    0, 0, 0 },
  { "lottery_sched", pick_lottery_shadow, 0, 0, 0 },
};

// GINI-xv6: choose the next RUNNABLE proc per sched_policy. scheduler() calls this (wired below).
// If the active policy's shadow is enabled and returns a proc, use it; else fall back to the
// primary. State is read WITHOUT p->lock (like procdump); the caller re-checks RUNNABLE under the
// lock before switching, so a stale read only ever costs one wasted pick.
struct proc *
gini_pick(void)
{
  struct proc *p;
  static int rr = 0;
  static uint lseed = 2463534242u;

  // SHADOW: run the student's version for the active policy, if enabled + implemented (non-0).
  int gpol = (sched_policy >= 0 && sched_policy < 3) ? sched_policy : 0;
  if(gini_shadow[gpol].enabled && gini_shadow[gpol].shadow){
    struct proc *sp = gini_shadow[gpol].shadow();
    if(sp){ gini_shadow[gpol].active = 1; return sp; }
  }
  gini_shadow[gpol].active = 0;

  if(sched_policy == 1){                  // PRIORITY (lower number = higher) with aging
    struct proc *best = 0;
    int best_eff = 0;
    for(p = proc; p < &proc[NPROC]; p++){
      if(p->state != RUNNABLE)
        continue;
      p->wait_ticks++;                    // aging: the longer it waits, the higher it climbs
      int eff = p->priority - p->wait_ticks / 8;
      if(best == 0 || eff < best_eff){ best = p; best_eff = eff; }
    }
    if(best){ best->wait_ticks = 0; return best; }
    return 0;
  }

  if(sched_policy == 2){                  // LOTTERY: draw a random ticket, weighted by p->tickets
    int total = 0;
    for(p = proc; p < &proc[NPROC]; p++)
      if(p->state == RUNNABLE)
        total += p->tickets > 0 ? p->tickets : 1;
    if(total == 0)
      return 0;
    lseed ^= lseed << 13; lseed ^= lseed >> 17; lseed ^= lseed << 5;   // xorshift PRNG
    int win = lseed % total, acc = 0;
    for(p = proc; p < &proc[NPROC]; p++){
      if(p->state != RUNNABLE)
        continue;
      acc += p->tickets > 0 ? p->tickets : 1;
      if(win < acc)
        return p;
    }
    return 0;
  }

  // default: ROUND-ROBIN (matches stock xv6)
  for(int i = 0; i < NPROC; i++){
    p = &proc[(rr + i) % NPROC];
    if(p->state == RUNNABLE){ rr = (rr + i + 1) % NPROC; return p; }
  }
  return 0;
}

// GINI-xv6: emit the shadow manifest — one line per shadowable function, so the oracle/AI can tell
// which student shadows are wired and healthy. present = the shadow file differs from the shipped
// baseline (the Load build stamps GINI_SCHED_HASH); active/faults are runtime.
void
gini_shadowdump(void)
{
  int present = (strncmp(GINI_SCHED_HASH, "baseline", 8) != 0);
  for(int i = 0; i < 3; i++)
    PRINTF("SHADOW %s present=%d enabled=%d active=%d faults=%d hash=%s\\n",
           gini_shadow[i].name, present, gini_shadow[i].enabled,
           gini_shadow[i].active, gini_shadow[i].faults, GINI_SCHED_HASH);
}
""".replace("PRINTF", PRINT), "GINI-xv6: scheduler control knobs")

# 1b) wire scheduler() to call gini_pick(): replace the stock inner selection loop (and its
#     optional found/wfi idle block, in newer xv6) with a policy-driven pick. The anchor is the
#     SCHEDULER loop specifically — it must contain `if (p->state == RUNNABLE)` AND
#     `swtch(&c->context, &p->context)`; this is what distinguishes it from allocproc's OWN
#     `for (p = proc; …) { acquire; … release; }` loop (which selects on UNUSED, has no swtch, and
#     appears FIRST in proc.c — a looser anchor grabs it and corrupts allocproc). Both the classic
#     and newer `int found` variants match; if the anchor isn't found the kernel still builds and
#     falls back to stock RR (gini_pick stays a defined-but-unused non-static fn).
regex_once("kernel/proc.c",
           r'(?s)(?:int found = 0;\s*\n\s*)?'
           r'for\s*\(p = proc; p < &proc\[NPROC\]; p\+\+\)\s*\{\s*'
           r'acquire\(&p->lock\);\s*'
           r'if\s*\(p->state == RUNNABLE\)\s*\{.*?'
           r'swtch\(&c->context, &p->context\);.*?\}\s*'
           r'release\(&p->lock\);\s*\n\s*\}'
           r'(?:\s*\n\s*if\s*\(found == 0\)\s*\{.*?\})?',
           "{\n"
           "      // GINI-xv6: policy-driven pick (round-robin/priority/lottery via sched_policy).\n"
           "      // Chosen lock-free, then re-checked RUNNABLE under its lock before switching.\n"
           "      p = gini_pick();\n"
           "      if(p){\n"
           "        acquire(&p->lock);\n"
           "        if(p->state == RUNNABLE){\n"
           "          p->state = RUNNING;\n"
           "          c->proc = p;\n"
           "          swtch(&c->context, &p->context);\n"
           "          c->proc = 0;\n"
           "        }\n"
           "        release(&p->lock);\n"
           "      } else {\n"
           "        intr_on();\n"
           "        asm volatile(\"wfi\");   // nothing runnable -> idle until the next interrupt\n"
           "      }\n"
           "    }",
           "GINI-xv6: policy-driven pick")

# 1c) per-proc scheduling fields (proc.h) + their defaults in allocproc (proc.c). priority
#     (lower = higher, default 10), tickets (lottery weight, default 1), level (MLFQ, for
#     student policies), wait_ticks (aging counter). Inserted after the last struct field.
regex_once("kernel/proc.h",
           r'(char name\[16\];[^\n]*\n)',
           r'\1  int priority;              // GINI-xv6: scheduling priority (lower = higher)\n'
           r'  int tickets;               // GINI-xv6: lottery ticket count\n'
           r'  int level;                 // GINI-xv6: MLFQ queue level (for student policies)\n'
           r'  int wait_ticks;            // GINI-xv6: aging counter (slices spent RUNNABLE)\n',
           "GINI-xv6: scheduling priority")

regex_once("kernel/proc.c",
           r'(p->state = USED;\n)',
           r'\1  p->priority = 10; p->tickets = 1; p->level = 0; p->wait_ticks = 0;  '
           r'// GINI-xv6 sched defaults\n',
           "GINI-xv6 sched defaults")

# 2) trap.c — the settable time-slice: preempt only every sched_quantum timer ticks. The
#    counter is PER-CPU (indexed by cpuid()) — a single global would be shared across harts, so
#    on SMP every core's timer bumps it and slices come out 1/ncpu too short. Declared before
#    usertrap()/kerneltrap() use it (right after the includes).
regex_once("kernel/trap.c",
           r'(#include "defs.h"\n)',
           r'\1\n// GINI-xv6: PER-CPU time-slice counter (see the which_dev==2 guards below).\n'
           r'int gini_qticks[NCPU];\nextern int sched_quantum;\n',
           "GINI-xv6: PER-CPU time-slice counter")

# 2a) trap.c — the LIVE PAGE-FAULT RING. Every user page fault (scause 12 instruction / 13 load /
#     15 store) is recorded (pid, cause, faulting VA from stval, faulting PC) into a 64-entry ring,
#     so the Memory face can show demand paging, stack growth, and COW copies AS THEY HAPPEN. The
#     kernel only CAPTURES — GINI classifies (lazy / cow-write / illegal) on its side. The ring
#     functions are APPENDED (no regex-escape hazard); usertrap() reaches gini_fault_note() through
#     its defs.h prototype, and console.c reaches gini_faultdump() the same way.
_GINI_FAULT = '''
// GINI-xv6: live page-fault ring — captured in usertrap, dumped over the serial (no gdb halt).
struct gini_flt { int pid; uint64 scause; uint64 va; uint64 epc; };
struct gini_flt gini_flt[64];
int gini_flt_i;

void
gini_fault_note(void)
{
  uint64 c = r_scause();
  if(c == 12 || c == 13 || c == 15){
    struct proc *p = myproc();
    struct gini_flt *e = &gini_flt[gini_flt_i % 64];
    e->pid = p ? p->pid : -1;
    e->scause = c;
    e->va = r_stval();
    e->epc = r_sepc();
    gini_flt_i++;
  }
}

void
gini_faultdump(void)
{
  int total = gini_flt_i;
  int start = total > 64 ? total - 64 : 0;
  for(int k = start; k < total; k++){
    struct gini_flt *e = &gini_flt[k % 64];
    PRINTF("FLT %d %d %p %p\\n", e->pid, (int)e->scause, (void*)e->va, (void*)e->epc);
  }
}
'''
append_once("kernel/trap.c", _GINI_FAULT.replace("PRINTF", PRINT),
            "GINI-xv6: live page-fault ring")

# usertrap(): `if (which_dev == 2)\n    yield();`  (space after `if` in current xv6). Interrupts
# are off at this point, so cpuid() is safe.
regex_once("kernel/trap.c",
           r"if\s*\(which_dev == 2\)\s*\n\s*yield\(\);",
           "if (which_dev == 2 && (++gini_qticks[cpuid()] >= sched_quantum)) "
           "{ gini_qticks[cpuid()] = 0; yield(); } // GINI-xv6 quantum",
           "GINI-xv6 quantum")

# usertrap(): capture page faults into the ring — right after the saved user PC is set, so it runs
# for EVERY trap but only records the three page-fault causes. Works whether the student's lazy/COW
# handler then fixes the fault or it falls through to setkilled().
regex_once("kernel/trap.c",
           r"(p->trapframe->epc = r_sepc\(\);)",
           r"\1\n  gini_fault_note(); // GINI-xv6: record page faults into the live ring",
           "GINI-xv6: record page faults")

# 2a2) trap.c — the TRAP-TAXONOMY RING. Where the fault ring above records only page faults, this
#      records EVERY user trap classified by cause — syscall / page-fault / timer / device /
#      illegal / other — with per-kind counters (the histogram) and a 64-entry ring (the live
#      feed). Captured at the SAME early anchor as the fault ring (before a fatal exception can
#      exit()), so a deliberate crash (bad pointer, illegal instruction) is still recorded.
#      Classification is from scause ALONE (matching devintr()'s own logic), so we never call
#      devintr() a second time and never consume an interrupt. Dumped over Ctrl-R, no gdb halt.
_GINI_TRAP = '''
// GINI-xv6: trap-taxonomy ring — every user trap, classified + counted + recorded (Traps face).
enum { GT_SYSCALL=0, GT_PAGEFAULT=1, GT_TIMER=2, GT_DEVICE=3, GT_ILLEGAL=4, GT_OTHER=5, GT_NKIND=6 };
uint64 gini_trapcount[GT_NKIND];
struct gini_trap gini_traps[64];
int gini_traps_i;

static int
gini_kind(uint64 c)
{
  if(c & 0x8000000000000000L){                 // interrupt (top bit set)
    if((c & 0xff) == 9) return GT_DEVICE;       // supervisor external (PLIC device)
    return GT_TIMER;                            // supervisor timer / software
  }
  if(c == 8) return GT_SYSCALL;                 // ecall from U-mode (a system call)
  if(c == 12 || c == 13 || c == 15) return GT_PAGEFAULT;   // instr / load / store page fault
  if(c == 2) return GT_ILLEGAL;                 // illegal instruction
  return GT_OTHER;
}

void
gini_traprec(void)
{
  uint64 c = r_scause();
  int kind = gini_kind(c);
  gini_trapcount[kind]++;
  struct proc *p = myproc();
  struct gini_trap *e = &gini_traps[gini_traps_i % 64];
  e->pid = p ? p->pid : 0;
  e->kind = kind;
  e->cause = c;
  e->epc = r_sepc();
  e->tval = r_stval();
  gini_traps_i++;
}

void
gini_trapdump(void)
{
  static char *kn[GT_NKIND] = {"syscall","pagefault","timer","device","illegal","other"};
  for(int k = 0; k < GT_NKIND; k++)
    PRINTF("TC %d %s %d\\n", k, kn[k], (int)gini_trapcount[k]);
  int total = gini_traps_i;
  int start = total > 64 ? total - 64 : 0;
  for(int k = start; k < total; k++){
    struct gini_trap *e = &gini_traps[k % 64];
    PRINTF("TR %d %d %p %p %p\\n", e->pid, e->kind,
           (void*)e->cause, (void*)e->epc, (void*)e->tval);
  }
}
'''
append_once("kernel/trap.c", _GINI_TRAP.replace("PRINTF", PRINT),
            "GINI-xv6: trap-taxonomy ring")

# usertrap(): record the trap into the taxonomy ring — same early anchor as the fault ring, so it
# runs for EVERY trap including fatal exceptions (which exit() before usertrapret()).
regex_once("kernel/trap.c",
           r"(p->trapframe->epc = r_sepc\(\);)",
           r"\1\n  gini_traprec(); // GINI-xv6: record the trap into the taxonomy ring",
           "GINI-xv6: record the trap into the taxonomy ring")

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

# 4a2) defs.h — prototypes for the VM/paging additions (fault ring + all-procs page-table dump).
# Separate block so an already-patched tree still picks these up (fresh clone each Docker build).
append_once("kernel/defs.h", """
// GINI-xv6 VM/paging additions
void            gini_fault_note(void);   // record a user page fault (called from usertrap)
void            gini_faultdump(void);    // print the live fault ring to the console
void            gini_vmdump_all(void);   // print EVERY user proc's page table (COW / sharing view)
""", "GINI-xv6 VM/paging additions")

# 4a3) defs.h — prototypes for the trap-taxonomy ring (counters + feed). Separate block so a fresh
# clone (each Docker build) picks it up; struct declared here, defined in trap.c.
append_once("kernel/defs.h", """
// GINI-xv6 trap-taxonomy additions
void            gini_traprec(void);      // classify + record a trap (called from usertrap)
void            gini_trapdump(void);     // print per-kind counters + the trap ring to the console
struct gini_trap { int pid; int kind; uint64 cause; uint64 epc; uint64 tval; };
extern uint64   gini_trapcount[6];
extern struct gini_trap gini_traps[64];
extern int      gini_traps_i;
""", "GINI-xv6 trap-taxonomy additions")

# 4a2) defs.h — the SHADOW types + prototypes (used by proc.c/console.c; declared before use).
append_once("kernel/defs.h", """
// GINI-xv6 SHADOW additions
#ifndef GINI_SCHED_HASH
#define GINI_SCHED_HASH "baseline"
#endif
struct gini_shadow {
  char *name;
  struct proc *(*shadow)(void);
  int enabled;
  int active;
  int faults;
};
extern struct gini_shadow gini_shadow[];
void            gini_shadowdump(void);
struct proc*    pick_rr_shadow(void);
struct proc*    pick_prio_shadow(void);
struct proc*    pick_lottery_shadow(void);
""", "GINI-xv6 SHADOW additions")

# 4a3) kernel/shadows/gini_sched.c — the ONE student-editable file (bind-mounted at runtime over
#      kernel/shadows/). Ships as stubs returning 0 ("not implemented -> use the primary"). Compiled
#      into the kernel via the Makefile OBJS registration below.
_SHADOW_STUB = '''// GINI-xv6 SHADOW FILE — the one file you edit for a scheduler assignment.
//
// Implement a policy's pick to REPLACE the shipped (deliberately-imperfect) primary. Return the
// RUNNABLE proc to run next, or 0 to fall back to the primary. This is read-only: read the fields
// below and return a proc; do NOT take locks. The scheduler re-checks your choice is RUNNABLE under
// its lock, so a wrong pick is safe.
//
// Fields available on each proc (iterate proc[0..NPROC-1]):
//   p->state      : UNUSED / USED / SLEEPING / RUNNABLE / RUNNING / ZOMBIE
//   p->priority   : scheduling priority (lower number = higher priority)
//   p->tickets    : lottery ticket count
//   p->wait_ticks : slices spent RUNNABLE without running (aging counter)
//   p->pid, p->name
//
// (This file lives in kernel/shadows/, so the kernel headers are one directory up.)
#include "../types.h"
#include "../param.h"
#include "../memlayout.h"
#include "../riscv.h"
#include "../spinlock.h"
#include "../proc.h"

extern struct proc proc[NPROC];

struct proc *
pick_rr_shadow(void)
{
  return 0;   // not implemented -> the round-robin primary runs. Write your version here.
}

struct proc *
pick_prio_shadow(void)
{
  return 0;   // assignment: fix priority starvation with aging (replace the primary).
}

struct proc *
pick_lottery_shadow(void)
{
  return 0;   // assignment: make CPU share track tickets.
}
'''
if (ROOT / "kernel").exists():
    _shdir = ROOT / "kernel" / "shadows"
    _shdir.mkdir(exist_ok=True)
    _shfile = _shdir / "gini_sched.c"
    if not _shfile.exists():
        _shfile.write_text(_SHADOW_STUB)
        applied.append("kernel/shadows/gini_sched.c: created")
    else:
        applied.append("kernel/shadows/gini_sched.c: already present")
    _mk, _mksrc = (ROOT / "Makefile"), None
    if _mk.exists():
        _mksrc = _mk.read_text()
    if _mksrc is None:
        skipped.append("Makefile: not found (shadow OBJS)")
    elif "$K/shadows/gini_sched.o" in _mksrc:
        applied.append("Makefile: shadow OBJS already registered")
    else:
        _new, _n = re.subn(r"(OBJS = \\\n)", r"\1  $K/shadows/gini_sched.o \\\n", _mksrc, count=1)
        if _n:
            _mk.write_text(_new)
            applied.append("Makefile: registered kernel/shadows/gini_sched.o")
        else:
            skipped.append("Makefile: OBJS anchor not found for the shadow file")

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
    // per-proc scheduling fields (priority/tickets/level/aging) — a separate line so the stock
    // procdump parser is untouched; the scheduler face reads these to show policy behaviour.
    PRINTF("PROC %d pri %d tk %d lv %d wait %d\\n",
           p->pid, p->priority, p->tickets, p->level, p->wait_ticks);
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

# 4d1) gini_vmdump_all(): EVERY user process's leaf mappings, tagged by pid, so the Memory face
# can put parent and child side by side and DERIVE sharing (same PA in two procs) — the whole
# basis of the copy-on-write experiment, with no kernel refcount required. Emits `VP pid name sz`
# per proc then `VL pid va pa flags` per leaf, where flags = the low 10 PTE bits (R/W/X/U AND the
# student's RSW/COW bit). The VA is accumulated during the walk (Sv39: index i at level L adds
# i<<(12+9L)); a leaf is level 0 or any PTE with R/W/X set.
_GINI_VMALL = '''
// GINI-xv6: dump all user page tables (leaf mappings, tagged by pid) — the COW / sharing view.
static void
gini_leafwalk(pagetable_t pt, uint64 va, int level, int pid)
{
  for(int i = 0; i < 512; i++){
    pte_t pte = pt[i];
    if(!(pte & PTE_V))
      continue;
    uint64 cva = va | ((uint64)i << (12 + 9*level));
    if(level == 0 || (pte & (PTE_R|PTE_W|PTE_X)))
      PRINTF("VL %d %p %p %d\\n", pid, (void*)cva, (void*)PTE2PA(pte), (int)(pte & 0x3FF));
    else
      gini_leafwalk((pagetable_t)PTE2PA(pte), cva, level - 1, pid);
  }
}

void
gini_vmdump_all(void)
{
  struct proc *p;
  for(p = proc; p < &proc[NPROC]; p++){
    if(p->state == UNUSED || p->pagetable == 0)
      continue;
    PRINTF("VP %d %s %p\\n", p->pid, p->name, (void*)p->sz);
    gini_leafwalk(p->pagetable, 0, 2, p->pid);
  }
}
'''
append_once("kernel/proc.c", _GINI_VMALL.replace("PRINTF", PRINT),
            "GINI-xv6: dump all user page tables")

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
           f"  case C('A'): {PRINT}(\"%c\",30); gini_vmdump_all(); {PRINT}(\"%c\",31); break;  "
           "// GINI: all user page tables (COW / sharing view)\n"
           f"  case C('E'): {PRINT}(\"%c\",30); gini_faultdump(); {PRINT}(\"%c\",31); break;  "
           "// GINI: live page-fault ring\n"
           f"  case C('R'): {PRINT}(\"%c\",30); gini_trapdump(); {PRINT}(\"%c\",31); break;  "
           "// GINI: trap-taxonomy counters + ring\n"
           r"  case C('C'): gini_break(); break;  // GINI: break a hung foreground (no SIGINT in xv6)\n"
           r"  case C(']'): if(sched_quantum < 100) sched_quantum++; break; // GINI: quantum up\n"
           r"  case C('\\\\'): sched_quantum = 1; break;  // GINI: quantum reset to 1\n"
           r"  case C('G'): if(sched_policy < 2) sched_policy++; break; // GINI: scheduler policy up\n"
           r"  case C('B'): sched_policy = 0; break;  // GINI: scheduler policy reset (round-robin)\n"
           r"  case C('K'): gini_shadow[sched_policy].enabled = !gini_shadow[sched_policy].enabled; break;  // GINI: toggle the current policy's shadow\n"
           f"  case C('W'): {PRINT}(\"%c\",30); gini_shadowdump(); {PRINT}(\"%c\",31); break;  "
           "// GINI: shadow manifest (0x1e/0x1f-bracketed)",
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
