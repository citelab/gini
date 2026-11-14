/*
 * fragment.c (collection of functions that implement the IP fragmentation
 * AUTHOR: Original version by Weiling Xu
 *         Revised by Muthucumaru Maheswaran
 * DATE:   Revised on July 15, 2005
 */


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
#include <slack/err.h>
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
 * Split pkt into fragments that fit the egress MTU, per RFC 791. Returns how many were made and
 * stored in frags[] (each its own allocation, owned by the caller -- normally handed straight to
 * IPSend2Output, which takes ownership), or 0 if it cannot be done. pkt itself is not modified
 * or freed.
 *
 * This was written for a datagram that could not actually exceed gpacket_t, and never ran
 * correctly once one could: one malloc for all fragments but the pointers for fragments 1..n
 * left uninitialised (a write through a stack pointer), the offset stored in bytes instead of
 * 8-byte units and in host order, DF SET on every fragment, sizes computed from the total
 * length including the header, and the fragments freed after being queued for output, which
 * then freed them again. The first packet that needed fragmenting killed the router.
 */
int fragmentIPPacket(gpacket_t *pkt, gpacket_t **frags)
{
	ip_packet_t *ip_pkt = (ip_packet_t *)pkt->data.data;
	int hlen = ip_pkt->ip_hdr_len << 2;
	int total = ntohs(ip_pkt->ip_pkt_len);
	int data_len = total - hlen;
	int link_mtu = findMTU(MTU_tbl, pkt->frame.dst_interface);
	int field = ntohs(ip_pkt->ip_frag_off);
	int base_off = (field & IP_OFFMASK) * 8;          /* this may itself be a fragment */
	int orig_mf = field & IP_MF;
	int max_data, off, len, n = 0, i;
	uchar *ipdata = (uchar *)ip_pkt + hlen;

	if (link_mtu <= hlen + 8 || data_len <= 0 || total > (int)sizeof(pkt->data.data))
		return 0;
	max_data = ((link_mtu - hlen) / 8) * 8;           /* all but the last carry a multiple of 8 */
	if ((data_len + max_data - 1) / max_data > MAX_FRAGMENTS)
		return 0;

	for (off = 0; off < data_len; off += len)
	{
		gpacket_t *f;
		ip_packet_t *fip;
		int last;

		len = data_len - off < max_data ? data_len - off : max_data;
		last = (off + len >= data_len);
		if ((f = (gpacket_t *)malloc(sizeof(gpacket_t))) == NULL)
		{
			for (i = 0; i < n; i++)
				free(frags[i]);
			return 0;
		}
		memcpy(f, pkt, sizeof(gpacket_t));
		fip = (ip_packet_t *)f->data.data;
		memcpy((uchar *)fip + hlen, ipdata + off, len);
		field = ((base_off + off) / 8) & IP_OFFMASK;
		if (!last || orig_mf)
			field |= IP_MF;
		fip->ip_frag_off = htons(field);                  /* DF clear: we are fragmenting */
		fip->ip_pkt_len = htons(hlen + len);
		fip->ip_cksum = 0;
		fip->ip_cksum = htons(checksum((uchar *)fip, fip->ip_hdr_len * 2));
		frags[n++] = f;
	}
	return n;
}


void deallocateFragments(gpacket_t **pkt_frags, int num_frags)
{
	int i;

	verbose(2, "[deallocateFragments]:: Deallocating fragment table memory ");
	for (i = 0; i < num_frags; i++)
		free(pkt_frags[i]);
}

