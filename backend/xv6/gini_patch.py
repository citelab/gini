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


# Edits a LAB depends on must not fail quietly. regex_once's skip-and-carry-on is right for an
# optional nicety -- the kernel still builds -- but A-Lab 02 needs its policies file, its syscalls
# and its run accounting to EXIST: a silently skipped edit here produces a kernel that builds,
# boots, and cannot run the lab, and nobody finds out until a student does. These record a
# failure, and the script exits non-zero at the end so the image build stops.
failed = []


def regex_must(rel, pattern, repl, marker):
    p, src = _read(rel)
    if src is None:
        failed.append(f"{rel}: file not found ({marker})")
        return
    if marker in src:
        applied.append(f"{rel}: {marker} already present")
        return
    new, n = re.subn(pattern, repl, src, count=1)
    if n:
        p.write_text(new)
        applied.append(f"{rel}: applied {marker}")
    else:
        failed.append(f"{rel}: anchor for {marker} not found")


def write_must(rel, text):
    p = ROOT / rel
    if not p.parent.exists():
        failed.append(f"{rel}: directory missing")
        return
    if p.exists():
        applied.append(f"{rel}: already present")
        return
    p.write_text(text)
    applied.append(f"{rel}: created")


# 1) proc.c — control globals + the policy-aware picker (non-static; unused is fine).
#    scheduler() is wired to call gini_pick() in edit 1b below, so switching sched_policy
#    changes scheduling LIVE (no rebuild). RR/priority/lottery ship built-in; students add
#    MLFQ/stride/etc. via the Scheduler Builder (a new sched_policy case + any per-proc fields).
append_once("kernel/proc.c", """
// GINI-xv6: scheduler control knobs (the Machine Lab bridge writes these live over the serial).
//   sched_policy: 0=round-robin  1=priority (lower number = higher, with aging)  2=lottery
//                 3=stride  (the policies themselves are in kernel/sched_policy.c)
//   sched_quantum: timer ticks per time-slice before preemption.
int sched_policy = 0;
int sched_quantum = 1;

// A-Lab 02's names, declared HERE as well as in defs.h. defs.h is a student file in A-Lab 01 and
// A-Lab 02: a folder keeps the copy it was seeded with, from whatever image was current then, and
// proc.c -- the image's own copy, in A-Lab 01 -- must still compile against an older defs.h that
// has never heard of stride. Duplicate prototypes are legal C; a missing one is a -Werror build
// failure on a machine whose owner changed nothing.
#ifndef GINI_NSCHEDSH
#define GINI_NSCHEDSH 3            // policies 0..2 have a shadow; stride (3) has none
#endif
struct proc *pick_rr(void);
struct proc *pick_prio(void);
struct proc *pick_lottery(void);
struct proc *pick_stride(void);
extern uint64 gini_tick_cycles;

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
// The policies themselves live in kernel/sched_policy.c (A-Lab 02: the one file that holds
// scheduling policy and nothing else); this is only the switch, plus the check that whatever a
// policy returns is something the scheduler can safely lock and run.
// State is read WITHOUT p->lock (like procdump); the caller re-checks RUNNABLE under the lock
// before switching, so a stale read only ever costs one wasted pick.
int gini_pick_rejects = 0;   // policy answers thrown out (SCHED line; User Code shows it)

struct proc *
gini_pick(void)
{
  struct proc *p;

  // SHADOW: run the student's version for the active policy. gini_shadow_call validates the
  // answer (points into proc[], on an entry boundary, RUNNABLE) — a bad pointer is rejected and
  // counted here rather than panicking in scheduler()'s acquire(&p->lock). Only policies 0..2
  // have shadows: the registry continues with the vm and fs entries, so indexing it with
  // policy 3 (stride) would call the page-fault shadow from the scheduler.
  if(sched_policy >= 0 && sched_policy < GINI_NSCHEDSH){
    struct proc *sp = (struct proc *)gini_shadow_call(&gini_shadow[sched_policy], 0);
    if(sp) return sp;
  }

  switch(sched_policy){
  case 1:  p = pick_prio();    break;
  case 2:  p = pick_lottery(); break;
  case 3:  p = pick_stride();  break;
  default: return pick_rr();
  }
  // The same check the shadows get. A policy is student code that runs on every tick: an answer
  // outside proc[], between two entries, or not RUNNABLE would be dereferenced by scheduler()'s
  // acquire() and panic the machine. Reject it, count it, and run round-robin for this one pick.
  if(p && !gsh_proc_valid(0, p)){
    gini_pick_rejects++;
    return pick_rr();
  }
  return p;
}

// GINI-xv6: emit the shadow manifest — one line per shadowable function, so the oracle/AI can tell
// which student shadows are wired and healthy. present = the shadow file differs from the shipped
// baseline (the Load build stamps GINI_SCHED_HASH); active/faults are runtime.
// NOTE: no -D is passed at build time (see Dockerfile _rebuild), so GINI_SCHED_HASH is ALWAYS the
// "baseline" placeholder below and this line reports present=0 hash=baseline. The agent recomputes
// the real md5 of the shadow source and RE-STAMPS the manifest before the Lab sees it, so the
// kernel-side hash here is a placeholder, not the source of truth. See _stamp_manifest in the agent.
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
           r'int gini_qticks[NCPU];\nextern int sched_quantum;\nextern uint64 gini_tick_cycles;\n'
           r'// GINI-xv6: mode-time ticks — sampled at each timer interrupt by privilege source\n'
           r'// (user-entry trap => user, kernel-entry => kernel, or idle when no proc runs). The\n'
           r'// CPU face reads the delta as a user/kernel/idle split, like top\'s us/sy. No CSR\n'
           r'// reads needed: the trap entry path already tells us where the CPU was.\n'
           r'uint64 gini_ut, gini_kt, gini_it;\n',
           "GINI-xv6: PER-CPU time-slice counter")

# 2a) trap.c — the LIVE PAGE-FAULT RING. Every user page fault (scause 12 instruction / 13 load /
#     15 store) is recorded (pid, cause, faulting VA from stval, faulting PC) into a 256-entry ring,
#     so the Memory face can show demand paging, stack growth, and COW copies AS THEY HAPPEN. The
#     kernel only CAPTURES — GINI classifies (lazy / cow-write / illegal) on its side. The ring
#     functions are APPENDED (no regex-escape hazard); usertrap() reaches gini_fault_note() through
#     its defs.h prototype, and console.c reaches gini_faultdump() the same way.
_GINI_FAULT = '''
// GINI-xv6: live page-fault ring — captured in usertrap, dumped over the serial (no gdb halt).
struct gini_flt { int pid; uint64 scause; uint64 va; uint64 epc; uint64 seq; };
struct gini_flt gini_flt[GINI_RING];
// uint64, NOT int: this counts every event forever. At int width it would wrap negative
// after ~2 billion events and `k % GINI_RING` would then index BEFORE the array — an
// out-of-bounds kernel write. Unsigned wrap is well-defined and, at 64 bits, unreachable.
uint64 gini_flt_i;

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
  uint64 total = gini_flt_i;
  uint64 start = total > GINI_RING ? total - GINI_RING : 0;
  for(uint64 k = start; k < total; k++){
    struct gini_flt *e = &gini_flt[k % GINI_RING];
    PRINTF("FLT %d %lu %p %p %lu\\n", e->pid, (uint64)e->scause, (void*)e->va, (void*)e->epc,
           (uint64)e->seq);
  }
}
'''
append_once("kernel/trap.c", _GINI_FAULT.replace("PRINTF", PRINT),
            "GINI-xv6: live page-fault ring")

# usertrap(): `if (which_dev == 2)\n    yield();`  (space after `if` in current xv6). Interrupts
# are off at this point, so cpuid() is safe.
regex_must("kernel/trap.c",   # must: A-Lab 02's run accounting (p->rtime) rides on it
           r"if\s*\(which_dev == 2\)\s*\n\s*yield\(\);",
           "if (which_dev == 2) { gini_ut++; p->rtime++; "   # GINI-xv6: user tick; run accounting
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
#      illegal / other — with per-kind counters (the histogram) and a 256-entry ring (the live
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
// uint64, NOT int: this counts every event forever. At int width it would wrap negative
// after ~2 billion events and `k % GINI_RING` would then index BEFORE the array — an
// out-of-bounds kernel write. Unsigned wrap is well-defined and, at 64 bits, unreachable.
uint64 gini_traps_i;

// GINI: one-shot trap capture (Traps & Interrupts face). The face ARMS a kind; the next trap of
// that kind is copied here and the arm clears itself. This replaces a gdb conditional breakpoint
// at usertrap that could only ever see user-mode traps and — worse — changed the answer: halting
// the guest to evaluate the condition let the timer deadline expire with interrupts off, so the
// tick was then taken in kerneltrap (uncatchable) instead of usertrap. Capturing here sees both
// modes, costs one compare per trap when disarmed, and does not perturb what it measures.
// GINI_CATCH_ANY and the externs live in defs.h.
int    gini_catch_kind    = -1;   // -1 disarmed | GINI_CATCH_ANY | GT_*
int    gini_catch_ready   = 0;    // 1 = gini_catch holds a captured trap (cleared on next arm)
struct gini_trap gini_catch;
int    gini_catch_user    = 0;    // 1 = taken from user mode (sstatus.SPP == 0)
uint64 gini_catch_tf[7];          // epc ra sp a0 a1 a2 a7 — valid only when gini_catch_user
int    gini_catch_qticks  = 0;    // this hart's quantum counter AT TRAP TIME
int    gini_catch_quantum = 0;    // sched_quantum at trap time (did this tick preempt?)

// The WHOLE trapframe, twice. `tfin` is the page as uservec left it, copied at the same instant
// as the CSRs above; `tfout` is the same page as userret is about to restore it, copied when the
// caught process next leaves for user mode. Seven registers was not enough to read a system call
// off (a1 and a2 were captured and never shown), and one snapshot could not show the point of the
// page at all: what the kernel CHANGED in it — epc += 4 and the return value laid over a0 for a
// system call, nothing whatsoever for a timer tick. struct copies, so the field list is xv6's own
// and a kernel that reorders struct trapframe still dumps by name (gini_tfprint).
//
// The two copies are paired by a TOKEN, the trap's event-clock stamp plus one (so 0 means none):
// `pend` holds it while the caught trap is still in the kernel, the return hook claims it with a
// CAS and stamps `outseq` with it, and the dump prints `out` only when outseq == inseq. A new
// catch therefore can never be shown with an older trap's return. Two harts writing tfout at
// once needs a re-arm inside the microseconds of another trap's return; that tear is accepted,
// like the ring race (known issue #9).
struct trapframe gini_catch_tfin, gini_catch_tfout;
uint64 gini_catch_inseq   = 0;    // token of the trap whose entry frame is in tfin (0 = none)
uint64 gini_catch_pend    = 0;    // token of a caught user trap that has not yet returned
int    gini_catch_pendpid = 0;    // ...and whose process it is
uint64 gini_catch_outseq  = 0;    // token of the trap whose return frame is in tfout

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
  // WHICH CORE took this trap. On one hart it is always 0 and says nothing; on two it is the
  // whole story, because a PLIC external interrupt is asserted to EVERY enabled hart and only
  // the one that wins plic_claim() services it. Without this the ring cannot tell the core that
  // did the work from the one that trapped and found irq == 0.
  e->hart = cpuid();
  // The process that was INTERRUPTED — not the one the interrupt concerns. A disk completion for
  // process A is stamped with whoever was on this core, because that is all the kernel knows at
  // trap time; the real owner is resolved later, by wakeup() on a channel.
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
  uint64 gseq = gini_stamp();  // GINI: event clock (kept in a local so the capture below matches)
  e->seq = gseq;
  gini_traps_i++;

  // -- one-shot capture (see gini_catch_* above) --------------------------------------------------
  // Sourced from LOCALS + the trap CSRs (which are unchanged since trap entry), NOT from *e: the
  // shared ring slot can tear when two harts land on the same gini_traps_i (accepted ring race,
  // known issue #9), and a torn capture would render as a nonsense frame — the exact thing the
  // Traps face must never show. The CAS makes exactly ONE hart write gini_catch, so co-firing harts
  // cannot splice it either. Lock-free; when disarmed this is one relaxed load and a branch.
  int want = gini_catch_kind;
  if(want != -1 && (want == GINI_CATCH_ANY || want == kind) &&
     __sync_bool_compare_and_swap(&gini_catch_kind, want, -1)){
    gini_catch.hart    = cpuid();
    gini_catch.pid     = p ? p->pid : 0;
    gini_catch.kind    = kind;
    gini_catch.cause   = c;
    gini_catch.epc     = r_sepc();
    gini_catch.tval    = r_stval();
    gini_catch.sstatus = r_sstatus();
    gini_catch.sie     = r_sie();
    gini_catch.sip     = r_sip();
    gini_catch.seq     = gseq;
    // SPP tells us the privilege we interrupted. A kernel-mode trap did NOT go through uservec and
    // wrote NO trapframe — presenting p->trapframe for it shows the process's last USER trap and
    // quietly lies, so the user-register array is zeroed and filled only when we came from user.
    gini_catch_user = ((gini_catch.sstatus & SSTATUS_SPP) == 0);
    for(int i = 0; i < 7; i++) gini_catch_tf[i] = 0;
    gini_catch_pend = 0;          // this catch supersedes any return still pending
    gini_catch_inseq = 0;
    if(gini_catch_user && p && p->trapframe){
      gini_catch_tf[0] = p->trapframe->epc;  gini_catch_tf[1] = p->trapframe->ra;
      gini_catch_tf[2] = p->trapframe->sp;   gini_catch_tf[3] = p->trapframe->a0;
      gini_catch_tf[4] = p->trapframe->a1;   gini_catch_tf[5] = p->trapframe->a2;
      gini_catch_tf[6] = p->trapframe->a7;
      // usertrap has just written epc, so this is the complete page as the trap left it — and
      // BEFORE a system call's epc += 4, which is the change the return copy will show.
      gini_catch_tfin = *p->trapframe;
      gini_catch_inseq = gseq + 1;
      gini_catch_pendpid = p->pid;
      __sync_synchronize();       // pid before the token that makes the return hook look at it
      gini_catch_pend = gseq + 1;
    }
    gini_catch_qticks  = gini_qticks[gini_catch.hart];
    gini_catch_quantum = sched_quantum;
    __sync_synchronize();        // publish the whole frame BEFORE ready is seen
    gini_catch_ready = 1;
  }
}

// The caught trap is about to return to user mode: copy the trapframe as userret will restore it.
// Called from prepare_return() after every kernel field and sepc are set, so nothing the kernel
// does to the page afterwards is missed. A process cannot trap again without first returning, and
// xv6 never reuses a pid, so "the next return of this pid" is this trap's return. A trap that
// never returns (exit, a killed process, a fatal fault) leaves the token pending, and the dump
// simply has no `out` line — which is the truth about it.
void
gini_catchret(void)
{
  uint64 tok = gini_catch_pend;
  if(tok == 0)
    return;
  struct proc *p = myproc();
  if(p == 0 || p->trapframe == 0 || p->pid != gini_catch_pendpid)
    return;
  if(!__sync_bool_compare_and_swap(&gini_catch_pend, tok, 0))
    return;
  gini_catch_tfout = *p->trapframe;
  __sync_synchronize();          // the frame before the token that pairs it
  gini_catch_outseq = tok;
}

// One trapframe on ONE line, every field by name in struct order. A single PRINTF so another
// hart's console output cannot land in the middle of it.
static void
gini_tfprint(char *tag, uint64 tok, struct trapframe *t)
{
  PRINTF("CTF %s %lu %p %p %p %p %p %p %p %p %p %p %p %p %p %p %p %p %p %p %p %p %p %p"
         " %p %p %p %p %p %p %p %p %p %p %p %p %p %p\\n", tag, tok,
         (void*)t->kernel_satp, (void*)t->kernel_sp, (void*)t->kernel_trap, (void*)t->epc,
         (void*)t->kernel_hartid, (void*)t->ra, (void*)t->sp, (void*)t->gp, (void*)t->tp,
         (void*)t->t0, (void*)t->t1, (void*)t->t2, (void*)t->s0, (void*)t->s1,
         (void*)t->a0, (void*)t->a1, (void*)t->a2, (void*)t->a3, (void*)t->a4, (void*)t->a5,
         (void*)t->a6, (void*)t->a7, (void*)t->s2, (void*)t->s3, (void*)t->s4, (void*)t->s5,
         (void*)t->s6, (void*)t->s7, (void*)t->s8, (void*)t->s9, (void*)t->s10, (void*)t->s11,
         (void*)t->t3, (void*)t->t4, (void*)t->t5, (void*)t->t6);
}

void
gini_trapdump(void)
{
  static char *kn[GT_NKIND] = {"syscall","pagefault","timer","device","illegal","other"};
  // The captured trap, if any. Non-destructive read: ready is NOT cleared here, only on the next
  // arm, so a slow poller cannot lose a capture. Fields are all small + bounded (no cumulative
  // counter here, so no (int)-wrap risk — cf. known issue #3), and h%d is labelled like TR's.
  PRINTF("CATCH %d %d %d %p %p %p %d %p %p %p %p %p %p %p %d %d h%d\\n",
         gini_catch_ready, gini_catch.kind, gini_catch_user,
         (void*)gini_catch.cause, (void*)gini_catch.epc, (void*)gini_catch.tval,
         gini_catch.pid,
         (void*)gini_catch_tf[0], (void*)gini_catch_tf[1], (void*)gini_catch_tf[2],
         (void*)gini_catch_tf[3], (void*)gini_catch_tf[4], (void*)gini_catch_tf[5],
         (void*)gini_catch_tf[6],
         gini_catch_qticks, gini_catch_quantum, gini_catch.hart);
  // The whole trapframe on the way in, and on the way out once the trap has returned. Separate
  // lines rather than more CATCH fields: an older agent's CATCH regex keeps matching, and an
  // older kernel simply has no CTF lines (see gini_catch_tfin).
  uint64 in = gini_catch_inseq;
  if(gini_catch_ready && in){
    gini_tfprint("in", in, &gini_catch_tfin);
    uint64 out = gini_catch_outseq;
    __sync_synchronize();        // read the token before the frame it vouches for
    if(out == in)
      gini_tfprint("out", out, &gini_catch_tfout);
  }
  for(int k = 0; k < GT_NKIND; k++)
    PRINTF("TC %d %s %lu\\n", k, kn[k], (uint64)gini_trapcount[k]);
  uint64 total = gini_traps_i;
  uint64 start = total > GINI_RING ? total - GINI_RING : 0;
  for(uint64 k = start; k < total; k++){
    struct gini_trap *e = &gini_traps[k % GINI_RING];
    // `h%d` is LABELLED, unlike every positional field before it. Two bare decimals in a row
    // — seq then hart — would be told apart only by their order, and a reader or a regex that
    // got that wrong would attribute traps to the wrong core in silence. One character of
    // prefix makes the field self-identifying wherever it turns up.
    PRINTF("TR %d %d %p %p %p %p %p %p %lu h%d\\n", e->pid, e->kind,
           (void*)e->cause, (void*)e->epc, (void*)e->tval,
           (void*)e->sstatus, (void*)e->sie, (void*)e->sip, (uint64)e->seq, e->hart);
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
regex_must("kernel/trap.c",   # must: A-Lab 02's run accounting (p->rtime) rides on it
           r"if\s*\(which_dev == 2 && myproc\(\) != 0"
           r"(?: && myproc\(\)->state == RUNNING)?\)\s*\n\s*yield\(\);",
           "if (which_dev == 2) { if (myproc() == 0) gini_it++; "                     # GINI modetime
           "else { gini_kt++; myproc()->rtime++; } } "                                # + run accounting
           "// GINI-xv6 modetime\n  "
           "if (which_dev == 2 && myproc() != 0 && (++gini_qticks[cpuid()] >= sched_quantum)) "
           "{ gini_qticks[cpuid()] = 0; yield(); } // GINI-xv6 quantum k",
           "GINI-xv6 quantum k")

# 2b) clockintr() — set the timer TICK to ~0.5s (QEMU virt timebase is 10 MHz -> 5,000,000).
#     Match the trailing `;` too, so the // comment doesn't swallow the statement terminator.
#     The tick length is a variable (gini_tick_cycles, kernel/gini_sys.c) so A-Lab 02's sharetest
#     can shorten it while it measures: 30 s at 0.5 s is only 60 scheduling decisions, too few
#     for a lottery's share to settle. Everything else keeps the 0.5 s tick the faces are built on.
regex_must("kernel/trap.c",
           r"w_stimecmp\(r_time\(\) \+ 1000000\);",
           "w_stimecmp(r_time() + gini_tick_cycles); // GINI-xv6: settable tick, ~0.5s by default",
           "GINI-xv6: settable tick")

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
  %(P)s("BC hits %%lu misses %%lu evicts %%lu nbuf %%d\\n",
        (uint64)gini_bc_hits, (uint64)gini_bc_misses, (uint64)gini_bc_evicts, NBUF);
  for(int i = 0; i < NBUF; i++){
    struct buf *b = &bcache.buf[i];
    %(P)s("BUF %%d %%d %%d %%d %%lu\\n", i, (int)b->blockno, (int)b->refcnt,
          b->valid, (uint64)b->lastuse);
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
  %(P)s("BA allocs %%lu meangap %%d last %%d nblocks %%d\\n",
        (uint64)gini_ba_allocs,
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

# The bitmap itself, the vm-shadow adapters (gsh_kalloc, gsh_kalloc_valid) and gini_kadump() used
# to be appended here. They live in kernel/gini_mem.c now (section 6), unchanged in behaviour: in
# A-Lab 03 the student's kalloc.c REPLACES this one, so nothing GINI needs at run time may live in
# it. kalloc.c keeps only the calls above, declared in defs.h.

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
    %(P)s("LOCK %%s %%lu %%lu\\n", gini_locks[i].name,
          (uint64)gini_locks[i].acquires, (uint64)gini_locks[i].spins);
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
extern uint64   gini_ring_i;
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
void            gini_catchret(void);     // the caught trap is returning: copy its trapframe
// sstatus/sie/sip are captured AT TRAP TIME: the live CSR dump can only ever describe the console
// interrupt that the dump itself caused, so honest interrupt state has to be recorded here.
struct gini_trap { int pid; int kind; uint64 cause; uint64 epc; uint64 tval;
                   uint64 sstatus; uint64 sie; uint64 sip; uint64 seq; int hart; };
extern uint64   gini_vmf_ok, gini_vmf_fail;   // vm shadow: handled vs. fell-through
extern uint64   gini_trapcount[6];
extern struct gini_trap gini_traps[GINI_RING];
extern uint64   gini_traps_i;
// GINI: one-shot trap capture (see gini_traprec). Armed by the console, read by the trapdump.
#define GINI_CATCH_ANY (-2)
extern int      gini_catch_kind;
extern int      gini_catch_ready;
extern struct gini_trap gini_catch;
extern int      gini_catch_user;
extern uint64   gini_catch_tf[7];
extern int      gini_catch_qticks;
extern int      gini_catch_quantum;
""", "GINI-xv6 trap-taxonomy additions")

