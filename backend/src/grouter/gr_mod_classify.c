/*
 * gr_mod_classify.c — QoS classifier: a DSCP marker.
 *
 * Marks packets whose IPv4 destination matches a CIDR with a Differentiated Services Code
 * Point (DSCP) in the IP ToS byte, then CONTINUEs. This is a *marker*: it tags traffic into
 * a class. Giving those classes differentiated *treatment* (priority queues) is a separate
 * egress-scheduler step — a later extension; marking is the piece that belongs in the
 * per-packet pipeline.
 *
 * Config:  gpipe add classify <cidr>:<dscp>     e.g.  add classify 10.0.3.0/24:ef
 *          gpipe add classify <dscp>            (match all)
 *   dscp = a name (be, ef, cs0..cs7, af11..af43) or a number 0..63.
 *
 * Sets the top 6 bits of the ToS byte (data[1]) to the DSCP, preserves the low 2 ECN bits,
 * and recomputes the IPv4 header checksum. Conforms to the gr_module_t ABI (gr_module.h).
 */
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <stdint.h>

#include "gr_module.h"
#include "gr_modules.h"

typedef struct { uint32_t net, mask; int dscp; } cls_state;

/* named DSCP -> value; -1 if unknown. */
static int dscp_by_name(const char *s)
{
    static const struct { const char *n; int v; } T[] = {
        {"be",0},{"default",0},{"cs0",0},{"cs1",8},{"cs2",16},{"cs3",24},
        {"cs4",32},{"cs5",40},{"cs6",48},{"cs7",56},
        {"af11",10},{"af12",12},{"af13",14},{"af21",18},{"af22",20},{"af23",22},
        {"af31",26},{"af32",28},{"af33",30},{"af41",34},{"af42",36},{"af43",38},
        {"ef",46},
    };
    unsigned i;
    for (i = 0; i < sizeof(T)/sizeof(T[0]); i++)
        if (strcmp(s, T[i].n) == 0) return T[i].v;
    return -1;
}

static unsigned short ip_csum(const unsigned char *h, int len)
{
    unsigned long sum = 0; int i;
    for (i = 0; i + 1 < len; i += 2) sum += ((unsigned long)h[i] << 8) | h[i+1];
    if (i < len) sum += (unsigned long)h[i] << 8;
    while (sum >> 16) sum = (sum & 0xffff) + (sum >> 16);
    return (unsigned short)(~sum);
}

static gr_verdict_t cls_process(gr_module_t *self, gpacket_t *pkt)
{
    cls_state *s = (cls_state *)self->state;
    gr_verdict_t v = { GR_CONTINUE, -1 };
    unsigned char *d = (unsigned char *)pkt->data.data;
    if ((gr_pkt_ipdst(pkt) & s->mask) == s->net)
    {
        d[1] = (unsigned char)((s->dscp << 2) | (d[1] & 0x03));   /* set DSCP, keep ECN */
        int ihl = (d[0] & 0x0f) * 4; if (ihl < 20) ihl = 20;
        d[10] = 0; d[11] = 0;                                     /* recompute IP checksum */
        unsigned short c = ip_csum(d, ihl);
        d[10] = (unsigned char)(c >> 8); d[11] = (unsigned char)(c & 0xff);
    }
    return v;   /* always CONTINUE — a marker never drops */
}

static void cls_destroy(gr_module_t *self) { free(self->state); free(self); }

/* gr_mod_classify(spec): the constructor the registry calls (`gpipe add classify …`). */
gr_module_t *gr_mod_classify(const char *spec)
{
    cls_state *s = (cls_state *)malloc(sizeof(cls_state));
    if (!s) return 0;
    s->net = 0; s->mask = 0; s->dscp = 0;          /* default: match all, DSCP 0 (best effort) */

    if (spec && spec[0])
    {
        char buf[80]; strncpy(buf, spec, sizeof buf - 1); buf[sizeof buf - 1] = 0;
        char *colon = strchr(buf, ':');
        char *dtok;
        if (colon)
        {
            int a=0,b=0,c=0,e=0,p=32;
            *colon = 0;
            sscanf(buf, "%d.%d.%d.%d/%d", &a,&b,&c,&e,&p);
            s->mask = (p >= 32) ? 0xffffffffu : (p <= 0 ? 0u : ~((1u << (32-p)) - 1u));
            s->net = (((uint32_t)a<<24)|((uint32_t)b<<16)|((uint32_t)c<<8)|(uint32_t)e) & s->mask;
            dtok = colon + 1;
        }
        else dtok = buf;                            /* only a DSCP given -> match all */

        int dv = (dtok[0] >= '0' && dtok[0] <= '9') ? atoi(dtok) : dscp_by_name(dtok);
        if (dv < 0) dv = 0;
        if (dv > 63) dv = 63;
        s->dscp = dv;
    }

    gr_module_t *m = (gr_module_t *)malloc(sizeof(gr_module_t));
    if (!m) { free(s); return 0; }
    m->type = "classify"; m->state = s; m->init = 0;
    m->process = cls_process; m->destroy = cls_destroy;
    return m;
}
