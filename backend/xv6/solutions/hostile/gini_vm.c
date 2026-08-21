// HOSTILE — illegal answers; every one must be rejected.
#include "../types.h"
#include "../param.h"
#include "../memlayout.h"
#include "../riscv.h"
#include "../spinlock.h"
#include "../proc.h"
#include "../defs.h"

// claims to have handled the fault and returns a bogus address: unaligned, not RAM, and nothing
// is actually mapped. Believing it would resume a process on a page that does not exist.
uint64
vmfault_shadow(pagetable_t pt, uint64 psz, uint64 va, int read)
{
  (void)pt; (void)psz; (void)va; (void)read;
  return 0x1234;
}

// hands back a page that is ALREADY ALLOCATED — the catastrophic case, and the entire reason S3
// ships with a page bitmap. Without the bitmap this is undetectable and corrupts live memory.
void *
kalloc_shadow(void)
{
  uint64 start = PGROUNDUP((uint64)end);
  for(uint64 pa = start; pa + PGSIZE <= PHYSTOP; pa += PGSIZE)
    if(gini_page_isset(pa))          // deliberately pick an IN-USE page
      return (void *)pa;
  return 0;
}