# 4a2) defs.h — the SHADOW types + prototypes (used by proc.c/console.c; declared before use).
append_once("kernel/defs.h", """
// GINI-xv6 SHADOW additions
// GINI_SCHED_HASH is a PLACEHOLDER: no -D overrides it at build (Dockerfile _rebuild passes none),
// so the kernel always emits hash=baseline. The agent re-stamps the manifest with the real md5
// (_stamp_manifest), which is what the Lab reads — do not treat this kernel-side value as truth.
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
#define GINI_NPOLICY 4            // policies: 0 rr, 1 priority, 2 lottery, 3 stride
#define GINI_NSCHEDSH 3           // shadows 0..2 are the scheduler policies that HAVE a shadow
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
    // rt = timer ticks this process has been running for (A-Lab 02's measurement).
    PRINTF("PROC %d pri %d tk %d lv %d wait %d rt %lu\\n",
           p->pid, p->priority, p->tickets, p->level, p->wait_ticks, (uint64)p->rtime);
    // per-proc alarm state (the sigalarm lab): period, ticks-since, handler VA, in-handler flag.
    // GINI owns these fields so the dump always compiles; the student writes sigalarm/sigreturn
    // (via the Syscall Builder) that DRIVE them, and this line is the live proof it works.
    PRINTF("ALARM %d %d %d %p %d\\n", p->pid, p->gini_alarm_interval, p->gini_alarm_ticks,
           (void*)p->gini_alarm_handler, p->gini_alarm_on);
    // What this process is waiting FOR. p->chan is set by sleep_prepare and CLEARED by wakeup,
    // so a non-zero chan on a RUNNABLE process is not a bug — it is the moment between "armed to
    // sleep" and actually sleeping, which is exactly the race sleep_prepare exists to close.
    // Printed for every process rather than only sleepers so that moment is visible too.
    if(p->chan)
      PRINTF("WAIT %d %p %s\\n", p->pid, p->chan, gini_chan_name(p->chan));
  }
  // rejects = policy answers gini_pick() threw out; tick = the timer tick in ms (sharetest
  // shortens it while it measures).
  PRINTF("SCHED policy %d quantum %d rejects %d tick %d\\n", sched_policy, sched_quantum,
         gini_pick_rejects, (int)(gini_tick_cycles / 10000));
  // policy roster (id -> display name) so the UI's selector is DATA-DRIVEN: add a policy in the
  // kernel and it auto-appears in the dropdown with no frontend change. Keep this list in step with
  // gini_shadow[]/gini_pick() when you add a policy (e.g. shortest-job-first).
  // gpn[] is sized by its initialiser, not by GINI_NPOLICY: an A-Lab 01 folder's older defs.h
  // still says 3, and the roster then simply stops at lottery -- as that kernel's policies do.
  { static char *gpn[] = { "round-robin", "priority", "lottery", "stride" };
    for(int gp = 0; gp < GINI_NPOLICY && gp < 4; gp++) PRINTF("POLICY %d %s\\n", gp, gpn[gp]); }
  // GINI-xv6: mode-time counters (user/kernel/idle timer ticks) so the CPU face can show the
  // us/sy/idle split, + this hart's control CSRs (trap vector, interrupt-enable config, last
  // trap cause). SIE reads 0 here (we're inside a handler) — the UI leans on `sie` (the enabled
  // sources) for the honest interrupt state, not the momentary global bit.
  { extern uint64 gini_ut, gini_kt, gini_it;
    PRINTF("MODETIME user %lu kernel %lu idle %lu\\n", (uint64)gini_ut, (uint64)gini_kt, (uint64)gini_it); }
  // WHOSE CSRs these are. The dump runs inside consoleintr, on whichever hart won
  // plic_claim() for GINI's own poll — so it is hart 0 on one poll and hart 1 on the next,
  // and the panel could not say which. Naming it turns "(this hart)" from an unanswerable
  // label into a fact.
  PRINTF("CSR sstatus %p sie %p sip %p stvec %p scause %p sepc %p hart %d\\n",
         (void*)r_sstatus(), (void*)r_sie(), (void*)r_sip(),
         (void*)r_stvec(), (void*)r_scause(), (void*)r_sepc(), cpuid());
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
  %(P)s("VMF handled %%lu fellthrough %%lu\\n", (uint64)gini_vmf_ok, (uint64)gini_vmf_fail);
  gini_kadump();          // GINI-xv6: page-allocator free/fragmentation counters
  for(p = proc; p < &proc[NPROC]; p++){
    if(p->state == RUNNING){
      // GINI-xv6 (#4, B3 Option 2): report the address-space anchors instead of leaving the
      // frontend to INFER them from leaf PTEs. p->sz is the program break; TRAPFRAME/TRAMPOLINE
      // are compile-time constants (memlayout.h). regions_from_leaves(leaves, sz) runs on these.
      %(P)s("VR %%p %%p %%p\\n", (void*)p->sz, (void*)TRAPFRAME, (void*)TRAMPOLINE);
      vmprint(p->pagetable);
      return;
    }
  }
}
""" % {"P": PRINT}, "GINI-xv6: print the RUNNING process's page table")

# 4d1) gini_vmdump_all(): EVERY user process's leaf mappings, tagged by pid, so the Memory face
# can put parent and child side by side and DERIVE sharing (same PA in two procs) — the whole
# basis of the copy-on-write experiment, with no kernel refcount required. Emits `VP pid name sz`
# per proc then `VL pid va pa flags level` per leaf, where flags = the low 10 PTE bits (R/W/X/U AND
# the student's RSW/COW bit), and level is 0 for a 4 KB page, 1 for a 2 MB megapage (A-Lab 03).
# The level came last, and later: a parser must default it to 0, because older images omit it. The VA is accumulated during the walk (Sv39: index i at level L adds
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
      PRINTF("VL %d %p %p %d %d\\n", pid, (void*)cva, (void*)PTE2PA(pte), (int)(pte & 0x3FF),
             level);
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
           f"  case C('T'): gini_obs_begin(); gini_dump(); gini_obs_end(); break;  "
           "// GINI: procs + registers (0x1e/0x1f-bracketed; agent hides it from the console)\n"
           f"  case C('V'): gini_obs_begin(); gini_vmdump(); gini_obs_end(); break;  "
           "// GINI: running-proc page table\n"
           f"  case C('F'): gini_obs_begin(); gini_fsdump(); gini_obs_end(); break;  "
           "// GINI: superblock + write-ahead log\n"
           f"  case C('S'): gini_obs_begin(); gini_scdump(); gini_obs_end(); break;  "
           "// GINI: syscall counts + trace ring\n"
           f"  case C('A'): gini_obs_begin(); gini_vmdump_all(); gini_obs_end(); break;  "
           "// GINI: all user page tables (COW / sharing view)\n"
           f"  case C('E'): gini_obs_begin(); gini_faultdump(); gini_obs_end(); break;  "
           "// GINI: live page-fault ring\n"
           f"  case C('R'): gini_obs_begin(); gini_trapdump(); gini_obs_end(); break;  "
           "// GINI: trap-taxonomy counters + ring\n"
           r"  case C('C'): gini_break(); break;  // GINI: break a hung foreground (no SIGINT in xv6)\n"
           r"  case C(']'): if(sched_quantum < 100) sched_quantum++; break; // GINI: quantum up\n"
           r"  case C('\\\\'): sched_quantum = 1; break;  // GINI: quantum reset to 1\n"
           # NOTE: no `case C('G')` / `case C('B')` for policy — policy is now a terminated
           # digit-entry (Ctrl-B <digits> \n, §4f6), because Ctrl-G is the shadow-index prefix
           # (§4f4) and the old N×Ctrl-G scheme silently toggled a shadow instead of switching
           # policy (known issue #1). Ctrl-B early-returns before this switch, so a `case` here
           # would be dead.
           r"  case C('K'): if(sched_policy >= 0 && sched_policy < 3) gini_shadow[sched_policy].enabled = !gini_shadow[sched_policy].enabled; break;  // GINI: toggle the current policy's shadow (0..2 have one; stride does not, and slot 3 is vmfault)\n"
           r"  case C('X'): gini_shadow_reset(); break;  // GINI: clear reject/call counters\n"
           f"  case C('L'): gini_obs_begin(); gini_lockdump(); gini_obs_end(); break;  "
           "// GINI: lock contention (0x1e/0x1f-bracketed)\n"
           r"  case C('Z'): gini_lockreset(); break;  // GINI: zero the lock counters",
           # NOTE: no `case C('W')` — Ctrl-W is now the command-mux PREFIX (§4f5). Shadowdump moved
           # to the self-escape Ctrl-W Ctrl-W; the agent's /shadows sends b"\x17\x17" to match.
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

# 4f4b) console.c — scheduler POLICY by index: Ctrl-B <digits> <terminator>. The old scheme drove
# policy with Ctrl-B then N×Ctrl-G, but Ctrl-G is the shadow-index PREFIX (4f4) and returns early,
# so N×Ctrl-G armed a shadow-index entry the next byte then TERMINATED — silently toggling shadow 0
# instead of switching policy (known issue #1). Give policy its own terminated digit-entry, exactly
# like the shadow-index one, so the two machines cannot collide. GINI_NPOLICY bounds it, so a new
# policy added to the roster works with no further edit here.
regex_once("kernel/console.c",
           r"(switch\s*\(c\)\s*\{)",
           "static int gini_polidx = -1;  // GINI: scheduler-policy index entry (-1 = idle)\n"
           "  if(gini_polidx >= 0){\n"
           "    if(c >= '0' && c <= '9') gini_polidx = gini_polidx * 10 + (c - '0');\n"
           "    else { if(gini_polidx < GINI_NPOLICY) sched_policy = gini_polidx; gini_polidx = -1; }\n"
           "    release(&cons.lock); return;\n"
           "  }\n"
           "  if(c == C('B')){ gini_polidx = 0; release(&cons.lock); return; }\n"
           r"  \1",
           "GINI: scheduler policy by index")

# 4f5) console.c — TWO-BYTE COMMAND MULTIPLEXER. The control-byte space is exhausted, so a fresh
# command space is multiplexed behind ONE prefix: Ctrl-W. `PREFIX <letter>` is a command, so every
# future console command needs no new byte. `backend/xv6/console_mux.py` is the PROVEN executable
# spec for this exact state machine (round-trip, passthrough, interleave-abort, fuzz) — keep the
# two in step. Ctrl-W's old job (shadowdump) moves to the self-escape Ctrl-W Ctrl-W. Two locks make
# it safe against a human typing into the same stream: selectors are PRINTABLE (a selector split
# from its prefix by a keystroke is just text, never a single-byte command), and an unrecognised
# byte ABORTS and is reprocessed as ordinary input (never a wrong command) — the discipline the
# Ctrl-G machine lacked (known issue #1). Placed after the other entry machines; an aborted byte
# falls through to the switch, which is faithful for every reachable input (the only bytes that
# would want an earlier machine are control bytes the human never types).
regex_once("kernel/console.c",
           r"(switch\s*\(c\)\s*\{)",
           "static int gini_mux = 0, gini_mux_arg = 0;  // 0 idle | 1 await-sel | 2 await-digits\n"
           "  if(gini_mux == 1){\n"
           "    gini_mux = 0;\n"
           "    if(c == C('W')){ gini_obs_begin(); gini_shadowdump(); gini_obs_end();"
           " release(&cons.lock); return; }  // PREFIX PREFIX -> shadowdump (its new binding)\n"
           "    if(c == 'a'){ gini_mux = 2; gini_mux_arg = 0; release(&cons.lock); return; }"
           "  // arm the trap capture; a kind digit + newline follow\n"
           "    if(c == 'r'){ gini_boardreset(); release(&cons.lock); return; }"
           "  // reset the kernel board (was dead code — known issue #4)\n"
           "    /* unrecognised selector -> abort: fall through and process c below */\n"
           "  } else if(gini_mux == 2){\n"
           "    if(c >= '0' && c <= '9'){ gini_mux_arg = gini_mux_arg * 10 + (c - '0');"
           " release(&cons.lock); return; }\n"
           # 10/13 (newline/CR) as ints, NOT '\\n'/'\\r' char literals: this string is a re.subn
           # REPLACEMENT, which turns a backslash-n into a real newline and corrupts the C literal.
           "    if(c == 10 || c == 13){ gini_mux = 0;"
           " gini_catch_kind = (gini_mux_arg == 9) ? GINI_CATCH_ANY : gini_mux_arg;"
           " gini_catch_ready = 0; release(&cons.lock); return; }  // newline/CR ends the kind arg\n"
           "    gini_mux = 0;  /* non-digit mid-argument -> abort: fall through */\n"
           "  }\n"
           "  if(c == C('W')){ gini_mux = 1; release(&cons.lock); return; }  // the prefix byte\n"
           r"  \1",
           "GINI: two-byte command mux (Ctrl-W prefix)")

