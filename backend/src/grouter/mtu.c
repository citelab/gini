/*
 * mtu.c — MTU-table pretty-printer (C).
 *
 * The MTU-table logic (init / add / delete / find / find-all-IPs) was ported to Zig in
 * mtu.zig (Z3). Only printMTUTable stays in C. Same `mtu_entry_t` layout.
 */
#include "mtu.h"
#include <stdio.h>
#include <slack/err.h>


/*
 * print mtu table
 */
void printMTUTable(mtu_entry_t mtable[])
{
	int i;

	printf("-----------------------------\n");
	printf("      M T U  T A B L E \n");
	printf("-----------------------------\n");
	printf("Inter. ID\tMTU \n");

	for (i = 0; i < MAX_MTU; i++)
		if (mtable[i].is_empty == FALSE)
			printf("%d\t%d\n", i, mtable[i].mtu);
	printf("---------------------------------\n");
	return;
}
