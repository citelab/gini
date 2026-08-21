// WRONG-ON-PURPOSE — scheduler. Every function here is legal C returning a LEGAL answer, so the
// validator accepts all of it. Only the BEHAVIOURAL measures can catch these, which is exactly
// what they exist to prove. If a mission passes with this file loaded, the mission is broken.
#include "../types.h"
#include "../param.h"
#include "../memlayout.h"
#include "../riscv.h"
#include "../spinlock.h"
#include "../proc.h"

extern struct proc proc[NPROC];

// BUG: no rotation — always returns the FIRST runnable process. pid 1 hogs the CPU and anything
// behind it starves.  Expect: every_runnable_runs == 0.
struct proc *
pick_rr_shadow(void)
{
  for(struct proc *p = proc; p < &proc[NPROC]; p++)
    if(p->state == RUNNABLE)
      return p;
  return 0;
}

// BUG: ignores priority AND aging — round-robin wearing a priority badge. The high-priority
// process gets no more CPU than anyone else.  Expect: cpu_share(highest_priority) ~ 1/N.
struct proc *
pick_prio_shadow(void)
{
  static int rr = 0;
  for(int i = 0; i < NPROC; i++){
    struct proc *p = &proc[(rr + i) % NPROC];
    if(p->state == RUNNABLE){
      rr = (rr + i + 1) % NPROC;
      return p;
    }
  }
  return 0;
}

// BUG: draws uniformly among PROCESSES instead of among TICKETS — the classic misreading of
// "lottery", and precisely the flaw the shipped kernel has.  Expect: share_ratio stays large.
struct proc *
pick_lottery_shadow(void)
{
  static uint seed = 88172645u;
  int n = 0;
  for(struct proc *p = proc; p < &proc[NPROC]; p++)
    if(p->state == RUNNABLE)
      n++;
  if(n == 0)
    return 0;
  seed ^= seed << 13; seed ^= seed >> 17; seed ^= seed << 5;
  int k = (int)(seed % (uint)n);
  for(struct proc *p = proc; p < &proc[NPROC]; p++)
    if(p->state == RUNNABLE && k-- == 0)
      return p;
  return 0;
}
