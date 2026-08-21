// HOSTILE — illegal answers; every one must be rejected.
#include "../types.h"
#include "../param.h"
#include "../memlayout.h"
#include "../riscv.h"      // pagetable_t — defs.h needs it
#include "../spinlock.h"
#include "../sleeplock.h"
#include "../fs.h"
#include "../buf.h"
#include "../defs.h"       // gini_block_free(), kalloc(), and friends

// returns a buffer that is IN USE (refcnt > 0). Recycling it under its owner would corrupt
// whatever that owner is reading or writing.
struct buf *
bget_evict_shadow(uint dev, uint blockno, struct buf *bufs, int nbuf)
{
  (void)dev; (void)blockno;
  for(int i = 0; i < nbuf; i++)
    if(bufs[i].refcnt != 0)
      return &bufs[i];
  return &bufs[0];
}

// returns a block that is already ALLOCATED — hand this to a growing file and two files share
// blocks, which is file-system corruption. The bitmap check is what stops it.
uint
balloc_shadow(uint dev, uint nblocks, uint last)
{
  (void)last;
  for(uint b = 1; b < nblocks; b++)
    if(!gini_block_free(dev, b))     // deliberately pick an IN-USE block
      return b;
  return 1;
}
