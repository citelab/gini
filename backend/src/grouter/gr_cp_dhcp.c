/*
 * gr_cp_dhcp.c  —  a DHCP server as a control-plane module (B2.4).
 *
 * Filters UDP/67, parses DISCOVER/REQUEST, allocates an address from a pool kept in module
 * state, and replies with a broadcast OFFER/ACK built with the BOOTP + DHCP option format.
 * The reply goes out the interface the request arrived on, via svc->send_udp (which builds
 * IP+UDP + checksums). A timer ages leases. All state is touched only on the single control
 * thread, so there are no locks here — the model doing its job.
 *
 *   gpipe cp add dhcp                          # serve 192.168.1.100.. on the request's subnet
 *   gpipe cp add dhcp 10.0.0.50 20 600         # pool base 10.0.0.50, 20 addrs, 600s lease
 */
#include "gr_control_plane.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define DHCP_DISCOVER 1
#define DHCP_REQUEST  3
#define DHCP_OFFER    2
#define DHCP_ACK      5
#define MAX_LEASES    64

typedef struct {
    uchar mac[6];
    uchar ip[4];
    int   used;
    long  ttl;          /* seconds remaining */
} lease_t;

typedef struct {
    const gr_cp_services_t *svc;
    uchar base[4];      /* first address in the pool */
    int   count;        /* pool size */
    int   lease_s;      /* lease seconds */
    lease_t leases[MAX_LEASES];
    int    auto_base;   /* if set, derive the pool from the request's subnet */
    long   served;
} dhcp_state_t;

static const uchar BCAST_MAC[6] = {0xff,0xff,0xff,0xff,0xff,0xff};
static const uchar COOKIE[4]    = {99,130,83,99};

/* find or allocate a lease for this client MAC; returns the lease or NULL if pool full */
static lease_t *lease_for(dhcp_state_t *s, const uchar *mac)
{
    int i, free_i = -1;
    for (i = 0; i < s->count && i < MAX_LEASES; i++)
    {
        if (s->leases[i].used && memcmp(s->leases[i].mac, mac, 6) == 0)
            return &s->leases[i];
        if (!s->leases[i].used && free_i < 0) free_i = i;
    }
    if (free_i < 0) return NULL;
    s->leases[free_i].used = 1;
    memcpy(s->leases[free_i].mac, mac, 6);
    memcpy(s->leases[free_i].ip, s->base, 4);
    s->leases[free_i].ip[3] = (uchar)(s->base[3] + free_i);   /* base + index (same /24) */
    return &s->leases[free_i];
}

/* append a DHCP option (code,len,data) to buf at *p */
static void opt(unsigned char *buf, int *p, int code, int len, const void *data)
{
    buf[(*p)++] = (unsigned char)code;
    buf[(*p)++] = (unsigned char)len;
    if (len) { memcpy(buf + *p, data, len); *p += len; }
}

static void dhcp_age(gr_cp_module_t *self, void *arg)
{
    dhcp_state_t *s = (dhcp_state_t *)self->state;
    int i;
    (void)arg;
    for (i = 0; i < s->count && i < MAX_LEASES; i++)
        if (s->leases[i].used && (s->leases[i].ttl -= 10) <= 0)
        {
            s->leases[i].used = 0;
            s->svc->log("dhcp: lease for %u.%u.%u.%u expired",
                        s->leases[i].ip[0], s->leases[i].ip[1],
                        s->leases[i].ip[2], s->leases[i].ip[3]);
        }
}

static int dhcp_start(gr_cp_module_t *self, const gr_cp_services_t *svc, const char *args)
{
    dhcp_state_t *s = (dhcp_state_t *)calloc(1, sizeof(dhcp_state_t));
    int a=192,b=168,c=1,d=100,count=50,lease=3600;
    if (!s) return -1;
    if (args && *args && sscanf(args, "%d.%d.%d.%d %d %d", &a,&b,&c,&d,&count,&lease) >= 4)
        s->auto_base = 0;
    else
        s->auto_base = 1;                       /* no base given -> derive from the subnet */
    s->base[0]=(uchar)a; s->base[1]=(uchar)b; s->base[2]=(uchar)c; s->base[3]=(uchar)d;
    s->count = (count > 0 && count < MAX_LEASES) ? count : 50;
    s->lease_s = lease > 0 ? lease : 3600;
    s->svc = svc;
    self->state = s;
    self->filter.proto = 17;                    /* UDP */
    self->filter.udp_dport = 67;                /* DHCP server port */
    svc->timer_add(self, 10000, dhcp_age, NULL);
    svc->log("dhcp: started, pool base %u.%u.%u.%u count %d lease %ds%s",
             s->base[0],s->base[1],s->base[2],s->base[3], s->count, s->lease_s,
             s->auto_base ? " (base auto from subnet)" : "");
    return 0;
}

