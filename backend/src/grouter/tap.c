/*
 * tap.c (Tap driver for the GINI router)
 * AUTHOR: Muthucumaru Maheswaran
 *
 * VERSION: 1.0
 */

#include <slack/err.h>
#include "sdn.h"        /* Z1: OpenFlow as ingress mode (seam) */

#include "packetcore.h"
#include "classifier.h"
#include "filter.h"
#include "protocols.h"
#include "message.h"
#include "gnet.h"
#include "arp.h"
#include "ip.h"
#include "ethernet.h"
#include "tapio.h"
#include <netinet/in.h>
#include <stdlib.h>


/*
 * TODO: Revision needed.
 * These modules are very similar to those found in ethernet.c. May be we
 * should redesign the modules (ethernet.c and tap.c) such that we can minimise
 * the replication?
 */

extern pktcore_t *pcore;
extern classlist_t *classifier;
extern filtertab_t *filter;


extern router_config rconfig;


void *toTapDev(void *arg)
{
	gpacket_t *inpkt = (gpacket_t *)arg;
	interface_t *iface;
	arp_packet_t *apkt;
	char tmpbuf[MAX_TMPBUF_LEN];
	int pkt_size;

	verbose(2, "[toTapDev]:: entering the function.. ");
	// find the outgoing interface and device...
	if ((iface = findInterface(inpkt->frame.dst_interface)) != NULL)
	{
		/* send IP packet or ARP reply */
		if (inpkt->data.header.prot == htons(ARP_PROTOCOL))
		{
			apkt = (arp_packet_t *) inpkt->data.data;
			COPY_MAC(apkt->src_hw_addr, iface->mac_addr);
			COPY_IP(apkt->src_ip_addr, gHtonl(tmpbuf, iface->ip_addr));
		}
		pkt_size = gpacketSize(inpkt);

		verbose(2, "[toTapDev]:: tap_sendto called for interface %d.. ", iface->interface_id);
		tap_sendto(iface->vpl_data, &(inpkt->data), pkt_size);
		free(inpkt);          // finally destroy the memory allocated to the packet..
	} else
		error("[toTapDev]:: ERROR!! Could not find outgoing interface ...");

	// this is just a dummy return -- return value not used.
	return arg;
}


/*
 * TODO: Can we do these without super user permissions?
 */
void* fromTapDev(void *arg)
{
	interface_t *iface = (interface_t *) arg;
	interface_array_t *iarr = (interface_array_t *)iface->iarray;
	uchar bcast_mac[] = MAC_BCAST_ADDR;
	gpacket_t *in_pkt;
	int pktsize;

	pthread_setcanceltype(PTHREAD_CANCEL_ASYNCHRONOUS, NULL);		// die as soon as cancelled
	while (1)
	{
		verbose(2, "[fromTapDev]:: Receiving a packet ...");
		if ((in_pkt = (gpacket_t *)malloc(sizeof(gpacket_t))) == NULL)
		{
			fatal("[fromTapDev]:: unable to allocate memory for packet.. ");
			return NULL;
		}

		bzero(in_pkt, sizeof(gpacket_t));
		pktsize = tap_recvfrom(iface->vpl_data, &(in_pkt->data), sizeof(pkt_data_t));
		pthread_testcancel();

		// check whether the incoming packet is a layer 2 broadcast or
		// meant for this node... otherwise should be thrown..
		// TODO: fix for promiscuous mode packet snooping.

		if ((COMPARE_MAC(in_pkt->data.header.dst, iface->mac_addr) != 0) &&
			(COMPARE_MAC(in_pkt->data.header.dst, bcast_mac) != 0))
		{
			verbose(1, "[fromTapDev]:: Packet[%d] dropped .. not for this router!? ", pktsize);
			free(in_pkt);
			continue;
		}

		// copy fields into the message from the packet..
		in_pkt->frame.src_interface = iface->interface_id;
		COPY_MAC(in_pkt->frame.src_hw_addr, iface->mac_addr);
		COPY_IP(in_pkt->frame.src_ip_addr, iface->ip_addr);

		// check for filtering.. if the it should be filtered.. then drop
		if (filteredPacket(filter, in_pkt))
		{
			verbose(2, "[fromTapDev]:: Packet filtered..!");
			free(in_pkt);
			continue;   // skip the rest of the loop
		}

		verbose(2, "[fromTapDev]:: Packet is sent for enqueuing..");
		enqueuePacket(pcore, in_pkt, sizeof(gpacket_t), (sdn_mode() == SDN_MODE_OPENFLOW));
	}
}

