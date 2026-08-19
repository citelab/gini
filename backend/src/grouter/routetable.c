/*
 * routetable.c (Route table) routines
 * AUTHOR: Revised by Muthucumaru Maheswaran
 * DATE:   Revised on December 23, 2004
 */

#include "routetable.h"
#include "gnet.h"
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <slack/err.h>



/*-------------------------------------------------------------------------
 *                   R O U T E  T A B L E  F U N C T I O N S
 *-------------------------------------------------------------------------*/

/*
 * These route table functions are not very efficient.. I guess because
 * of the small topologies that are targeted here... efficiency is not
 * a major concern...
 */


int rtbl_replace_indx;         // indicate which entry in routing table should be replaced


/*
 * Find an interface corresponding to an IP address
 * Result stored in pbNhop and ppsInterfaceRet
 * Returns NO_ERROR if match found, ERROR if no match found
 */
int findRouteEntry(route_entry_t route_tbl[], uchar *ip_addr, uchar *nhop, int *ixface)
{
	int icount;
	uchar null_ip_addr[] = {0, 0, 0, 0};
	char tmpbuf[MAX_TMPBUF_LEN];
	/* Longest-prefix match in a SINGLE pass — track the best (widest mask) match inline.
	 * The old version collected matches into a fixed mindex[4] and could overflow when more
	 * than 4 routes matched (>4 overlapping subnets); this cannot overflow. */
	int k = -1;
	int nmlen = -1;

	for (icount = 0; icount < MAX_ROUTES; icount++)
	{
		if (route_tbl[icount].is_empty == TRUE) continue;
		verbose(2, "[findRouteEntry]:: Testing the route entry.. g %s at RT[%d], net %s netmask %s nexthop %s int %d",
			       IP2Dot(tmpbuf, ip_addr), icount, IP2Dot(tmpbuf+15, route_tbl[icount].network),
			       IP2Dot(tmpbuf+30, route_tbl[icount].netmask), IP2Dot(tmpbuf+45, route_tbl[icount].nexthop),
			       route_tbl[icount].interface);
		if (compareIPUsingMask(ip_addr, route_tbl[icount].network, route_tbl[icount].netmask) == 0)
		{
			int l = netMaskLen(route_tbl[icount].netmask);
			verbose(2, "[findRouteEntry]:: Found a route for %s at RT[%d], net %s netmask %s nexthop %s int %d",
			       IP2Dot(tmpbuf, ip_addr), icount, IP2Dot(tmpbuf+15, route_tbl[icount].network),
			       IP2Dot(tmpbuf+30, route_tbl[icount].netmask), IP2Dot(tmpbuf+45, route_tbl[icount].nexthop),
			       route_tbl[icount].interface);
			if (l > nmlen) { nmlen = l; k = icount; }
		}
	}

	if (k >= 0)
	{
		if (COMPARE_IP(route_tbl[k].nexthop, null_ip_addr) == 0)
			COPY_IP(nhop, ip_addr);
		else
			COPY_IP(nhop, route_tbl[k].nexthop);

		*ixface = route_tbl[k].interface;

		return EXIT_SUCCESS;
	}
	else
	{
		verbose(2, "[findRouteEntry]:: No match for %s in route table", IP2Dot(tmpbuf, ip_addr));
		return EXIT_FAILURE;
	}
}


/*
 * Add a route entry to the table, if entry found update, else fill in an empty one.
 * If no empty entry, overwrite a used one (round-robin via rtbl_replace_indx) — but
 * NEVER a CONNECTED entry: evicting a router's own interface subnet silently destroys
 * its connectivity (a hazard when a buggy control-plane protocol floods the table).
 * If every slot is connected, the add is refused loudly instead.
 */
