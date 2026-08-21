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
// Adapters: the registry is generic (void *(*)(void *)) but STUDENTS write naturally-typed
// functions — `struct proc *pick_rr_shadow(void)` — so each entry goes through a one-line shim.
static void *gsh_rr(void *a)      { (void)a; return pick_rr_shadow(); }
static void *gsh_prio(void *a)    { (void)a; return pick_prio_shadow(); }
static void *gsh_lottery(void *a) { (void)a; return pick_lottery_shadow(); }

// Validator for a picked process. The old code returned the student's pointer straight to
// scheduler(), which does acquire(&p->lock) — so a garbage pointer was dereferenced and panicked
// BEFORE the RUNNABLE re-check could help. Check the pointer really addresses a table entry first.
static int
gsh_proc_valid(void *arg, void *ans)
{
  struct proc *p = (struct proc *)ans;
  (void)arg;
  if(p < proc || p >= &proc[NPROC])                             return 0;   // inside proc[]
  if(((uint64)p - (uint64)proc) % sizeof(struct proc) != 0)     return 0;   // on an entry boundary
  return p->state == RUNNABLE;                                              // and schedulable
}

struct gini_shadow gini_shadow[GINI_NSHADOW] = {
  { "rr_sched",      "sched", gsh_rr,      gsh_proc_valid,    0, 0, 0, 0, 0 },
  { "prio_sched",    "sched", gsh_prio,    gsh_proc_valid,    0, 0, 0, 0, 0 },
  { "lottery_sched", "sched", gsh_lottery, gsh_proc_valid,    0, 0, 0, 0, 0 },
  // vm: the adapter + validator live in vm.c, where walk()/ismapped() are in scope
  { "vmfault",       "vm",    gsh_vmfault, gsh_vmfault_valid, 0, 0, 0, 0, 0 },
  // fs: buffer-cache eviction — adapter/validator in bio.c, where bcache is private
  { "bget_evict",    "fs",    gsh_bget,    gsh_bget_valid,    0, 0, 0, 0, 0 },
  { "balloc",        "fs",    gsh_balloc,  gsh_balloc_valid,  0, 0, 0, 0, 0 },
  { "kalloc",        "vm",    gsh_kalloc,  gsh_kalloc_valid,  0, 0, 0, 0, 0 },
};

// Toggle any shadow by index (console Ctrl-G <n>) — works for every subsystem, unlike Ctrl-K
// which could only ever reach the current scheduler policy.
void
gini_shadow_toggle(int i)
{
  if(i >= 0 && i < GINI_NSHADOW)
    gini_shadow[i].enabled = !gini_shadow[i].enabled;
}

// Clear the per-shadow counters (console Ctrl-X) so a student can re-measure after a fix without
// rebooting — otherwise one early mistake keeps a mission's `rejects == 0` objective red forever.
void
gini_shadow_reset(void)
{
  for(int i = 0; i < GINI_NSHADOW; i++){
    gini_shadow[i].rejects = 0;
    gini_shadow[i].calls = 0;
  }
}

