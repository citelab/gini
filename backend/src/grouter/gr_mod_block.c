/*
 * gr_mod_block.c — a native gRouter pipeline module (C).
 *
 * DROPs packets whose IPv4 destination equals a configured address, and otherwise lets them
 * CONTINUE down the pipeline. The "hello, world" of a native module: the same job as the ACL
 * module, but as a self-contained example of writing a gr_module_t.
 *
 * (This was briefly gr_mod_block.zig during the Zig experiment; restored to C when Zig was
 * removed so the router is a single systems language — C — with Lua for student modules.)
 *
 * It conforms to the gr_module_t C ABI (include/gr_module.h), so the runner chains it exactly
 * like any other module. It reads the packet only through the gr_pkt_ipdst() accessor, so it
 * never has to touch the gpacket_t layout directly.
 */
#include <stdlib.h>
#include <stdint.h>

#include "gr_module.h"
#include "gr_modules.h"

typedef struct { uint32_t ip; } block_state;

static gr_verdict_t block_process(gr_module_t *self, gpacket_t *pkt)
{
    block_state *s = (block_state *)self->state;
    gr_verdict_t v = { GR_CONTINUE, -1 };
    if (gr_pkt_ipdst(pkt) == s->ip)
        v.action = GR_DROP;
    return v;
}

static void block_destroy(gr_module_t *self)
{
    free(self->state);
    free(self);
}

/* gr_mod_block(ip): the constructor the registry calls (`gpipe add block <ip>`). */
gr_module_t *gr_mod_block(const char *ip)
{
    uint32_t parsed = 0;
    gr_parse_ipv4(ip, &parsed);          /* 0.0.0.0 on a bad arg -> matches nothing */

    block_state *s = (block_state *)malloc(sizeof(block_state));
    if (!s) return 0;
    s->ip = parsed;

    gr_module_t *m = (gr_module_t *)malloc(sizeof(gr_module_t));
    if (!m) { free(s); return 0; }
    m->type = "block";
    m->state = s;
    m->init = 0;
    m->process = block_process;
    m->destroy = block_destroy;
    return m;
}
