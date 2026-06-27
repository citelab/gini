/*
 * gr_mcast.h  —  B3 multicast group membership table.
 *
 * The control plane (IGMP snooping, or the `gpipe mcast join` console command) records which
 * interfaces have members of which multicast group; the data plane (ip.c forwarding) reads it
 * to decide which interfaces to replicate a multicast datagram onto. Like gr_state, the table
 * is rwlock-protected so the control thread can write while the forwarding worker reads.
 *
 * Membership is kept as a per-group bitmap of interface ids (bit i set == interface i has a
 * member), which is all the data plane needs to fan a packet out.
 */
#ifndef __GR_MCAST_H__
#define __GR_MCAST_H__

#include "grouter.h"   /* uchar */
#include <stdint.h>

void     gr_mcast_init(void);

/* control-plane writes */
void     gr_mcast_join(uchar *group, int iface);    /* add iface to group's member set  */
void     gr_mcast_leave(uchar *group, int iface);   /* remove; drops the group if empty */

/* data-plane read: bitmap of interfaces with a member of `group` (0 = none) */
uint32_t gr_mcast_lookup(uchar *group);

/* console: write a human-readable dump of the table into out */
int      gr_mcast_show(char *out, int outlen);

#endif /* __GR_MCAST_H__ */
