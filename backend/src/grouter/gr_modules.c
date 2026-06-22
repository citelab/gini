/*
 * gr_modules.c  —  Z2 built-in inline modules conforming to the gr_module_t ABI.
 *
 * For the demo/test these read the IPv4 destination directly from the packet (the IP
 * header begins at data.data[0]; dst is at offset 16). Production modules use the parsed
 * ip_packet_t metadata; the ABI and the runner are identical either way.
 */
#include "gr_modules.h"
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>

/* ---- ACL / firewall -------------------------------------------------------- */
typedef struct { uint32_t net, mask; } acl_state;

static uint32_t pkt_dst(gpacket_t *pkt)
{
    const unsigned char *d = (const unsigned char *)pkt->data.data;
    return ((uint32_t)d[16] << 24) | ((uint32_t)d[17] << 16) |
           ((uint32_t)d[18] << 8)  |  (uint32_t)d[19];
}

static gr_verdict_t acl_process(gr_module_t *self, gpacket_t *pkt)
{
    acl_state *s = (acl_state *)self->state;
    gr_verdict_t v = { GR_CONTINUE, -1 };
    if ((pkt_dst(pkt) & s->mask) == s->net)
        v.action = GR_DROP;
    return v;
}

static void acl_destroy(gr_module_t *self)
{
    free(self->state);
    free(self);
}

gr_module_t *gr_mod_acl(const char *deny_cidr)
{
    int a = 0, b = 0, c = 0, e = 0, p = 32;
    sscanf(deny_cidr, "%d.%d.%d.%d/%d", &a, &b, &c, &e, &p);
    acl_state *s = (acl_state *)malloc(sizeof(acl_state));
    s->mask = (p >= 32) ? 0xffffffffu : (p <= 0 ? 0u : ~((1u << (32 - p)) - 1u));
    s->net = (((uint32_t)a << 24) | ((uint32_t)b << 16) |
              ((uint32_t)c << 8) | (uint32_t)e) & s->mask;
    gr_module_t *m = (gr_module_t *)malloc(sizeof(gr_module_t));
    m->type = "acl"; m->state = s; m->init = 0;
    m->process = acl_process; m->destroy = acl_destroy;
    return m;
}

/* ---- NAT (source rewrite) -------------------------------------------------- */
typedef struct { unsigned char ip[4]; } nat_state;

static unsigned short ip_checksum(const unsigned char *h, int len)
{
    unsigned long sum = 0;
    int i;
    for (i = 0; i + 1 < len; i += 2)
        sum += ((unsigned long)h[i] << 8) | h[i + 1];
    if (i < len)
        sum += (unsigned long)h[i] << 8;
    while (sum >> 16)
        sum = (sum & 0xffff) + (sum >> 16);
    return (unsigned short)(~sum);
}

static gr_verdict_t nat_process(gr_module_t *self, gpacket_t *pkt)
{
    nat_state *s = (nat_state *)self->state;
    unsigned char *d = (unsigned char *)pkt->data.data;
    d[12] = s->ip[0]; d[13] = s->ip[1]; d[14] = s->ip[2]; d[15] = s->ip[3];  /* src IP */
    int ihl = (d[0] & 0x0f) * 4;
    if (ihl < 20) ihl = 20;
    d[10] = 0; d[11] = 0;                       /* recompute IP header checksum */
    unsigned short c = ip_checksum(d, ihl);
    d[10] = (unsigned char)(c >> 8); d[11] = (unsigned char)(c & 0xff);
    gr_verdict_t v = { GR_CONTINUE, -1 };       /* (L4 checksum fixup: TODO) */
    return v;
}

static void nat_destroy(gr_module_t *self) { free(self->state); free(self); }

gr_module_t *gr_mod_nat(const char *snat_ip)
{
    int a = 0, b = 0, c = 0, e = 0;
    sscanf(snat_ip, "%d.%d.%d.%d", &a, &b, &c, &e);
    nat_state *s = (nat_state *)malloc(sizeof(nat_state));
    s->ip[0] = (unsigned char)a; s->ip[1] = (unsigned char)b;
    s->ip[2] = (unsigned char)c; s->ip[3] = (unsigned char)e;
    gr_module_t *m = (gr_module_t *)malloc(sizeof(gr_module_t));
    m->type = "nat"; m->state = s; m->init = 0;
    m->process = nat_process; m->destroy = nat_destroy;
    return m;
}

/* ---- counter / tap --------------------------------------------------------- */
typedef struct { long n; } cnt_state;

static gr_verdict_t cnt_process(gr_module_t *self, gpacket_t *pkt)
{
    (void)pkt;
    ((cnt_state *)self->state)->n++;
    gr_verdict_t v = { GR_CONTINUE, -1 };
    return v;
}

