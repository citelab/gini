/*
 * routetable.c — route-table pretty-printer (C).
 *
 * The route-table LOGIC (init / add / delete / longest-prefix lookup) was ported to
 * Zig in routetable.zig (Z3). Only printRouteTable stays in C, because it reaches into
 * interface_t (findInterface) and uses IP2Dot formatting. Same `route_entry_t` layout.
 */
#include "routetable.h"
#include "gnet.h"
#include <stdio.h>
#include <slack/err.h>


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
	printf("Index\tNetwork\t\tNetmask\t\tNexthop\t\tInterface \n");

	for (i = 0; i < MAX_ROUTES; i++)
		if (route_tbl[i].is_empty != TRUE)
		{
			iface = findInterface(route_tbl[i].interface);
			printf("[%d]\t%s\t%s\t%s\t\t%s\n", i, IP2Dot(tmpbuf, route_tbl[i].network),
			       IP2Dot((tmpbuf+20), route_tbl[i].netmask), IP2Dot((tmpbuf+40), route_tbl[i].nexthop), iface->device_name);
			rcount++;
		}
	printf("-----------------------------------------------------------------\n");
	printf("      %d number of routes found. \n", rcount);
	return;
}
