/*
 * gr_cp_rip.c  —  a distance-vector routing protocol as a control-plane module (B2.4).
 *
 * A small RIP-style protocol: every router periodically advertises its whole route table to
 * its neighbours (UDP/520, broadcast), and on hearing a neighbour's table it runs the
 * Bellman-Ford step (cost = advertised metric + 1) and installs the better routes with
 * svc->route_add. Learned routes age out if a neighbour goes quiet. Connected networks (the
 * router's own interfaces) are seeded at metric 1 and advertised but never installed or aged.
 *
 * All state lives on the single control thread, so there are no locks. RIP infinity = 16.
 *
 *   gpipe cp add rip            # advertise every 10s, age learned routes after ~60s
 *   gpipe cp add rip 15         # advertise every 15s
 */
#include "gr_control_plane.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define RIP_INFINITY  16
#define MAX_RIP_ROUTES 64
#define MAX_IFACE      16
#define RIP_PORT       520

typedef struct {
    uchar net[4], mask[4], nexthop[4];
    int   metric, iface, connected, ttl;
    int   used;
} rip_route_t;

typedef struct {
    const gr_cp_services_t *svc;
    rip_route_t routes[MAX_RIP_ROUTES];
    int   adv_s;            /* advertise period (seconds) */
    long  adv_count;
} rip_state_t;

static const uchar BCAST_MAC[6] = {0xff,0xff,0xff,0xff,0xff,0xff};
static const uchar MASK24[4]    = {255,255,255,0};

static rip_route_t *find_route(rip_state_t *s, const uchar *net, const uchar *mask)
{
    int i;
    for (i = 0; i < MAX_RIP_ROUTES; i++)
        if (s->routes[i].used &&
            memcmp(s->routes[i].net, net, 4) == 0 && memcmp(s->routes[i].mask, mask, 4) == 0)
            return &s->routes[i];
    return NULL;
}

static rip_route_t *alloc_route(rip_state_t *s)
{
    int i;
    for (i = 0; i < MAX_RIP_ROUTES; i++)
        if (!s->routes[i].used) { memset(&s->routes[i], 0, sizeof(rip_route_t));
                                  s->routes[i].used = 1; return &s->routes[i]; }
    return NULL;
}

/* seed the directly-connected networks (interface IP & /24) at metric 1 */
static void seed_connected(rip_state_t *s)
{
    int i;
    for (i = 0; i < MAX_IFACE; i++)
    {
        uchar ip[4], net[4];
        if (s->svc->iface_addr(i, ip) != 0) continue;
        net[0]=ip[0]; net[1]=ip[1]; net[2]=ip[2]; net[3]=0;
        if (!find_route(s, net, MASK24))
        {
            rip_route_t *r = alloc_route(s);
            if (!r) break;
            memcpy(r->net, net, 4); memcpy(r->mask, MASK24, 4);
            r->metric = 1; r->iface = i; r->connected = 1; r->ttl = 0;
        }
    }
}

/* build a RIPv2 response listing every known route; returns length */
static int build_response(rip_state_t *s, unsigned char *buf, int cap)
{
    int p = 0, i;
    buf[p++] = 2; buf[p++] = 2; buf[p++] = 0; buf[p++] = 0;     /* command=response, version=2 */
    for (i = 0; i < MAX_RIP_ROUTES && p + 20 <= cap; i++)
    {
        rip_route_t *r = &s->routes[i];
        int m;
        if (!r->used) continue;
        buf[p++]=0; buf[p++]=2;          /* AFI = IP */
        buf[p++]=0; buf[p++]=0;          /* route tag */
        memcpy(buf+p, r->net, 4); p+=4;
        memcpy(buf+p, r->mask,4); p+=4;
        buf[p++]=0;buf[p++]=0;buf[p++]=0;buf[p++]=0;            /* next hop 0.0.0.0 */
        m = r->metric;
        buf[p++]=(m>>24)&0xff; buf[p++]=(m>>16)&0xff; buf[p++]=(m>>8)&0xff; buf[p++]=m&0xff;
    }
    return p;
}

static void rip_advertise(gr_cp_module_t *self, void *arg)
{
    rip_state_t *s = (rip_state_t *)self->state;
    unsigned char buf[512];
    int len, i;
    uchar bcast_ip[4] = {255,255,255,255};
    (void)arg;
    len = build_response(s, buf, sizeof buf);
    for (i = 0; i < MAX_IFACE; i++)             /* advertise out every interface */
    {
        uchar ip[4];
        if (s->svc->iface_addr(i, ip) != 0) continue;
        s->svc->send_udp(i, BCAST_MAC, ip, bcast_ip, RIP_PORT, RIP_PORT, buf, len);
    }
    s->adv_count++;
}

