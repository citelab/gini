/*
 * gr_cp_igmp.c  —  IGMP snooping as a control-plane module (B3).
 *
 * Watches IGMP (IP protocol 2) membership messages and keeps the multicast membership table
 * (gr_mcast) up to date, so the data-plane multicast forwarder in ip.c knows which interfaces
 * have members of which group. Handles IGMPv2 reports/leaves and the IGMPv3 membership reports
 * that modern Linux hosts send when an application joins a group. No timers, no sending — it
 * only listens and records.
 *
 *   gpipe cp add igmp
 *
 * (For deterministic tests without real hosts, `gpipe mcast join <group> <iface>` writes the
 * same table directly.)
 */
#include "gr_control_plane.h"
#include "gr_mcast.h"
#include <stdlib.h>
#include <string.h>

typedef struct { const gr_cp_services_t *svc; long n; } igmp_state_t;

static int igmp_start(gr_cp_module_t *self, const gr_cp_services_t *svc, const char *args)
{
    igmp_state_t *s = (igmp_state_t *)calloc(1, sizeof(igmp_state_t));
    (void)args;
    if (!s) return -1;
    s->svc = svc;
    self->state = s;
    self->filter.proto = 2;        /* IGMP */
    svc->log("igmp: snooping membership reports");
    return 0;
}

static void rec(igmp_state_t *s, int join, uchar *grp, int iface)
{
    if (join) gr_mcast_join(grp, iface); else gr_mcast_leave(grp, iface);
    s->n++;
    s->svc->log("igmp: %s %u.%u.%u.%u on if%d",
                join ? "join" : "leave", grp[0], grp[1], grp[2], grp[3], iface);
}

static void igmp_on_packet(gr_cp_module_t *self, gpacket_t *pkt)
{
    igmp_state_t *s = (igmp_state_t *)self->state;
    unsigned char *d = (unsigned char *)pkt->data.data;
    int ihl = (d[0] & 0x0f) * 4; if (ihl < 20) ihl = 20;
    int total = ((int)d[2] << 8) | d[3];
    unsigned char *g = d + ihl;            /* IGMP message */
    int glen = total - ihl, iface = pkt->frame.src_interface;

    if (glen < 8) return;
    switch (g[0])
    {
    case 0x12:                              /* v1 membership report  -> join */
    case 0x16:                              /* v2 membership report  -> join */
        rec(s, 1, g + 4, iface); break;
    case 0x17:                              /* v2 leave group        -> leave */
        rec(s, 0, g + 4, iface); break;
    case 0x22:                              /* v3 membership report (group records) */
    {
        int nrec = ((int)g[6] << 8) | g[7], p = 8, k;
        for (k = 0; k < nrec && p + 8 <= glen; k++)
        {
            int rtype = g[p], auxw = g[p + 1];
            int nsrc = ((int)g[p + 2] << 8) | g[p + 3];
            uchar *grp = g + p + 4;
            /* INCLUDE with no sources = leave; EXCLUDE (or anything else) = join */
            if ((rtype == 1 || rtype == 3) && nsrc == 0) rec(s, 0, grp, iface);
            else rec(s, 1, grp, iface);
            p += 8 + nsrc * 4 + auxw * 4;
        }
        break;
    }
    default: break;                          /* queries etc.: ignore */
    }
}

static void igmp_stop(gr_cp_module_t *self)
{
    if (self->state) { free(self->state); self->state = NULL; }
}

gr_cp_module_t *gr_cp_igmp_create(void)
{
    gr_cp_module_t *m = (gr_cp_module_t *)calloc(1, sizeof(gr_cp_module_t));
    if (!m) return NULL;
    m->name = "igmp";
    m->start = igmp_start;
    m->on_packet = igmp_on_packet;
    m->stop = igmp_stop;
    return m;
}
