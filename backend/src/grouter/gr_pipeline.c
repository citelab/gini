/*
 * gr_pipeline.c  —  Z2 module-graph runner. Pure gpacket_t + libc; no globals.
 */
#include "gr_pipeline.h"

void gr_pipeline_init(gr_pipeline_t *p)
{
    p->count = 0;
}

int gr_pipeline_add(gr_pipeline_t *p, gr_module_t *m)
{
    if (p->count >= GR_MAX_MODULES || m == 0)
        return -1;
    p->modules[p->count] = m;
    return p->count++;
}

void gr_pipeline_clear(gr_pipeline_t *p)
{
    int i;
    for (i = 0; i < p->count; i++)
        if (p->modules[i] && p->modules[i]->destroy)
            p->modules[i]->destroy(p->modules[i]);
    p->count = 0;
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
    for (i = 0; i < p->count; i++)
    {
        gr_module_t *m = p->modules[i];
        v = m->process(m, pkt);
        if (v.action != GR_CONTINUE)   /* terminal: DROP/FORWARD/TO_HOST/CONSUMED */
        {
            gr_pipeline_stopped_at = i;
            return v;
        }
    }
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
