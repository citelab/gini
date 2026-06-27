/*
 * gr_mod_legacy.c  —  Z2: the legacy firewall as a graph module.
 *
 * Wraps the existing filter.c (filteredPacket) so the router's built-in firewall
 * composes in the pipeline alongside ACL / NAT / Lua / native. Couples to the global
 * `filter` table, so it's built with the full router (CORE) and behind the legacy
 * control commands; the standalone runner test stays pure (acl/counter/nat).
 *
 * (Classifier/QoS is deliberately NOT a pipeline module — class tagging + scheduling
 * live in the packetcore scheduler, a separate concern from inline forwarding.)
 */
#include "gr_modules.h"
#include "filter.h"      /* filteredPacket, filtertab_t */
#include <stdlib.h>

extern filtertab_t *filter;   /* the router's global filter table (grouter.c) */

static gr_verdict_t filter_process(gr_module_t *self, gpacket_t *pkt)
{
    (void)self;
    gr_verdict_t v = { GR_CONTINUE, -1 };
    if (filter && filteredPacket(filter, pkt))
        v.action = GR_DROP;
    return v;
}

static void filter_destroy(gr_module_t *self) { free(self); }

gr_module_t *gr_mod_filter(void)
{
    gr_module_t *m = (gr_module_t *)malloc(sizeof(gr_module_t));
    m->type = "filter"; m->state = 0; m->init = 0;
    m->process = filter_process; m->destroy = filter_destroy;
    return m;
}
