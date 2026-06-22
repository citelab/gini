/*
 * gr_mcast.c  —  B3 multicast group membership table (rwlock-protected).
 *
 * One row per active group: the group address and a bitmap of the interfaces that have a
 * member. Writers are the control plane (IGMP snoop / console); the reader is the forwarding
 * worker in ip.c. Small and fixed-size, like the route and ARP tables.
 */
#include "gr_mcast.h"
#include <pthread.h>
#include <string.h>
#include <stdio.h>

#define MAX_MGROUPS 32

typedef struct {
    uchar    group[4];
    uint32_t iface_mask;     /* bit i set == interface i has a member */
    int      used;
} mgroup_t;

static mgroup_t g_groups[MAX_MGROUPS];
static pthread_rwlock_t mc_lock = PTHREAD_RWLOCK_INITIALIZER;

void gr_mcast_init(void)
{
    pthread_rwlock_wrlock(&mc_lock);
    memset(g_groups, 0, sizeof g_groups);
    pthread_rwlock_unlock(&mc_lock);
}

static mgroup_t *find_locked(uchar *group)
{
    int i;
    for (i = 0; i < MAX_MGROUPS; i++)
        if (g_groups[i].used && memcmp(g_groups[i].group, group, 4) == 0)
            return &g_groups[i];
    return 0;
}

void gr_mcast_join(uchar *group, int iface)
{
    if (iface < 0 || iface >= 32) return;
    pthread_rwlock_wrlock(&mc_lock);
    {
        mgroup_t *g = find_locked(group);
        if (!g)
        {
            int i;
            for (i = 0; i < MAX_MGROUPS; i++)
                if (!g_groups[i].used)
                { g = &g_groups[i]; g->used = 1; memcpy(g->group, group, 4); g->iface_mask = 0; break; }
        }
        if (g) g->iface_mask |= (1u << iface);
    }
    pthread_rwlock_unlock(&mc_lock);
}

void gr_mcast_leave(uchar *group, int iface)
{
    if (iface < 0 || iface >= 32) return;
    pthread_rwlock_wrlock(&mc_lock);
    {
        mgroup_t *g = find_locked(group);
        if (g)
        {
            g->iface_mask &= ~(1u << iface);
            if (g->iface_mask == 0) g->used = 0;     /* no members left -> free the row */
        }
    }
    pthread_rwlock_unlock(&mc_lock);
}

uint32_t gr_mcast_lookup(uchar *group)
{
    uint32_t mask = 0;
    pthread_rwlock_rdlock(&mc_lock);
    {
        mgroup_t *g = find_locked(group);
        if (g) mask = g->iface_mask;
    }
    pthread_rwlock_unlock(&mc_lock);
    return mask;
}

int gr_mcast_show(char *out, int outlen)
{
    int i, n = 0;
    pthread_rwlock_rdlock(&mc_lock);
    n += snprintf(out + n, outlen - n, "multicast groups:");
    {
        int any = 0;
        for (i = 0; i < MAX_MGROUPS && n < outlen; i++)
            if (g_groups[i].used)
            {
                int b; any = 1;
                n += snprintf(out + n, outlen - n, "\n  %u.%u.%u.%u ->",
                              g_groups[i].group[0], g_groups[i].group[1],
                              g_groups[i].group[2], g_groups[i].group[3]);
                for (b = 0; b < 32 && n < outlen; b++)
                    if (g_groups[i].iface_mask & (1u << b))
                        n += snprintf(out + n, outlen - n, " if%d", b);
            }
        if (!any) snprintf(out + n, outlen - n, " (none)");
    }
    pthread_rwlock_unlock(&mc_lock);
    return 0;
}