static void cnt_destroy(gr_module_t *self)
{
    free(self->state);
    free(self);
}

gr_module_t *gr_mod_counter(void)
{
    cnt_state *s = (cnt_state *)malloc(sizeof(cnt_state));
    s->n = 0;
    gr_module_t *m = (gr_module_t *)malloc(sizeof(gr_module_t));
    m->type = "counter"; m->state = s; m->init = 0;
    m->process = cnt_process; m->destroy = cnt_destroy;
    return m;
}

long gr_mod_counter_value(gr_module_t *m)
{
    return ((cnt_state *)m->state)->n;
}

/* ---- shared packet accessors ---------------------------------------------- *
 * The IP datagram begins at pkt->data.data[0] (the Ethernet header is the separate
 * pkt->data.header). These give Lua scripts and native modules a clean, language-neutral
 * view of a packet without each one re-deriving byte offsets.                  */
uint32_t gr_pkt_ipsrc(gpacket_t *pkt)
{
    const unsigned char *d = (const unsigned char *)pkt->data.data;
    return ((uint32_t)d[12] << 24) | ((uint32_t)d[13] << 16) |
           ((uint32_t)d[14] << 8)  |  (uint32_t)d[15];
}
uint32_t gr_pkt_ipdst(gpacket_t *pkt)
{
    const unsigned char *d = (const unsigned char *)pkt->data.data;
    return ((uint32_t)d[16] << 24) | ((uint32_t)d[17] << 16) |
           ((uint32_t)d[18] << 8)  |  (uint32_t)d[19];
}
int gr_pkt_proto(gpacket_t *pkt) { return ((const unsigned char *)pkt->data.data)[9]; }
int gr_pkt_ttl(gpacket_t *pkt)   { return ((const unsigned char *)pkt->data.data)[8]; }
int gr_pkt_len(gpacket_t *pkt)
{
    const unsigned char *d = (const unsigned char *)pkt->data.data;
    return ((int)d[2] << 8) | d[3];
}

/* parse "a.b.c.d" into a host-order uint32; 0 on success, -1 on error. */
int gr_parse_ipv4(const char *s, uint32_t *out)
{
    int a = 0, b = 0, c = 0, e = 0;
    if (!s || sscanf(s, "%d.%d.%d.%d", &a, &b, &c, &e) != 4)
        return -1;
    *out = ((uint32_t)a << 24) | ((uint32_t)b << 16) | ((uint32_t)c << 8) | (uint32_t)e;
    return 0;
}

/* ---- native module registry ----------------------------------------------- *
 * Maps a name to a constructor so `gpipe add <name> [arg]` can build any registered
 * module -- a built-in, or a student-written native module (see gr_mod_block.zig).
 * Adding a native module = write it, then add one line here.                    */
extern gr_module_t *gr_mod_block(const char *ip);   /* native example, written in Zig */

static gr_module_t *ctor_counter(const char *a) { (void)a; return gr_mod_counter(); }
#ifdef GR_LEGACY_MODULES
static gr_module_t *ctor_filter(const char *a)  { (void)a; return gr_mod_filter(); }
#endif

typedef struct {
    const char *name;
    gr_module_t *(*ctor)(const char *arg);
    int needs_arg;
} gr_modreg_t;

static const gr_modreg_t REGISTRY[] = {
    { "acl",     gr_mod_acl,   1 },
    { "nat",     gr_mod_nat,   1 },
    { "counter", ctor_counter, 0 },
#ifdef GR_LEGACY_MODULES
    { "filter",  ctor_filter,  0 },
#endif
    { "block",   gr_mod_block, 1 },   /* native (Zig): drop by destination IP */
};

static const gr_modreg_t *reg_find(const char *name)
{
    unsigned i;
    if (name)
        for (i = 0; i < sizeof(REGISTRY) / sizeof(REGISTRY[0]); i++)
            if (strcmp(name, REGISTRY[i].name) == 0)
                return &REGISTRY[i];
    return 0;
}

gr_module_t *gr_module_create(const char *name, const char *arg)
{
    const gr_modreg_t *r = reg_find(name);
    return r ? r->ctor(arg) : 0;
}

/* 1 if <name> requires an argument, 0 if not, -1 if the name is unknown. */
int gr_module_needs_arg(const char *name)
{
    const gr_modreg_t *r = reg_find(name);
    return r ? r->needs_arg : -1;
}

/* space-separated list of registered module names (for usage messages). */
const char *gr_module_names(void)
{
    static char buf[160];
    unsigned i; int n = 0;
    buf[0] = '\0';
    for (i = 0; i < sizeof(REGISTRY) / sizeof(REGISTRY[0]); i++)
        n += snprintf(buf + n, sizeof(buf) - n, "%s%s", i ? " " : "", REGISTRY[i].name);
    return buf;
}
