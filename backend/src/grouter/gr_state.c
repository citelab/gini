/*
 * gr_state.c  —  Z1 state manager. Wraps the existing global tables with rwlocks so
 * the CLI and the forwarding threads stop racing on them.
 */
#include "gr_state.h"
#include "arp.h"       /* ARPFindEntry, ARPAddEntry */
#include "routetable.h" /* route_entry_t, MAX_ROUTES */
#include <pthread.h>
#include <string.h>

extern route_entry_t route_tbl[];   /* defined in routetable.c */

static pthread_rwlock_t route_lock = PTHREAD_RWLOCK_INITIALIZER;
static pthread_rwlock_t arp_lock   = PTHREAD_RWLOCK_INITIALIZER;

void gr_state_init(void)
{
    /* rwlocks are statically initialized; hook for future state setup */
}

int gr_route_lookup(uchar *ip_addr, uchar *nexthop, int *out_iface)
{
    int r;
    pthread_rwlock_rdlock(&route_lock);
    r = findRouteEntry(route_tbl, ip_addr, nexthop, out_iface);
    pthread_rwlock_unlock(&route_lock);
    return r;
}

void gr_route_add(uchar *net, uchar *mask, uchar *nhop, int iface)
{
    pthread_rwlock_wrlock(&route_lock);
    addRouteEntry(route_tbl, net, mask, nhop, iface);
    pthread_rwlock_unlock(&route_lock);
}

void gr_route_del(int index)
{
    pthread_rwlock_wrlock(&route_lock);
    deleteRouteEntryByIndex(route_tbl, index);
    pthread_rwlock_unlock(&route_lock);
}

/* Delete the route whose (network, netmask) match exactly. Control protocols think in
 * (net, mask), not table indices; this finds the slot under the write lock and removes it. */
void gr_route_del_match(uchar *net, uchar *mask)
{
    int i;
    pthread_rwlock_wrlock(&route_lock);
    for (i = 0; i < MAX_ROUTES; i++)
        if (!route_tbl[i].is_empty &&
            memcmp(route_tbl[i].network, net, 4) == 0 &&
            memcmp(route_tbl[i].netmask, mask, 4) == 0)
        {
            deleteRouteEntryByIndex(route_tbl, i);
            break;
        }
    pthread_rwlock_unlock(&route_lock);
}

int gr_arp_find(uchar *ip_addr, uchar *mac_out)
{
    int r;
    pthread_rwlock_rdlock(&arp_lock);
    r = ARPFindEntry(ip_addr, mac_out);
    pthread_rwlock_unlock(&arp_lock);
    return r;
}

void gr_arp_add(uchar *ip_addr, uchar *mac)
{
    pthread_rwlock_wrlock(&arp_lock);
    ARPAddEntry(ip_addr, mac);
    pthread_rwlock_unlock(&arp_lock);
}
