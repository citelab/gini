// REFERENCE SOLUTION — virtual-memory shadows. NOT shipped in the image.
#include "../types.h"
#include "../param.h"
#include "../memlayout.h"
#include "../riscv.h"
#include "../spinlock.h"
#include "../proc.h"
#include "../defs.h"

// LAZY ALLOCATION. sbrklazy() grew the address space without allocating anything; the first
// touch traps here. Allocate a page, zero it (never hand a process another's leftovers), map it
// user-writable, and return the physical address.
uint64
vmfault_shadow(pagetable_t pt, uint64 psz, uint64 va, int read)
{
  uint64 mem;
  (void)read;                       // a load or a store both just need the page present

  if(va >= psz)                     // past the process's brk: not a lazy page, genuinely a fault
    return 0;
  va = PGROUNDDOWN(va);
  if(ismapped(pt, va))              // someone already handled it (another hart raced us)
    return 0;
  if((mem = (uint64)kalloc()) == 0) // out of memory: let the shipped path report it
    return 0;
  memset((void *)mem, 0, PGSIZE);
  if(mappages(pt, va, PGSIZE, mem, PTE_W | PTE_U | PTE_R) != 0){
    kfree((void *)mem);             // mapping failed -> do not leak the page
    return 0;
  }
  return mem;
}

// PHYSICAL PAGE ALLOCATION — first-fit over the allocation bitmap, scanning upward from the end
// of the kernel image. Handing out the LOWEST free page keeps allocations packed at the bottom
// of RAM, so the free region above stays one long contiguous run (max_free_run stays high).
void *
kalloc_shadow(void)
{
  uint64 start = PGROUNDUP((uint64)end);
  for(uint64 pa = start; pa + PGSIZE <= PHYSTOP; pa += PGSIZE)
    if(!gini_page_isset(pa))
      return (void *)pa;
  return 0;                         // no free page: fall back (the shipped allocator will fail too)
}
