/*
 * mtu.c (maximum transfer unit) routines
 * AUTHOR: Revised by Muthucumaru Maheswaran
 * DATE:   Revised on December 23, 2004
 */

#include "mtu.h"
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <slack/err.h>


/*-------------------------------------------------------------------------
 *                   M T U  T A B L E  F U N C T I O N S
 *-------------------------------------------------------------------------*/

/*
 * MTU table is organized as a direct indexed table.
 * 
 */


/*
 * initialize the MTU table to be empty
 */
void MTUTableInit(mtu_entry_t mtable[])
{
	int i;

	for(i = 0; i < MAX_MTU; i++)
		mtable[i].is_empty = TRUE;

	verbose(2, "[initMTUTable]:: table initialized..");

	return;
}

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


/*
 * Find the MTU of the given interface. Return -1 if the interface
 * was not found in the table
 */
int findMTU(mtu_entry_t mtable[], int index)
{
	if (mtable[index].is_empty != TRUE) 
		return mtable[index].mtu;
	verbose(2, "[findMTU]:: No entry found in MTU table for index %d ", index);
	return -1;
}


/*
 * returns EXIT_FAILURE if the entry is not found in the MTU table.
 * Otherwise, EXIT_SUCCESS is returned and the ip_addr is copied
 */
int findInterfaceIP(mtu_entry_t mtable[], int index, 
		    uchar *ip_addr)
{
	if (mtable[index].is_empty != TRUE)
	{
		COPY_IP(ip_addr, mtable[index].ip_addr);
		return EXIT_SUCCESS;
	}
	
	return EXIT_FAILURE;
}



/*
 * return all interface IPs. This is useful to determine whether an
 * incoming packet is destined to this router (ultimately!). For instance,
 * when the router's another interface is probed, we want to detect that
 * and reply accordingly.
 */
int findAllInterfaceIPs(mtu_entry_t mtable[], uchar buf[][4])
{
	int i, count = 0;
	
	for (i = 0; i < MAX_MTU; i++)
		if (mtable[i].is_empty == FALSE)
		{
			COPY_IP(buf[count], mtable[i].ip_addr);
			count++;
		}

	verbose(2, "[findAllInterfaceIPs]:: output buffer ...");
	return count;
}



/*
 * delete the MTU entry that corresponds to the given index from
 * the MTU table.
 */

void deleteMTUEntry(mtu_entry_t mtable[], int index)
{
	if (mtable[index].is_empty != TRUE)
	{
		mtable[index].is_empty = TRUE;
		verbose(2, "[deleteMTUEntry]:: Table cleared of references to interface: %d", index);
		return;
	}

	verbose(2, "[deleteMTUEntry]:: Can't find entry for interface: %d", index);
	return;
}


/*
 * add MTU entry by argument index,mtu is the new value
 */
void addMTUEntry(mtu_entry_t mtable[], int index, 
		 int mtu, uchar *ip_addr)
{
	// check validity of the specified value, set to DEFAULT_MTU if invalid
	if (mtu <= 0)
	{
		verbose(2, "[addMTUEntry]:: mtu<0 or no value set for mtu, MTU set to default value");
		mtu=DEFAULT_MTU;
	}

	mtable[index].is_empty = FALSE;
	mtable[index].mtu = mtu;
	COPY_IP(mtable[index].ip_addr, ip_addr);
    
	return;
}
