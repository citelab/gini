// HOSTILE — returns ILLEGAL answers on purpose. Nothing here should ever reach the kernel: every
// call must be caught by the validator, counted in `rejects`, and replaced by the shipped policy.
// The machine must stay perfectly usable with this loaded. If it panics, the validator is wrong.
#include "../types.h"
#include "../param.h"
#include "../memlayout.h"
#include "../riscv.h"
#include "../spinlock.h"
#include "../proc.h"

extern struct proc proc[NPROC];

// a wild pointer — the old code passed this straight to acquire(&p->lock) and panicked
struct proc *
pick_rr_shadow(void)
{
  return (struct proc *)0xdeadbeefUL;
}

// inside proc[] but not on an entry boundary — a plausible pointer-arithmetic bug
struct proc *
pick_prio_shadow(void)
{
  return (struct proc *)((char *)proc + 3);
}

// a REAL proc slot that is not RUNNABLE — the subtlest of the three, and the one a careless
// student actually writes (forgetting the state check)
struct proc *
pick_lottery_shadow(void)
{
  for(struct proc *p = proc; p < &proc[NPROC]; p++)
    if(p->state == SLEEPING)
      return p;
  return &proc[0];
}
