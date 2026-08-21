// WRONG-ON-PURPOSE — virtual memory. Legal answers, wrong behaviour.
#include "../types.h"
#include "../param.h"
#include "../memlayout.h"
#include "../riscv.h"
#include "../spinlock.h"
#include "../proc.h"
#include "../defs.h"

// BUG: only handles LOAD faults and declines stores. Everything it does handle is handled
// correctly (so the validator is happy), but half the faults fall through to the shipped code.
// Expect: faults_handled > 0 AND faults_fellthrough > 0 — a partially-implemented handler.
uint64
vmfault_shadow(pagetable_t pt, uint64 psz, uint64 va, int read)
{
  uint64 mem;
  if(!read)                        // a store fault: "not mine"
    return 0;
  if(va >= psz)
    return 0;
  va = PGROUNDDOWN(va);
  if(ismapped(pt, va))
    return 0;
  if((mem = (uint64)kalloc()) == 0)
    return 0;
  memset((void *)mem, 0, PGSIZE);
  if(mappages(pt, va, PGSIZE, mem, PTE_W | PTE_U | PTE_R) != 0){
    kfree((void *)mem);
    return 0;
  }
  return mem;
}

// BUG: hands out the HIGHEST free page instead of the lowest. Every page is genuinely free, so
// the validator accepts every answer — but allocations march down from the top while frees
// punch holes, so the free space stops being one long run.
// Expect: max_free_run degrades badly compared with the reference.
void *
kalloc_shadow(void)
{
  uint64 start = PGROUNDUP((uint64)end);
  for(uint64 pa = PHYSTOP - PGSIZE; pa >= start; pa -= PGSIZE)
    if(!gini_page_isset(pa))
      return (void *)pa;
  return 0;
}
