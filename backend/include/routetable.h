/*
 * routetable.h (header file for Route table data structures
 * AUTHOR: Originally written by Weiling Xu
 *         Revised by Muthucumaru Maheswaran
 * DATE: July 11, 2005
 *
 */

#ifndef __ROUTE_TABLE_H__
#define __ROUTE_TABLE_H__


/*
 * Private definitions: only used within the IP module
 */

#include "grouter.h"

#define MAX_ROUTES                      20	// maximum route table size

// route origin tags: who installed the entry. Shown by `route show`, and used to
// protect connected routes from round-robin eviction when the table is full.
#define ROUTE_ORIGIN_CONNECTED          'C'	// interface subnet (nexthop 0.0.0.0)
#define ROUTE_ORIGIN_STATIC             'S'	// CLI `route add` (boot config / operator)
#define ROUTE_ORIGIN_DYNAMIC            'D'	// control-plane protocol (e.g. RIP in Lua)


/*
 * route table entry
 */
typedef struct _route_entry_t
{
	bool is_empty;			        // indicates whether entry is used or not
	uchar network[4];			// Network IP address
	uchar netmask[4];			// Netmask
	uchar nexthop[4];			// Nexthop IP address
	int  interface;			        // output interface
	uchar origin;			        // ROUTE_ORIGIN_* — who installed this entry
} route_entry_t;

// prototypes of the functions provided for the route table handling..

void RouteTableInit(route_entry_t route_tbl[]);
void addRouteEntry(route_entry_t route_tbl[], uchar* nwork, uchar* nmask, uchar* nhop, int interface);
void addRouteEntryTagged(route_entry_t route_tbl[], uchar* nwork, uchar* nmask, uchar* nhop,
                         int interface, uchar origin);
void deleteRouteEntryByIndex(route_entry_t route_tbl[], int i);

int findRouteEntry(route_entry_t route_tbl[], uchar *ip_addr, uchar *nhop, int *ixface);

#endif
