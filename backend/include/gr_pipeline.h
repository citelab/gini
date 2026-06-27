/*
 * gr_pipeline.h  —  Z2 module-graph runner.
 *
 * An ordered series of gr_module_t (the "drop onto the base" inline modules). A packet
 * flows through them; each returns a verdict. CONTINUE passes to the next module; any
 * other verdict (DROP/FORWARD/TO_HOST/CONSUMED) is terminal. If the packet runs the whole
 * pipeline as CONTINUE, the caller falls through to the base forwarding (route -> rewrite).
 *
 * This is what makes the Router Lab's drag-and-drop real: the editor's ordered module
 * list maps 1:1 onto a gr_pipeline, and the step debugger walks gr_pipeline_run().
 */
#ifndef __GR_PIPELINE_H__
#define __GR_PIPELINE_H__

#include "gr_module.h"

#define GR_MAX_MODULES 32

typedef struct
{
    gr_module_t *modules[GR_MAX_MODULES];
    int          count;
} gr_pipeline_t;

void         gr_pipeline_init(gr_pipeline_t *p);
int          gr_pipeline_add(gr_pipeline_t *p, gr_module_t *m);   /* index, or -1 if full */
void         gr_pipeline_clear(gr_pipeline_t *p);                 /* destroy all modules   */
gr_verdict_t gr_pipeline_run(gr_pipeline_t *p, gpacket_t *pkt);

/* The process-wide pipeline (one router = one process). The forwarding path runs it;
 * the CLI / control protocol edits it. Lazily initialized, empty by default. */
gr_pipeline_t *gr_default_pipeline(void);

#endif /* __GR_PIPELINE_H__ */