// The one dispatch path for every shadow: ask the student, accept "not implemented", VALIDATE a
// real answer, and fall back to the primary (counting a reject) when the answer is unusable.
void *
gini_shadow_call(struct gini_shadow *s, void *arg)
{
  void *ans;
  if(!s->enabled || !s->shadow){ s->active = 0; return 0; }
  s->calls++;
  ans = s->shadow(arg);
  if(!ans){ s->active = 0; return 0; }                 // "not implemented" -> primary
  if(s->valid && !s->valid(arg, ans)){                 // wrong answer -> primary, and say so
    s->rejects++;
    s->active = 0;
    return 0;
  }
  s->active = 1;
  return ans;
}

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

  // SHADOW: run the student's version for the active policy. gini_shadow_call validates the
  // answer (points into proc[], on an entry boundary, RUNNABLE) — a bad pointer is rejected and
  // counted here rather than panicking in scheduler()'s acquire(&p->lock).
  {
    int gpol = (sched_policy >= 0 && sched_policy < GINI_NPOLICY) ? sched_policy : 0;
    struct proc *sp = (struct proc *)gini_shadow_call(&gini_shadow[gpol], 0);
    if(sp) return sp;
  }

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

  if(sched_policy == 2){                  // LOTTERY — shipped version is DELIBERATELY FLAWED: it
                                          // picks UNIFORMLY at random and IGNORES p->tickets. The
                                          // lottery assignment is to weight the draw by tickets.
    int n = 0;
    for(p = proc; p < &proc[NPROC]; p++)
      if(p->state == RUNNABLE)
        n++;
    if(n == 0)
      return 0;
    lseed ^= lseed << 13; lseed ^= lseed >> 17; lseed ^= lseed << 5;   // xorshift PRNG
    int win = lseed % n, i = 0;
    for(p = proc; p < &proc[NPROC]; p++){
      if(p->state != RUNNABLE)
        continue;
      if(i++ == win)                      // uniform: ignores tickets (the bug to fix)
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
  for(int i = 0; i < GINI_NSHADOW; i++)
    PRINTF("SHADOW %s present=%d enabled=%d active=%d faults=%d rejects=%d calls=%d "
           "sub=%s hash=%s\\n",
           gini_shadow[i].name, present, gini_shadow[i].enabled,
           gini_shadow[i].active, gini_shadow[i].faults, gini_shadow[i].rejects,
           gini_shadow[i].calls, gini_shadow[i].subsystem, GINI_SCHED_HASH);
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

# 1d) per-proc ALARM state (proc.h) for the sigalarm lab. GINI OWNS these fields so gini_dump
#     always compiles; the STUDENT writes sigalarm/sigreturn (scaffolded by the Syscall Builder)
#     that set/read them, plus the usertrap countdown that fires the handler. Inserted after the
#     name field, same as the scheduling fields.
regex_once("kernel/proc.h",
           r'(char name\[16\];[^\n]*\n)',
           r'\1  uint64 gini_alarm_handler;  // GINI-xv6: alarm handler VA (0 = none) — sigalarm lab\n'
           r'  int gini_alarm_interval;   // GINI-xv6: alarm period in timer ticks\n'
           r'  int gini_alarm_ticks;      // GINI-xv6: ticks since the last fire\n'
           r'  int gini_alarm_on;         // GINI-xv6: a handler is running now (re-entrancy guard)\n',
           "GINI-xv6: alarm handler VA")

regex_once("kernel/proc.c",
           r'(p->state = USED;\n)',
           r'\1  p->gini_alarm_handler = 0; p->gini_alarm_interval = 0; p->gini_alarm_ticks = 0; '
           r'p->gini_alarm_on = 0;  // GINI-xv6 alarm defaults\n',
           "GINI-xv6 alarm defaults")

# 2) trap.c — the settable time-slice: preempt only every sched_quantum timer ticks. The
#    counter is PER-CPU (indexed by cpuid()) — a single global would be shared across harts, so
#    on SMP every core's timer bumps it and slices come out 1/ncpu too short. Declared before
#    usertrap()/kerneltrap() use it (right after the includes).
regex_once("kernel/trap.c",
           r'(#include "defs.h"\n)',
           r'\1\n// GINI-xv6: PER-CPU time-slice counter (see the which_dev==2 guards below).\n'
           r'int gini_qticks[NCPU];\nextern int sched_quantum;\n'
           r'// GINI-xv6: mode-time ticks — sampled at each timer interrupt by privilege source\n'
           r'// (user-entry trap => user, kernel-entry => kernel, or idle when no proc runs). The\n'
           r'// CPU face reads the delta as a user/kernel/idle split, like top\'s us/sy. No CSR\n'
           r'// reads needed: the trap entry path already tells us where the CPU was.\n'
           r'uint64 gini_ut, gini_kt, gini_it;\n',
           "GINI-xv6: PER-CPU time-slice counter")

# 2a) trap.c — the LIVE PAGE-FAULT RING. Every user page fault (scause 12 instruction / 13 load /
#     15 store) is recorded (pid, cause, faulting VA from stval, faulting PC) into a 64-entry ring,
#     so the Memory face can show demand paging, stack growth, and COW copies AS THEY HAPPEN. The
#     kernel only CAPTURES — GINI classifies (lazy / cow-write / illegal) on its side. The ring
#     functions are APPENDED (no regex-escape hazard); usertrap() reaches gini_fault_note() through
#     its defs.h prototype, and console.c reaches gini_faultdump() the same way.
_GINI_FAULT = '''
// GINI-xv6: live page-fault ring — captured in usertrap, dumped over the serial (no gdb halt).
struct gini_flt { int pid; uint64 scause; uint64 va; uint64 epc; uint64 seq; };
struct gini_flt gini_flt[GINI_RING];
int gini_flt_i;

void
gini_fault_note(void)
{
  uint64 c = r_scause();
  if(c == 12 || c == 13 || c == 15){
    struct proc *p = myproc();
    struct gini_flt *e = &gini_flt[gini_flt_i % GINI_RING];
    e->pid = p ? p->pid : -1;
    e->scause = c;
    e->va = r_stval();
    e->epc = r_sepc();
    e->seq = gini_stamp();     // GINI: event clock
    gini_flt_i++;
  }
}

void
gini_faultdump(void)
{
  int total = gini_flt_i;
  int start = total > GINI_RING ? total - GINI_RING : 0;
  for(int k = start; k < total; k++){
    struct gini_flt *e = &gini_flt[k % GINI_RING];
    PRINTF("FLT %d %d %p %p %d\\n", e->pid, (int)e->scause, (void*)e->va, (void*)e->epc,
           (int)e->seq);
  }
}
'''
append_once("kernel/trap.c", _GINI_FAULT.replace("PRINTF", PRINT),
            "GINI-xv6: live page-fault ring")

# usertrap(): `if (which_dev == 2)\n    yield();`  (space after `if` in current xv6). Interrupts
# are off at this point, so cpuid() is safe.
regex_once("kernel/trap.c",
           r"if\s*\(which_dev == 2\)\s*\n\s*yield\(\);",
           "if (which_dev == 2) { gini_ut++; "        # GINI-xv6: this timer tick hit user code
           "if (++gini_qticks[cpuid()] >= sched_quantum) "
           "{ gini_qticks[cpuid()] = 0; yield(); } } // GINI-xv6 quantum",
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
// the global event clock (see defs.h). Atomic: traps fire on every hart.
uint64 gini_seq;
uint64
gini_stamp(void)
{
  return __sync_fetch_and_add(&gini_seq, 1);
}

uint64 gini_trapcount[GT_NKIND];
struct gini_trap gini_traps[GINI_RING];
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
  struct gini_trap *e = &gini_traps[gini_traps_i % GINI_RING];
  e->pid = p ? p->pid : 0;
  e->kind = kind;
  e->cause = c;
  e->epc = r_sepc();
  e->tval = r_stval();
  // the interrupt state AS IT WAS when this trap happened (sstatus.SPP = the privilege we
  // interrupted, SPIE = whether interrupts were enabled); the poll-time CSR read cannot see this
  e->sstatus = r_sstatus();
  e->sie = r_sie();
  e->sip = r_sip();
  e->seq = gini_stamp();       // GINI: event clock
  gini_traps_i++;
}

void
gini_trapdump(void)
{
  static char *kn[GT_NKIND] = {"syscall","pagefault","timer","device","illegal","other"};
  for(int k = 0; k < GT_NKIND; k++)
    PRINTF("TC %d %s %d\\n", k, kn[k], (int)gini_trapcount[k]);
  int total = gini_traps_i;
  int start = total > GINI_RING ? total - GINI_RING : 0;
  for(int k = start; k < total; k++){
    struct gini_trap *e = &gini_traps[k % GINI_RING];
    PRINTF("TR %d %d %p %p %p %p %p %p %d\\n", e->pid, e->kind,
           (void*)e->cause, (void*)e->epc, (void*)e->tval,
           (void*)e->sstatus, (void*)e->sie, (void*)e->sip, (int)e->seq);
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

# 2a3) kerneltrap(): also record traps taken while the CPU is in the kernel — DEVICE interrupts
#      (UART rx while sh sleeps in read(), virtio disk completion) arrive here, not in usertrap, so
#      without this hook the "device" bucket stays empty. Anchored on `uint64 scause = r_scause();`,
#      which is UNIQUE to kerneltrap (usertrap reads r_scause() inline). gini_traprec classifies
#      from scause alone, so it doesn't need which_dev here.
regex_once("kernel/trap.c",
           r"(uint64 scause = r_scause\(\);)",
           r"\1\n  gini_traprec(); // GINI-xv6: record kernel-mode traps (device interrupts)",
           "GINI-xv6: record kernel-mode traps")

# kerneltrap(): `if (which_dev == 2 && myproc() != 0[ && ...])\n    yield();` — trailing
# `&& myproc()->state == RUNNING` was dropped in current xv6, so match it optionally.
regex_once("kernel/trap.c",
           r"if\s*\(which_dev == 2 && myproc\(\) != 0"
           r"(?: && myproc\(\)->state == RUNNING)?\)\s*\n\s*yield\(\);",
           "if (which_dev == 2) { if (myproc() == 0) gini_it++; else gini_kt++; } "  # GINI modetime
           "// GINI-xv6 modetime\n  "
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

// GINI-xv6 SHADOW (vm): lazy/demand page-fault handling. The primary is vmfault() below; the
// student's version lives in kernel/shadows/gini_vm.c and returns the physical page it mapped,
// or 0 to fall back. Adapter + validator live HERE because the check needs ismapped()/PHYSTOP.
uint64 gini_vmf_ok, gini_vmf_fail;      // handled vs. fell-through counters (Memory face)

void *
gsh_vmfault(void *arg)
{
  struct gini_vmf_arg *a = (struct gini_vmf_arg *)arg;
  return (void *)vmfault_shadow(a->pt, a->psz, a->va, a->read);
}

// A claimed handle is only accepted if it is REAL: a page-aligned physical page inside RAM, and
// the faulting address genuinely mapped afterwards. Otherwise the primary runs and we count a
// reject — a student's half-finished handler can't leave the process running on a lie.
int
gsh_vmfault_valid(void *arg, void *ans)
{
  struct gini_vmf_arg *a = (struct gini_vmf_arg *)arg;
  uint64 pa = (uint64)ans;
  if(pa %% PGSIZE)                          return 0;
  if(pa < KERNBASE || pa >= PHYSTOP)        return 0;
  return ismapped(a->pt, PGROUNDDOWN(a->va));
}
""" % {"P": PRINT}, "GINI-xv6: print a page table")

# 3b) vmfault(): give the student's shadow first refusal. Hooked INSIDE vmfault rather than at
#     usertrap's call site, so every caller (usertrap AND the copyin/copyout paths) goes through
#     the same seam. gini_shadow_call validates the answer before it is believed; 0 from the
#     shadow means "not implemented", which falls through to the stock body unchanged.
regex_once("kernel/vm.c",
           r"(vmfault\(pagetable_t pagetable, uint64 psz, uint64 va, int read\)\s*\n\{\s*\n)",
           r"\1"
           "  {  // GINI-xv6 SHADOW: the student's lazy-allocation handler gets first refusal\n"
           "    struct gini_vmf_arg gva = { pagetable, psz, va, read };\n"
           "    uint64 gpa = (uint64)gini_shadow_call(&gini_shadow[GINI_SH_VMFAULT], &gva);\n"
           "    if(gpa){ gini_vmf_ok++; return gpa; }\n"
           "    gini_vmf_fail++;\n"
           "  }\n",
           "GINI-xv6: vmfault shadow hook")

# 3c) bio.c — BUFFER-CACHE instrumentation + the eviction SHADOW (S1).
#     xv6 has no swapping, so the buffer cache is the only real cache-replacement policy in the
#     kernel — this is where LRU vs clock vs random becomes measurable. `lastuse` (a tick stamp)
#     is what gives a student the recency data their policy needs.
regex_once("kernel/buf.h",
           r"(  uint refcnt;\n)",
           r"\1  uint lastuse;      // GINI-xv6: tick of the last hit — recency for the eviction lab\n",
           "GINI-xv6: buf lastuse stamp")

# hit path: count it and refresh recency
regex_once("kernel/bio.c",
           r"(if \(b->dev == dev && b->blockno == blockno\) \{\n\s*b->refcnt\+\+;\n)",
           r"\1      gini_bc_hits++; b->lastuse = ticks;   // GINI-xv6: cache hit\n",
           "GINI-xv6: bcache hit counter")

# miss path: let the student's policy pick the victim BEFORE the stock LRU scan runs
regex_once("kernel/bio.c",
           r"(  // Not cached\.\n)",
           r"\1"
           "  gini_bc_misses++;\n"
           "  {  // GINI-xv6 SHADOW: the student's eviction policy chooses which buffer to recycle\n"
           "    struct gini_bget_arg gba = { dev, blockno, bcache.buf, NBUF };\n"
           "    struct buf *gv = (struct buf *)gini_shadow_call(&gini_shadow[GINI_SH_BGET], &gba);\n"
           "    if(gv){\n"
           "      gv->dev = dev; gv->blockno = blockno; gv->valid = 0; gv->refcnt = 1;\n"
           "      gv->lastuse = ticks; gini_bc_evicts++;\n"
           "      release(&bcache.lock);\n"
           "      acquiresleep(&gv->lock);\n"
           "      return gv;\n"
           "    }\n"
           "  }\n",
           "GINI-xv6: bcache eviction shadow")

# stock recycle path: same bookkeeping so the two policies are compared on equal terms
regex_once("kernel/bio.c",
           r"(      b->refcnt = 1;\n)",
           r"\1      b->lastuse = ticks; gini_bc_evicts++;   // GINI-xv6: shipped-policy eviction\n",
           "GINI-xv6: bcache evict counter")

append_once("kernel/bio.c", """
// GINI-xv6: buffer-cache telemetry + the eviction shadow's adapter/validator. They live here
// because everything they touch (bcache) is private to this file.
uint64 gini_bc_hits, gini_bc_misses, gini_bc_evicts;

void *
gsh_bget(void *arg)
{
  struct gini_bget_arg *a = (struct gini_bget_arg *)arg;
  return (void *)bget_evict_shadow(a->dev, a->blockno, a->bufs, a->nbuf);
}

// Total and cheap: a victim must be one of OUR buffers and must not be in use. This is why the
// eviction shadow is the safest of the four — a wrong answer cannot corrupt anything, it is
// simply refused and the shipped LRU runs instead.
int
gsh_bget_valid(void *arg, void *ans)
{
  struct buf *b = (struct buf *)ans;
  (void)arg;
  if(b < bcache.buf || b >= &bcache.buf[NBUF])                       return 0;
  if(((uint64)b - (uint64)bcache.buf) %% sizeof(struct buf) != 0)     return 0;
  return b->refcnt == 0;
}

// One line of counters, then one per buffer — the Storage Lab's cache grid reads these.
void
gini_bcdump(void)
{
  %(P)s("BC hits %%d misses %%d evicts %%d nbuf %%d\\n",
        (int)gini_bc_hits, (int)gini_bc_misses, (int)gini_bc_evicts, NBUF);
  for(int i = 0; i < NBUF; i++){
    struct buf *b = &bcache.buf[i];
    %(P)s("BUF %%d %%d %%d %%d %%d\\n", i, (int)b->blockno, (int)b->refcnt,
          b->valid, (int)b->lastuse);
  }
}
""" % {"P": PRINT}, "GINI-xv6: bcache telemetry + eviction shadow")

# 3d) fs.c — BLOCK-ALLOCATOR shadow (S4) + fragmentation telemetry.
#     The student CHOOSES a free block; the kernel does the bookkeeping (mark the bitmap, zero
#     the block). That split is deliberate: policy is the lesson, and the dangerous part — the
#     on-disk bitmap — never depends on their code being right.
regex_once("kernel/fs.c",
           r"(balloc\(uint dev\)\n\{\n)",
           r"\1"
           "  {  // GINI-xv6 SHADOW: the student's allocation policy picks the block\n"
           "    struct gini_balloc_arg gaa = { dev, sb.size, gini_ba_last };\n"
           "    uint gb = (uint)(uint64)gini_shadow_call(&gini_shadow[GINI_SH_BALLOC], &gaa);\n"
           "    if(gb){\n"
           "      struct buf *gbp = bread(dev, BBLOCK(gb, sb));\n"
           "      gbp->data[(gb % BPB) / 8] |= 1 << (gb % 8);   // validated free -> mark in use\n"
           "      log_write(gbp);\n"
           "      brelse(gbp);\n"
           "      bzero(dev, gb);\n"
           "      gini_ba_note(gb);\n"
           "      return gb;\n"
           "    }\n"
           "  }\n",
           "GINI-xv6: balloc shadow hook")

# the shipped path gets the same bookkeeping, so both policies are measured on equal terms
regex_once("kernel/fs.c",
           r"(        bzero\(dev, b \+ bi\);\n)",
           r"\1        gini_ba_note(b + bi);   // GINI-xv6: fragmentation telemetry\n",
           "GINI-xv6: balloc telemetry (shipped path)")

append_once("kernel/fs.c", """
// GINI-xv6: block-allocator telemetry + the allocation shadow's adapter/validator.
// LOCALITY is the lesson here: consecutive blocks of a file that sit far apart cost seeks, so we
// track the mean gap between successive allocations — the number an allocation-policy mission is
// graded on.
uint64 gini_ba_allocs, gini_ba_gapsum;
uint   gini_ba_last;

void
gini_ba_note(uint bno)
{
  if(gini_ba_last)
    gini_ba_gapsum += (bno > gini_ba_last) ? (bno - gini_ba_last) : (gini_ba_last - bno);
  gini_ba_last = bno;
  gini_ba_allocs++;
}

// Is this block marked free in the on-disk bitmap? Exposed to the student's shadow so they can
// write a policy, and used by the validator to check their answer.
int
gini_block_free(uint dev, uint bno)
{
  struct buf *bp;
  int used;
  if(bno == 0 || bno >= sb.size)
    return 0;
  bp = bread(dev, BBLOCK(bno, sb));
  used = bp->data[(bno %% BPB) / 8] & (1 << (bno %% 8));
  brelse(bp);
  return !used;
}

void *
gsh_balloc(void *arg)
{
  struct gini_balloc_arg *a = (struct gini_balloc_arg *)arg;
  return (void *)(uint64)balloc_shadow(a->dev, a->size, a->last);
}

// Cheap because the bitmap already exists: a returned block must be a real data block that the
// bitmap agrees is free. Handing back an allocated block would corrupt the file system — this is
// exactly the class of mistake the validator exists to stop.
int
gsh_balloc_valid(void *arg, void *ans)
{
  struct gini_balloc_arg *a = (struct gini_balloc_arg *)arg;
  uint bno = (uint)(uint64)ans;
  if(bno <= sb.bmapstart || bno >= sb.size)     // never metadata, never out of range
    return 0;
  return gini_block_free(a->dev, bno);
}

// The free/used bitmap itself, hex-packed, so the Storage Lab can draw the disk as a map and a
// student can SEE fragmentation instead of reading a number.
void
gini_bmapdump(void)
{
  int nbytes = (sb.size + 7) / 8;
  %(P)s("BA allocs %%d meangap %%d last %%d nblocks %%d\\n",
        (int)gini_ba_allocs,
        (int)(gini_ba_allocs > 1 ? gini_ba_gapsum / (gini_ba_allocs - 1) : 0),
        (int)gini_ba_last, (int)sb.size);
  %(P)s("BMAP ");
  for(int i = 0; i < nbytes; i++){
    struct buf *bp = bread(ROOTDEV, BBLOCK(i * 8, sb));
    int byte = bp->data[((i * 8) %% BPB) / 8];
    brelse(bp);
    %(P)s("%%x%%x", (byte >> 4) & 0xf, byte & 0xf);
  }
  %(P)s("\\n");
}
""" % {"P": PRINT}, "GINI-xv6: balloc telemetry + shadow")

# 3e) kalloc.c — PHYSICAL PAGE ALLOCATOR shadow (S3) + the allocation bitmap that makes it safe.
#     Unlike the other three, a wrong answer here (a page that is already in use) silently
#     corrupts unrelated kernel memory and stock xv6 cannot detect it. So S3 ships WITH a bitmap:
#     one bit per physical page = 4096 bytes for xv6's 128 MiB, maintained inside the kmem lock
#     that kalloc/kfree already hold. It doubles as the data behind the fragmentation view.
regex_once("kernel/kalloc.c",
           r"(void\nkfree\(void \*pa\)\n\{\n)",
           r"\1  gini_page_clear((uint64)pa);   // GINI-xv6: mark free in the allocation bitmap\n",
           "GINI-xv6: kfree bitmap clear")

regex_once("kernel/kalloc.c",
           r"(  if \(r\)\n    memset\(\(char \*\)r, 5, PGSIZE\); // fill with junk\n)",
           "  if (r)\n"
           "    gini_page_set((uint64)r);      // GINI-xv6: mark in use BEFORE the junk fill\n"
           r"\1",
           "GINI-xv6: kalloc bitmap set")

# the shadow gets first refusal, inside kalloc so every caller shares the seam
regex_once("kernel/kalloc.c",
           r"(kalloc\(void\)\n\{\n  struct run \*r;\n)",
           r"\1"
           "\n"
           "  {  // GINI-xv6 SHADOW: the student's page allocator picks the page\n"
           "    void *gp = gini_shadow_call(&gini_shadow[GINI_SH_KALLOC], 0);\n"
           "    if(gp){\n"
           "      gini_page_set((uint64)gp);\n"
           "      gini_ka_shadow++;\n"
           "      memset((char *)gp, 5, PGSIZE);\n"
           "      return gp;\n"
           "    }\n"
           "  }\n",
           "GINI-xv6: kalloc shadow hook")

append_once("kernel/kalloc.c", """
// GINI-xv6: the physical-page allocation bitmap — 1 bit per page. This is what makes a student
// page allocator SAFE to run: without it, a shadow that hands back a live page corrupts memory
// undetectably. 4 KB of state for 128 MiB of RAM, updated inside the lock kalloc/kfree already
// take, and it is also the data source for the Memory Lab's fragmentation view.
uint8 gini_pagemap[((PHYSTOP - KERNBASE) / PGSIZE + 7) / 8];
uint64 gini_ka_shadow;

static inline uint64 gini_pg_idx(uint64 pa) { return (pa - KERNBASE) / PGSIZE; }

static int
gini_pg_inrange(uint64 pa)
{
  return pa >= KERNBASE && pa < PHYSTOP;
}

void
gini_page_set(uint64 pa)
{
  uint64 i;
  if(!gini_pg_inrange(pa)) return;
  i = gini_pg_idx(pa);
  gini_pagemap[i / 8] |= (1 << (i %% 8));
}

void
gini_page_clear(uint64 pa)
{
  uint64 i;
  if(!gini_pg_inrange(pa)) return;
  i = gini_pg_idx(pa);
  gini_pagemap[i / 8] &= ~(1 << (i %% 8));
}

int
gini_page_isset(uint64 pa)
{
  uint64 i;
  if(!gini_pg_inrange(pa)) return 0;
  i = gini_pg_idx(pa);
  return (gini_pagemap[i / 8] >> (i %% 8)) & 1;
}

void *
gsh_kalloc(void *arg)
{
  (void)arg;
  return kalloc_shadow();
}

// EXACT, not probabilistic: the page must be aligned, inside RAM, and genuinely free per the
// bitmap. (xv6's kfree also poisons freed pages with 0x01, which is a cheap secondary check —
// but the bitmap is authoritative and costs one bit.)
int
gsh_kalloc_valid(void *arg, void *ans)
{
  uint64 pa = (uint64)ans;
  (void)arg;
  if(pa %% PGSIZE)            return 0;
  if(!gini_pg_inrange(pa))    return 0;
  if((char *)pa < end)        return 0;    // below the kernel's own end = not allocatable
  return !gini_page_isset(pa);
}

// Free-page count + the largest CONTIGUOUS free run — the fragmentation score a buddy-allocator
// mission is graded on (a first-fit list scores badly here once memory churns).
void
gini_kadump(void)
{
  uint64 npages = (PHYSTOP - KERNBASE) / PGSIZE;
  uint64 free = 0, run = 0, best = 0;
  for(uint64 i = 0; i < npages; i++){
    if(gini_page_isset(KERNBASE + i * PGSIZE)){
      run = 0;
    } else {
      free++; run++;
      if(run > best) best = run;
    }
  }
  %(P)s("KA free %%d total %%d maxrun %%d shadow %%d\\n",
        (int)free, (int)npages, (int)best, (int)gini_ka_shadow);
}
""" % {"P": PRINT}, "GINI-xv6: page bitmap + kalloc shadow")

# 3f) spinlock.c/h — LOCK CONTENTION telemetry. The one phenomenon in this kernel that is
#     completely invisible today, and the thing 6.1810 students read as text from a test program
#     while GINI students could watch it move.
#
#     Counters are aggregated BY LOCK NAME, not per instance: the 64 per-process locks are all
#     initlock(..., "proc") and what a student wants is "how contended is the proc lock", not 64
#     rows. Since `name` is a string literal, its POINTER identifies the group — so the slot is
#     resolved once in initlock and cached in the lock itself, keeping acquire() O(1).
# The stat struct is defined HERE rather than in defs.h: spinlock.h is included before defs.h in
# most kernel files, so the member below needs the type in scope already.
regex_once("kernel/spinlock.h",
           r"(// Mutual exclusion lock\.\n)",
           "// GINI-xv6: per-NAME lock contention counters (see spinlock.c).\n"
           "#define GINI_NLOCK 24\n"
           "struct gini_lockstat {\n"
           "  char *name;\n"
           "  uint64 acquires;   // times this lock was taken\n"
           "  uint64 spins;      // failed test-and-set attempts = time burned waiting\n"
           "};\n"
           "struct gini_lockstat *gini_lock_slot(char *name);\n\n"
           r"\1",
           "GINI-xv6: lockstat type")

regex_once("kernel/spinlock.h",
           r"(  struct cpu \*cpu; // The cpu holding the lock\.\n)",
           r"\1  struct gini_lockstat *gstat;  // GINI-xv6: contention counters for this lock's NAME\n",
           "GINI-xv6: spinlock stat slot")

regex_once("kernel/spinlock.c",
           r"(initlock\(struct spinlock \*lk, char \*name\)\n\{\n)",
           r"\1  lk->gstat = gini_lock_slot(name);   // GINI-xv6: resolve the counter slot ONCE\n",
           "GINI-xv6: initlock stat slot")

# count the spin iterations — that IS contention: every iteration is a failed test-and-set
# because another CPU holds the lock.
regex_once("kernel/spinlock.c",
           r"  while \(__atomic_exchange_n\(&lk->locked, 1, __ATOMIC_ACQUIRE\) != 0\)\n    ;\n",
           "  uint64 gspins = 0;               // GINI-xv6: failed test-and-set attempts\n"
           "  while (__atomic_exchange_n(&lk->locked, 1, __ATOMIC_ACQUIRE) != 0)\n"
           "    gspins++;\n"
           "  if (lk->gstat) {                 // atomic: locks sharing a NAME share this slot\n"
           "    __sync_fetch_and_add(&lk->gstat->acquires, 1);\n"
           "    if (gspins)\n"
           "      __sync_fetch_and_add(&lk->gstat->spins, gspins);\n"
           "  }\n",
           "GINI-xv6: acquire contention counters")

append_once("kernel/spinlock.c", """
// GINI-xv6: lock-contention telemetry. `acquires` counts how often a lock was taken; `spins`
// counts failed test-and-set attempts — i.e. time a CPU burned waiting for another CPU. The
// ratio is the number the lock lab is about, and it is ZERO on a single core (nobody to contend
// with), which is why GINI now boots xv6 multi-core.
struct gini_lockstat gini_locks[GINI_NLOCK];

// Resolve (or claim) the slot for this lock NAME. Called once per lock from initlock, so the
// linear scan never appears on the acquire path.
struct gini_lockstat *
gini_lock_slot(char *name)
{
  int i;
  if(name == 0)
    return 0;
  for(i = 0; i < GINI_NLOCK; i++){
    if(gini_locks[i].name == name)
      return &gini_locks[i];
    if(gini_locks[i].name == 0){
      gini_locks[i].name = name;
      return &gini_locks[i];
    }
  }
  return 0;                      // table full: this lock goes uncounted rather than mis-counted
}

void
gini_lockreset(void)
{
  for(int i = 0; i < GINI_NLOCK; i++){
    gini_locks[i].acquires = 0;
    gini_locks[i].spins = 0;
  }
}

void
gini_lockdump(void)
{
  %(P)s("LOCKCPU %%d\\n", NCPU);
  for(int i = 0; i < GINI_NLOCK; i++){
    if(gini_locks[i].name == 0)
      continue;
    %(P)s("LOCK %%s %%d %%d\\n", gini_locks[i].name,
          (int)gini_locks[i].acquires, (int)gini_locks[i].spins);
  }
}
""" % {"P": PRINT}, "GINI-xv6: lock contention telemetry")

# 4) defs.h — prototypes for the non-static additions. The syscall counter/ring types + externs
# live here (declared BEFORE syscall() uses them; defined in syscall.c).
append_once("kernel/defs.h", """
// GINI-xv6 additions
// EVENT CLOCK: one monotonic counter stamped into every recorded event, so the trap, syscall
// and page-fault rings MERGE-SORT into a single ordered story (the OS HUD X-ray). Ticks cannot
// do this — a whole fork/exec happens inside one ~0.5s GINI tick. Declared FIRST because the
// ring externs below use it.
#define GINI_RING 256      // per-ring capacity: one program launch must fit in a ring
void            vmprint(pagetable_t);
void            gini_dump(void);
void            gini_vmdump(void);
void            gini_fsdump(void);
void            gini_logdump(void);
void            gini_scdump(void);
void            gini_break(void);
void            gini_kill(int);
void            gini_setprio(int, int);
void            gini_setticket(int, int);
struct proc*    gini_pick(void);
extern int      sched_policy;
extern int      sched_quantum;
struct gini_sc { int pid; int num; uint64 a0; uint64 ret; uint64 seq; };
extern uint64   gini_sccount[64];
extern struct gini_sc gini_ring[GINI_RING];
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
// sstatus/sie/sip are captured AT TRAP TIME: the live CSR dump can only ever describe the console
// interrupt that the dump itself caused, so honest interrupt state has to be recorded here.
struct gini_trap { int pid; int kind; uint64 cause; uint64 epc; uint64 tval;
                   uint64 sstatus; uint64 sie; uint64 sip; uint64 seq; };
extern uint64   gini_vmf_ok, gini_vmf_fail;   // vm shadow: handled vs. fell-through
extern uint64   gini_trapcount[6];
extern struct gini_trap gini_traps[GINI_RING];
extern int      gini_traps_i;
""", "GINI-xv6 trap-taxonomy additions")

# 4a2) defs.h — the SHADOW types + prototypes (used by proc.c/console.c; declared before use).
append_once("kernel/defs.h", """
// GINI-xv6 SHADOW additions
#ifndef GINI_SCHED_HASH
#define GINI_SCHED_HASH "baseline"
#endif
// A shadow is a decision the student may take over. `shadow` returns their answer (0 = "not
// implemented", run the primary); `valid` is the kernel's sanity check on that answer. The
// scheduler could skip validation because a bad pick only wastes a slice, but an allocator that
// hands back a live block corrupts the disk — so every shadow now gets checked, and a rejected
// answer falls back to the primary and is COUNTED instead of being acted on.
struct gini_shadow {
  char *name;
  char *subsystem;                       // "sched" | "vm" | "fs" — which lab owns it
  void *(*shadow)(void *arg);
  int  (*valid)(void *arg, void *ans);   // 0 = no check needed
  int enabled;
  int active;
  int faults;
  int rejects;                           // answers the validator threw out
  int calls;                             // times the student's code was asked
};
extern struct gini_shadow gini_shadow[];
#define GINI_NPOLICY 3            // shadows 0..2 are the scheduler policies (indexed by sched_policy)
#define GINI_SH_VMFAULT 3         // vm: lazy/demand page-fault handling
#define GINI_SH_BGET    4         // fs: buffer-cache eviction (which buffer to recycle)
#define GINI_SH_BALLOC  5         // fs: disk-block allocation (which free block to hand out)
#define GINI_SH_KALLOC  6         // vm: physical page allocation (which free page to hand out)
#define GINI_NSHADOW 7            // total registry size
void *          gini_shadow_call(struct gini_shadow *s, void *arg);
void            gini_shadow_toggle(int i);
void            gini_shadow_reset(void);
void            gini_shadowdump(void);
struct proc*    pick_rr_shadow(void);
struct proc*    pick_prio_shadow(void);
struct proc*    pick_lottery_shadow(void);
// vm shadow: the args the student's handler receives, plus the adapter/validator defined in vm.c
struct gini_vmf_arg { pagetable_t pt; uint64 psz; uint64 va; int read; };
void *          gsh_vmfault(void *arg);
int             gsh_vmfault_valid(void *arg, void *ans);
uint64          vmfault_shadow(pagetable_t pt, uint64 psz, uint64 va, int read);
// fs shadow (buffer-cache eviction): the student gets the whole buffer array so they can write a
// policy over it (recency lives in b->lastuse) without needing bcache's private linked list.
struct buf;
struct gini_bget_arg { uint dev; uint blockno; struct buf *bufs; int nbuf; };
void *          gsh_bget(void *arg);
int             gsh_bget_valid(void *arg, void *ans);
struct buf *    bget_evict_shadow(uint dev, uint blockno, struct buf *bufs, int nbuf);
void            gini_bcdump(void);
extern uint64   gini_bc_hits, gini_bc_misses, gini_bc_evicts;
// fs shadow (block allocation): the student picks a FREE block; the kernel marks the bitmap.
struct gini_balloc_arg { uint dev; uint size; uint last; };
void *          gsh_balloc(void *arg);
int             gsh_balloc_valid(void *arg, void *ans);
uint            balloc_shadow(uint dev, uint nblocks, uint last);
int             gini_block_free(uint dev, uint bno);
void            gini_ba_note(uint bno);
void            gini_bmapdump(void);
extern uint64   gini_ba_allocs, gini_ba_gapsum;
extern uint     gini_ba_last;
// vm shadow (physical page allocation) + the bitmap that makes validating it possible
void *          gsh_kalloc(void *arg);
int             gsh_kalloc_valid(void *arg, void *ans);
void *          kalloc_shadow(void);
// first address past the kernel image — a page allocator must not hand out anything below it
// (kalloc.c has this extern privately; the shadow files need it too)
extern char     end[];
void            gini_page_set(uint64 pa);
void            gini_page_clear(uint64 pa);
int             gini_page_isset(uint64 pa);
void            gini_kadump(void);
extern uint64   gini_ka_shadow;
// lock contention telemetry. NOTE: deliberately NO `extern struct gini_lockstat gini_locks[]`
// here — start.c includes defs.h WITHOUT spinlock.h, so the type is incomplete there and an
// array of an incomplete type is a hard error. Nothing outside spinlock.c needs the array.
extern uint64   gini_seq;
uint64          gini_stamp(void);
void            gini_lockdump(void);
void            gini_lockreset(void);
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
// Each function below MIRRORS the shipped scheduler for that policy. Some are DELIBERATELY FLAWED —
// that IS the assignment: the Machine Lab shows the misbehaviour; find the bug and fix it here,
// then click Load. `round-robin` is correct, included as a worked example of the contract.
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

// ROUND-ROBIN — correct. A worked example of the pick contract; nothing to fix here.
struct proc *
pick_rr_shadow(void)
{
  static int rr = 0;
  for(int i = 0; i < NPROC; i++){
    struct proc *p = &proc[(rr + i) % NPROC];
    if(p->state == RUNNABLE){ rr = (rr + i + 1) % NPROC; return p; }
  }
  return 0;
}

// PRIORITY — DELIBERATELY FLAWED. Ties go to the lowest-slot (≈lowest pid) proc and the aging is
// weak (wait_ticks / 8), so one process hogs the CPU and the others starve (watch the ⚠ badge).
// Your job: make it fair — e.g. round-robin among the highest-priority runnable procs, and/or age
// strongly enough that a starved proc is promoted. Lower priority NUMBER = higher priority.
struct proc *
pick_prio_shadow(void)
{
  struct proc *p, *best = 0;
  int best_eff = 0;
  for(p = proc; p < &proc[NPROC]; p++){
    if(p->state != RUNNABLE)
      continue;
    p->wait_ticks++;
    int eff = p->priority - p->wait_ticks / 8;
    if(best == 0 || eff < best_eff){ best = p; best_eff = eff; }
  }
  if(best){ best->wait_ticks = 0; return best; }
  return 0;
}

// LOTTERY — DELIBERATELY FLAWED. Picks UNIFORMLY at random and ignores p->tickets, so CPU share is
// even no matter how many tickets a proc holds. Your job: weight the draw by tickets so a proc with
// N tickets is N× as likely to be chosen (sum tickets, draw in [0,total), walk until the running
// total passes the draw).
struct proc *
pick_lottery_shadow(void)
{
  static uint lseed = 88172645u;
  struct proc *p;
  int n = 0;
  for(p = proc; p < &proc[NPROC]; p++)
    if(p->state == RUNNABLE)
      n++;
  if(n == 0)
    return 0;
  lseed ^= lseed << 13; lseed ^= lseed >> 17; lseed ^= lseed << 5;
  int win = lseed % n, i = 0;
  for(p = proc; p < &proc[NPROC]; p++){
    if(p->state != RUNNABLE)
      continue;
    if(i++ == win)
      return p;
  }
  return 0;
}
'''
# The VM shadow lives in its OWN student file: one file per subsystem, each independently
# present/enabled, so a student working on paging never has to look at the scheduler file.
_SHADOW_VM_STUB = '''// kernel/shadows/gini_vm.c — YOUR virtual-memory code. GINI bind-mounts this file; edit it and
// click Load to rebuild the kernel with your version.
//
// LAZY ALLOCATION. sbrklazy() grows a process's address space WITHOUT allocating memory. The
// pages do not exist until the process touches one, which traps here. Your job: allocate a
// physical page, zero it, map it at the faulting address, and return the physical address.
// Return 0 for "not mine" — the shipped implementation then runs, so a half-finished handler
// never breaks the machine.
//
// GINI validates whatever you return: it must be a page-aligned physical page inside RAM, and
// the faulting address must genuinely be mapped afterwards. A wrong answer is REJECTED (the
// shipped version runs instead) and counted in the Machine Lab — it can never corrupt memory.
//
// Useful helpers (kernel/defs.h): kalloc(), kfree(), memset(), mappages(), ismapped(),
// walk(pagetable, va, alloc)   ·   PGSIZE, PGROUNDDOWN(va), PTE_R/PTE_W/PTE_U
#include "../types.h"
#include "../param.h"
#include "../memlayout.h"
#include "../riscv.h"
#include "../spinlock.h"
#include "../proc.h"
#include "../defs.h"

// Handle a demand/lazy page fault at `va` for a process whose address space is `psz` bytes.
// `read` is 1 for a load fault, 0 for a store fault.
// Return: the physical address you mapped, or 0 to let the shipped implementation handle it.
uint64
vmfault_shadow(pagetable_t pt, uint64 psz, uint64 va, int read)
{
  (void)pt; (void)psz; (void)va; (void)read;
  return 0;        // not implemented -> the shipped lazy allocator runs
}

// ---------------------------------------------------------------------------------------------
// PHYSICAL PAGE ALLOCATION. Every page of memory the kernel hands out comes from here. xv6 keeps
// a simple free LIST, so pages come back in whatever order they were freed — which scatters a
// process's memory across RAM. A better policy keeps free memory in large contiguous runs.
//
//   gini_page_isset(pa)  -> 1 if that physical page is currently ALLOCATED
//   end                  -> first address PAST the kernel image; pages below it are not
//                           yours to give out (start your scan at PGROUNDUP((uint64)end))
//   KERNBASE .. PHYSTOP  -> the physical range; pages are PGSIZE apart and page-aligned
//
// Return a FREE, page-aligned physical address, or 0 to let the shipped free-list allocator run.
//
// GINI keeps a bitmap of every physical page and checks your answer against it, so returning a
// page that is already in use is REJECTED and counted — it can never corrupt memory. Your score
// is the largest contiguous free run (see the Memory Lab).
void *
kalloc_shadow(void)
{
  return 0;        // not implemented -> the shipped free-list allocator runs
}
'''

_SHADOW_FS_STUB = '''// kernel/shadows/gini_fs.c — YOUR file-system code. GINI bind-mounts this file; edit it and
// click Load to rebuild the kernel with your version.
//
// BUFFER-CACHE EVICTION. xv6 keeps NBUF disk blocks in memory. When a block is needed that is
// not cached, some buffer must be recycled — and WHICH one you pick is the whole subject of
// cache replacement. (xv6 has no swapping, so this is the only real replacement policy in the
// kernel; everything you know about LRU, clock and random applies right here.)
//
// You are handed the whole buffer array. For each buffer:
//   b->refcnt   : 0 = free to recycle. ANYTHING ELSE IS IN USE — never return it.
//   b->lastuse  : tick of the last cache hit on this buffer (recency: smaller = older)
//   b->blockno  : which disk block it currently holds
//   b->valid    : has data been read from disk yet
//
// Return the buffer to recycle, or 0 to let the shipped LRU policy decide.
//
// GINI validates your answer: it must be one of these buffers and must have refcnt == 0. A bad
// answer is REJECTED (the shipped policy runs instead) and counted in the Machine Lab — so you
// cannot corrupt the file system here, only lose hit rate. Watch the cache grid in the Storage
// Lab: your victims flash red as they are recycled.
#include "../types.h"
#include "../param.h"
#include "../memlayout.h"
#include "../riscv.h"      // pagetable_t — defs.h needs it
#include "../spinlock.h"
#include "../sleeplock.h"
#include "../fs.h"
#include "../buf.h"
#include "../defs.h"       // gini_block_free(), kalloc(), and friends

struct buf *
bget_evict_shadow(uint dev, uint blockno, struct buf *bufs, int nbuf)
{
  (void)dev; (void)blockno; (void)bufs; (void)nbuf;
  return 0;        // not implemented -> the shipped LRU policy runs
}

// ---------------------------------------------------------------------------------------------
// BLOCK ALLOCATION. When a file grows, the kernel needs a free disk block — and WHICH one it
// picks decides whether the file's blocks end up next to each other (fast) or scattered across
// the disk (slow). The shipped allocator takes the first free block it finds, which fragments
// badly once files are created and deleted.
//
//   gini_block_free(dev, bno)  -> 1 if block `bno` is free in the on-disk bitmap
//   nblocks                    -> total blocks on the disk (valid data blocks are below this)
//   last                       -> the block handed out most recently: allocate NEAR it for
//                                 locality, and the mean gap (your score) drops
//
// Return a FREE block number, or 0 to let the shipped allocator decide.
//
// You only CHOOSE — GINI marks the bitmap and zeroes the block for you, and validates that your
// answer really is a free data block first. A wrong answer is REJECTED and counted, so a buggy
// policy costs you locality, never the file system.
uint
balloc_shadow(uint dev, uint nblocks, uint last)
{
  (void)dev; (void)nblocks; (void)last;
  return 0;        // not implemented -> the shipped first-fit allocator runs
}
'''

if (ROOT / "kernel").exists():
    _shdir = ROOT / "kernel" / "shadows"
    _shdir.mkdir(exist_ok=True)
    # one file per subsystem: {filename: stub}
    for _fname, _stub in (("gini_sched.c", _SHADOW_STUB), ("gini_vm.c", _SHADOW_VM_STUB),
                          ("gini_fs.c", _SHADOW_FS_STUB)):
        _shfile = _shdir / _fname
        if not _shfile.exists():
            _shfile.write_text(_stub)
            applied.append(f"kernel/shadows/{_fname}: created")
        else:
            applied.append(f"kernel/shadows/{_fname}: already present")
    _mk, _mksrc = (ROOT / "Makefile"), None
    if _mk.exists():
        _mksrc = _mk.read_text()
    if _mksrc is None:
        skipped.append("Makefile: not found (shadow OBJS)")
    else:
        for _obj in ("gini_sched.o", "gini_vm.o", "gini_fs.o"):
            if f"$K/shadows/{_obj}" in _mksrc:
                applied.append(f"Makefile: {_obj} already registered")
                continue
            _mksrc, _n = re.subn(r"(OBJS = \\\n)", r"\1  $K/shadows/" + _obj + r" \\\n",
                                 _mksrc, count=1)
            if _n:
                _mk.write_text(_mksrc)
                applied.append(f"Makefile: registered kernel/shadows/{_obj}")
            else:
                skipped.append(f"Makefile: OBJS anchor not found for {_obj}")

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
    // per-proc alarm state (the sigalarm lab): period, ticks-since, handler VA, in-handler flag.
    // GINI owns these fields so the dump always compiles; the student writes sigalarm/sigreturn
    // (via the Syscall Builder) that DRIVE them, and this line is the live proof it works.
    PRINTF("ALARM %d %d %d %p %d\\n", p->pid, p->gini_alarm_interval, p->gini_alarm_ticks,
           (void*)p->gini_alarm_handler, p->gini_alarm_on);
  }
  PRINTF("SCHED policy %d quantum %d\\n", sched_policy, sched_quantum);
  // policy roster (id -> display name) so the UI's selector is DATA-DRIVEN: add a policy in the
  // kernel and it auto-appears in the dropdown with no frontend change. Keep this list in step with
  // gini_shadow[]/gini_pick() when you add a policy (e.g. shortest-job-first).
  { static char *gpn[] = { "round-robin", "priority", "lottery" };
    for(int gp = 0; gp < 3; gp++) PRINTF("POLICY %d %s\\n", gp, gpn[gp]); }
  // GINI-xv6: mode-time counters (user/kernel/idle timer ticks) so the CPU face can show the
  // us/sy/idle split, + this hart's control CSRs (trap vector, interrupt-enable config, last
  // trap cause). SIE reads 0 here (we're inside a handler) — the UI leans on `sie` (the enabled
  // sources) for the honest interrupt state, not the momentary global bit.
  { extern uint64 gini_ut, gini_kt, gini_it;
    PRINTF("MODETIME user %d kernel %d idle %d\\n", (int)gini_ut, (int)gini_kt, (int)gini_it); }
  PRINTF("CSR sstatus %p sie %p sip %p stvec %p scause %p sepc %p\\n",
         (void*)r_sstatus(), (void*)r_sie(), (void*)r_sip(),
         (void*)r_stvec(), (void*)r_scause(), (void*)r_sepc());
  // per-CPU: which pid each core runs (Gantt strips) + that proc's live registers (from its
  // trapframe) — so every CPU has its own register/memory view, not just one.
  for(int ci = 0; ci < NCPU; ci++){
    struct proc *rp = cpus[ci].proc;
    if(rp){
      PRINTF("CPU %d pid %d\\n", ci, rp->pid);
      if(rp->trapframe){
        struct trapframe *tf = rp->trapframe;
        PRINTF("REGS cpu %d pid %d pc %p sp %p ra %p s0 %p a0 %p a7 %p satp %p sz %p\\n",
               ci, rp->pid, (void*)tf->epc, (void*)tf->sp, (void*)tf->ra, (void*)tf->s0,
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
  // vm-shadow telemetry: page faults the student's handler took vs. ones that fell through
  %(P)s("VMF handled %%d fellthrough %%d\\n", (int)gini_vmf_ok, (int)gini_vmf_fail);
  gini_kadump();          // GINI-xv6: page-allocator free/fragmentation counters
  for(p = proc; p < &proc[NPROC]; p++){
    if(p->state == RUNNING){
      vmprint(p->pagetable);
      return;
    }
  }
}
""" % {"P": PRINT}, "GINI-xv6: print the RUNNING process's page table")

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

// GINI-xv6: CONTROL-PLANE kill. The UI's Kill button used to type `kill <pid>` at the shell, so the
// kill had to be SCHEDULED (sh wakes, forks, execs kill) — under load it waited behind the very
// workload it was killing (looked unresponsive). This kills a specific pid straight from the console
// interrupt (like gini_break/Ctrl-C): immediate, load-independent. Only flips killed 0->1 (no
// p->lock; cons.lock is held) — the victim exits at its next timer trap. Sleeping victims see it on
// wake. The shell `kill` still works (typed in the Terminal); this is just the responsive button.
void
gini_kill(int pid)
{
  for(struct proc *p = proc; p < &proc[NPROC]; p++){
    if(p->pid == pid && p->pid > 2){
      p->killed = 1;
      PRINTF("[gini] killed pid %d\\n", pid);
      return;
    }
  }
  PRINTF("[gini] kill: no pid %d\\n", pid);
}

// GINI-xv6: CONTROL-PLANE per-proc scheduling setters. Without these every proc is priority 10 /
// 1 ticket, so the PRIORITY scheduler acts like round-robin and LOTTERY weighting is invisible.
// Set from the console interrupt (cons.lock held) — a benign int write the scheduler reads on its
// next pick. The UI drives these so priority/lottery experiments have real differences to schedule
// on, and the priority-fix / lottery-fix assignments become gradable.
void
gini_setprio(int pid, int v)
{
  for(struct proc *p = proc; p < &proc[NPROC]; p++){
    if(p->pid == pid){ p->priority = v; PRINTF("[gini] pid %d priority %d\\n", pid, v); return; }
  }
  PRINTF("[gini] setprio: no pid %d\\n", pid);
}

void
gini_setticket(int pid, int n)
{
  for(struct proc *p = proc; p < &proc[NPROC]; p++){
    if(p->pid == pid){ p->tickets = n; PRINTF("[gini] pid %d tickets %d\\n", pid, n); return; }
  }
  PRINTF("[gini] setticket: no pid %d\\n", pid);
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
  gini_bcdump();          // GINI-xv6: buffer-cache counters + per-buffer state (the cache grid)
  gini_bmapdump();        // GINI-xv6: allocator counters + the free/used block map
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
           r"  case C('X'): gini_shadow_reset(); break;  // GINI: clear reject/call counters\n"
           f"  case C('L'): {PRINT}(\"%c\",30); gini_lockdump(); {PRINT}(\"%c\",31); break;  "
           "// GINI: lock contention (0x1e/0x1f-bracketed)\n"
           r"  case C('Z'): gini_lockreset(); break;  // GINI: zero the lock counters\n"
           f"  case C('W'): {PRINT}(\"%c\",30); gini_shadowdump(); {PRINT}(\"%c\",31); break;  "
           "// GINI: shadow manifest (0x1e/0x1f-bracketed)",
           "gini_dump();")

# 4f2) console.c — CONTROL-PLANE kill (pid-carrying). The switch above handles single control chars;
# a kill needs a pid, so we add a tiny state machine BEFORE the switch: Ctrl-Y (C('Y')) starts pid
# entry, subsequent digits accumulate, and any terminator (e.g. '\n') fires gini_kill() straight from
# the UART interrupt — no shell scheduling, so the Kill button can't be starved by the workload it's
# killing (the old `kill <pid>` typed at the shell could). Digits are consumed here so they never
# echo or reach the shell line buffer. cons.lock is held across consoleintr, so release before the
# early return. Anchored on consoleintr's `switch(c)` (unique in console.c).
regex_once("kernel/console.c",
           r"(switch\s*\(c\)\s*\{)",
           "static int gini_killpid = -1;  // GINI: control-plane kill pid-entry (-1 = idle)\n"
           "  if(gini_killpid >= 0){\n"
           "    if(c >= '0' && c <= '9') gini_killpid = gini_killpid * 10 + (c - '0');\n"
           "    else { gini_kill(gini_killpid); gini_killpid = -1; }\n"
           "    release(&cons.lock); return;\n"
           "  }\n"
           "  if(c == C('Y')){ gini_killpid = 0; release(&cons.lock); return; }\n"
           r"  \1",
           "GINI: control-plane kill pid-entry")

# 4f3) console.c — CONTROL-PLANE per-proc scheduling setters. Two-number entry ("<pid> <val>"):
# Ctrl-O begins a priority set, Ctrl-N a tickets set; digits accumulate into pid, a space/comma
# advances to the value, any terminator ('\n') fires gini_setprio/gini_setticket from the interrupt.
# Same no-scheduling, no-echo pattern as the kill entry. Anchored on consoleintr's switch(c).
regex_once("kernel/console.c",
           r"(switch\s*\(c\)\s*\{)",
           "static int gini_ctl_op = 0, gini_ctl_pid = 0, gini_ctl_val = 0, gini_ctl_stage = 0;"
           "  // GINI: control-plane sched set\n"
           "  if(gini_ctl_op){\n"
           "    if(c >= '0' && c <= '9'){ if(gini_ctl_stage == 0) gini_ctl_pid = gini_ctl_pid*10 + (c-'0');"
           " else gini_ctl_val = gini_ctl_val*10 + (c-'0'); }\n"
           "    else if(c == ' ' || c == ','){ gini_ctl_stage = 1; }\n"
           "    else { if(gini_ctl_op == 1) gini_setprio(gini_ctl_pid, gini_ctl_val);"
           " else gini_setticket(gini_ctl_pid, gini_ctl_val); gini_ctl_op = 0; }\n"
           "    release(&cons.lock); return;\n"
           "  }\n"
           "  if(c == C('O')){ gini_ctl_op = 1; gini_ctl_pid = gini_ctl_val = gini_ctl_stage = 0;"
           " release(&cons.lock); return; }\n"
           "  if(c == C('N')){ gini_ctl_op = 2; gini_ctl_pid = gini_ctl_val = gini_ctl_stage = 0;"
           " release(&cons.lock); return; }\n"
           r"  \1",
           "GINI: control-plane sched set")

# 4f4) console.c — toggle ANY shadow by index: Ctrl-G <digits> <terminator>. Ctrl-K only ever
# reached the current scheduler policy, which stops working the moment shadows exist outside the
# scheduler (vm, fs). One digit-entry mechanism now covers every present and future shadow.
regex_once("kernel/console.c",
           r"(switch\s*\(c\)\s*\{)",
           "static int gini_shidx = -1;  // GINI: shadow-toggle index entry (-1 = idle)\n"
           "  if(gini_shidx >= 0){\n"
           "    if(c >= '0' && c <= '9') gini_shidx = gini_shidx * 10 + (c - '0');\n"
           "    else { gini_shadow_toggle(gini_shidx); gini_shidx = -1; }\n"
           "    release(&cons.lock); return;\n"
           "  }\n"
           "  if(c == C('G')){ gini_shidx = 0; release(&cons.lock); return; }\n"
           r"  \1",
           "GINI: shadow toggle by index")

# 4g) syscall.c — per-syscall counters (histogram) + a recent-call trace ring (strace view).
#     The definitions + gini_scdump go at end-of-file (types/externs are declared in defs.h so
#     syscall() can use them above); Ctrl-S dumps them.
_SCDUMP = '''
// GINI-xv6: per-syscall counters + recent-call trace ring (Machine Lab histogram + strace).
uint64 gini_sccount[64];
struct gini_sc gini_ring[GINI_RING];
int gini_ring_i;

void
gini_scdump(void)
{
  for(int i = 0; i < 64; i++)
    if(gini_sccount[i])
      PRINTF("SC %d %d\\n", i, (int)gini_sccount[i]);
  int total = gini_ring_i;
  int start = total > GINI_RING ? total - GINI_RING : 0;
  for(int k = start; k < total; k++){
    struct gini_sc *e = &gini_ring[k % GINI_RING];
    PRINTF("TRACE %d %d %p %p %d\\n", e->pid, e->num, (void*)e->a0,
           (void*)e->ret, (int)e->seq);
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
           "    int gsi = gini_ring_i++ % GINI_RING;                    // GINI: trace ring\n"
           "    gini_ring[gsi].pid = p->pid; gini_ring[gsi].num = num;\n"
           "    gini_ring[gsi].a0 = gsc_a0; gini_ring[gsi].ret = p->trapframe->a0;\n"
           "    gini_ring[gsi].seq = gini_stamp();               // GINI: event clock",
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
