/*
 * gr_delay_ctl.c  —  owns the two runtime delay lines and their (re)configuration.
 * See gr_delay_ctl.h. The emit callbacks live in packetcore.c (ingress) and gnet.c (egress).
 */
#include <stdio.h>
#include <string.h>

#include "gr_delay_ctl.h"

gr_delayline_t *gr_ingress_line = NULL;
gr_delayline_t *gr_egress_line  = NULL;

void gr_delay_set(int egress, double base_ms, double jitter_ms, double corr, int limit)
{
    gr_delayline_t **slot = egress ? &gr_egress_line : &gr_ingress_line;
    gr_delay_emit_fn emit = egress ? gr_egress_emit : gr_ingress_emit;

    /* zero delay and zero jitter == off */
    if (base_ms <= 0.0 && jitter_ms <= 0.0)
    {
        gr_delay_off(egress);
        return;
    }
    if (*slot)
        gr_delay_config(*slot, base_ms, jitter_ms, corr, limit);
    else
        *slot = gr_delay_create(emit, base_ms, jitter_ms, corr, limit);
}

void gr_delay_off(int egress)
{
    gr_delayline_t **slot = egress ? &gr_egress_line : &gr_ingress_line;
    gr_delayline_t  *old  = *slot;
    *slot = NULL;              /* fast path bypasses first ... */
    if (old) gr_delay_destroy(old);   /* ... then tear the line down */
}

static int describe_one(char *buf, int n, const char *name, gr_delayline_t *dl)
{
    if (dl == NULL)
        return snprintf(buf, n, "  %-7s: off\n", name);
    return snprintf(buf, n,
        "  %-7s: base %.1f ms  jitter %.1f ms  corr %.2f  limit %d  "
        "(held %ld, passed %ld, dropped %ld)\n",
        name, gr_delay_base_ms(dl), gr_delay_jitter_ms(dl), gr_delay_corr(dl),
        gr_delay_limit(dl), gr_delay_held(dl), gr_delay_passed(dl), gr_delay_dropped(dl));
}

int gr_delay_describe(char *buf, int n)
{
    int k = snprintf(buf, n, "link delay:\n");
    if (k < 0 || k >= n) return k;
    k += describe_one(buf + k, n - k, "ingress", gr_ingress_line);
    if (k < n) k += describe_one(buf + k, n - k, "egress", gr_egress_line);
    return k;
}
