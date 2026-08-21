// REFERENCE SOLUTION — scheduler shadows. NOT shipped in the image; see solutions/README.md.
#include "../types.h"
#include "../param.h"
#include "../memlayout.h"
#include "../riscv.h"
#include "../spinlock.h"
#include "../proc.h"

extern struct proc proc[NPROC];

// ROUND ROBIN — every runnable process gets a turn, in order, starting after the last one we
// picked. The rotating cursor is what makes it fair: a fixed scan from proc[0] would always
// favour low pids.
struct proc *
pick_rr_shadow(void)
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

// PRIORITY with AGING — lowest `priority` number wins, but every slice a process waits raises
// its effective priority, so a low-priority process cannot be starved forever by a busy one.
struct proc *
pick_prio_shadow(void)
{
  struct proc *best = 0;
  int best_eff = 0;
  for(struct proc *p = proc; p < &proc[NPROC]; p++){
    if(p->state != RUNNABLE)
      continue;
    p->wait_ticks++;
    int eff = p->priority - p->wait_ticks / 8;   // aging: 8 slices waited == 1 priority level
    if(best == 0 || eff < best_eff){
      best = p;
      best_eff = eff;
    }
  }
  if(best)
    best->wait_ticks = 0;                        // it ran: its aging credit resets
  return best;
}

// LOTTERY — each process holds `tickets` tickets; draw one ticket uniformly at random and run
// whoever owns it. CPU share then tracks ticket share, which is the property being measured.
// (The shipped version ignores tickets and picks uniformly among processes — that is the bug.)
struct proc *
pick_lottery_shadow(void)
{
  static uint seed = 2463534242u;
  int total = 0;
  for(struct proc *p = proc; p < &proc[NPROC]; p++)
    if(p->state == RUNNABLE)
      total += (p->tickets > 0) ? p->tickets : 1;
  if(total <= 0)
    return 0;

  seed ^= seed << 13; seed ^= seed >> 17; seed ^= seed << 5;   // xorshift
  int draw = (int)(seed % (uint)total);

  for(struct proc *p = proc; p < &proc[NPROC]; p++){
    if(p->state != RUNNABLE)
      continue;
    draw -= (p->tickets > 0) ? p->tickets : 1;
    if(draw < 0)
      return p;                                  // this process owns the drawn ticket
  }
  return 0;
}
