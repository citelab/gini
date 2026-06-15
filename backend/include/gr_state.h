/*
 * gr_state.h  —  Z1 state manager: the single, LOCKED path for mutable router state.
 *
 * The "ugliness": the CLI thread mutates the routing table, ARP cache, and interface
 * array while the forwarding threads read them, with no locking -> data races. These
 * accessors hold the right rwlock (read for lookups, write for changes). Call sites
 * (ip.c forwarding, arp.c, cli.c route/arp commands) migrate to these incrementally;
 * the test harness guards each move. Pure additive seam — nothing breaks by declaring.
 */
#ifndef __GR_STATE_H__
#define __GR_STATE_H__

#include "routetable.h"   /* route_entry_t, uchar */
#include "message.h"

void gr_state_init(void);

/* routing table (rwlock-protected) */
int  gr_route_lookup(uchar *ip_addr, uchar *nexthop, int *out_iface);   /* read  */
void gr_route_add(uchar *net, uchar *mask, uchar *nhop, int iface);     /* write */
void gr_route_del(int index);                                          /* write */

/* ARP cache (rwlock-protected) */
int  gr_arp_find(uchar *ip_addr, uchar *mac_out);                       /* read  */
void gr_arp_add(uchar *ip_addr, uchar *mac);                            /* write */

#endif /* __GR_STATE_H__ */
