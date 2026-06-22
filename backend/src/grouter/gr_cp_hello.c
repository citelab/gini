/*
 * gr_cp_hello.c  —  the B2 control-plane "hello" demo module.
 *
 * This is to the control plane what gr_mod_block.zig is to the data plane: the smallest
 * thing that exercises the whole new path end to end. On a timer it sends a tiny "hello"
 * packet (IP protocol 253, the RFC 3692 experimental number), and it logs every hello it
 * receives. It proves the three new capabilities at once — timers, origination, and reception
 * — and it is the template a real protocol (DHCP, a routing protocol) is filled out from.
 *
 *   gpipe cp add hello                 # hellos to 255.255.255.255 every 5s
 *   gpipe cp add hello 10.0.0.2 3000   # hellos to 10.0.0.2 every 3s
 */
#include "gr_control_plane.h"
#include "gr_modules.h"     /* gr_pkt_ipsrc */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define HELLO_PROTO 253     /* RFC 3692 experimentation; OSPF analogously uses 89 */

typedef struct {
    const gr_cp_services_t *svc;
    uchar peer[4];          /* where to send hellos */
    int   period_ms;
    int   timer_id;
    long  sent, recv;
} hello_state_t;

static void hello_tick(gr_cp_module_t *self, void *arg)
{
    hello_state_t *s = (hello_state_t *)self->state;
    char msg[32];
    int n = snprintf(msg, sizeof msg, "HELLO #%ld", s->sent + 1);
    (void)arg;
    if (s->svc->send_ipv4(s->peer, HELLO_PROTO, msg, n) == 0)
    {
        s->sent++;
        s->svc->log("hello: sent #%ld to %u.%u.%u.%u",
                    s->sent, s->peer[0], s->peer[1], s->peer[2], s->peer[3]);
    }
    else
        s->svc->log("hello: send to %u.%u.%u.%u failed (no route?)",
                    s->peer[0], s->peer[1], s->peer[2], s->peer[3]);
}

static int hello_start(gr_cp_module_t *self, const gr_cp_services_t *svc, const char *args)
{
    hello_state_t *s = (hello_state_t *)calloc(1, sizeof(hello_state_t));
    int a = 255, b = 255, c = 255, d = 255, period = 5000;
    if (!s) return -1;
    if (args && *args)
        sscanf(args, "%d.%d.%d.%d %d", &a, &b, &c, &d, &period);
    s->svc = svc;
    s->peer[0] = (uchar)a; s->peer[1] = (uchar)b; s->peer[2] = (uchar)c; s->peer[3] = (uchar)d;
    s->period_ms = period > 0 ? period : 5000;
    self->state = s;

    /* receive any hello (IP proto 253), from anywhere */
    self->filter.proto = HELLO_PROTO;

    s->timer_id = svc->timer_add(self, s->period_ms, hello_tick, NULL);
    svc->log("hello: started, peer=%u.%u.%u.%u period=%dms",
             s->peer[0], s->peer[1], s->peer[2], s->peer[3], s->period_ms);
    return 0;
}

static void hello_on_packet(gr_cp_module_t *self, gpacket_t *pkt)
{
    hello_state_t *s = (hello_state_t *)self->state;
    uint32_t src = gr_pkt_ipsrc(pkt);
    s->recv++;
    s->svc->log("hello: received #%ld from %u.%u.%u.%u",
                s->recv, (src >> 24) & 0xff, (src >> 16) & 0xff,
                (src >> 8) & 0xff, src & 0xff);
}

static void hello_stop(gr_cp_module_t *self)
{
    hello_state_t *s = (hello_state_t *)self->state;
    if (s)
    {
        if (s->svc && s->timer_id > 0) s->svc->timer_del(s->timer_id);
        free(s);
        self->state = NULL;
    }
}

gr_cp_module_t *gr_cp_hello_create(void)
{
    gr_cp_module_t *m = (gr_cp_module_t *)calloc(1, sizeof(gr_cp_module_t));
    if (!m) return NULL;
    m->name = "hello";
    m->start = hello_start;
    m->on_packet = hello_on_packet;
    m->stop = hello_stop;
    return m;
}
