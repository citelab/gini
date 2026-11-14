/*
 * gr_pipeline.c  —  Z2 module-graph runner. Pure gpacket_t + libc; no globals.
 */
#define _GNU_SOURCE
#include <pthread.h>
#include "gr_pipeline.h"

/*
 * One lock for the pipeline, held by the packet path for a whole run and by the CLI for any edit.
 * There was none: Router Lab's Deploy sends `gpipe clear` and then its adds, and clear destroyed
 * modules -- lua_close on a Lua state, free on a module's state -- while the worker was inside
 * that module's process() with a packet. Under traffic that was a crash in the worker thread,
 * or a corrupted heap that crashed later somewhere else. `gpipe trace` ran a Lua module on the
 * CLI thread against the same lua_State the worker was using, and a lua_State is not
 * thread-safe. Recursive, because the packet watch reads a module's name while the worker may
 * already hold it.
 */
static pthread_mutex_t pl_lock = PTHREAD_RECURSIVE_MUTEX_INITIALIZER_NP;

void gr_pipeline_lock(void)   { pthread_mutex_lock(&pl_lock); }
void gr_pipeline_unlock(void) { pthread_mutex_unlock(&pl_lock); }

void gr_pipeline_init(gr_pipeline_t *p)
{
    p->count = 0;
}

int gr_pipeline_add(gr_pipeline_t *p, gr_module_t *m)
{
    int i;
    gr_pipeline_lock();
    if (p->count >= GR_MAX_MODULES || m == 0)
    {
        gr_pipeline_unlock();
        return -1;
    }
    p->modules[p->count] = m;
    i = p->count++;
    gr_pipeline_unlock();
    return i;
}

void gr_pipeline_clear(gr_pipeline_t *p)
{
    int i, n;
    gr_pipeline_lock();
    n = p->count;
    p->count = 0;                   /* nothing can reach a module once its destroy begins */
    for (i = 0; i < n; i++)
        if (p->modules[i] && p->modules[i]->destroy)
            p->modules[i]->destroy(p->modules[i]);
    gr_pipeline_unlock();
}

/* Which module ended the last run on THIS thread, or -1 when the packet continued through all of
 * them. The packet watch reads it to say WHICH box dropped a packet; gr_verdict_t has no room for
 * it, and widening the ABI every module conforms to for one reader would be the wrong trade.
 * Thread-local, so it is right whichever thread runs the pipeline. */
__thread int gr_pipeline_stopped_at = -1;

gr_verdict_t gr_pipeline_run(gr_pipeline_t *p, gpacket_t *pkt)
{
    gr_verdict_t v = { GR_CONTINUE, -1 };
    int i;
    gr_pipeline_stopped_at = -1;
    if (p->count == 0)                 /* the common case: no modules, no lock */
        return v;
    gr_pipeline_lock();
    for (i = 0; i < p->count; i++)
    {
        gr_module_t *m = p->modules[i];
        v = m->process(m, pkt);
        if (v.action != GR_CONTINUE)   /* terminal: DROP/FORWARD/TO_HOST/CONSUMED */
        {
            gr_pipeline_stopped_at = i;
            break;
        }
    }
    gr_pipeline_unlock();
    return v;   /* CONTINUE through all -> caller runs base forwarding */
}

static gr_pipeline_t g_default;
static int g_default_inited = 0;

gr_pipeline_t *gr_default_pipeline(void)
{
    if (!g_default_inited)
    {
        gr_pipeline_init(&g_default);
        g_default_inited = 1;
    }
    return &g_default;
}
