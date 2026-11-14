/*
 * host_stack.c  —  Z1 lwIP adapter. The ONE place that knows about lwIP.
 *
 * Owns the UDP/TCP termination for traffic addressed TO the router: the dispatch
 * (host_stack_input) and the lwIP wrappers (UDPProcess/TCPProcess, moved here from
 * ip.c). The core forwarding path only ever calls host_stack_input(). Build a pure
 * forwarder that doesn't need lwIP with -DGR_NO_HOST_STACK (and drop the lwIP sources;
 * see build.sh LUA/host-stack knobs).
 */
#include "host_stack.h"
#include "ip.h"          /* ip_packet_t, UDPProcess/TCPProcess prototypes */
#include "protocols.h"   /* UDP_PROTOCOL, TCP_PROTOCOL */

#ifndef GR_NO_HOST_STACK

#include <stdlib.h>      /* malloc, EXIT_SUCCESS */
#include <arpa/inet.h>   /* ntohs */
#include "routetable.h"  /* route_entry_t, route_tbl */
#include "pbuf.h"        /* struct pbuf, PBUF_REF */
#include "udp.h"         /* udp_input, UDP_HLEN */
#include "tcp.h"
#include "tcp_impl.h"    /* tcp_input */

extern route_entry_t route_tbl[];

int host_stack_init(void) { return 0; }
int host_stack_enabled(void) { return 1; }

int host_stack_input(gpacket_t *pkt)
{
    ip_packet_t *ip = (ip_packet_t *)pkt->data.data;
    if (ip->ip_prot == UDP_PROTOCOL) { UDPProcess(pkt); return 1; }
    if (ip->ip_prot == TCP_PROTOCOL) { TCPProcess(pkt); return 1; }
    return 0;   /* not a host-stack protocol (ICMP etc. handled by the core) */
}

/*
 * The pbuf lwIP reads one incoming packet through. It used to be a bare malloc: `ref`, `next`
 * and `flags` were whatever the heap held, and udp_input/tcp_input end in pbuf_free(), which
 * decrements ref and, if that reached 0, walked the garbage `next` -- a segfault on some packets
 * to the router (a traceroute to it, nc/iperf at its address) and not others. Now it is zeroed,
 * and ref starts at 2: lwIP's pbuf_free takes it to 1 and leaves the memory alone (otherwise it
 * would push this malloc'd struct into lwIP's own pool), and the caller frees it afterwards.
 */
static struct pbuf *host_pbuf(void *payload, int len)
{
    struct pbuf *p = calloc(1, sizeof(struct pbuf));
    if (p == NULL)
        return NULL;
    p->payload = payload;
    p->len = (u16_t)len;
    p->tot_len = p->len;
    p->type = PBUF_REF;
    p->ref = 2;
    p->next = NULL;
    return p;
}

/* UDP processing via lwIP (moved from ip.c). */
int UDPProcess(gpacket_t *in_pkt)
{
    verbose(2, "[UDPProcess]:: packet received for processing...");

    struct pbuf *p = host_pbuf(in_pkt->data.data,
                               ((ip_packet_t *)(in_pkt->data.data))->ip_hdr_len * 4 + UDP_HLEN);
    if (p == NULL)
        return EXIT_FAILURE;

    udp_input(p, in_pkt, route_tbl[in_pkt->frame.src_interface].netmask,
              route_tbl[in_pkt->frame.src_interface].network);
    free(p);
    return EXIT_SUCCESS;
}

/* TCP processing via lwIP (moved from ip.c). */
int TCPProcess(gpacket_t *in_pkt)
{
    verbose(2, "[TCPProcess]:: packet received for processing...");

    int len = ntohs(((ip_packet_t *)(in_pkt->data.data))->ip_pkt_len);
    struct pbuf *p;

    if (len > (int)sizeof(in_pkt->data.data))     /* a length field larger than the packet */
        return EXIT_FAILURE;
    if ((p = host_pbuf(in_pkt->data.data, len)) == NULL)
        return EXIT_FAILURE;

    tcp_input(p, in_pkt);
    free(p);
    return EXIT_SUCCESS;
}

#else  /* pure forwarder: lwIP not compiled/linked */

int host_stack_init(void) { return 0; }
int host_stack_enabled(void) { return 0; }
int host_stack_input(gpacket_t *pkt) { (void)pkt; return 0; }

#endif
