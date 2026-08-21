// WRONG-ON-PURPOSE — file system. Legal answers, wrong behaviour.
#include "../types.h"
#include "../param.h"
#include "../memlayout.h"
#include "../riscv.h"      // pagetable_t — defs.h needs it
#include "../spinlock.h"
#include "../sleeplock.h"
#include "../fs.h"
#include "../buf.h"
#include "../defs.h"       // gini_block_free(), kalloc(), and friends

// BUG: evicts the MOST recently used buffer — LRU inverted. Every victim is free (refcnt == 0)
// so the validator accepts them all; the cache simply throws away exactly the block it is about
// to want again.  Expect: cache_hit_rate well below the reference.
struct buf *
bget_evict_shadow(uint dev, uint blockno, struct buf *bufs, int nbuf)
{
  struct buf *best = 0;
  (void)dev; (void)blockno;
  for(int i = 0; i < nbuf; i++){
    struct buf *b = &bufs[i];
    if(b->refcnt != 0)
      continue;
    if(best == 0 || b->lastuse > best->lastuse)   // NEWEST stamp wins — backwards
      best = b;
  }
  return best;
}

// BUG: always scans from the far END of the disk, so consecutive blocks of one file land as far
// apart as the free list allows. Every block returned really is free, so nothing is rejected.
// Expect: mean_gap far larger than the reference.
uint
balloc_shadow(uint dev, uint nblocks, uint last)
{
  (void)last;
  for(uint b = nblocks - 1; b > 0; b--)
    if(gini_block_free(dev, b))
      return b;
  return 0;
}
