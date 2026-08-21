/*
 * gr_control_plane.h  —  B2 control-plane module API.
 *
 * The data-plane module ABI (gr_module.h) is reactive: a module's process() runs only
 * when a packet is forwarded through it. Control protocols (DHCP, a routing protocol,
 * multicast membership) need three things that ABI cannot give them:
 *
 *   1. timers    — act on a clock (send a hello, age a lease) with no packet to trigger it
 *   2. send      — originate a packet on the router's own initiative
 *   3. receive   — get packets addressed to the router or to a broadcast/multicast group
 *                  (these branch off in IPIncomingPacket BEFORE the forwarding pipeline)
 *
 * plus a start/stop lifecycle and a safe concurrency story.
 *
 * THREADING MODEL (the load-bearing decision): one serialized control thread owns every
 * control-module callback. A module's start(), on_packet(), and all its timer callbacks run
 * on that ONE thread, so a module never needs a lock for its own state. The only crossings
 * are route/ARP writes (locked by gr_state) and sending (the output queue is locked), both
 * already thread-safe. The forwarding worker only ever COPIES a matched packet onto the
 * control queue and signals — it never runs module code.
 *
 * This is additive: nothing changes until a control module is loaded with `gpipe cp add`.
 */
#ifndef __GR_CONTROL_PLANE_H__
#define __GR_CONTROL_PLANE_H__

#include <stddef.h>
#include <pthread.h>
#include "message.h"   /* gpacket_t, uchar */

typedef struct gr_cp_module gr_cp_module_t;

/* ---- the services the runtime offers a control module (the "southbound" calls) ---- */
typedef struct gr_cp_services
{
    /* originate packets */
    int  (*send_ipv4)(const uchar *dst, int proto, const void *payload, int len);
    int  (*send_raw)(int iface, const uchar *dst_mac, int prot,
                     const void *payload, int len);   /* L2, pre-IP: e.g. DHCP to a host with no IP */
    /* build IP+UDP (with checksums) and send out one interface to a given MAC. The one-stop
     * call for UDP control protocols (DHCP, RIP) that must reach a broadcast/unconfigured
     * peer where a routed send_ipv4 would fail. dst_mac may be the broadcast MAC. */
    int  (*send_udp)(int iface, const uchar *dst_mac,
                     const uchar *src_ip, const uchar *dst_ip,
                     int sport, int dport, const void *data, int len);

    /* routing table (thread-safe; wraps gr_state) */
    void (*route_add)(const uchar *net, const uchar *mask, const uchar *nhop, int iface);
    void (*route_del)(const uchar *net, const uchar *mask);     /* by match, not index */
    int  (*route_lookup)(const uchar *dst, uchar *nhop, int *iface);

    /* timers (the callback fires on the control thread) */
    int  (*timer_add)(gr_cp_module_t *self, int period_ms,
                      void (*cb)(gr_cp_module_t *self, void *arg), void *arg);
    void (*timer_del)(int timer_id);

    /* interface inventory, for building protocol messages */
    int  (*iface_count)(void);
    int  (*iface_addr)(int iface, uchar *ip);   /* 0 on success, -1 if no such iface */

    void (*log)(const char *fmt, ...);
} gr_cp_services_t;

/* ---- which packets a module wants delivered to on_packet() ---- */
typedef struct gr_cp_filter
{
    int   proto;            /* IP protocol number, or 0 = any                          */
    int   udp_dport;        /* UDP dest port, or 0 = any (checked only when proto==UDP) */
    uchar dst_addr[4];      /* match dest address (e.g. a multicast group); 0.0.0.0 = any */
    uchar dst_mask[4];      /* mask applied to dst_addr                                 */
} gr_cp_filter_t;

/* ---- what a control module implements (the "northbound" side) ---- */
struct gr_cp_module
{
    const char    *name;        /* "hello", "dhcp", "rip" ...        */
    void          *state;       /* module-private                    */
    gr_cp_filter_t filter;      /* declares what on_packet() receives */
    int  (*start)(gr_cp_module_t *self, const gr_cp_services_t *svc, const char *args);
    void (*on_packet)(gr_cp_module_t *self, gpacket_t *pkt);   /* a matched packet (a copy) */
    void (*stop)(gr_cp_module_t *self);
    /* OPTIONAL: write a live status snapshot into out (<= outlen bytes, return bytes
     * written). Called from the CLI thread, so it must only read state that is safe to
     * read cross-thread (e.g. a published snapshot buffer). Backs `gpipe cp status`,
     * which the Multicast HUD polls. NULL = module has no status. */
    int  (*status)(gr_cp_module_t *self, char *out, size_t outlen);
};

/* ---- runtime entry points ---- */

/* Start the single control thread. Call once from main(); returns its thread id (0 on
 * failure). The thread sits idle until a module is registered. */
pthread_t gr_cp_thread_init(void);

/* Register (and start) a control module by name, with optional args. 0 on success, -1 on
 * failure (unknown name / start() failed). Backs `gpipe cp add`. */
int  gr_cp_add(const char *name, const char *args, char *out, size_t outlen);

/* Stop + unregister every loaded control module. Backs `gpipe cp stop`. */
void gr_cp_stop_all(void);

/* Human-readable list of loaded control modules. Backs `gpipe cp list`. */
int  gr_cp_list(char *out, size_t outlen);

/* Concatenated status snapshots of every module that implements status(). Backs
 * `gpipe cp status` (polled by the Multicast HUD). */
int  gr_cp_status(char *out, size_t outlen);

/* Space-separated registry names, for usage messages. */
const char *gr_cp_names(void);

/* Hand a router-bound / broadcast / multicast packet to the control plane. Called from the
 * forwarding worker (ip.c). Cheap no-op when no module is loaded; otherwise filter-matches
 * and copies the packet onto the control queue. Never runs module code itself. */
void gr_cp_deliver(gpacket_t *pkt);

#endif /* __GR_CONTROL_PLANE_H__ */