static void rip_age(gr_cp_module_t *self, void *arg)
{
    rip_state_t *s = (rip_state_t *)self->state;
    int i;
    (void)arg;
    for (i = 0; i < MAX_RIP_ROUTES; i++)
    {
        rip_route_t *r = &s->routes[i];
        if (!r->used || r->connected) continue;
        if ((r->ttl -= s->adv_s) <= 0)
        {
            s->svc->route_del(r->net, r->mask);
            s->svc->log("rip: route %u.%u.%u.0/24 expired",
                        r->net[0], r->net[1], r->net[2]);
            r->used = 0;
        }
    }
}

static int rip_start(gr_cp_module_t *self, const gr_cp_services_t *svc, const char *args)
{
    rip_state_t *s = (rip_state_t *)calloc(1, sizeof(rip_state_t));
    int adv = 10;
    if (!s) return -1;
    if (args && *args) sscanf(args, "%d", &adv);
    s->svc = svc;
    s->adv_s = adv > 0 ? adv : 10;
    self->state = s;
    self->filter.proto = 17;            /* UDP */
    self->filter.udp_dport = RIP_PORT;
    seed_connected(s);
    svc->timer_add(self, s->adv_s * 1000, rip_advertise, NULL);
    svc->timer_add(self, s->adv_s * 1000, rip_age, NULL);
    svc->log("rip: started, advertising every %ds", s->adv_s);
    return 0;
}

static void rip_on_packet(gr_cp_module_t *self, gpacket_t *pkt)
{
    rip_state_t *s = (rip_state_t *)self->state;
    const unsigned char *d = (const unsigned char *)pkt->data.data;
    int ihl = (d[0] & 0x0f) * 4; if (ihl < 20) ihl = 20;
    int total = ((int)d[2] << 8) | d[3];
    const unsigned char *rp = d + ihl + 8;          /* RIP message */
    int riplen = total - ihl - 8, i, n;
    uchar nbr[4];                                   /* neighbour = IP source = next hop */
    int iface = pkt->frame.src_interface;

    if (riplen < 4 || rp[0] != 2) return;           /* only responses */
    memcpy(nbr, d + 12, 4);                          /* IP source address */
    n = (riplen - 4) / 20;

    for (i = 0; i < n; i++)
    {
        const unsigned char *e = rp + 4 + i * 20;
        uchar net[4], mask[4];
        int adv = ((int)e[16]<<24)|((int)e[17]<<16)|((int)e[18]<<8)|e[19];
        int cost = adv + 1; if (cost > RIP_INFINITY) cost = RIP_INFINITY;
        rip_route_t *r;
        memcpy(net, e + 4, 4); memcpy(mask, e + 8, 4);

        r = find_route(s, net, mask);
        if (r && r->connected) continue;            /* never override a connected net */

        if (!r)
        {
            if (cost >= RIP_INFINITY) continue;     /* don't add unreachable */
            r = alloc_route(s);
            if (!r) return;
            memcpy(r->net, net, 4); memcpy(r->mask, mask, 4);
            memcpy(r->nexthop, nbr, 4);
            r->metric = cost; r->iface = iface; r->ttl = s->adv_s * 6;
            s->svc->route_add(net, mask, nbr, iface);
            s->svc->log("rip: learned %u.%u.%u.0/24 via %u.%u.%u.%u metric %d",
                        net[0],net[1],net[2], nbr[0],nbr[1],nbr[2],nbr[3], cost);
        }
        else if (memcmp(r->nexthop, nbr, 4) == 0)   /* update from current next hop */
        {
            r->ttl = s->adv_s * 6;
            if (cost != r->metric)
            {
                r->metric = cost;
                if (cost >= RIP_INFINITY) { s->svc->route_del(net, mask); r->used = 0; }
                else s->svc->route_add(net, mask, nbr, iface);
            }
        }
        else if (cost < r->metric)                  /* a better path appeared */
        {
            memcpy(r->nexthop, nbr, 4);
            r->metric = cost; r->iface = iface; r->ttl = s->adv_s * 6;
            s->svc->route_add(net, mask, nbr, iface);
            s->svc->log("rip: better path to %u.%u.%u.0/24 via %u.%u.%u.%u metric %d",
                        net[0],net[1],net[2], nbr[0],nbr[1],nbr[2],nbr[3], cost);
        }
    }
}

static void rip_stop(gr_cp_module_t *self)
{
    if (self->state) { free(self->state); self->state = NULL; }
}

gr_cp_module_t *gr_cp_rip_create(void)
{
    gr_cp_module_t *m = (gr_cp_module_t *)calloc(1, sizeof(gr_cp_module_t));
    if (!m) return NULL;
    m->name = "rip";
    m->start = rip_start;
    m->on_packet = rip_on_packet;
    m->stop = rip_stop;
    return m;
}
