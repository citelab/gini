/*
 * fragment.c (collection of functions that implement the IP fragmentation
 * AUTHOR: Original version by Weiling Xu
 *         Revised by Muthucumaru Maheswaran
 * DATE:   Revised on July 15, 2005
 */

#include "verbose.h"
#include <math.h>
#include "message.h"
#include "grouter.h"
#include "moduledefs.h"
#include "routetable.h"
#include "mtu.h"
#include "protocols.h"
#include "ip.h"
#include "fragment.h"
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#ifdef __APPLE__
#include "slack/err.h"
#endif
#include <netinet/in.h>

extern mtu_entry_t MTU_tbl[MAX_MTU];	   


/*
 * return 1 (TRUE) if fragmentation is needed for the given packet
 * WE ASSUME THE NEXT HOP IS ALREADY SET IN THE GPACKET HEADER.
 * THIS FUNCTION WILL FAIL IF THIS ASSUMPTION IS NOT TRUE!!
 */
int needFragmentation(gpacket_t *pkt)
{
	ip_packet_t *ip_pkt = (ip_packet_t *)pkt->data.data;	
	int link_mtu;

	verbose(2, "[needFragmentation]:: Checking whether the packet needs fragmentation.. ");
	link_mtu = findMTU(MTU_tbl, pkt->frame.dst_interface);
	if (link_mtu < ntohs(ip_pkt->ip_pkt_len))                 // need fragmentation
		return 1;
	else
		return 0;
}


/*
 * with the current data structures, fragmentation is a hypothetical
 * requirement! the gpacket_t datastructure has a hardcoded limit for
 * IP packets. therefore, we cannot have larger frames to require
 * fragmentation. may be we can remove this hardcoding and dynamically
 * create the payload -- this will require dynamic memory allocations and
 * deallocations. 
 */
int fragmentIPPacket(gpacket_t *pkt, gpacket_t **frags)
{
	ip_packet_t *ip_pkt = (ip_packet_t *)pkt->data.data;	
	int link_mtu, num_frags, i;
	int frag_offset, frag_len;
	ip_packet_t *this_ippkt;
	uchar *ipdata_ptr;

	link_mtu = findMTU(MTU_tbl, pkt->frame.dst_interface);
	
	num_frags = (int) ceil(((double) ntohs(ip_pkt->ip_pkt_len))/((double) link_mtu));
	frag_len = ntohs(ip_pkt->ip_pkt_len)/num_frags;

	*frags = (gpacket_t *) malloc(sizeof(gpacket_t) * num_frags);
	if (*frags == NULL)
		verbose(1, "[fragmentIPPacket]:: unable to allocate memory ");

	frag_offset = 0;
	ipdata_ptr = (uchar *)ip_pkt + (ip_pkt->ip_hdr_len << 2);

	for (i = 0; i < (num_frags -1); i++)
	{
		memcpy(frags[i], pkt, sizeof(gpacket_t));
		this_ippkt = (ip_packet_t *)frags[i]->data.data;
		this_ippkt->ip_frag_off = frag_offset;
		memcpy(((uchar *)this_ippkt + (this_ippkt->ip_hdr_len << 2)), 
		       (ipdata_ptr + frag_offset), frag_len);
		SET_MF_BITS(this_ippkt->ip_frag_off);
		SET_DF_BITS(this_ippkt->ip_frag_off);
		this_ippkt->ip_pkt_len = htons((ip_pkt->ip_hdr_len << 2) + frag_len);
		this_ippkt->ip_cksum = 0;
		this_ippkt->ip_cksum = htons(checksum((uchar *)this_ippkt, this_ippkt->ip_hdr_len *2));
		frag_offset += frag_len;
	}
	frag_len = ntohs(ip_pkt->ip_pkt_len) - frag_offset;
	memcpy(frags[i], pkt, sizeof(gpacket_t));
	this_ippkt = (ip_packet_t *)frags[i]->data.data;
	this_ippkt->ip_frag_off = frag_offset;
	memcpy(((uchar *)this_ippkt + (this_ippkt->ip_hdr_len << 2)), 
	       (ipdata_ptr + frag_offset), frag_len);
	RESET_MF_BITS(this_ippkt->ip_frag_off);
	RESET_DF_BITS(this_ippkt->ip_frag_off);
	this_ippkt->ip_pkt_len = htons((ip_pkt->ip_hdr_len << 2) + frag_len);
	this_ippkt->ip_cksum = 0;
	this_ippkt->ip_cksum = htons(checksum((uchar *)this_ippkt, this_ippkt->ip_hdr_len *2));

	return num_frags;
}


void deallocateFragments(gpacket_t **pkt_frags, int num_frags)
{
	int i;

	verbose(2, "[deallocateFragments]:: Deallocating fragment table memory ");
	for (i = 0; i < num_frags; i++)
		free(pkt_frags[i]);
}