# 4g) syscall.c — per-syscall counters (histogram) + a recent-call trace ring (strace view).
#     The definitions + gini_scdump go at end-of-file (types/externs are declared in defs.h so
#     syscall() can use them above); Ctrl-S dumps them.
_SCDUMP = '''
// GINI-xv6: per-syscall counters + recent-call trace ring (Machine Lab histogram + strace).
uint64 gini_sccount[64];
struct gini_sc gini_ring[GINI_RING];
// uint64, NOT int: this counts every event forever. At int width it would wrap negative
// after ~2 billion events and `k % GINI_RING` would then index BEFORE the array — an
// out-of-bounds kernel write. Unsigned wrap is well-defined and, at 64 bits, unreachable.
uint64 gini_ring_i;

void
gini_scdump(void)
{
  for(int i = 0; i < 64; i++)
    if(gini_sccount[i])
      PRINTF("SC %d %lu\\n", i, (uint64)gini_sccount[i]);
  uint64 total = gini_ring_i;
  uint64 start = total > GINI_RING ? total - GINI_RING : 0;
  for(uint64 k = start; k < total; k++){
    struct gini_sc *e = &gini_ring[k % GINI_RING];
    PRINTF("TRACE %d %d %p %p %lu\\n", e->pid, e->num, (void*)e->a0,
           (void*)e->ret, (uint64)e->seq);
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


# ---------------------------------------------------------------------------------------------
# 4h) THE KERNEL BOARD — subsystem residency, the exact call matrix between subsystems, the three
#     doors into the kernel, and the size of the path that does NOT go through the kernel at all.
#
#     Why this exists: students believe the kernel is AMBIENT — always running, everywhere, in
#     parallel with their program. It is not. It is code that runs on their process's CPU, between
#     their instructions, and then gets out of the way. Everything below is in service of making
#     that measurable rather than merely asserted.
#
#     Three separate measurements, deliberately not conflated:
#
#       gini_edge[a][b]  EXACT count of calls that crossed from subsystem a into subsystem b.
#                        Frequency. Drawn as arrow width.
#       gini_resid[s]    Timer-sampled CPU residency per subsystem. COST. Drawn as block shade.
#                        These two DISAGREE, and the disagreement is the lesson: bcache is asked
#                        601 times for 2% of the time; the disk is asked 12 times for 12% of it.
#       gini_uinstr      Retired instructions executed in USER mode between kernel entries. This
#                        is the path that never touches the kernel, and it dwarfs everything else.
#
#     Cost: GINI_NSUB^2 + GINI_NSUB uint64s ~ 1.3 KB, fixed forever, plus ~4 instructions per
#     instrumented call. No rings, so no overflow question and nothing to arm — it is always on.
append_once("kernel/defs.h", """
// GINI-xv6 KERNEL BOARD: which subsystem is the CPU in, who called whom, and how much ran with
// no kernel involvement at all. See section 4h of gini_patch.py.
#define GINI_NSUB     14
#define GSUB_USER      0   // not in the kernel at all — the wide path
#define GSUB_TRAP      1
#define GSUB_SYSCALL   2
#define GSUB_PROC      3
#define GSUB_MEM       4
#define GSUB_FILE      5
#define GSUB_PIPE      6
#define GSUB_INODE     7
#define GSUB_LOG       8
#define GSUB_BCACHE    9
#define GSUB_DISK     10
#define GSUB_CONSOLE  11
#define GSUB_PLIC     12
#define GSUB_OTHER    13
#define GINI_TRAIL    64   // ring of real residency samples: where the CPU actually WAS
#define GINI_PATH    128   // ring of real subsystem transitions, recorded only while ARMED
extern uint64   gini_edge[GINI_NSUB][GINI_NSUB];
// Calls made while GINI was reading the machine. The Lab polls several times a second, and each
// poll raises a UART interrupt and writes a dump — real kernel work, caused by US. Counted apart
// so the board can draw it grey and dashed instead of passing our footprint off as the workload's.
extern uint64   gini_edge_obs[GINI_NSUB][GINI_NSUB];
extern uint64   gini_resid[GINI_NSUB];
extern uint64   gini_resid_n;      // total residency samples taken — the board's confidence
extern uint64   gini_door[3];      // 0 asked (ecall) | 1 couldn't (fault) | 2 seized (interrupt)
extern uint64   gini_uinstr;       // retired user-mode instructions, summed
extern uint64   gini_uentry;       // kernel entries that contributed to gini_uinstr
extern int      gini_obs[];        // per-hart: are we inside a GINI dump right now?
extern uchar    gini_trail[];      // recent residency samples, newest at gini_trail_i-1
extern uint64   gini_trail_i;
void            gini_obs_begin(void);
void            gini_obs_end(void);
// SLEEP CHANNELS. `state == SLEEPING` says a process is blocked; it does not say what will wake
// it. xv6 sleeps on an ADDRESS — sleep(chan, lk) — and wakeup(chan) matches on that address, so
// the answer to "who is going to wake you?" is sitting in p->chan and is invisible to a student.
//
// A raw pointer teaches nothing, so each subsystem registers the address (or range) it sleeps on
// together with a name a human can read. Registration rather than a lookup table because the
// interesting structs — bcache, log, cons — are STATIC to their own files and cannot be named
// from anywhere else.
#define GINI_NCHAN 12
void            gini_chan_add(void *lo, void *hi, char *name);
char*           gini_chan_name(void *chan);
// THE TRACE. Aggregates say the block cache was asked 601 times; they cannot say the read went
// syscall -> file -> inode -> bcache -> disk IN THAT ORDER. This ring records the real ordered
// path, and only while armed: unarmed it costs one predictable branch, so the board stays
// always-on and the expensive thing stays opt-in.
struct gini_hop { uint64 seq; uchar from; uchar to; uchar pid; };
extern struct gini_hop gini_path[];
extern uint64   gini_path_i;
extern int      gini_path_arm;
void            gini_path_toggle(void);
// Declared with no bound: defs.h is included before param.h in some translation units, so it
// cannot spell NCPU. Defined with the real size in trap.c, and needed HERE because trap.c's own
// prepare_return hook sits above the definitions.
extern int      gini_sub[];        // which subsystem each hart is in, right now
extern uint64   gini_umark[];      // instret when each hart last returned to user
int             gini_subenter(int);
void            gini_subleave(int *);
void            gini_boarddump(void);
void            gini_boardreset(void);
void            gini_doorrec(void);
void            gini_ktick(void);
// One line at the top of a subsystem entry point. The cleanup attribute restores the previous
// subsystem on EVERY exit path — early returns, gotos, all of them — so there is no exit
// bookkeeping to get wrong and nothing to forget when the function grows a new return.
#define GINI_SUB(s) int __gsub __attribute__((cleanup(gini_subleave), unused)) = gini_subenter(s)
""", "GINI-xv6 KERNEL BOARD")

# 4h1) riscv.h — read the retired-instruction counter. This is what makes the no-kernel path
# COUNTABLE instead of hand-waved; without it the widest path on the board would be the only
# unmeasured one.
# NOT append_once: riscv.h wraps its C in `#ifndef __ASSEMBLER__ ... #endif` because
# trampoline.S includes it, so anything appended past the guard is handed to the ASSEMBLER and
# the build dies on "unrecognized opcode `static inline uint64'". Anchor inside the guard instead,
# next to r_time(), which is the same kind of CSR read.
regex_once("kernel/riscv.h",
           r"(// machine-mode cycle counter\n)",
           "// GINI-xv6: r_instret — retired instruction count. Readable from supervisor mode only\n"
           "// because start.c sets mcounteren bit 2 (IR). This is what makes the path that never\n"
           "// enters the kernel MEASURABLE rather than merely asserted.\n"
           "static inline uint64\n"
           "r_instret()\n"
           "{\n"
           "  uint64 x;\n"
           "  asm volatile(\"csrr %0, instret\" : \"=r\" (x) );\n"
           "  return x;\n"
           "}\n\n"
           r"\1",
           "GINI-xv6: r_instret")

# 4h2) start.c — xv6 enables only the TIME counter for supervisor mode (bit 1). Add bit 2 (IR) so
# the kernel may read `instret` at all. Without this the csrr above traps as an illegal
# instruction, which is a spectacular way to discover you forgot one bit.
regex_once("kernel/start.c",
           r"w_mcounteren\(r_mcounteren\(\) \| 2\);",
           "w_mcounteren(r_mcounteren() | 6);  // GINI-xv6: mcounteren INSTRET (bit1 TIME + bit2 IR)",
           "GINI-xv6: mcounteren INSTRET")

# 4h3) trap.c — the three doors, and the user-instruction accounting that brackets them.
# Anchored on the existing gini_fault_note() hook, which is already the first thing usertrap does
# after saving the user pc, so gini_sub[] still says USER when gini_doorrec() reads it.
regex_once("kernel/trap.c",
           r"(  gini_fault_note\(\); // GINI-xv6: record page faults into the live ring\n)",
           r"\1  gini_doorrec();    // GINI-xv6: classify the door, bank user instructions\n",
           "GINI-xv6: doorrec")

# The other end of the bracket: we are about to sret back to user, so start the user-instruction
# clock and mark the CPU as no longer being in the kernel. `w_sepc(p->trapframe->epc);` appears
# only in prepare_return (kerneltrap uses a local `sepc`).
regex_once("kernel/trap.c",
           r"(  w_sepc\(p->trapframe->epc\);\n)",
           r"\1  gini_umark[cpuid()] = r_instret();  // GINI-xv6: user-instruction clock starts\n"
           "  gini_sub[cpuid()] = GSUB_USER;      // GINI-xv6: leaving the kernel entirely\n",
           "GINI-xv6: user instret mark")

# The caught trap's RETURN copy (see gini_catchret). Same anchor as the instret mark — the one line
# in prepare_return after which the kernel touches the trapframe no further — and it is the return
# path for every trap from user mode, so no trap kind needs its own hook.
regex_once("kernel/trap.c",
           r"(  w_sepc\(p->trapframe->epc\);\n)",
           r"\1  gini_catchret();                   // GINI-xv6: catch the return\n",
           "GINI-xv6: catch the return")

# Timer ticks that arrive while the CPU is already in the kernel. usertrap's sample is taken by
# gini_doorrec (where the subsystem still reads USER); this is the kernel-side half, and without
# it every kernel subsystem would show zero residency.
regex_once("kernel/trap.c",
           r"(uint64 scause = r_scause\(\);)",
           r"\1\n  gini_ktick();  // GINI-xv6: sample which subsystem this hart is in",
           "GINI-xv6: ktick")

append_once("kernel/trap.c", """
// GINI-xv6 KERNEL BOARD core. See section 4h of gini_patch.py for the design.
uint64 gini_edge[GINI_NSUB][GINI_NSUB];
uint64 gini_edge_obs[GINI_NSUB][GINI_NSUB];
uint64 gini_resid[GINI_NSUB];
uint64 gini_resid_n;
uint64 gini_door[3];
uint64 gini_uinstr;
uint64 gini_uentry;
int    gini_sub[NCPU];        // which subsystem each hart is executing in, right now
uint64 gini_umark[NCPU];      // instret at the moment we last returned to user
int    gini_obs[NCPU];        // per-hart: inside a GINI dump (see gini_obs_begin)
uchar  gini_trail[GINI_TRAIL];
uint64 gini_trail_i;
struct gini_hop gini_path[GINI_PATH];
uint64 gini_path_i;
int    gini_path_arm;         // 0 = off; recording costs nothing until a student asks for it

static char *gini_subname[GINI_NSUB] = {
  "user", "trap", "syscall", "proc", "memory", "file", "pipe",
  "inode", "log", "bcache", "disk", "console", "plic", "other",
};

// Enter a subsystem. Returns the previous one so the cleanup handler can restore it.
//
// The edge is counted only when the subsystem actually CHANGES, which is what makes the numbers
// mean "calls this block received from outside" rather than "calls, including internal ones".
// A bio function calling another bio function is not block traffic and must not look like it.
//
// cpuid() is read without disabling interrupts: a migration between the read and the store would
// misattribute a single event. That is sampling-grade error on a counter used for shading a
// rectangle, and paying push_off/pop_off on every instrumented call to avoid it would cost far
// more than it is worth — and would drag the lock subsystem onto this path.
int
gini_subenter(int s)
{
  int h = cpuid();
  int prev = gini_sub[h];
  gini_sub[h] = s;
  if(prev != s){
    if(gini_obs[h])
      __sync_fetch_and_add(&gini_edge_obs[prev][s], 1);   // this call is OURS, not the workload's
    else {
      __sync_fetch_and_add(&gini_edge[prev][s], 1);
      // The ordered path, when a student has armed it. Our own polling is excluded here for the
      // same reason it is excluded above: a trace of GINI reading the machine teaches nothing.
      if(gini_path_arm){
        uint64 k = __sync_fetch_and_add(&gini_path_i, 1) %% GINI_PATH;
        struct proc *pp = myproc();
        gini_path[k].seq = gini_stamp();
        gini_path[k].from = (uchar)prev;
        gini_path[k].to = (uchar)s;
        gini_path[k].pid = (uchar)(pp ? pp->pid : 0);
      }
    }
  }
  return prev;
}

// Arm or disarm the trace, clearing the ring each time it is armed so a student always gets ONE
// clean path rather than the tail of whatever happened earlier.
void
gini_path_toggle(void)
{
  gini_path_arm = !gini_path_arm;
  if(gini_path_arm)
    gini_path_i = 0;
}

// Bracket a GINI dump. The 0x1e/0x1f pair already existed so the agent could strip these dumps
// from the human console; raising the flag on the same boundary costs nothing and makes every
// call the dump provokes attributable to the measurement instead of the machine.
//
// LIMIT, stated rather than hidden: the UART interrupt that DELIVERS the control character fires
// before the flag can be raised, so roughly three events per poll (uartintr -> consoleintr) are
// still counted as real. The dump itself — the overwhelming bulk — is captured.
void
gini_obs_begin(void)
{
  gini_obs[cpuid()] = 1;
  %(P)s("%%c", 30);
}

void
gini_obs_end(void)
{
  %(P)s("%%c", 31);
  gini_obs[cpuid()] = 0;
}

void
gini_subleave(int *prev)
{
  gini_sub[cpuid()] = *prev;
}

// Is this trap a supervisor TIMER interrupt? Residency is sampled on the timer and nothing else.
//
// Sampling ONLY on the timer keeps the estimate unbiased — device interrupts arrive correlated
// with device activity, so sampling on those would systematically over-report the disk and the
// console. The price is resolution: GINI deliberately slows the tick to ~0.5 s (section 2b, so
// the scheduler Gantt is watchable), which means residency accrues at about TWO SAMPLES PER
// SECOND. A 1-second HUD window therefore holds ~2 samples and can only ever report nothing,
// half, or all of it. That is why gini_resid_n is reported: the board shows the sample count so
// it never
// claims a precision it does not have, and the shading is meaningful only over longer windows.
static int
gini_is_tick(uint64 c)
{
  return (c & 0x8000000000000000L) && ((c & 0xff) == 5);
}

// One residency observation. Also appended to a small ring of REAL positions — the CPU marker
// draws from this rather than from an aggregate, so every dot on screen is somewhere the hart
// actually was, never a position synthesised from a distribution.
static void
gini_sample(int s)
{
  gini_resid[s]++;
  gini_resid_n++;
  gini_trail[gini_trail_i %% GINI_TRAIL] = (uchar)s;
  gini_trail_i++;
}

// Called from the top of kerneltrap: the CPU was already in the kernel, and gini_sub says which
// subsystem, so this attributes the tick to whatever was actually running.
void
gini_ktick(void)
{
  if(gini_is_tick(r_scause()))
    gini_sample(gini_sub[cpuid()]);
}

// Called from the top of usertrap, BEFORE anything else touches gini_sub — so the residency
// sample below still sees GSUB_USER and user time is counted rather than swallowed by the trap.
void
gini_doorrec(void)
{
  uint64 c = r_scause();
  int h = cpuid();

  if(gini_is_tick(c))
    gini_sample(gini_sub[h]);           // still USER here: this is the no-kernel path's time

  // The three doors, by AGENCY. A fault is not an interrupt, and students conflate them
  // constantly; this is the same three-way split usertrap itself makes a few lines below.
  if(c & 0x8000000000000000L)
    gini_door[2]++;                     // seized: a device or the timer wanted attention
  else if(c == 8)
    gini_door[0]++;                     // asked: the program executed ecall
  else
    gini_door[1]++;                     // couldn't: page fault, illegal instruction, ...

  // Bank the instructions that ran in user mode since we last returned there. gini_umark is 0
  // before the first return to user, and that one sample is skipped rather than counted as a
  // gigantic bogus run.
  if(gini_umark[h]){
    gini_uinstr += r_instret() - gini_umark[h];
    gini_uentry++;
  }
  gini_sub[h] = GSUB_TRAP;
}

// ---- sleep-channel registry -----------------------------------------------------------------
// Each entry claims an address or a half-open range [lo, hi). Ranges matter: the block cache and
// the process table are arrays, and a process sleeping on buf 12 or waiting for child slot 7 is
// sleeping on an address INSIDE them, never on the array itself.
struct gini_chanreg { void *lo; void *hi; char *name; };
static struct gini_chanreg gini_chans[GINI_NCHAN];
static int gini_nchan;

void
gini_chan_add(void *lo, void *hi, char *name)
{
  if(gini_nchan >= GINI_NCHAN || lo == 0)
    return;
  gini_chans[gini_nchan].lo = lo;
  gini_chans[gini_nchan].hi = hi ? hi : (void*)((char*)lo + 1);
  gini_chans[gini_nchan].name = name;
  gini_nchan++;
}

char*
gini_chan_name(void *chan)
{
  if(chan == 0)
    return "";
  for(int i = 0; i < gini_nchan; i++)
    if(chan >= gini_chans[i].lo && chan < gini_chans[i].hi)
      return gini_chans[i].name;
  // A process sleeping on its OWN proc struct is inside wait(), which is by far the most common
  // sleep in a quiet system — worth naming even though proc[] is registered as a range, because
  // "a child to exit" says far more than "the process table".
  return "?";
}

void
gini_boardreset(void)
{
  for(int i = 0; i < GINI_NSUB; i++){
    gini_resid[i] = 0;
    for(int j = 0; j < GINI_NSUB; j++)
      gini_edge[i][j] = gini_edge_obs[i][j] = 0;
  }
  gini_door[0] = gini_door[1] = gini_door[2] = 0;
  gini_uinstr = 0;
  gini_uentry = 0;
  gini_resid_n = 0;
  gini_trail_i = 0;
}

// Counters are CUMULATIVE. The frontend differences successive dumps into per-window rates; the
// kernel never resets them on its own, so a missed poll costs resolution but never correctness.
void
gini_boarddump(void)
{
  %(P)s("BOARDN %%d\\n", GINI_NSUB);
  for(int i = 0; i < GINI_NSUB; i++)
    %(P)s("BSUB %%d %%s %%lu\\n", i, gini_subname[i], (uint64)gini_resid[i]);
  for(int i = 0; i < GINI_NSUB; i++)
    for(int j = 0; j < GINI_NSUB; j++){
      if(gini_edge[i][j])
        %(P)s("BEDGE %%d %%d %%lu\\n", i, j, (uint64)gini_edge[i][j]);
      if(gini_edge_obs[i][j])
        %(P)s("BEOBS %%d %%d %%lu\\n", i, j, (uint64)gini_edge_obs[i][j]);
    }
  %(P)s("BDOOR %%lu %%lu %%lu\\n", (uint64)gini_door[0], (uint64)gini_door[1], (uint64)gini_door[2]);
  // How many residency samples the numbers above rest on. At a ~0.5s tick this is small, and
  // the board says so rather than shading a rectangle as if it knew.
  %(P)s("BSAMP %%lu\\n", (uint64)gini_resid_n);
  // The last GINI_TRAIL real positions, oldest first — every one an actual observation.
  {
    uint64 n = gini_trail_i < GINI_TRAIL ? gini_trail_i : GINI_TRAIL;
    %(P)s("BTRAIL %%d", (int)n);
    for(uint64 k = gini_trail_i - n; k < gini_trail_i; k++)
      %(P)s(" %%d", (int)gini_trail[k %% GINI_TRAIL]);
    %(P)s("\\n");
  }
  // The armed trace: the real ordered path, oldest hop first.
  %(P)s("BARM %%d\\n", gini_path_arm);
  {
    uint64 n = gini_path_i < GINI_PATH ? gini_path_i : GINI_PATH;
    for(uint64 k = gini_path_i - n; k < gini_path_i; k++){
      struct gini_hop *hp = &gini_path[k %% GINI_PATH];
      %(P)s("BPATH %%lu %%d %%d %%d\\n", (uint64)hp->seq, (int)hp->from, (int)hp->to, (int)hp->pid);
    }
  }
  // Printed as two numbers rather than a ratio: the frontend differences them, and a ratio
  // cannot be differenced.
  %(P)s("BUSER %%lu %%lu\\n", (uint64)(gini_uinstr / 1000), (uint64)gini_uentry);
}
""" % {"P": PRINT}, "GINI-xv6 KERNEL BOARD core")

# 4h6) SLEEP CHANNELS — naming the thing a blocked process is waiting for.
#
# `state == SLEEPING` says a process is blocked. It does not say WHY, or who will wake it. In this
# xv6 the answer is in `p->chan`: sleep_prepare(chan) records an address, sleep() blocks, and
# wakeup(chan) matches on that same address and clears it. The whole mechanism is a pointer
# comparison, and it is completely invisible from outside.
#
# Each subsystem registers the address (or array range) it sleeps on, at its own init, because the
# interesting structs — bcache, itable, log, cons — are STATIC to their own files and cannot be
# named from anywhere else. Ranges matter: a process blocked on buf 12 or waiting for child slot 7
# is sleeping on an address INSIDE an array, never on the array itself.
#
# Not registered: pipes. They are allocated per pipe, so there is no fixed address to claim; a
# pipe wait shows its raw channel instead, which is still better than "sleeping" with no object.
_CHAN_REG = [
    ("kernel/console.c", r"(consoleinit\(void\)\n\{\n)",
     '  gini_chan_add(&cons.r, 0, "console input");   // a keystroke\n'),
    ("kernel/log.c", r"(initlog\(int dev, struct superblock \*sb\)\n\{\n)",
     '  gini_chan_add(&log, &log + 1, "the log");     // commit / space in a transaction\n'),
    ("kernel/proc.c", r"(procinit\(void\)\n\{\n)",
     '  gini_chan_add(proc, &proc[NPROC], "a child to exit");   // wait()\n'),
    ("kernel/bio.c", r"(binit\(void\)\n\{\n)",
     '  gini_chan_add(bcache.buf, &bcache.buf[NBUF], "a disk block");  // buf + its sleeplock\n'),
    ("kernel/fs.c", r"(iinit\(\)\n\{\n)",
     '  gini_chan_add(itable.inode, &itable.inode[NINODE], "an inode");  // inode sleeplock\n'),
    ("kernel/uart.c", r"(uartinit\(void\)\n\{\n)",
     '  gini_chan_add(&tx_chan, 0, "room in the UART buffer");\n'),
    ("kernel/virtio_disk.c", r"(virtio_disk_init\(void\)\n\{\n)",
     '  gini_chan_add(&disk.free[0], &disk.free[NUM], "a free disk descriptor");\n'),
]
for _rel, _anchor, _line in _CHAN_REG:
    regex_once(_rel, _anchor, r"\1" + _line, "gini_chan_add")

# `&ticks` is sys_sleep's channel and lives in trap.c, where the registry itself is — registered
# from trapinit rather than from a subsystem, since ticks belongs to no subsystem.
regex_once("kernel/trap.c",
           r"(trapinit\(void\)\n\{\n)",
           # NOT one raw string: `\"` inside an r"" stays a literal backslash-quote and lands in
           # the C source as `\"the next timer tick\"`, which does not compile.
           r"\1" + '  gini_chan_add(&ticks, &ticks + 1, "the next timer tick");  // sys_sleep\n',
           "GINI-xv6: ticks chan")

# 4h4) The probes themselves. One line at the top of each subsystem ENTRY POINT — the functions
# other subsystems call — and nothing internal. That is what makes the counts mean "traffic this
# block received", and it also keeps the patch surface to the module boundaries, which are the
# part of xv6 least likely to be reshuffled by a student.
#
# NOT instrumented, deliberately:
#   spinlock.c / sleeplock.c  acquire() is called from everywhere; as a subsystem it would either
#                             swallow the board or vanish. Contention is already measured by the
#                             lock telemetry (3f) and belongs on block BORDERS, not as a block.
#   string.c / printk.c       same problem, no teaching value.
#   anything named gini_*     OUR OWN instrumentation. The Lab polls these several times a second,
#                             so counting them would show the disk and console busy because we are
#                             measuring, not because the machine is. Third time this observer
#                             effect has bitten (the CSR strip, the HUD trap lane, now this), so
#                             it is a rule here rather than a case-by-case fix.
_BOARD_PROBES = [
    ("kernel/syscall.c", "SYSCALL", ["syscall"]),
    ("kernel/proc.c", "PROC", ["kfork", "kexit", "kwait", "growproc", "yield", "sleep",
                               "wakeup", "kkill", "allocproc", "freeproc"]),
    ("kernel/exec.c", "PROC", ["kexec"]),
    ("kernel/vm.c", "MEM", ["uvmalloc", "uvmdealloc", "uvmcopy", "uvmunmap", "mappages",
                            "walkaddr", "copyout", "copyin", "uvmcreate", "uvmfree"]),
    ("kernel/kalloc.c", "MEM", ["kalloc", "kfree"]),
    ("kernel/file.c", "FILE", ["fileread", "filewrite", "fileclose", "filealloc", "filedup",
                               "filestat"]),
    ("kernel/pipe.c", "PIPE", ["piperead", "pipewrite", "pipealloc", "pipeclose"]),
    ("kernel/fs.c", "INODE", ["readi", "writei", "namei", "nameiparent", "ialloc", "iupdate",
                              "iget", "ilock", "iunlock", "iput", "itrunc", "dirlookup",
                              "dirlink", "balloc", "bfree"]),
    ("kernel/log.c", "LOG", ["begin_op", "end_op", "log_write"]),
    ("kernel/bio.c", "BCACHE", ["bread", "bwrite", "brelse", "bpin", "bunpin", "bget"]),
    ("kernel/virtio_disk.c", "DISK", ["virtio_disk_rw", "virtio_disk_intr"]),
    ("kernel/uart.c", "CONSOLE", ["uartwrite", "uartputc_sync", "uartintr"]),
    ("kernel/console.c", "CONSOLE", ["consoleread", "consolewrite", "consoleintr", "consputc"]),
    ("kernel/plic.c", "PLIC", ["plic_claim", "plic_complete"]),
]

for _rel, _sub, _fns in _BOARD_PROBES:
    for _fn in _fns:
        # xv6's house style puts the return type on its own line, so the function NAME starts a
        # line — which makes this anchor unambiguous. A prototype ends in ';' and a call is
        # indented, so neither can match. `(?m)` because regex_once does not pass re.M and the
        # anchor is worthless without it (this cost 69 silently skipped probes to discover).
        #
        # The inserted comment carries the per-function marker so re-running the patcher on an
        # already-patched tree is a no-op — the Docker build does exactly that.
        regex_once(_rel,
                   r"(?m)(^[A-Za-z_][\w \*]*\n" + _fn + r"\([^;{]*\)\n\{\n)",
                   r"\1  GINI_SUB(GSUB_" + _sub + f");  // GINI-xv6: board probe {_fn}\n",
                   f"GINI-xv6: board probe {_fn}")

# 4h5) console.c — Ctrl-D dumps the board. (Ctrl-D is not special in xv6's consoleintr; the shell
# has no EOF handling that would swallow it.)
regex_once("kernel/console.c",
           r"(case C\('L'\):[^\n]*\n)",
           r"\1"
           f"  case C('D'): gini_obs_begin(); gini_boarddump(); gini_obs_end(); break;  "
           "// GINI: kernel board (0x1e/0x1f-bracketed)\n"
           r"  case C('Q'): gini_path_toggle(); break;  // GINI: arm/disarm the kernel-path trace" "\n",
           "GINI: board dump key")


# 4i) EXECUTABLE heap — SBRK_EXEC, so a program can build instructions and run them.
#
# Why this is needed at all: growproc() calls uvmalloc(..., PTE_W), so heap pages come back
# PTE_R|PTE_U|PTE_W with NO PTE_X. Jumping into sbrk memory therefore raises scause 12
# (instruction page fault), and usertrap routes only 13 and 15 to vmfault() — 12 falls through
# to "unexpected scause" and setkilled(). So without this the corridor walker does not walk, it
# dies on its first instruction.
#
# This follows the existing SBRK_EAGER/SBRK_LAZY flag mechanism rather than inventing a new
# syscall, and adds a SEPARATE growproc_exec() instead of giving growproc() a permission
# parameter — every existing caller of growproc() then keeps its exact current behaviour.
#
# W and X together violate W^X, which a production kernel would not allow. That is deliberate
# here and worth saying out loud to students: the corridor has to be written before it can be
# run, and xv6 has no mprotect to drop the write permission afterwards.
append_once("kernel/vm.h", """
#define SBRK_EXEC  3   // GINI-xv6: eager, and mapped EXECUTABLE (see growproc_exec)
""", "GINI-xv6: SBRK_EXEC")

append_once("kernel/proc.c", """
// GINI-xv6: grow user memory by n bytes, mapped WRITABLE **and EXECUTABLE**, so a process can
// generate instructions into the new pages and then jump into them. Deliberately a separate
// function from growproc() so that ordinary sbrk() keeps mapping non-executable pages.
//
// Shrinking is not supported here — pass a negative n to plain growproc(), which does not care
// about permissions when unmapping.
int
growproc_exec(int n)
{
  uint64 sz;
  struct proc *p = myproc();

  if(n <= 0)
    return growproc(n);
  sz = p->sz;
  if(sz + n > TRAPFRAME)
    return -1;
  if((sz = uvmalloc(p->pagetable, sz, sz + n, PTE_W | PTE_X)) == 0)
    return -1;
  p->sz = sz;
  return 0;
}
""", "GINI-xv6: growproc_exec")

append_once("kernel/defs.h", """
// GINI-xv6: executable heap growth (SBRK_EXEC) — see proc.c
int             growproc_exec(int);
""", "GINI-xv6: growproc_exec decl")

# sys_sbrk's existing shape is `if (t == SBRK_EAGER || n < 0) { growproc } else { lazy }`, so the
# SBRK_EXEC arm has to be tested BEFORE that or a request for executable memory silently falls
# into the lazy branch and comes back non-executable.
regex_once("kernel/sysproc.c",
           r"  (if \(t == SBRK_EAGER \|\| n < 0\) \{)",
           "  if (t == SBRK_EXEC && n > 0) {          // GINI-xv6: eager AND executable\n"
           "    if (growproc_exec(n) < 0) {\n"
           "      return -1;\n"
           "    }\n"
           "  } else " r"\1",
           "GINI-xv6: SBRK_EXEC arm")

# Note: sysproc.c and user/ulib.c both already `#include "vm.h"` (that is where SBRK_EAGER and
# SBRK_LAZY live and both files use them), so SBRK_EXEC resolves in both with no include added.

append_once("user/user.h", """
char *sbrkexec(int);   // GINI-xv6: grow the heap with EXECUTABLE pages
""", "GINI-xv6: sbrkexec decl")

append_once("user/ulib.c", """
// GINI-xv6: like sbrk(), but the new pages are mapped executable so generated instructions in
// them can be jumped into. Used by `walker` to build a corridor of NOPs for the PC to walk.
char *
sbrkexec(int n)
{
  return sys_sbrk(n, SBRK_EXEC);
}
""", "GINI-xv6: sbrkexec")


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

    # walker — the SECOND pass is the lesson.
    #
    # Pass one faults every page in; pass two touches exactly the same pages and faults ZERO
    # times. Same work, same addresses, no kernel involvement at all. Students reliably believe a
    # memory access costs something every time, and one run of this fixes that.
    #
    # NOTE, and worth telling students: xv6 has NO SWAP. A faulted-in page stays for the life of
    # the process, so a walker cannot thrash physical memory however large the walk — it just runs
    # out and sbrklazy fails. Thrashing lives in the BUFFER CACHE instead (see sgrind), because
    # NBUF is 30. Memory faults are a one-time cost; cache misses are a recurring one.
    "walker": """#include "kernel/types.h"
#include "user/user.h"
// Make the PROGRAM COUNTER walk a long straight corridor of memory, slowly enough to watch.
//
//   walker                64 pages (256 KB), ~1 tick per page
//   walker 128            128 pages (512 KB)
//   walker 64 2           64 pages, ~2 ticks dwell on each
//
// Every other program in the launcher moves a DATA pointer while the PC stays parked in a tiny
// loop. This one is the opposite: it builds a corridor of NOP instructions and jumps into the
// start of it, so the PC itself slides from low addresses to high ones. In the Machine Lab the
// PC readout climbs a staircase across a quarter of a megabyte instead of sitting on one value.
//
// HOW THE CORRIDOR IS BUILT. Each 4 KB page holds:
//
//     +0    a 12-byte DELAY BLOCK   (lui t0, N / addi t0,t0,-1 / bne t0,zero,-4)
//     +12   1021 NOPs, to the end of the page
//     ...   the very last page ends in `ret` instead of a NOP
//
// Execution enters at the first byte, spends nearly all of its time in that page's delay block,
// then crosses the remaining NOPs in a few microseconds and lands in the next page's block. So
// the PC steps page by page, dwelling at one address per page.
//
// WHY THE DELAY IS COPIED IN RATHER THAN CALLED. The obvious design is `nop; call slowdown;
// nop; call slowdown; ...` with one shared delay routine. It does not work, for two reasons.
// The PC would sit at the SHARED routine's address while it burns time, so sampling would find
// it there essentially every time and the corridor would look like a single hot address -- the
// exact picture `spin` already gives. And `call` writes the return address into ra, which is
// the register holding the corridor's OWN return address, so the closing `ret` would jump back
// into the middle of the corridor instead of returning here.
//
// WHY THE ITERATION COUNT IS MEASURED, NOT ASSUMED. How many loop iterations fill one tick
// depends on the host, on QEMU's translator, and on how many other processes are running. So
// walker times the real block, in the real corridor, before deciding how long to make it.
#define PGSZ    4096
#define MAXP    512
#define NWORD   (PGSZ / 4)                 // 1024 instruction slots per page

#define NOP     0x00000013u                // addi zero,zero,0
#define RET     0x00008067u                // jalr zero,0(ra)
#define ADDI_M1 0xFFF28293u                // addi t0,t0,-1
#define BNE_BK  0xFE029EE3u                // bne  t0,zero,-4   (back to the addi)

// lui SIGN-EXTENDS bit 31 into the upper half of the 64-bit register. An imm20 of 0x80000 or
// more therefore loads a NEGATIVE count, and `addi -1` then walks it further from zero -- the
// loop would run for about 2^64 iterations, which is indistinguishable from a hung machine. So
// the usable range stops one step short of that.
#define IMM20_MAX 0x7FFFFu                 // -> at most 0x7FFFF000 iterations, still positive

// lui t0, imm20  ->  t0 = imm20 << 12, so the loop runs (imm20 << 12) times. The count lives
// entirely in the top 20 bits of this one word, which is why the dwell can be retuned by
// rewriting a single instruction instead of rebuilding the corridor.
static uint32
lui_t0(uint32 imm20)
{
  if(imm20 > IMM20_MAX)
    imm20 = IMM20_MAX;
  if(imm20 == 0)
    imm20 = 1;                             // a zero count means "loop 2^64 times", not "skip"
  return (imm20 << 12) | (5 << 7) | 0x37;
}

#define MAXC   ((uint64)IMM20_MAX << 12)   // most iterations one block can be asked for
#define MAXBLK 256                         // 3 words each: 3072 of a page's 4096 bytes

// Fill one page: enough back-to-back delay blocks to burn `total` iterations, then NOPs to the
// end. Several blocks rather than one because a single lui tops out at ~2.1 billion iterations,
// which on a fast host is only a few ticks -- asking for a longer dwell used to clamp silently
// and the page went by quicker than requested.
//
// Consecutive blocks also make the dwell itself visible: the PC steps from one block to the next
// WITHIN the page, so a long dwell reads as a slow climb rather than a frozen address.
static void
fill_page(uint32 *w, int nwords, uint64 total)
{
  int nblk = (int)((total + MAXC - 1) / MAXC);
  uint32 each;

  if(nblk < 1)      nblk = 1;
  if(nblk > MAXBLK) nblk = MAXBLK;
  each = (uint32)(total / (uint64)nblk);

  for(int b = 0; b < nblk; b++){
    w[3*b + 0] = lui_t0(each >> 12);       // granularity is 4096 iterations; that is plenty
    w[3*b + 1] = ADDI_M1;
    w[3*b + 2] = BNE_BK;
  }
  for(int i = 3*nblk; i < nwords; i++)
    w[i] = NOP;
}

// How many delay blocks a page needs for `total` iterations — and so whether the request fits.
static int
blocks_for(uint64 total)
{
  int n = (int)((total + MAXC - 1) / MAXC);
  return n < 1 ? 1 : n;
}

// Instructions written as data are not necessarily visible to instruction fetch until the
// pipeline's stale copies are discarded. Cheap here, and the one line that makes this program a
// correct example of generating code rather than a lucky one.
static void
sync_icache(void)
{
  asm volatile ("fence.i" ::: "memory");
}

static void
run(uint32 *code)
{
  ((void (*)(void))code)();
}

// Time the REAL block in the REAL corridor: build a one-block-then-return stub at `w`, run it
// until the tick counter has advanced `nticks`, and report how many iterations that took.
// Returns iterations per tick.
static uint64
calibrate(uint32 *w, int nticks)
{
  uint64 probe = 1ul << 20;                // 1,048,576 iterations per run
  uint64 reps = 0;
  int t0;

  w[0] = lui_t0((uint32)(probe >> 12));
  w[1] = ADDI_M1;
  w[2] = BNE_BK;
  w[3] = RET;
  sync_icache();

  t0 = uptime();
  while(uptime() == t0)                    // start on a tick boundary, so the first partial
    ;                                      // tick is not counted as a whole one
  t0 = uptime();
  while(uptime() - t0 < nticks){
    run(w);
    reps++;
  }
  if(reps < 1)
    reps = 1;                              // one run already outlasted the window
  return (reps * probe) / (uint64)nticks;  // 64-bit: reps*probe overflows 32 bits on a fast host
}

int
main(int argc, char *argv[])
{
  int pages  = (argc > 1) ? atoi(argv[1]) : 64;
  int dwell  = (argc > 2) ? atoi(argv[2]) : 1;     // ticks to sit on each page
  int laps   = (argc > 3) ? atoi(argv[3]) : 0;     // 0 = keep walking until killed
  uint32 *code;
  char *raw;

  if(pages < 2)    pages = 2;
  if(pages > MAXP) pages = MAXP;
  if(dwell < 1)    dwell = 1;

  // Ordinary sbrk() memory is mapped read/write but NOT executable, so jumping into it raises an
  // instruction page fault and the kernel kills the process. sbrkexec() asks for the same pages
  // with PTE_X set as well. (Writable AND executable at once is exactly what a hardened kernel
  // refuses; xv6 has no mprotect to drop the write permission after the corridor is built.)
  //
  // One page more than the corridor needs, because sbrk returns the process's OLD size, which is
  // only page-aligned by luck -- and uvmalloc starts mapping at PGROUNDUP of it. An unaligned
  // return would put the first bytes of the corridor in the PREVIOUS page, which was mapped
  // without PTE_X and would fault on the first instruction.
  raw = sbrkexec((pages + 1) * PGSZ);
  if(raw == SBRK_ERROR){
    printf("walker: cannot get %d executable pages\\n", pages + 1);
    exit(1);
  }
  code = (uint32 *)(((uint64)raw + PGSZ - 1) & ~((uint64)PGSZ - 1));

  uint64 per_tick = calibrate(code, 4);
  uint64 want = per_tick * (uint64)dwell;
  if(want < 4096)
    want = 4096;                           // below one lui step the dwell is unmeasurable

  // Say so if the dwell cannot be honoured, rather than quietly walking faster than asked.
  if(blocks_for(want) > MAXBLK){
    want = MAXC * MAXBLK;
    printf("walker: dwell too long for one page, using ~%d ticks instead\\n",
           (int)(want / per_tick));
  }

  for(int p = 0; p < pages; p++)
    fill_page(code + (uint64)p * NWORD, NWORD, want);
  code[(uint64)pages * NWORD - 1] = RET;   // the way back out of the corridor
  sync_icache();

  printf("walker: corridor %d pages (%d KB), %d delay block%s per page (~%d tick%s)\\n",
         pages, pages * 4, blocks_for(want), blocks_for(want) == 1 ? "" : "s",
         dwell, dwell == 1 ? "" : "s");
  printf("walker: PC walks 0x%lx .. 0x%lx\\n",
         (uint64)code, (uint64)code + (uint64)pages * PGSZ);

  // Timing its own walk turns the prediction into something the student can check: pages x dwell
  // is what it should be, and a number that disagrees means the machine was busy elsewhere.
  // Walk it again and again. One pass is over in pages x dwell ticks -- about half a minute at
  // the defaults -- which is shorter than it takes to set up an observation, so by default this
  // keeps lapping until the student kills it, exactly as spin does. Each lap retraces the same
  // addresses, so the PC reads as a repeating ramp rather than one climb: whoever missed the
  // start can wait for the next one. A third argument caps the number of laps.
  for(int lap = 1; laps == 0 || lap <= laps; lap++){
    int t0 = uptime();
    run(code);                             // the walk itself
    int elapsed = uptime() - t0;

    // One short line per lap, and a lap is tens of seconds. Deliberately infrequent: this is the
    // same serial channel GINI's state dumps use, and a chatty program corrupts them.
    printf("walker: lap %d walked in %d ticks (expected ~%d)\\n", lap, elapsed, pages * dwell);
  }
  exit(0);
}
""",
    "toucher": """#include "kernel/types.h"
#include "user/user.h"
// TOUCH N pages twice. Pass 1 faults them in; pass 2 finds them mapped and faults zero times.
// This moves a DATA pointer across a large span -- the PC stays in the small loop below.
// (For the PROGRAM COUNTER walking a large span, see walker.)
//   toucher             48 pages, sequential
//   toucher 64           64 pages, sequential
//   toucher 64 rand      shuffled order  — same count of faults, scattered addresses
//   toucher 64 stride    every 7th page, wrapping
#define MAXP 512
static int order[MAXP];
static unsigned long seed = 12345;

static int
nextrand(void)
{
  seed = seed * 1103515245u + 12345u;
  return (seed >> 16) & 0x7fff;
}

static int
gcd(int a, int b)
{
  while(b){ int t = a % b; a = b; b = t; }
  return a;
}

// A stride only visits every page when it is COPRIME with the page count. A fixed stride of 7
// silently visits one seventh of them whenever n is a multiple of 7 — which would make pass 1
// fault far fewer times than the student predicted, and pass 2 fault for the pages pass 1 never
// reached. Both halves of the lesson would be wrong, quietly. So pick the first stride that
// actually covers.
static int
coprime_stride(int n)
{
  for(int s = 7; s < n; s++)
    if(gcd(s, n) == 1)
      return s;
  return 1;                                        // n < 8, or prime-poor: plain sequential
}

int
main(int argc, char *argv[])
{
  int n = (argc > 1) ? atoi(argv[1]) : 48;
  char *mode = (argc > 2) ? argv[2] : "seq";
  if(n < 1) n = 1;
  if(n > MAXP) n = MAXP;

  // Grow the address space by n pages and allocate NOTHING. Every page below is still a promise.
  char *base = sbrklazy(n * 4096);
  if(base == SBRK_ERROR){
    printf("toucher: cannot grow by %d pages\\n", n);
    exit(1);
  }

  for(int i = 0; i < n; i++)
    order[i] = i;
  if(mode[0] == 's' && mode[1] == 't'){            // stride
    int s = coprime_stride(n);
    for(int i = 0; i < n; i++)
      order[i] = (i * s) % n;
  } else if(mode[0] == 'r'){                       // rand: Fisher-Yates
    for(int i = n - 1; i > 0; i--){
      int j = nextrand() % (i + 1);
      int t = order[i]; order[i] = order[j]; order[j] = t;
    }
  }

  // PASS 1 — every touch is a first touch, so every touch is a page fault.
  for(int i = 0; i < n; i++){
    base[order[i] * 4096] = 1;
    if((i % 8) == 7) pause(1);                    // paced so the fault-in is watchable
  }

  pause(4);                                        // a clear gap between the two passes

  // PASS 2 — identical addresses, identical order. Watch the fault counter NOT move.
  for(int i = 0; i < n; i++){
    base[order[i] * 4096] += 1;
    if((i % 8) == 7) pause(1);                    // same pacing, so only the faults differ
  }

  printf("toucher: %d pages, %s, two passes done\\n", n, mode);
  exit(0);
}
""",

    # sgrind — buffer-cache pressure, with the cliff as a dial.
    #
    # NBUF is 30. Touch fewer than that many distinct blocks and they all stay cached; touch more
    # and every pass evicts what the next pass needs. The hit rate does not decline gently, it
    # falls off a cliff, and the student can compute where the cliff is BEFORE running it.
    "sgrind": """#include "kernel/types.h"
#include "kernel/fcntl.h"
#include "user/user.h"
// Read K distinct files round and round. The block cache holds NBUF = 30 buffers.
//   sgrind         K = 20  — fits in the cache, hit rate stays high
//   sgrind 60      K = 60  — every pass evicts what the next pass wants
static char buf[512];

static void
nameof(char *dst, int i)                            // "sg0" … "sg511"; no sprintf in xv6
{
  int k = 0;
  dst[k++] = 's'; dst[k++] = 'g';
  if(i >= 100) dst[k++] = '0' + (i / 100) % 10;
  if(i >= 10)  dst[k++] = '0' + (i / 10) % 10;
  dst[k++] = '0' + i % 10;
  dst[k] = 0;
}

int
main(int argc, char *argv[])
{
  int k = (argc > 1) ? atoi(argv[1]) : 20;
  char name[8];
  if(k < 1) k = 1;
  if(k > 400) k = 400;                              // FSSIZE is 2000 blocks; stay well inside

  for(int i = 0; i < 512; i++)
    buf[i] = 'x';
  for(int i = 0; i < k; i++){                       // create the working set once
    nameof(name, i);
    int fd = open(name, O_CREATE | O_WRONLY);
    if(fd < 0){ k = i; break; }                     // out of inodes: shrink to what we made
    write(fd, buf, sizeof(buf));
    close(fd);
  }
  printf("sgrind: working set %d blocks (cache holds 30)\\n", k);

  for(;;){                                          // now read them round and round
    for(int i = 0; i < k; i++){
      nameof(name, i);
      int fd = open(name, O_RDONLY);
      if(fd >= 0){
        read(fd, buf, sizeof(buf));
        close(fd);
      }
    }
    pause(1);
  }
  return 0;
}
""",

    # mgrind — allocator, page-table and fork pressure, with nothing else moving.
    #
    # `grind` exercises everything at once, which makes it useless for attribution: when a lock
    # heats up you cannot say why. mgrind and sgrind each lean on ONE subsystem, so the lock
    # profile becomes a diagnosis — run one, read which lock heats, and the answer is the program
    # you ran.
    "mgrind": """#include "kernel/types.h"
#include "user/user.h"
// Hammer the physical page allocator, the page-table walk, and fork's address-space copy.
//   mgrind         16 pages a cycle
//   mgrind 64      more per cycle, harder on kalloc
int
main(int argc, char *argv[])
{
  int pages = (argc > 1) ? atoi(argv[1]) : 16;
  if(pages < 1) pages = 1;
  if(pages > 256) pages = 256;

  for(;;){
    char *p = sbrk(pages * 4096);                   // eager: real pages, right now
    if(p != SBRK_ERROR){
      for(int i = 0; i < pages; i++)                // walk the page table installing mappings
        p[i * 4096] = 1;
      int pid = fork();                             // copies the whole address space
      if(pid == 0){
        p[0] = 2;                                   // a write to a shared page
        exit(0);
      }
      if(pid > 0)
        wait(0);
      sbrk(-pages * 4096);                          // hand every page back to the allocator
    }
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

# ---------------------------------------------------------------------------------------------
# 5) A-LAB 02 — FAIR SHARE. What GINI owns so the scheduling assignment can be done and measured.
#
#    The student OWNS: syscall.h syscall.c sysproc.c user.h usys.pl proc.h proc.c defs.h and
#    kernel/sched_policy.c (plus user/sharetest.c, seeded by the lab). Those are real kernel files,
#    linked into the lab folder by the A-Lab mechanism; the shadow registry is not involved.
#    GINI OWNS everything here, and keeps it OUT of the student's edit sites:
#
#      kernel/sched_policy.c  the policies, shipped in their starting state: round-robin and
#                             priority working, lottery drawing uniformly, stride a stub that
#                             runs round-robin (so an unfinished Part C measures badly instead of
#                             idling the machine). gini_pick() in proc.c switches between them.
#      p->rtime               timer ticks run, per process, counted on every hart's timer branch
#                             (NOT inside clockintr's cpuid()==0 guard). The measurement must
#                             survive the student's mistakes, so they never write it.
#      kernel/gini_sys.c      GINI's own syscalls -- setpolicy, getrtime, settick -- numbered
#      kernel/gini_syscall.h  from 64 and dispatched before the stock table, so the student's
#      user/gini.h            syscall.h still ends at SYS_sync 22 as the handout says, and their
#      user/gini_usys.S       user.h / usys.pl hold nothing of ours.
#      STRIDE_C               in param.h, so sysproc.c and sched_policy.c both see it.
#      user/policy            `policy` / `policy N` at the Keyboard -- the lab's selector.
#
#    Every edit here is a regex_must/write_must: a lab that silently lost one cannot be started.
# ---------------------------------------------------------------------------------------------
_SCHED_POLICY = """// kernel/sched_policy.c — the scheduling policies, and nothing else.
//
// Every policy has the same shape: scan proc[], consider the RUNNABLE processes, and return the
// one that should run next -- or 0 when nothing is runnable, which makes the scheduler idle until
// the next interrupt. gini_pick() in proc.c calls the policy chosen with `policy N`:
//
//   0  pick_rr       round-robin
//   1  pick_prio     priority, with aging (a lower number is a higher priority)
//   2  pick_lottery  lottery
//   3  pick_stride   stride
//
// Whatever a policy returns is checked before it is used: it must point at an entry of proc[],
// and that process must be RUNNABLE. Anything else is rejected and counted (User Code shows the
// count), and that one pick falls back to round-robin.
//
// Nothing here takes a lock, just like procdump: a process can change state between your scan
// and the switch. The scheduler re-checks RUNNABLE under p->lock, so a stale read costs one pick.
//
// STRIDE_C, 1<<20, is defined in param.h, so every kernel file can use it.

#include "types.h"
#include "param.h"
#include "memlayout.h"
#include "riscv.h"
#include "spinlock.h"
#include "proc.h"
#include "defs.h"

extern struct proc proc[NPROC];

// A xorshift pseudo-random generator: fast, and good enough to draw a lottery with. Seeded from
// the clock the first time it is used, so two boots do not draw the same lottery.
uint
gini_rand(void)
{
  static uint seed = 0;
  if(seed == 0)
    seed = (uint)r_time() | 1;
  seed ^= seed << 13;
  seed ^= seed >> 17;
  seed ^= seed << 5;
  return seed;
}

// ROUND-ROBIN: the first RUNNABLE process after the one picked last time.
struct proc *
pick_rr(void)
{
  static int next = 0;
  for(int i = 0; i < NPROC; i++){
    struct proc *p = &proc[(next + i) % NPROC];
    if(p->state == RUNNABLE){
      next = (next + i + 1) % NPROC;
      return p;
    }
  }
  return 0;
}

// PRIORITY: the lowest effective priority wins. Every pick a process spends waiting makes it a
// little more important (aging), so a low-priority process is never starved forever.
struct proc *
pick_prio(void)
{
  struct proc *p, *best = 0;
  int best_eff = 0;
  for(p = proc; p < &proc[NPROC]; p++){
    if(p->state != RUNNABLE)
      continue;
    p->wait_ticks++;
    int eff = p->priority - p->wait_ticks / 8;
    if(best == 0 || eff < best_eff){
      best = p;
      best_eff = eff;
    }
  }
  if(best)
    best->wait_ticks = 0;
  return best;
}

// LOTTERY: count the entries in the draw, draw one at random, and walk to the winner.
struct proc *
pick_lottery(void)
{
  struct proc *p;
  int n = 0;

  for(p = proc; p < &proc[NPROC]; p++)
    if(p->state == RUNNABLE)
      n++;
  if(n == 0)
    return 0;

  int win = gini_rand() % n, i = 0;
  for(p = proc; p < &proc[NPROC]; p++){
    if(p->state != RUNNABLE)
      continue;
    if(i++ == win)
      return p;
  }
  return 0;
}

// STRIDE: not written yet. Until it is, policy 3 runs round-robin.
struct proc *
pick_stride(void)
{
  return pick_rr();
}
"""

_GINI_SYSCALL_H = """// GINI's own system calls -- part of the machine, not of any assignment. They are numbered far
// above the stock table (which ends at SYS_sync 22) and dispatched before it, so a student adding
// a call at 23 never meets them. Included by kernel/defs.h and user/gini_usys.S.
#define GINI_SYS_BASE        64
#define SYS_gini_setpolicy   64   // setpolicy(n)
#define SYS_gini_getrtime    65   // getrtime(pid)
#define SYS_gini_settick     66   // settick(ms)
#define SYS_gini_memctl      67   // memctl(op, a, b) -- A-Lab 03 (gini_mem.c)
"""

_GINI_SYS_C = """// kernel/gini_sys.c — GINI's own system calls (see gini_syscall.h). Not student code.
//
//   setpolicy(n)  n < 0: return the current policy. Otherwise switch to it and return the old one,
//                 or -1 if there is no such policy. What the `policy` command calls; it does not
//                 need the console control path the Machine Lab uses.
//   getrtime(pid) timer ticks that process has spent running (p->rtime), or -1 if no such process.
//                 Read-only: sharetest prints shares from it.
//   settick(ms)   ms <= 0: return the timer tick length in ms. Otherwise set it (10..1000) and return
//                 the old one. sharetest shortens the tick while it measures and puts it back.

#include "types.h"
#include "param.h"
#include "memlayout.h"
#include "riscv.h"
#include "spinlock.h"
#include "proc.h"
#include "defs.h"
#include "gini_syscall.h"   // directly: an older student defs.h does not include it

// A-Lab 03 (gini_mem.c). In defs.h too, and repeated for the same reason as the include above.
uint64 gini_memctl(void);

extern struct proc proc[NPROC];
extern int sched_policy;

// The timer tick, in QEMU virt timebase cycles (10 MHz): 5,000,000 is ~0.5 s, GINI's tick.
uint64 gini_tick_cycles = 5000000;

uint64
gini_syscall(int num)
{
  int a;
  argint(0, &a);
  switch(num){
  case SYS_gini_setpolicy: {
    int old = sched_policy;
    if(a < 0)
      return old;
    if(a >= GINI_NPOLICY)
      return -1;
    sched_policy = a;
    return old;
  }
  case SYS_gini_getrtime:
    for(struct proc *p = proc; p < &proc[NPROC]; p++)
      if(p->state != UNUSED && p->pid == a)
        return p->rtime;
    return -1;
  case SYS_gini_settick: {
    int old = (int)(gini_tick_cycles / 10000);
    if(a <= 0)
      return old;
    if(a < 10 || a > 1000)
      return -1;
    gini_tick_cycles = (uint64)a * 10000;
    return old;
  }
  case SYS_gini_memctl:
    return gini_memctl();
  }
  PRINTF("%d %s: unknown GINI sys call %d\\n", myproc()->pid, myproc()->name, num);
  return -1;
}
""".replace("PRINTF", PRINT)

_GINI_USER_H = """// GINI's own system calls -- part of the machine, not of any assignment.
int setpolicy(int n);   // n < 0: the current policy; else switch, return the old one (-1: no such)
int getrtime(int pid);  // timer ticks that process has run, or -1
int settick(int ms);    // ms <= 0: the tick length in ms; else set it (10..1000), return the old one

// A-Lab 03: GINI's allocator instrument (kernel/gini_mem.c, kernel/gini_mem.h)
uint64 memctl(int op, uint64 a, uint64 b);
"""

_GINI_USYS_S = """# GINI's own system calls (kernel/gini_syscall.h) -- kept out of usys.pl, which is a student file.
#include "kernel/gini_syscall.h"
.global setpolicy
setpolicy:
 li a7, SYS_gini_setpolicy
 ecall
 ret
.global getrtime
getrtime:
 li a7, SYS_gini_getrtime
 ecall
 ret
.global settick
settick:
 li a7, SYS_gini_settick
 ecall
 ret
.global memctl
memctl:
 li a7, SYS_gini_memctl
 ecall
 ret
"""

_POLICY_PROG = """#include "kernel/types.h"
#include "user/user.h"
#include "user/gini.h"

// policy      print the scheduling policy the kernel is running
// policy N    switch to policy N, on the running machine, no rebuild
static char *names[] = { "round-robin", "priority", "lottery", "stride" };

static char *
name(int n)
{
  return (n >= 0 && n < 4) ? names[n] : "?";
}

int
main(int argc, char *argv[])
{
  if(argc < 2){
    int n = setpolicy(-1);
    if(n < 0){
      // setpolicy is GINI's call, dispatched by a hook in syscall.c. A lab folder whose syscall.c
      // was seeded from an older image has no hook, and the kernel says "unknown sys call 64".
      fprintf(2, "policy: this kernel's syscall.c is older than GINI's own system calls; "
                 "use the Process Scheduler face to change policy\\n");
      exit(1);
    }
    printf("policy %d (%s)\\n", n, name(n));
    exit(0);
  }
  char *a = argv[1];
  int ok = a[0] != 0;
  for(char *c = a; *c; c++)
    if(*c < '0' || *c > '9')
      ok = 0;
  int old = ok ? setpolicy(atoi(a)) : -1;
  if(old < 0){
    fprintf(2, "policy: %s is not a policy (0=rr 1=priority 2=lottery 3=stride)\\n", a);
    exit(1);
  }
  printf("policy %d (%s), was %d (%s)\\n", atoi(a), name(atoi(a)), old, name(old));
  exit(0);
}
"""

if (ROOT / "kernel").exists():
    write_must("kernel/sched_policy.c", _SCHED_POLICY)
    write_must("kernel/gini_syscall.h", _GINI_SYSCALL_H)
    write_must("kernel/gini_sys.c", _GINI_SYS_C)
    write_must("user/gini.h", _GINI_USER_H)
    write_must("user/gini_usys.S", _GINI_USYS_S)
    for _obj in ("sched_policy.o", "gini_sys.o"):
        regex_must("Makefile", r"(OBJS = \\\n)", r"\1  $K/" + _obj + r" \\\n",
                   "$K/" + _obj)
    # every user program links GINI's stubs too (ULIB), built like usys.o
    regex_must("Makefile", r"(ULIB = [^\n]*)", r"\1 $U/gini_usys.o", "$U/gini_usys.o")
    regex_must("Makefile", r"(\$U/usys\.o : \$U/usys\.S\n\t[^\n]*\n)",
               r"\1\n$U/gini_usys.o : $U/gini_usys.S kernel/gini_syscall.h\n"
               r"\t$(CC) $(CFLAGS) -c -o $U/gini_usys.o $U/gini_usys.S\n",
               "$U/gini_usys.o : ")

    regex_must("kernel/param.h", r"\Z",
               "\n#define STRIDE_C     (1<<20)  // GINI-xv6: stride scheduling's constant (A-Lab 02)\n",
               "GINI-xv6: stride scheduling's constant")
    regex_must("kernel/proc.h", r"(char name\[16\];[^\n]*\n)",
               r"\1  uint64 rtime;              // GINI-xv6: timer ticks run (A-Lab 02 measures this)\n",
               "GINI-xv6: timer ticks run")
    regex_must("kernel/proc.c", r"(p->state = USED;\n)",
               r"\1  p->rtime = 0;  // GINI-xv6 run accounting\n",
               "GINI-xv6 run accounting")
    # GINI's calls go first: numbered from 64, they never reach the stock table or the counters.
    regex_must("kernel/syscall.c", r"(num = p->trapframe->a7;\n)",
               r"\1  if(num >= GINI_SYS_BASE){ p->trapframe->a0 = gini_syscall(num); return; }"
               r"  // GINI-xv6 own calls\n",
               "GINI-xv6 own calls")
    append_once("kernel/defs.h", """
// GINI-xv6 A-Lab 02: the policies (kernel/sched_policy.c) and GINI's own syscalls (gini_sys.c)
#include "gini_syscall.h"
uint            gini_rand(void);
struct proc*    pick_rr(void);
struct proc*    pick_prio(void);
struct proc*    pick_lottery(void);
struct proc*    pick_stride(void);
extern int      gini_pick_rejects;
extern uint64   gini_tick_cycles;
uint64          gini_syscall(int);
""", "GINI-xv6 A-Lab 02: the policies")
    if "GINI-xv6 A-Lab 02: the policies" not in (ROOT / "kernel" / "defs.h").read_text():
        failed.append("kernel/defs.h: A-Lab 02 prototypes not added")
    add_uprog("policy", _POLICY_PROG)
    if "$U/_policy\\" not in (ROOT / "Makefile").read_text():
        failed.append("Makefile: _policy not registered")


# ---------------------------------------------------------------------------------------------
# 6) A-LAB 03 — BUDDY ALLOCATION AND MEGAPAGES. Compiled into EVERY lab's kernel, because the
#    image is one image, and inert in every lab but A-Lab 03.
#
#    The student OWNS kernel/kalloc.c (seeded with the buddy starter by a-lab-03.yaml, replacing
#    the image's) and kernel/vm.c (the image's, below). buddy.h, buddytest.c and megatest.c are
#    shown to them and never built from their copy. GINI OWNS:
#
#      kernel/gini_mem.c   the allocation bitmap that used to sit at the end of kalloc.c (3e),
#                          moved because in A-Lab 03 the student's kalloc.c REPLACES the image's,
#                          so nothing GINI needs at run time may live in it; plus the bookkeeping
#                          around kalloc_block()/kfree_block(), an audit of the student's free
#                          lists, and memctl(). kalloc_block/kfree_block are weak references, so
#                          memctl() returns -1 on every other lab's kernel.
#      kernel/buddy.h      the allocator's structures. In the image, not a seed: gini_mem.c
#                          includes it in every build.
#      kernel/gini_mem.h   what memctl() reports, shared with the two test programs.
#      vm.c megapages      walk_level(), megapte(), demote(), and uvmunmap()/uvmcopy() that
#                          handle a 2 MB leaf. Inert elsewhere: only the student's A-Lab 03
#                          uvmalloc() ever makes one. What the student writes -- the leaf check in
#                          walk(), the offset in walkaddr(), megapages in uvmalloc() -- must never
#                          be here.
#      memctl              system call 67, GINI's own (gini_sys.c), so it stays out of usys.pl.
#      buddytest megatest  in UPROGS for every lab. On another lab's kernel they print one line,
#                          "buddytest: memctl failed -- is this GINI's A-Lab 03 kernel?", and exit.
#
#    The sources are the public gini-side files of the lab, verbatim, except that the bitmap in
#    gini_mem.c is weak (see there) and keeps the comments it carried in kalloc.c; and vm.c repeats
#    three declarations, because an A-Lab 01/02 folder keeps an older defs.h (see _VM_WALK_LEVEL). Anchored on kalloc.c existing, like 3e; every edit after that
#    is a regex_must/write_must, because a lab that silently lost one cannot be done.
# ---------------------------------------------------------------------------------------------
_BUDDY_H = r"""// kernel/buddy.h -- the buddy allocator's data structures and helper macros. A-Lab 03.
//
// Read all of it. Do not change it: GINI reads these structures to check your allocator.
//
// Include after types.h, param.h, memlayout.h, spinlock.h and riscv.h.
//
// Every block is 2^k pages long, for k = 0 .. MAXK, and starts at a multiple of its own size:
//
//     k      0    1    2    3    4     5     6     7     8    9
//     size   4K   8K   16K  32K  64K   128K  256K  512K  1M   2M
//
// k is only an index: free list k holds the free blocks of 2^k pages.

#define MAXK       9                    // 2^9 pages = 2 MB, the largest block
#define NPAGES     ((PHYSTOP - KERNBASE) / PGSIZE)

// The size in bytes of a block of 2^k pages.
#define BLOCKSIZE(k)   ((uint64)PGSIZE << (k))

// Physical address <-> page number. Page 0 is the page at KERNBASE.
#define PA2IDX(pa)     (((uint64)(pa) - KERNBASE) / PGSIZE)
#define IDX2PA(i)      (KERNBASE + (uint64)(i) * PGSIZE)

// The buddy of the block of 2^k pages at pa. Correct only if pa is a multiple of BLOCKSIZE(k),
// which every such block in a buddy allocator is. (KERNBASE is a multiple of 2 MB, so "a multiple
// of BLOCKSIZE(k)" means the same thing for the address and for the page number.)
#define BUDDY(pa, k)   ((uint64)(pa) ^ BLOCKSIZE(k))

// page_info[]: one byte per physical page. For the FIRST page of a FREE block it holds
// PI_FREE | k. For every other page it is 0: pages inside a free block, pages in use, and the
// kernel image. Keeping it correct is your job.
#define PI_FREE        0x80
#define PI_K(b)        ((b) & 0x0f)

extern uint8 page_info[NPAGES];

// A free block is threaded onto its free list through its own first bytes: the memory is free,
// so the allocator may use it. You never touch next and prev yourself -- the helpers do.
struct block {
  struct block *next;
  struct block *prev;
};

// The allocator's state: free list k, and a counter for it, for every k. The helpers keep
// nfree[k] equal to the length of list k.
struct buddy {
  struct spinlock lock;
  struct block *free[MAXK + 1];
  uint64 nfree[MAXK + 1];
};

extern struct buddy buddy;

// GINI's helpers (kalloc.c, top half). They change the lists and counters, never page_info.
// The caller must hold buddy.lock -- the wrappers already do when they call your functions.
void   block_push(uint64 pa, int k);    // put the block at pa on list k
uint64 block_pop(int k);                // take a block off list k; 0 if it is empty
void   block_remove(uint64 pa, int k);  // take this particular block off list k

// Yours (kalloc.c, bottom half).
void   buddy_init(uint64 start, uint64 end);
uint64 buddy_alloc(int k);              // a free block of 2^k pages, or 0
void   buddy_free(uint64 pa, int k);    // the block of 2^k pages at pa is free again
"""

_GINI_MEM_H = r"""// kernel/gini_mem.h -- what memctl() reports. GINI's, shared by the kernel and the A-Lab 03
// test programs. Not student code.

#define MC_STATE     1   // memctl(MC_STATE, &state)       audit the allocator now
#define MC_ALLOC     2   // memctl(MC_ALLOC, size)         kalloc_block(size), held for the test
#define MC_FREE      3   // memctl(MC_FREE, pa, size)      kfree_block(pa, size) of held pages
#define MC_RELEASE   4   // memctl(MC_RELEASE)             free every held page
#define MC_FRAGMENT  5   // memctl(MC_FRAGMENT)            leave no 2 MB block free (see gini_mem.c)
#define MC_PTCOUNT   6   // memctl(MC_PTCOUNT, &ptcount)   count this process's leaves in a range

#define MC_NSIZES   10   // 4K, 8K, ... 2M: MAXK + 1

struct memstate {
  uint64 nfree[MC_NSIZES];       // free blocks of each size (4K first), counted on the lists
  uint64 freepages;              // pages on the free lists
  uint64 overlaps;               // blocks handed out holding a page already in use, plus free
                                 //   blocks holding a page in use or a page on another list
  uint64 unaccounted;            // pages GINI knows are free that are on no free list
  uint64 misplaced;              // free blocks at the wrong address, of the wrong size, or with
                                 //   the wrong mark in page_info
  uint64 doublefree;             // frees GINI refused: the pages were already free
  uint64 start;                  // first address the allocator may hand out
  uint64 boot_nfree[MC_NSIZES];  // what buddy_init() built
  uint64 ideal_nfree[MC_NSIZES]; // what it should have built
  uint64 boot_problems;          // overlaps + unaccounted + misplaced at boot
  uint64 held;                   // pages the tests are holding right now
};

struct ptcount {
  uint64 va, len;                // in: the range to look at
  uint64 l1;                     // out: level-1 leaves (2 MB) touching the range
  uint64 l0;                     // out: level-0 leaves (4 KB) in the range
  uint64 l1_misaligned;          // out: level-1 leaves whose physical address is not 2 MB-aligned
  uint64 l1_unowned;             // out: level-1 leaves over memory the allocator calls free
};
"""

_GINI_MEM_C = r"""// kernel/gini_mem.c -- GINI's side of the physical page allocator. Not student code.
//
//   * the allocation bitmap (1 bit per page, 1 = in use) that every other GINI view reads, and
//     the Memory face's free/fragmentation dump;
//   * the bookkeeping kalloc_block()/kfree_block() do around the student's buddy_alloc() and
//     buddy_free(): overlaps and refused double frees;
//   * A-Lab 03's instrument: an audit of the student's free lists against the bitmap, a record of
//     what buddy_init() built at boot, and memctl(), the system call buddytest and megatest use.
//
// The audit never trusts the student's structures to be sane: it bounds every walk and checks
// every pointer before following it, so a broken allocator is reported, not crashed into.

#include "types.h"
#include "param.h"
#include "memlayout.h"
#include "spinlock.h"
#include "riscv.h"
#include "proc.h"
#include "defs.h"
#include "buddy.h"
#include "gini_mem.h"

extern char end[];

// ---------------------------------------------------------------------------- the buddy state
// Defined here, not in the student's kalloc.c, because this file is compiled into EVERY lab's
// kernel. A-Lab 03's kalloc.c uses these; every other lab's kalloc.c ignores them.
struct buddy buddy;
uint8 page_info[NPAGES];

// Only A-Lab 03's kalloc.c defines these. Weak, so the other labs' kernels still link; memctl()
// refuses to run without them.
void *kalloc_block(uint64) __attribute__((weak));
void  kfree_block(void *, uint64) __attribute__((weak));

// ---------------------------------------------------------------------------- the bitmap
// Moved here from kalloc.c, unchanged in behaviour. Every definition is WEAK: an A-Lab 01
// folder armed on an older GINI keeps a kalloc.c that still defines all of it, and its strong
// copy then wins at link time instead of the kernel failing with "multiple definition".
//
// What the bitmap is for (from kalloc.c, where it began): 1 bit per page, and it is what makes a
// student page allocator SAFE to run -- without it, an allocator that hands back a live page
// corrupts memory undetectably. 4 KB of state for 128 MiB of RAM, updated inside the lock the
// allocator already holds, and the data source for the Memory Lab's fragmentation view.

uint8 gini_pagemap[(NPAGES + 7) / 8] __attribute__((weak));
uint64 gini_ka_shadow __attribute__((weak));

static inline uint64 gini_pg_idx(uint64 pa) { return (pa - KERNBASE) / PGSIZE; }
static int gini_pg_inrange(uint64 pa) { return pa >= KERNBASE && pa < PHYSTOP; }

__attribute__((weak)) void
gini_page_set(uint64 pa)
{
  if(!gini_pg_inrange(pa)) return;
  uint64 i = gini_pg_idx(pa);
  gini_pagemap[i / 8] |= (1 << (i % 8));
}

__attribute__((weak)) void
gini_page_clear(uint64 pa)
{
  if(!gini_pg_inrange(pa)) return;
  uint64 i = gini_pg_idx(pa);
  gini_pagemap[i / 8] &= ~(1 << (i % 8));
}

__attribute__((weak)) int
gini_page_isset(uint64 pa)
{
  if(!gini_pg_inrange(pa)) return 0;
  uint64 i = gini_pg_idx(pa);
  return (gini_pagemap[i / 8] >> (i % 8)) & 1;
}

// The vm shadow table still names the page allocator; A-Labs never arm it.
__attribute__((weak)) void *
gsh_kalloc(void *arg)
{
  (void)arg;
  return kalloc_shadow();
}

// EXACT, not probabilistic: the page must be aligned, inside RAM, and genuinely free per the
// bitmap. (xv6's kfree also poisons freed pages with 0x01, which is a cheap secondary check --
// but the bitmap is authoritative and costs one bit.)
__attribute__((weak)) int
gsh_kalloc_valid(void *arg, void *ans)
{
  uint64 pa = (uint64)ans;
  (void)arg;
  if(pa % PGSIZE)          return 0;
  if(!gini_pg_inrange(pa)) return 0;
  if((char *)pa < end)     return 0;
  return !gini_page_isset(pa);
}

// Free-page count + the largest CONTIGUOUS free run -- the fragmentation score.
//
// Start where the ALLOCATOR starts, not where physical memory starts. The pages below
// PGROUNDUP(end) are the kernel's own image: they are never freed, so their bits stay 0 -- and a
// 0 bit means "free". This counted the kernel itself as free memory, about 50 pages of it, on
// every machine until it was found while building the sysinfo assignment, where a student walks
// kmem.freelist and gets the right answer: the two disagreed by exactly the size of the kernel
// image, and the student's number was the correct one.
__attribute__((weak)) void
gini_kadump(void)
{
  uint64 first = (PGROUNDUP((uint64)end) - KERNBASE) / PGSIZE;
  uint64 free = 0, run = 0, best = 0;
  for(uint64 i = first; i < NPAGES; i++){
    if(gini_page_isset(KERNBASE + i * PGSIZE)){
      run = 0;
    } else {
      free++; run++;
      if(run > best) best = run;
    }
  }
  printk("KA free %d total %d maxrun %d shadow %d\n",
        (int)free, (int)NPAGES, (int)best, (int)gini_ka_shadow);
}

// ---------------------------------------------------------------------------- the wrappers' side

static uint64 gini_overlaps;      // blocks buddy_alloc() returned that held a page in use
static uint64 gini_doublefree;    // frees refused because the pages were already free

// buddy.lock is held. The pages of [pa, pa + 2^k pages) have just been handed out.
void
gini_block_taken(uint64 pa, int k)
{
  int clash = 0;
  for(uint64 p = pa; p < pa + BLOCKSIZE(k); p += PGSIZE){
    if(gini_page_isset(p))
      clash = 1;
    gini_page_set(p);
  }
  if(clash)
    gini_overlaps++;
}

// buddy.lock is held. Returns 1 if the block may go back to buddy_free(), 0 if any of its pages
// is already free -- freeing it again would put the same memory on two lists.
int
gini_block_given(uint64 pa, int k)
{
  for(uint64 p = pa; p < pa + BLOCKSIZE(k); p += PGSIZE)
    if(!gini_page_isset(p)){
      gini_doublefree++;
      return 0;
    }
  for(uint64 p = pa; p < pa + BLOCKSIZE(k); p += PGSIZE)
    gini_page_clear(p);
  return 1;
}

// ---------------------------------------------------------------------------- the audit

static uint8 seen[(NPAGES + 7) / 8];  // pages found on a free list during one audit

static int  seen_get(uint64 i) { return (seen[i / 8] >> (i % 8)) & 1; }
static void seen_set(uint64 i) { seen[i / 8] |= (1 << (i % 8)); }

static uint64
first_page(void)
{
  return PGROUNDUP((uint64)end);
}

// buddy.lock is held. Walks every free list and checks it against the bitmap and page_info.
static void
audit(struct memstate *s)
{
  uint64 start = first_page();

  memset(seen, 0, sizeof(seen));
  for(int k = 0; k <= MAXK; k++){
    uint64 n = 0;
    struct block *b = buddy.free[k];
    while(b){
      uint64 pa = (uint64)b;
      if(n >= NPAGES || pa % PGSIZE || pa < start || pa >= PHYSTOP){
        s->misplaced++;                // a pointer that is not a free page: stop following it
        break;
      }
      n++;
      if(pa % BLOCKSIZE(k) || pa + BLOCKSIZE(k) > PHYSTOP || page_info[PA2IDX(pa)] != (PI_FREE | k))
        s->misplaced++;
      int bad = 0;
      for(uint64 p = pa; p < pa + BLOCKSIZE(k) && p < PHYSTOP; p += PGSIZE){
        uint64 i = PA2IDX(p);
        if(seen_get(i) || gini_page_isset(p))
          bad = 1;
        seen_set(i);
      }
      if(bad)
        s->overlaps++;
      b = b->next;
    }
    s->nfree[k] = n;
    if(n != buddy.nfree[k])
      s->misplaced++;                  // the counter and the list disagree
    s->freepages += n << k;
  }
  for(uint64 p = start; p < PHYSTOP; p += PGSIZE)
    if(!gini_page_isset(p) && !seen_get(PA2IDX(p)))
      s->unaccounted++;
}

static uint64 boot_nfree[MC_NSIZES], ideal_nfree[MC_NSIZES], boot_problems;
static struct spinlock held_lock;

// Called by kinit() once buddy_init() has returned. Records what it built, what it should have
// built, and whether the lists were sound -- buddytest init reports this, because by the time any
// program runs, the boot state is gone.
void
gini_buddy_booted(void)
{
  struct memstate s;

  initlock(&held_lock, "memctl");

  // GINI's own reference: the largest aligned block at every step, from first_page() up.
  uint64 a = first_page();
  while(a + PGSIZE <= PHYSTOP){
    int k = MAXK;
    while(k > 0 && (a % BLOCKSIZE(k) || a + BLOCKSIZE(k) > PHYSTOP))
      k--;
    ideal_nfree[k]++;
    a += BLOCKSIZE(k);
  }

  memset(&s, 0, sizeof(s));
  acquire(&buddy.lock);
  audit(&s);
  release(&buddy.lock);
  for(int k = 0; k <= MAXK; k++)
    boot_nfree[k] = s.nfree[k];
  boot_problems = s.overlaps + s.unaccounted + s.misplaced;
}

// ---------------------------------------------------------------------------- memctl()

static uint8 held[(NPAGES + 7) / 8];   // pages a test is holding (allocated through memctl)
static uint64 nheld;

static int  held_get(uint64 pa) { uint64 i = PA2IDX(pa); return (held[i / 8] >> (i % 8)) & 1; }
static void held_set(uint64 pa) { uint64 i = PA2IDX(pa); held[i / 8] |= (1 << (i % 8)); nheld++; }
static void held_clr(uint64 pa) { uint64 i = PA2IDX(pa); held[i / 8] &= ~(1 << (i % 8)); nheld--; }

static void
hold(uint64 pa, int k)
{
  acquire(&held_lock);
  for(uint64 p = pa; p < pa + BLOCKSIZE(k); p += PGSIZE)
    held_set(p);
  release(&held_lock);
}

// Give back [pa, pa + 2^k pages) if every page of it is held. Returns 0, or -1 if not.
static int
unhold(uint64 pa, int k)
{
  if(k < 0 || k > MAXK || pa % BLOCKSIZE(k) || pa < first_page() || pa + BLOCKSIZE(k) > PHYSTOP)
    return -1;
  acquire(&held_lock);
  for(uint64 p = pa; p < pa + BLOCKSIZE(k); p += PGSIZE)
    if(!held_get(p)){
      release(&held_lock);
      return -1;
    }
  for(uint64 p = pa; p < pa + BLOCKSIZE(k); p += PGSIZE)
    held_clr(p);
  release(&held_lock);
  kfree_block((void *)pa, BLOCKSIZE(k));
  return 0;
}

static uint64
release_all(void)
{
  uint64 n = 0;
  for(uint64 p = first_page(); p < PHYSTOP; p += PGSIZE){
    acquire(&held_lock);
    int h = held_get(p);
    if(h)
      held_clr(p);
    release(&held_lock);
    if(h){
      kfree_block((void *)p, PGSIZE);
      n++;
    }
  }
  return n;
}

// Leave no 2 MB block free, and nearly all of memory free in smaller blocks: take every 2 MB
// block, keep its first page, and give the other 511 back as pieces of 4K, 8K, ... 1M. No piece can
// merge back into 2 MB while that first page is held. megatest uses this to check that fork
// falls back to 4 KB pages; MC_RELEASE undoes it.
static uint64
fragment(void)
{
  uint64 n = 0;
  while(n < NPAGES / 512){
    uint64 pa = (uint64)kalloc_block(MEGAPGSIZE);
    if(pa == 0)
      break;
    hold(pa, 0);
    for(int j = 0; j < MAXK; j++)
      kfree_block((void *)(pa + BLOCKSIZE(j)), BLOCKSIZE(j));
    n++;
  }
  return n;
}

// GINI's own page-table walk -- deliberately not the student's walk().
static void
ptcount(pagetable_t pt, struct ptcount *c)
{
  uint64 va = c->va, last = c->va + c->len;
  if(last > MAXVA)
    last = MAXVA;
  while(va < last){
    pte_t e2 = pt[PX(2, va)];
    if(!(e2 & PTE_V) || PTE_LEAF(e2)){
      va = (va + (1L << 30)) & ~((1L << 30) - 1);
      continue;
    }
    pte_t e1 = ((pagetable_t)PTE2PA(e2))[PX(1, va)];
    if(!(e1 & PTE_V)){
      va = MEGAROUNDDOWN(va) + MEGAPGSIZE;
      continue;
    }
    if(PTE_LEAF(e1)){
      c->l1++;
      if(PTE2PA(e1) % MEGAPGSIZE)
        c->l1_misaligned++;
      else if(!gini_page_isset(PTE2PA(e1)))
        c->l1_unowned++;
      va = MEGAROUNDDOWN(va) + MEGAPGSIZE;
      continue;
    }
    pte_t e0 = ((pagetable_t)PTE2PA(e1))[PX(0, va)];
    if((e0 & PTE_V) && PTE_LEAF(e0))
      c->l0++;
    va = PGROUNDDOWN(va) + PGSIZE;
  }
}

uint64
gini_memctl(void)
{
  int op;
  uint64 a, b;

  argint(0, &op);
  argaddr(1, &a);
  argaddr(2, &b);
  if(kalloc_block == 0 || kfree_block == 0)
    return -1;                         // not A-Lab 03's kernel
  switch(op){
  case MC_STATE: {
    struct memstate s;
    memset(&s, 0, sizeof(s));
    acquire(&buddy.lock);
    audit(&s);
    s.overlaps += gini_overlaps;
    s.doublefree = gini_doublefree;
    release(&buddy.lock);
    s.start = first_page();
    for(int k = 0; k <= MAXK; k++){
      s.boot_nfree[k] = boot_nfree[k];
      s.ideal_nfree[k] = ideal_nfree[k];
    }
    s.boot_problems = boot_problems;
    s.held = nheld;
    return copyout(myproc()->pagetable, myproc()->sz, a, (char *)&s, sizeof(s));
  }
  case MC_ALLOC: {
    int k = -1;
    for(int j = 0; j <= MAXK; j++)
      if(BLOCKSIZE(j) == a)
        k = j;
    if(k < 0)
      return 0;
    uint64 pa = (uint64)kalloc_block(a);
    if(pa)
      hold(pa, k);
    return pa;
  }
  case MC_FREE: {
    for(int j = 0; j <= MAXK; j++)
      if(BLOCKSIZE(j) == b)
        return unhold(a, j);
    return -1;
  }
  case MC_RELEASE:
    return release_all();
  case MC_FRAGMENT:
    return fragment();
  case MC_PTCOUNT: {
    struct ptcount c;
    if(copyin(myproc()->pagetable, myproc()->sz, (char *)&c, a, sizeof(c)) < 0)
      return -1;
    c.l1 = c.l0 = c.l1_misaligned = c.l1_unowned = 0;
    ptcount(myproc()->pagetable, &c);
    return copyout(myproc()->pagetable, myproc()->sz, a, (char *)&c, sizeof(c));
  }
  }
  return -1;
}
""".replace("printk(", PRINT + "(")

_BUDDYTEST = r"""// buddytest -- the allocator instrument for A-Lab 03. Do not edit: GINI reads what it prints.
//
//   buddytest                 one block of every size, 4K to 2M, then the state of the free lists
//   buddytest init            what buddy_init() built at boot, against what it should have built
//   buddytest alloc [seed]    every size, in a random order, each checked as it comes back
//   buddytest churn [seed]    thousands of random allocations and frees; are the lists back?
//   buddytest pieces [seed]   a 2 MB block freed a page at a time, in a random order
//   buddytest guard           a buddy that is free but smaller must not be merged
//
// The allocations are made by the kernel, through GINI's memctl() system call, with the same
// kalloc_block() and kfree_block() the rest of the kernel uses. Every check is made against
// GINI's own record of which pages are in use, not against what your free lists say.
//
// Each subcommand ends with one verdict line, "<name>: ok" or "<name>: FAIL ...".

#include "kernel/types.h"
#include "user/user.h"
#include "user/gini.h"
#include "kernel/gini_mem.h"

#define NSIZES   MC_NSIZES          // 4K, 8K, ... 2M
#define PG       4096UL
#define SIZE(k)  (PG << (k))

// "4K", "8K", ... "512K", "1M", "2M"
static char *
label(int k)
{
  static char *l[] = { "4K", "8K", "16K", "32K", "64K", "128K", "256K", "512K", "1M", "2M" };
  return l[k];
}

static struct memstate st;

static uint64 rng;

static uint64
rnd(void)
{
  rng ^= rng << 13;
  rng ^= rng >> 7;
  rng ^= rng << 17;
  return rng;
}

static void
seed(int argc, char **argv)
{
  rng = argc > 2 ? (uint64)atoi(argv[2]) : (uint64)uptime();
  rng = rng * 2654435761UL + 88172645463325252UL;
  if(rng == 0)
    rng = 1;
  for(int i = 0; i < 4; i++)
    rnd();
}

static void
state(struct memstate *s)
{
  if(memctl(MC_STATE, (uint64)s, 0) != 0){
    printf("buddytest: memctl failed -- is this GINI's A-Lab 03 kernel?\n");
    exit(1);
  }
}

static void
freeline(char *title, uint64 *n)
{
  printf("%s", title);
  for(int k = 0; k < NSIZES; k++)
    printf(" %s:%d", label(k), (int)n[k]);
  printf("\n");
}

static int
same(uint64 *a, uint64 *b)
{
  for(int k = 0; k < NSIZES; k++)
    if(a[k] != b[k])
      return 0;
  return 1;
}

// The two lines that must stay 0 for the whole lab, and the two that GINI also watches.
static int
health(struct memstate *s)
{
  printf("overlaps: %d\n", (int)s->overlaps);
  printf("unaccounted: %d\n", (int)s->unaccounted);
  if(s->misplaced)
    printf("misplaced: %d\n", (int)s->misplaced);
  if(s->doublefree)
    printf("double frees refused: %d\n", (int)s->doublefree);
  return s->overlaps == 0 && s->unaccounted == 0 && s->misplaced == 0 && s->doublefree == 0;
}

static void
release(void)
{
  memctl(MC_RELEASE, 0, 0);
}

// ---------------------------------------------------------------------------- plain

static void
plain(void)
{
  uint64 pa[NSIZES];
  for(int k = 0; k < NSIZES; k++){
    pa[k] = memctl(MC_ALLOC, SIZE(k), 0);
    if(pa[k] == 0)
      printf("%s: none\n", label(k));
    else if(pa[k] % SIZE(k))
      printf("%s: misaligned (%p)\n", label(k), (void *)pa[k]);
    else
      printf("%s: ok\n", label(k));
  }
  for(int k = 0; k < NSIZES; k++)
    if(pa[k])
      memctl(MC_FREE, pa[k], SIZE(k));
  state(&st);
  freeline("free:", st.nfree);
  health(&st);
}

// ---------------------------------------------------------------------------- init

static void
init(void)
{
  state(&st);
  printf("start: %p\n", (void *)st.start);
  freeline("boot free: ", st.boot_nfree);
  freeline("should be: ", st.ideal_nfree);
  if(st.boot_problems)
    printf("boot audit: %d problem(s) -- overlapping, missing or misplaced blocks\n",
           (int)st.boot_problems);
  else
    printf("boot audit: clean\n");
  if(st.boot_problems == 0 && same(st.boot_nfree, st.ideal_nfree))
    printf("init: ok\n");
  else if(st.boot_problems)
    printf("init: FAIL the free lists at boot were not sound\n");
  else
    printf("init: FAIL memory was not given out as the largest aligned blocks\n");
}

// ---------------------------------------------------------------------------- alloc

static void
alloc(void)
{
  int seq[NSIZES], bad = 0;
  uint64 pa[NSIZES];
  struct memstate before;

  for(int k = 0; k < NSIZES; k++)
    seq[k] = k;
  for(int i = NSIZES - 1; i > 0; i--){
    int j = rnd() % (i + 1), t = seq[i];
    seq[i] = seq[j];
    seq[j] = t;
  }

  for(int i = 0; i < NSIZES; i++){
    int k = seq[i];
    state(&before);
    pa[k] = memctl(MC_ALLOC, SIZE(k), 0);
    state(&st);
    if(pa[k] == 0){
      printf("alloc %s: none\n", label(k));
      bad = 1;
    } else if(pa[k] % SIZE(k)){
      printf("alloc %s: at %p, which is not a multiple of its size\n", label(k), (void *)pa[k]);
      bad = 1;
    } else if(before.freepages - st.freepages != (1UL << k)){
      printf("alloc %s: free memory fell by %d pages, not %d\n", label(k),
             (int)(before.freepages - st.freepages), 1 << k);
      bad = 1;
    } else if(st.overlaps != before.overlaps){
      printf("alloc %s: the block holds a page that was in use or still free\n", label(k));
      bad = 1;
    } else {
      printf("alloc %s: ok\n", label(k));
    }
  }
  for(int k = 0; k < NSIZES; k++)
    if(pa[k])
      memctl(MC_FREE, pa[k], SIZE(k));
  release();
  state(&st);
  if(!health(&st))
    bad = 1;
  printf(bad ? "alloc: FAIL\n" : "alloc: ok\n");
}

// ---------------------------------------------------------------------------- churn

#define NHOLD 512

static void
churn(void)
{
  static uint64 hpa[NHOLD];
  static int hk[NHOLD];
  int n = 0, allocs = 0, frees = 0;
  struct memstate before;

  state(&before);
  freeline("free before:", before.nfree);
  for(int op = 0; op < 4000; op++){
    if(n < NHOLD && (n == 0 || rnd() % 100 < 55)){
      uint64 r = rnd() % 100;
      int k = r < 50 ? 0 : r < 70 ? 1 : r < 82 ? 2 : r < 90 ? 3 : r < 95 ? 4 : r < 98 ? 6 : 9;
      uint64 pa = memctl(MC_ALLOC, SIZE(k), 0);
      if(pa){
        hpa[n] = pa;
        hk[n] = k;
        n++;
        allocs++;
      }
    } else {
      int i = rnd() % n;
      memctl(MC_FREE, hpa[i], SIZE(hk[i]));
      hpa[i] = hpa[n - 1];
      hk[i] = hk[n - 1];
      n--;
      frees++;
    }
  }
  while(n > 0){                        // free the rest in a random order
    int i = rnd() % n;
    memctl(MC_FREE, hpa[i], SIZE(hk[i]));
    hpa[i] = hpa[n - 1];
    hk[i] = hk[n - 1];
    n--;
    frees++;
  }
  release();
  state(&st);
  freeline("free after: ", st.nfree);
  printf("blocks: %d allocated, %d freed\n", allocs, frees);
  int ok = health(&st);
  if(!ok)
    printf("churn: FAIL\n");
  else if(st.freepages != before.freepages)
    printf("churn: FAIL %d pages did not come back\n", (int)(before.freepages - st.freepages));
  else if(!same(st.nfree, before.nfree))
    printf("churn: FAIL every page came back, but not as the blocks it left as\n");
  else
    printf("churn: ok\n");
}

// ---------------------------------------------------------------------------- pieces

static void
pieces(void)
{
  static int perm[512];
  struct memstate before;

  state(&before);
  uint64 pa = memctl(MC_ALLOC, SIZE(9), 0);
  if(pa == 0){
    printf("pieces: FAIL there is no 2 MB block to start with\n");
    return;
  }
  for(int i = 0; i < 512; i++)
    perm[i] = i;
  for(int i = 511; i > 0; i--){
    int j = rnd() % (i + 1), t = perm[i];
    perm[i] = perm[j];
    perm[j] = t;
  }
  for(int i = 0; i < 512; i++)
    memctl(MC_FREE, pa + (uint64)perm[i] * PG, PG);
  release();
  state(&st);
  freeline("free before:", before.nfree);
  freeline("free after: ", st.nfree);
  int ok = health(&st);
  if(!ok)
    printf("pieces: FAIL\n");
  else if(st.nfree[9] != before.nfree[9] || !same(st.nfree, before.nfree))
    printf("pieces: FAIL the 512 pages did not come back together as one 2M block\n");
  else
    printf("pieces: ok\n");
}

// ---------------------------------------------------------------------------- guard

// Four pages, P..P+3 (16K). Free P+2 alone: a free 4K block whose buddy P+3 is in use. Then free
// P..P+1 (8K): its 8K buddy would start at P+2 -- where there is a free block, but a 4K one, so
// the two must NOT merge. If they do, a free 16K block holds P+3, which is in use.
static void
guard(void)
{
  struct memstate before, mid;

  state(&before);
  uint64 p = memctl(MC_ALLOC, SIZE(2), 0);
  if(p == 0){
    printf("guard: FAIL no 16K block\n");
    return;
  }
  memctl(MC_FREE, p + 2 * PG, PG);
  memctl(MC_FREE, p, SIZE(1));
  state(&mid);
  int merged = mid.overlaps != before.overlaps;
  memctl(MC_FREE, p + 3 * PG, PG);
  release();
  state(&st);
  freeline("free before:", before.nfree);
  freeline("free after: ", st.nfree);
  int ok = health(&st);
  if(merged)
    printf("guard: FAIL a block merged with a smaller free block\n");
  else if(!ok)
    printf("guard: FAIL\n");
  else if(!same(st.nfree, before.nfree))
    printf("guard: FAIL the four pages did not come back together\n");
  else
    printf("guard: ok\n");
}

int
main(int argc, char *argv[])
{
  seed(argc, argv);
  state(&st);                          // exits with a message on a kernel without A-Lab 03
  release();                           // anything a crashed earlier run left held
  if(argc < 2)
    plain();
  else if(strcmp(argv[1], "init") == 0)
    init();
  else if(strcmp(argv[1], "alloc") == 0)
    alloc();
  else if(strcmp(argv[1], "churn") == 0)
    churn();
  else if(strcmp(argv[1], "pieces") == 0)
    pieces();
  else if(strcmp(argv[1], "guard") == 0)
    guard();
  else {
    fprintf(2, "usage: buddytest [init | alloc | churn | pieces | guard] [seed]\n");
    exit(1);
  }
  exit(0);
}
"""

_MEGATEST = r"""// megatest -- the megapage instrument for A-Lab 03. Do not edit: GINI reads what it prints.
//
//   megatest                  all of the below, one report
//   megatest map              an 8 MB heap at a 2 MB boundary: which entries map it?
//   megatest data [seed]      a pattern across the heap, and read()/write() through it
//   megatest edge [seed]      a heap that starts and ends off a 2 MB boundary
//   megatest shrink [seed]    shrink into a megapage, then past it
//   megatest fork [seed]      fork copies megapages, and falls back when there are no 2 MB blocks
//   megatest leaks            all of the above; is every page, and every 2 MB block, back?
//
// Every test runs in a child process of its own, so each one starts from a small heap and ends
// with an exit() that frees everything. Page-table entries are counted by GINI's own walk of
// your page table (memctl), not by your walk().

#include "kernel/types.h"
#include "kernel/fcntl.h"
#include "user/user.h"
#include "user/gini.h"
#include "kernel/gini_mem.h"

#define PG    4096UL
#define MEGA  (2UL * 1024 * 1024)
#define HEAP  (4 * MEGA)              // 8 MB

static uint64 rng, seed0;

static uint64
rnd(void)
{
  rng ^= rng << 13;
  rng ^= rng >> 7;
  rng ^= rng << 17;
  return rng;
}

static uint64
word(uint64 addr)                      // the pattern: a function of the seed and the address
{
  uint64 x = (addr ^ seed0) * 0x9E3779B97F4A7C15UL;
  return x ^ (x >> 29);
}

static void
count(uint64 va, uint64 len, struct ptcount *c)
{
  memset(c, 0, sizeof(*c));
  c->va = va;
  c->len = len;
  memctl(MC_PTCOUNT, (uint64)c, 0);
}

static char *
grow_to_boundary(void)                 // the heap now ends at a 2 MB boundary
{
  uint64 cur = (uint64)sbrk(0);
  uint64 b = (cur + MEGA - 1) & ~(MEGA - 1);
  if(b > cur && sbrk(b - cur) == SBRK_ERROR)
    return 0;
  return sbrk(0);
}

static int
zero(char *p, uint64 len)
{
  uint64 *w = (uint64 *)p;
  for(uint64 i = 0; i < len / 8; i++)
    if(w[i] != 0)
      return 0;
  return 1;
}

static void
fill(char *p, uint64 len)
{
  uint64 *w = (uint64 *)p;
  for(uint64 i = 0; i < len / 8; i++)
    w[i] = word((uint64)&w[i]);
}

static int
check(char *p, uint64 len)
{
  uint64 *w = (uint64 *)p;
  for(uint64 i = 0; i < len / 8; i++)
    if(w[i] != word((uint64)&w[i]))
      return 0;
  return 1;
}

// ---------------------------------------------------------------------------- the tests
// Each prints its lines and returns 1 if everything it checked was right.

static int
t_map(int quiet)
{
  struct ptcount c;
  char *base = grow_to_boundary();
  if(base == 0 || sbrk(HEAP) == SBRK_ERROR){
    printf("heap: sbrk failed\n");
    return 0;
  }
  count((uint64)base, HEAP, &c);
  if(!quiet){
    printf("heap: 8 MB at %p\n", base);
    printf("leaves 4K: %d\n", (int)c.l0);
    printf("leaves 2M: %d\n", (int)c.l1);
    if(c.l1_misaligned)
      printf("leaves 2M not 2 MB-aligned: %d\n", (int)c.l1_misaligned);
    if(c.l1_unowned)
      printf("leaves 2M over free memory: %d\n", (int)c.l1_unowned);
  }
  int ok = zero(base, HEAP);
  if(!ok)
    printf("heap: new memory is not zero\n");
  return ok && c.l1 == 4 && c.l0 == 0 && c.l1_misaligned == 0 && c.l1_unowned == 0;
}

static int
t_data(void)
{
  struct ptcount c;
  char *base = grow_to_boundary();
  if(base == 0 || sbrk(HEAP) == SBRK_ERROR){
    printf("data: sbrk failed\n");
    return 0;
  }
  count((uint64)base, HEAP, &c);
  if(c.l1 == 0){
    // Without megapages there is nothing here to test: 4 KB pages always hold their data.
    printf("data: FAIL the heap has no megapages yet, so there is nothing to test\n");
    return 0;
  }
  fill(base, HEAP);
  int ok = check(base, HEAP);
  printf("data: %s\n", ok ? "ok" : "FAIL what was written is not what reads back");

  // read() into the heap -- copyout() through walkaddr() -- at an address well inside the first
  // megapage, then write() out of the heap -- copyin() -- from inside another.
  static char want[1024], got[256];   // got: less than a pipe holds (512)
  int rok = 1;
  uint64 off = MEGA / 2 + 8 * (rnd() % 1000) + 3;
  char *dst = base + off;
  int fd = open("README", O_RDONLY);
  int n = fd < 0 ? -1 : read(fd, want, sizeof(want));
  if(fd >= 0)
    close(fd);
  fd = open("README", O_RDONLY);
  int m = fd < 0 ? -1 : read(fd, dst, sizeof(want));
  if(fd >= 0)
    close(fd);
  if(n <= 0 || m != n || memcmp(dst, want, n) != 0)
    rok = 0;
  // everything outside what read() wrote must still hold the pattern (8-byte aligned edges)
  uint64 lo = ((uint64)dst & ~7UL), hi = (((uint64)dst + n + 7) & ~7UL);
  if(!check(base, lo - (uint64)base) || !check((char *)hi, (uint64)base + HEAP - hi))
    rok = 0;

  int p[2];
  char *src = base + 3 * MEGA + 8 * (rnd() % 1000);
  if(pipe(p) < 0 || write(p[1], src, sizeof(got)) != sizeof(got) ||
     read(p[0], got, sizeof(got)) != sizeof(got) || memcmp(got, src, sizeof(got)) != 0)
    rok = 0;
  close(p[0]);
  close(p[1]);
  printf("read into heap: %s\n", rok ? "ok" : "FAIL read() or write() used the wrong place in a megapage");
  return ok && rok;
}

static int
t_edge(void)
{
  struct ptcount c;
  // Start 3 pages below a boundary, end 2 pages past the second boundary after it:
  // 3 small pages, 2 megapages, 2 small pages.
  uint64 cur = (uint64)sbrk(0);
  uint64 b = ((cur + 3 * PG + MEGA - 1) & ~(MEGA - 1));
  if(sbrk(b - 3 * PG - cur) == SBRK_ERROR){
    printf("edge: sbrk failed\n");
    return 0;
  }
  char *start = sbrk(0);
  uint64 len = 3 * PG + 2 * MEGA + 2 * PG;
  if(sbrk(len) == SBRK_ERROR){
    printf("edge: sbrk failed\n");
    return 0;
  }
  count((uint64)start, len, &c);
  if(c.l0 != 5 || c.l1 != 2 || c.l1_misaligned != 0){
    printf("edge: FAIL expected 3 + 2 small pages around 2 megapages, found %d small and %d mega\n",
           (int)c.l0, (int)c.l1);
    return 0;
  }
  if(!zero(start, len)){
    printf("edge: FAIL new memory is not zero\n");
    return 0;
  }
  fill(start, len);
  if(!check(start, len)){
    printf("edge: FAIL what was written is not what reads back\n");
    return 0;
  }
  printf("edge: ok\n");
  return 1;
}

static int
t_shrink(void)
{
  struct ptcount c;
  char *base = grow_to_boundary();
  if(base == 0 || sbrk(HEAP) == SBRK_ERROR){
    printf("shrink: sbrk failed\n");
    return 0;
  }
  fill(base, HEAP);
  // Into the last megapage: it must be demoted, and the half that stays must keep its data.
  sbrk(-(int)(MEGA / 2));
  count((uint64)base, HEAP, &c);
  int ok1 = c.l1 == 3 && c.l0 == 256 && check(base, HEAP - MEGA / 2);
  // Past everything: no leaf may be left.
  sbrk(-(int)(HEAP - MEGA / 2));
  count((uint64)base, HEAP, &c);
  int ok2 = c.l1 == 0 && c.l0 == 0;
  // Grow again: the memory must be usable, and zero.
  if(sbrk(HEAP) == SBRK_ERROR)
    ok2 = 0;
  else
    ok2 = ok2 && zero(base, HEAP);
  if(ok1 && ok2)
    printf("shrink: ok\n");
  else if(!ok1)
    printf("shrink: FAIL shrinking into a megapage should leave 3 megapages and 256 small pages\n");
  else
    printf("shrink: FAIL shrinking past the heap should leave no entries, and growing again zeroed memory\n");
  return ok1 && ok2;
}

// The child checks what fork gave it, then scribbles over all of it. want2m: leaves expected.
static int
forked(char *base, int want2m, char *label)
{
  int pid = fork();
  if(pid < 0){
    printf("%s: FAIL fork failed\n", label);
    return 0;
  }
  if(pid == 0){
    struct ptcount c;
    count((uint64)base, HEAP, &c);
    int ok = check(base, HEAP) &&
             (want2m ? (c.l1 == 4 && c.l0 == 0) : (c.l1 == 0 && c.l0 == 2048));
    if(!ok)
      printf("%s: the child found %d megapages and %d small pages\n", label, (int)c.l1, (int)c.l0);
    memset(base, 0x5a, HEAP);
    exit(ok ? 0 : 1);
  }
  int st = 1;
  wait(&st);
  int ok = st == 0 && check(base, HEAP);   // the parent's copy must be untouched
  printf("%s: %s\n", label, ok ? "ok" : "FAIL");
  return ok;
}

static int
t_fork(void)
{
  char *base = grow_to_boundary();
  if(base == 0 || sbrk(HEAP) == SBRK_ERROR){
    printf("fork: sbrk failed\n");
    return 0;
  }
  fill(base, HEAP);
  int ok = forked(base, 1, "fork");
  memctl(MC_FRAGMENT, 0, 0);           // now there is no free 2 MB block anywhere
  ok = forked(base, 0, "fork without 2M blocks") && ok;
  memctl(MC_RELEASE, 0, 0);
  return ok;
}

// ---------------------------------------------------------------------------- driver

static int
run(int (*t)(void))
{
  int pid = fork();
  if(pid < 0){
    printf("megatest: fork failed\n");
    return 0;
  }
  if(pid == 0)
    exit(t() ? 0 : 1);
  int st = 1;
  wait(&st);
  return st == 0;
}

static int t_map_loud(void) { return t_map(0); }

static int
all(void)
{
  int ok = run(t_map_loud);
  ok = run(t_data) && ok;
  ok = run(t_edge) && ok;
  ok = run(t_shrink) && ok;
  ok = run(t_fork) && ok;
  return ok;
}

static void
leaks(void)
{
  struct memstate before, after;
  memctl(MC_STATE, (uint64)&before, 0);
  all();
  memctl(MC_RELEASE, 0, 0);
  memctl(MC_STATE, (uint64)&after, 0);
  printf("free pages: %d before, %d after\n", (int)before.freepages, (int)after.freepages);
  printf("2 MB blocks: %d before, %d after\n", (int)before.nfree[9], (int)after.nfree[9]);
  if(after.freepages != before.freepages)
    printf("leaks: FAIL %d pages did not come back\n", (int)(before.freepages - after.freepages));
  else if(after.nfree[9] != before.nfree[9])
    printf("leaks: FAIL every page came back, but not every 2 MB block\n");
  else if(after.overlaps || after.unaccounted || after.misplaced || after.doublefree)
    printf("leaks: FAIL the free lists are not sound\n");
  else
    printf("leaks: ok\n");
}

static void
verdict(char *name, int ok)
{
  printf("%s: %s\n", name, ok ? "ok" : "FAIL");
}

int
main(int argc, char *argv[])
{
  seed0 = argc > 2 ? (uint64)atoi(argv[2]) : (uint64)uptime();
  rng = seed0 * 2654435761UL + 1;
  struct memstate probe;
  if(memctl(MC_STATE, (uint64)&probe, 0) != 0){
    printf("megatest: memctl failed -- is this GINI's A-Lab 03 kernel?\n");
    exit(1);
  }
  memctl(MC_RELEASE, 0, 0);            // anything a crashed earlier run left held
  if(argc < 2)
    all();
  else if(strcmp(argv[1], "map") == 0)
    verdict("map", run(t_map_loud));
  else if(strcmp(argv[1], "data") == 0)
    run(t_data);
  else if(strcmp(argv[1], "edge") == 0)
    run(t_edge);
  else if(strcmp(argv[1], "shrink") == 0)
    run(t_shrink);
  else if(strcmp(argv[1], "fork") == 0)
    run(t_fork);
  else if(strcmp(argv[1], "leaks") == 0)
    leaks();
  else {
    fprintf(2, "usage: megatest [map | data | edge | shrink | fork | leaks] [seed]\n");
    exit(1);
  }
  exit(0);
}
"""

# vm.c, GINI's megapage code. Each piece is the image's vm.c as gini-side/kernel/vm.c has it.
_VM_WALK_LEVEL = """
// GINI (A-Lab 03), provided: read it, do not change it.
// Like walk(), but stop at the given level and return the entry for va there. Returns 0 if a
// table on the way down is missing (and alloc is 0, or kalloc failed), or if a leaf above that
// level already maps va.
pte_t *
walk_level(pagetable_t pagetable, uint64 va, int level, int alloc)
{
  if (va >= MAXVA)
    panic("walk_level");

  for (int l = 2; l > level; l--) {
    pte_t *pte = &pagetable[PX(l, va)];
    if (*pte & PTE_V) {
      if (PTE_LEAF(*pte))
        return 0;
      pagetable = (pagetable_t)PTE2PA(*pte);
    } else {
      if (!alloc || (pagetable = (pde_t *)kalloc()) == 0)
        return 0;
      memset(pagetable, 0, PGSIZE);
      *pte = PA2PTE(pagetable) | PTE_V;
    }
  }
  return &pagetable[PX(level, va)];
}

// GINI (A-Lab 03), provided: the level-1 entry for va if it is a megapage, else 0.
static pte_t *
megapte(pagetable_t pagetable, uint64 va)
{
  pte_t *pte = walk_level(pagetable, va, 1, 0);
  if (pte && (*pte & PTE_V) && PTE_LEAF(*pte))
    return pte;
  return 0;
}

// GINI (A-Lab 03): these three are declared in defs.h too. They are repeated here because a lab
// folder armed on an older GINI keeps its own defs.h, which has never heard of them, and this
// file is built in every lab.
void *kalloc_block(uint64) __attribute__((weak));
void kfree_block(void *, uint64) __attribute__((weak));
int demote(pagetable_t, uint64);
"""

_VM_UNMAP_LOOP = """  uint64 last = va + npages * PGSIZE;
  for (a = va; a < last; a += PGSIZE) {
    pte_t *mpte = megapte(pagetable, a);
    if (mpte) {                    // A-Lab 03: a megapage
      if (a == MEGAROUNDDOWN(a) && a + MEGAPGSIZE <= last) {
        if (do_free)
          kfree_block((void *)PTE2PA(*mpte), MEGAPGSIZE);
        *mpte = 0;
        a += MEGAPGSIZE - PGSIZE;  // the loop adds the last PGSIZE
        continue;
      }
      // Only part of it goes: split it into 512 small pages first.
      if (demote(pagetable, a) != 0)
        panic("uvmunmap: demote");
    }
"""

_VM_DEMOTE = """// GINI (A-Lab 03), provided: replace the megapage that maps va with a level-0 table of 512
// entries mapping the same 2 MB, with the same permissions. The process sees no difference. Returns 0, or -1 if
// there was no page for the new table.
int
demote(pagetable_t pagetable, uint64 va)
{
  pte_t *mpte = megapte(pagetable, va);
  if (mpte == 0)
    return 0;
  pagetable_t t = (pagetable_t)kalloc();
  if (t == 0)
    return -1;
  uint64 pa = PTE2PA(*mpte);
  uint64 flags = PTE_FLAGS(*mpte);
  for (int i = 0; i < 512; i++)
    t[i] = PA2PTE(pa + (uint64)i * PGSIZE) | flags;
  *mpte = PA2PTE(t) | PTE_V;
  return 0;
}

"""

_VM_COPY_MEGA = """    pte_t *mpte = megapte(old, i);
    if (mpte) {                    // A-Lab 03: a megapage in the parent
      pa = PTE2PA(*mpte);
      flags = PTE_FLAGS(*mpte);
      pte_t *npte = walk_level(new, i, 1, 1);
      if (npte && (*npte & PTE_V) == 0 && (mem = kalloc_block(MEGAPGSIZE)) != 0) {
        memmove(mem, (char *)pa, MEGAPGSIZE);
        *npte = PA2PTE(mem) | flags;
        i += MEGAPGSIZE - PGSIZE;
        continue;
      }
      // No 2 MB block: give the child 512 small pages holding the same data.
      for (uint64 j = 0; j < MEGAPGSIZE; j += PGSIZE, i += PGSIZE) {
        if ((mem = kalloc()) == 0)
          goto err;
        memmove(mem, (char *)(pa + j), PGSIZE);
        if (mappages(new, i, PGSIZE, (uint64)mem, flags) != 0) {
          kfree(mem);
          goto err;
        }
      }
      i -= PGSIZE;                 // the loop adds it back
      continue;
    }
"""

if (ROOT / "kernel" / "kalloc.c").exists():
    write_must("kernel/buddy.h", _BUDDY_H)
    write_must("kernel/gini_mem.h", _GINI_MEM_H)
    write_must("kernel/gini_mem.c", _GINI_MEM_C)
    regex_must("Makefile", r"(  \$K/kalloc\.o \\\n)", r"\1  $K/gini_mem.o \\\n", "$K/gini_mem.o")

    # Inside riscv.h's `#ifndef __ASSEMBLER__` is not needed: these are macros only, which the
    # assembler's preprocessor accepts. At the end, after MAXVA, where the handout points.
    regex_must("kernel/riscv.h", r"(#define MAXVA \(1L << \(9 \+ 9 \+ 9 \+ 12 - 1\)\)\n)",
               r"\1" "\n"
               "// GINI A-Lab 03: megapages. A level-1 leaf maps 2 MB, and its physical address must be a\n"
               "// multiple of 2 MB. An entry is a leaf if any of R, W or X is set.\n"
               "#define MEGAPGSIZE (1L << 21)\n"
               "#define MEGAROUNDUP(sz)  (((sz)+MEGAPGSIZE-1) & ~(MEGAPGSIZE-1))\n"
               "#define MEGAROUNDDOWN(a) (((a)) & ~(MEGAPGSIZE-1))\n"
               "#define PTE_LEAF(pte) ((pte) & (PTE_R | PTE_W | PTE_X))\n",
               "GINI A-Lab 03: megapages")

    # The declarations the handout says "GINI has added". defs.h is the image's in A-Lab 03.
    regex_must("kernel/defs.h", r"(void            kfree\(void \*\);\n)",
               r"\1"
               "// A-Lab 03: a block of 4096, 8192, ... 2 MB bytes. Only A-Lab 03's kalloc.c defines these.\n"
               "// Weak, so every other lab's kernel still links: vm.c's megapage code that calls them only runs\n"
               "// when a megapage exists, and only A-Lab 03 ever makes one.\n"
               "void*           kalloc_block(uint64) __attribute__((weak));\n"
               "void            kfree_block(void *, uint64) __attribute__((weak));\n"
               "\n"
               "// gini_mem.c -- GINI's side of the page allocator (A-Lab 03)\n"
               "void            gini_buddy_booted(void);\n"
               "void            gini_block_taken(uint64, int);\n"
               "int             gini_block_given(uint64, int);\n"
               "uint64          gini_memctl(void);\n",
               "A-Lab 03: a block of 4096")
    regex_must("kernel/defs.h",
               r"(int             copyout\(pagetable_t, uint64, uint64, char \*, uint64\);\n)",
               r"\1"
               "pte_t *         walk_level(pagetable_t, uint64, int, int);   // GINI, A-Lab 03\n"
               "int             demote(pagetable_t, uint64);                 // GINI, A-Lab 03\n",
               "walk_level(pagetable_t, uint64, int, int);")

    regex_must("kernel/vm.c", r"(  return &pagetable\[PX\(0, va\)\];\n\}\n)",
               lambda m: m.group(1) + _VM_WALK_LEVEL,
               "walk_level(pagetable_t pagetable, uint64 va, int level, int alloc)")
    regex_must("kernel/vm.c",
               r"(// Optionally free the physical memory\.\n)(void\nuvmunmap\()",
               r"\1// GINI (A-Lab 03), provided: this version also unmaps megapages, and demotes one that is only\n"
               r"// partly inside the range. Read it, do not change it.\n\2",
               "this version also unmaps megapages")
    regex_must("kernel/vm.c",
               r"(panic\(\"uvmunmap: not aligned\"\);\n\n)"
               r"  for \(a = va; a < va \+ npages \* PGSIZE; a \+= PGSIZE\) \{\n",
               lambda m: m.group(1) + _VM_UNMAP_LOOP,
               "Only part of it goes: split it into 512 small pages first.")
    regex_must("kernel/vm.c",
               r"(    \*pte = 0;\n  \}\n\}\n\n)(// Allocate PTEs and physical memory to grow)",
               lambda m: m.group(1) + _VM_DEMOTE + m.group(2),
               "demote(pagetable_t pagetable, uint64 va)\n{")
    regex_must("kernel/vm.c",
               r"(// its memory into a child's page table\.\n)",
               r"\1// GINI (A-Lab 03), provided: this version also copies megapages, and falls back to 4 KB pages\n"
               r"// when no 2 MB block is free. Read it, do not change it.\n",
               "this version also copies megapages")
    regex_must("kernel/vm.c",
               r"(  char \*mem;\n\n  for \(i = 0; i < sz; i \+= PGSIZE\) \{\n)(    if \(\(pte = walk\(old, i, 0\)\) == 0\))",
               lambda m: m.group(1) + _VM_COPY_MEGA + m.group(2),
               "No 2 MB block: give the child 512 small pages")

    for _name, _src in (("buddytest", _BUDDYTEST), ("megatest", _MEGATEST)):
        add_uprog(_name, _src)
        if f"$U/_{_name}\\" not in (ROOT / "Makefile").read_text():
            failed.append(f"Makefile: _{_name} not registered")

print("GINI xv6 patch:")
for a in applied:
    print("  applied:", a)
for s in skipped:
    print("  SKIPPED:", s)
for f in failed:
    print("  FAILED:", f)
# Never fail the build for a skipped invasive edit — the kernel still compiles + boots. A FAILED
# edit is different: a lab depends on it (see regex_must), so stop the image build here.
if failed:
    sys.exit(1)
