// REFERENCE SOLUTION — file-system shadows. NOT shipped in the image.
#include "../types.h"
#include "../param.h"
#include "../memlayout.h"
#include "../riscv.h"      // pagetable_t — defs.h needs it
#include "../spinlock.h"
#include "../sleeplock.h"
#include "../fs.h"
#include "../buf.h"
#include "../defs.h"       // gini_block_free(), kalloc(), and friends

// BUFFER-CACHE EVICTION — least recently used. Every hit stamps b->lastuse with the current
// tick, so the buffer with the SMALLEST stamp is the one untouched longest. Buffers with
// refcnt > 0 are in use and are never legal victims.
struct buf *
bget_evict_shadow(uint dev, uint blockno, struct buf *bufs, int nbuf)
{
  struct buf *best = 0;
  (void)dev; (void)blockno;
  for(int i = 0; i < nbuf; i++){
    struct buf *b = &bufs[i];
    if(b->refcnt != 0)
      continue;                                  // in use — not ours to take
    if(best == 0 || b->lastuse < best->lastuse)
      best = b;                                  // older stamp wins
  }
  return best;
}

// BLOCK ALLOCATION — next-fit for locality. Start looking just after the block we handed out
// last, so a growing file gets consecutive blocks and the mean gap stays near 1. Wrap to the
// start of the data region if we run off the end. (The shipped allocator always scans from
// block 0, so once early blocks free up a file ends up scattered.)
uint
balloc_shadow(uint dev, uint nblocks, uint last)
{
  uint start = (last + 1 < nblocks) ? last + 1 : 1;

  for(uint b = start; b < nblocks; b++)          // forward from the last allocation
    if(gini_block_free(dev, b))
      return b;
  for(uint b = 1; b < start; b++)                // wrapped: take the first free block below it
    if(gini_block_free(dev, b))
      return b;
  return 0;                                      // disk full — let the shipped path report it
}