static void dhcp_on_packet(gr_cp_module_t *self, gpacket_t *pkt)
{
    dhcp_state_t *s = (dhcp_state_t *)self->state;
    const unsigned char *d = (const unsigned char *)pkt->data.data;
    int ihl = (d[0] & 0x0f) * 4; if (ihl < 20) ihl = 20;
    int total = ((int)d[2] << 8) | d[3];
    const unsigned char *dh = d + ihl + 8;          /* DHCP message (after IP+UDP) */
    int dhlen = total - ihl - 8;
    int i, msgtype = 0;
    uchar climac[6], server_ip[4], mask[4] = {255,255,255,0};
    unsigned char reply[512]; int p;
    lease_t *ls;

    if (dhlen < 240 || memcmp(dh + 236, COOKIE, 4) != 0) return;   /* not DHCP */

    memcpy(climac, dh + 28, 6);                      /* chaddr = client MAC */

    /* walk options for the message type (53) */
    for (i = 240; i < dhlen; )
    {
        int code = dh[i++];
        if (code == 0) continue;                      /* pad */
        if (code == 255) break;                       /* end */
        if (i >= dhlen) break;
        { int olen = dh[i++];
          if (code == 53 && olen >= 1) msgtype = dh[i];
          i += olen; }
    }
    if (msgtype != DHCP_DISCOVER && msgtype != DHCP_REQUEST) return;

    /* server identity = our address on the interface the request came in on */
    if (s->svc->iface_addr(pkt->frame.src_interface, server_ip) != 0)
        return;                                       /* no address there: cannot serve */

    ls = lease_for(s, climac);
    if (!ls) { s->svc->log("dhcp: pool exhausted, ignoring request"); return; }
    ls->ttl = s->lease_s;
    if (s->auto_base)                                  /* keep pool host part, adopt the subnet */
    { ls->ip[0]=server_ip[0]; ls->ip[1]=server_ip[1]; ls->ip[2]=server_ip[2]; }

    /* ---- build the BOOTP/DHCP reply ---- */
    memset(reply, 0, sizeof reply);
    reply[0]=2; reply[1]=1; reply[2]=6; reply[3]=0;    /* op=REPLY htype=1 hlen=6 hops=0 */
    memcpy(reply + 4, dh + 4, 4);                       /* xid */
    memcpy(reply + 10, dh + 10, 2);                     /* flags (broadcast bit) */
    memcpy(reply + 16, ls->ip, 4);                      /* yiaddr = offered address */
    memcpy(reply + 20, server_ip, 4);                  /* siaddr = server */
    memcpy(reply + 28, climac, 6);                      /* chaddr */
    memcpy(reply + 236, COOKIE, 4);
    p = 240;
    { unsigned char t = (msgtype == DHCP_DISCOVER) ? DHCP_OFFER : DHCP_ACK;
      unsigned char lease_be[4] = { (unsigned char)(s->lease_s>>24),(unsigned char)(s->lease_s>>16),
                                    (unsigned char)(s->lease_s>>8),(unsigned char)s->lease_s };
      opt(reply,&p,53,1,&t);                            /* message type */
      opt(reply,&p,54,4,server_ip);                     /* server identifier */
      opt(reply,&p,51,4,lease_be);                      /* lease time */
      opt(reply,&p,1,4,mask);                           /* subnet mask */
      opt(reply,&p,3,4,server_ip);                      /* router = us */
      opt(reply,&p,6,4,server_ip);                      /* DNS = us (placeholder) */
      reply[p++] = 255; }                               /* end */

    /* broadcast the reply out the request's interface */
    { uchar bcast_ip[4] = {255,255,255,255};
      s->svc->send_udp(pkt->frame.src_interface, BCAST_MAC, server_ip, bcast_ip, 67, 68, reply, p);
    }
    s->served++;
    s->svc->log("dhcp: %s -> %u.%u.%u.%u for %02x:%02x:%02x:%02x:%02x:%02x",
                msgtype == DHCP_DISCOVER ? "OFFER" : "ACK",
                ls->ip[0],ls->ip[1],ls->ip[2],ls->ip[3],
                climac[0],climac[1],climac[2],climac[3],climac[4],climac[5]);
}

static void dhcp_stop(gr_cp_module_t *self)
{
    if (self->state) { free(self->state); self->state = NULL; }
}

gr_cp_module_t *gr_cp_dhcp_create(void)
{
    gr_cp_module_t *m = (gr_cp_module_t *)calloc(1, sizeof(gr_cp_module_t));
    if (!m) return NULL;
    m->name = "dhcp";
    m->start = dhcp_start;
    m->on_packet = dhcp_on_packet;
    m->stop = dhcp_stop;
    return m;
}