void addRouteEntryTagged(route_entry_t route_tbl[], uchar* nwork, uchar* nmask, uchar* nhop,
                         int interface, uchar origin)
{
	int i;
	int ifree = -1;
	char tmpbuf[MAX_TMPBUF_LEN];

	// First check if the entry is already in the table, if it is, update it
	for (i = 0; i < MAX_ROUTES; i++)
	{
		if (route_tbl[i].is_empty == TRUE)
		{
			if (ifree < 0) ifree = i;

		} else if ((COMPARE_IP(nwork, route_tbl[i].network)) == 0)
		{
			if ((COMPARE_IP(nmask, route_tbl[i].netmask)) == 0)
			{
				// match: last writer wins, and the origin tag records who
				COPY_IP(route_tbl[i].nexthop, nhop);
				route_tbl[i].interface = interface;
				route_tbl[i].origin = origin;

				verbose(2, "[addRouteEntry]:: updated route table entry #%d", i);
				return;
			}
		}
	}

	if (ifree < 0)
	{
		// table full: round-robin eviction, skipping CONNECTED entries
		for (i = 0; i < MAX_ROUTES; i++)
		{
			int cand = (rtbl_replace_indx + i) % MAX_ROUTES;
			if (route_tbl[cand].origin != ROUTE_ORIGIN_CONNECTED)
			{
				ifree = cand;
				rtbl_replace_indx = (cand + 1) % MAX_ROUTES;
				break;
			}
		}
		if (ifree < 0)
		{
			error("[addRouteEntry]:: route table full of connected routes; cannot add %s",
			      IP2Dot(tmpbuf, nwork));
			return;
		}
	}

	COPY_IP(route_tbl[ifree].network, nwork);
	COPY_IP(route_tbl[ifree].netmask, nmask);
	COPY_IP(route_tbl[ifree].nexthop, nhop);
	route_tbl[ifree].interface = interface;
	route_tbl[ifree].origin = origin;
	route_tbl[ifree].is_empty = FALSE;

	verbose(2, "[addRouteEntry]:: wrote route entry #%d (origin %c)", ifree, origin);
	return;
}


/*
 * Untagged add (existing callers): the origin is derived — a zero nexthop is a
 * CONNECTED (interface) route, anything else is STATIC (CLI/boot `route add`).
 * Control-plane protocols use addRouteEntryTagged(..., ROUTE_ORIGIN_DYNAMIC).
 */
void addRouteEntry(route_entry_t route_tbl[], uchar* nwork, uchar* nmask, uchar* nhop, int interface)
{
	uchar null_ip_addr[] = {0, 0, 0, 0};
	uchar origin = (COMPARE_IP(nhop, null_ip_addr) == 0) ?
	               ROUTE_ORIGIN_CONNECTED : ROUTE_ORIGIN_STATIC;
	addRouteEntryTagged(route_tbl, nwork, nmask, nhop, interface, origin);
}



/*
 * delete route table entry by argument index i
 */
void deleteRouteEntryByIndex(route_entry_t route_tbl[], int i)
{
	route_tbl[i].is_empty = TRUE;
	verbose(2, "[deleteRouteEntryByIndex]:: route entry #%d deleted", i);
	return;
}


/*
 * delete route table entries related to
 * interface specified by argument indx
 */
void deleteRouteEntryByInterface(route_entry_t route_tbl[], int interface)
{
	int i;

	for (i = 0; i < MAX_ROUTES; i++)
		if ((route_tbl[i].is_empty == FALSE) &&
		    (route_tbl[i].interface == interface))
			deleteRouteEntryByIndex(route_tbl, i);

	verbose(2, "[deleteRouteEntryByInterface]:: table cleared of references to interface: %d", interface);
	return;
}


/*
 * initialize the route table to be empty
 */
void RouteTableInit(route_entry_t route_tbl[])
{
	int i;

	rtbl_replace_indx = 0;

	for(i = 0; i < MAX_ROUTES; i++)
	{
		route_tbl[i].is_empty = TRUE;
		route_tbl[i].origin = ROUTE_ORIGIN_STATIC;
	}
	verbose(2, "[initRouteTable]:: table initialized");

	return;
}


/*
 * print the route table
 */
void printRouteTable(route_entry_t route_tbl[])
{
	int i, rcount = 0;
	char tmpbuf[MAX_TMPBUF_LEN];
	interface_t *iface;

	printf("\n=================================================================\n");
	printf("      R O U T E  T A B L E \n");
	printf("-----------------------------------------------------------------\n");
	printf("Index\tNetwork\t\tNetmask\t\tNexthop\t\tInterface\tOrigin \n");

	for (i = 0; i < MAX_ROUTES; i++)
		if (route_tbl[i].is_empty != TRUE)
		{
			iface = findInterface(route_tbl[i].interface);
			printf("[%d]\t%s\t%s\t%s\t\t%s\t%c\n", i, IP2Dot(tmpbuf, route_tbl[i].network),
			       IP2Dot((tmpbuf+20), route_tbl[i].netmask), IP2Dot((tmpbuf+40), route_tbl[i].nexthop),
			       iface->device_name, route_tbl[i].origin ? route_tbl[i].origin : '?');
			rcount++;
		}
	printf("-----------------------------------------------------------------\n");
	printf("      %d number of routes found. \n", rcount);
	return;
}
