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
