/*
 * gr_mod_rate.c — a native gRouter pipeline module (C): a token-bucket policer.
 *
 * Meters the packet rate through this stage and DROPs packets that arrive faster than the
 * configured rate, letting the rest CONTINUE. This is a *policer* (drop over-rate), not a
 * shaper: the gr_module_t verdict ABI is CONTINUE/DROP with no way to queue or delay a
 * packet, so we meter-and-drop rather than delay-and-smooth. (A true shaper would need a
 * queue/defer verdict and an egress scheduler — a later extension.)
 *
 * Config:  gpipe add rate <pps>[/<burst>]     e.g.  add rate 100   or   add rate 100/200
 *   pps   = sustained packets per second (the token refill rate).
 *   burst = bucket depth in packets (default = pps, i.e. one second's worth) — how large a
 *           momentary burst may pass before the bucket empties.
 *
 * Classic token bucket: the bucket refills at <pps> tokens/second up to <burst>; each packet
 * spends one token; a packet with no token to spend is dropped. Conforms to the gr_module_t
 * ABI (gr_module.h) and reads no packet fields — it polices on arrival time alone.
 */
#include <stdlib.h>
#include <stdio.h>
#include <time.h>

#include "gr_module.h"
#include "gr_modules.h"

typedef struct
{
    double          rate;       /* tokens (packets) per second        */
    double          burst;      /* bucket capacity, in tokens         */
    double          tokens;     /* tokens currently in the bucket     */
    long            drops;      /* packets dropped so far (stats)     */
    struct timespec last;       /* time of the previous packet        */
    int             primed;     /* 0 until the first packet is seen   */
} rate_state;

static gr_verdict_t rate_process(gr_module_t *self, gpacket_t *pkt)
{
    (void)pkt;                                  /* rate only — packet contents are irrelevant */
    rate_state *s = (rate_state *)self->state;
    gr_verdict_t v = { GR_CONTINUE, -1 };
    struct timespec t;

    clock_gettime(CLOCK_MONOTONIC, &t);
    if (s->primed)
    {
        double dt = (double)(t.tv_sec - s->last.tv_sec)
                  + (double)(t.tv_nsec - s->last.tv_nsec) / 1e9;
        if (dt > 0)
        {
            s->tokens += dt * s->rate;          /* refill */
            if (s->tokens > s->burst) s->tokens = s->burst;
        }
    }
    else
    {
        s->tokens = s->burst;                   /* start full: allow one initial burst */
        s->primed = 1;
    }
    s->last = t;

    if (s->tokens >= 1.0)
    {
        s->tokens -= 1.0;                        /* spend a token, let it through */
        v.action = GR_CONTINUE;
    }
    else
    {
        s->drops++;                              /* over rate: policed out */
        v.action = GR_DROP;
    }
    return v;
}

static void rate_destroy(gr_module_t *self)
{
    free(self->state);
    free(self);
}

/* gr_mod_rate(spec): the constructor the registry calls (`gpipe add rate <pps>[/<burst>]`). */
gr_module_t *gr_mod_rate(const char *spec)
{
    double pps = 0, burst = 0;
    if (spec) sscanf(spec, "%lf/%lf", &pps, &burst);   /* "pps" or "pps/burst" */
    if (pps <= 0)   pps = 1;                            /* sane floors on a bad arg */
    if (burst <= 0) burst = pps;                        /* default burst = 1s of packets */

    rate_state *s = (rate_state *)malloc(sizeof(rate_state));
    if (!s) return 0;
    s->rate = pps; s->burst = burst; s->tokens = burst;
    s->drops = 0;  s->primed = 0;

    gr_module_t *m = (gr_module_t *)malloc(sizeof(gr_module_t));
    if (!m) { free(s); return 0; }
    m->type = "rate"; m->state = s; m->init = 0;
    m->process = rate_process; m->destroy = rate_destroy;
    return m;
}

/* Packets dropped by this policer so far (for stats/inspection). */
long gr_mod_rate_drops(gr_module_t *m)
{
    return ((rate_state *)m->state)->drops;
}
