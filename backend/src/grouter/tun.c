/*
 * This is the low level driver for the tun interface. 
 * 
 * Copyright (C) 2015 Ahmed Youssef (ahmed.youssef@mail.mcgill.ca)
 * Licensed under the GPL.
 */

#include <slack/err.h>
#include "sdn.h"        /* Z1: OpenFlow as ingress mode (seam) */
#include "openflow.h"   /* OFP_DL_TYPE_ETH2_CUTOFF (ingress ethertype guard) */
#include "tun.h"
#include "packetcore.h"
#include "classifier.h"
#include "filter.h"
#include "protocols.h"
#include "message.h"
#include "gnet.h"
#include "arp.h"
#include "ip.h"
#include "ethernet.h"
#include <netinet/in.h>
#include <stdlib.h>
#include <sys/socket.h>
#include <arpa/inet.h>

extern pktcore_t *pcore;
extern classlist_t *classifier;
extern filtertab_t *filter;


extern router_config rconfig;

void *toTunDev(void *arg)
{
	gpacket_t *inpkt = (gpacket_t *)arg;
	interface_t *iface;
	arp_packet_t *apkt;
	char tmpbuf[MAX_TMPBUF_LEN];
	int pkt_size;

	verbose(2, "[toTunDev]:: entering the function.. ");
	// find the outgoing interface and device...
	if ((iface = findInterface(inpkt->frame.dst_interface)) != NULL)
	{
		/* send IP packet or ARP reply. As a router we rewrite the ARP sender to our
		 * own egress MAC/IP (proxy-ARP-ish); but as an OpenFlow SWITCH we must NOT touch
		 * the frame — rewriting would launder the host MACs and break L2 learning. So
		 * only rewrite when NOT acting as an OpenFlow switch. (toEthernetDev does the
		 * same via !frame.openflow.) */
		if (!(sdn_mode() == SDN_MODE_OPENFLOW) &&
				inpkt->data.header.prot == htons(ARP_PROTOCOL))
		{
			apkt = (arp_packet_t *) inpkt->data.data;
			COPY_MAC(apkt->src_hw_addr, iface->mac_addr);
			COPY_IP(apkt->src_ip_addr, gHtonl(tmpbuf, iface->ip_addr));
		}
		pkt_size = gpacketSize(inpkt);
		verbose(2, "[toTunDev]:: tun_sendto called for interface %d.. ", iface->interface_id);
		tun_sendto(iface->vpl_data, &(inpkt->data), pkt_size);
		free(inpkt);          // finally destroy the memory allocated to the packet..
	} else
		error("[toTunDev]:: ERROR!! Could not find outgoing interface ...");

	// this is just a dummy return -- return value not used.
	return arg;
}


void* fromTunDev(void *arg)
{
    interface_t *iface = (interface_t *) arg;
    interface_array_t *iarr = (interface_array_t *)iface->iarray;
    uchar bcast_mac[] = MAC_BCAST_ADDR;
    gpacket_t *in_pkt;
    int pktsize;
    char tmpbuf[MAX_TMPBUF_LEN];
    
    pthread_setcanceltype(PTHREAD_CANCEL_ASYNCHRONOUS, NULL);		// die as soon as cancelled
    while (1)
    {
        verbose(2, "[fromTunDev]:: Receiving a packet ...");
        if ((in_pkt = (gpacket_t *)malloc(sizeof(gpacket_t))) == NULL)
        {
            fatal("[fromTunDev]:: unable to allocate memory for packet.. ");
            return NULL;
        }

        bzero(in_pkt, sizeof(gpacket_t));
        pktsize = tun_recvfrom(iface->vpl_data, &(in_pkt->data), sizeof(pkt_data_t));
        pthread_testcancel();
        
        verbose(2, "[fromTunDev]:: Destination MAC is %s ", MAC2Colon(tmpbuf, in_pkt->data.header.dst));
      
        // In OpenFlow mode the gRouter is an L2 SWITCH, so it must accept frames whose
        // destination MAC is some other host (that's the whole point of switching). Only
        // apply the "is it for me?" router filter when NOT acting as an OpenFlow switch.
        // (fromEthernetDev has the identical guard; the tun path needs it too.)
        if (!(sdn_mode() == SDN_MODE_OPENFLOW) &&
                (COMPARE_MAC(in_pkt->data.header.dst, iface->mac_addr) != 0) &&
                (COMPARE_MAC(in_pkt->data.header.dst, bcast_mac) != 0))
        {
            verbose(1, "[fromTunDev]:: Packet[%d] dropped .. not for this router!? ", pktsize);
            free(in_pkt);
            continue;
        }

        // OpenFlow-switch safety filter.
        //
        // This used to drop EVERY ethertype except IPv4 and ARP. That silently broke
        // topology discovery: openflow.discovery probes links with LLDP (0x88CC), and
        // dropping it here meant no LLDP ever reached the flow table, so no LinkEvent
        // ever fired and any controller app needing a network-wide view
        // (forwarding.l2_multi, openflow.spanning_tree) sat with an empty topology
        // doing nothing. It would have blocked 802.1Q work for the same reason.
        //
        // The real hazard the original guard was reaching for is the 802.3/LLC branch
        // of openflow_flowtable_match_packet(): for prot < OFP_DL_TYPE_ETH2_CUTOFF it
        // indexes into data[] using the frame's OWN contents. Ethernet II types above
        // the cutoff are safe -- the matcher's IP and ARP parsing is guarded by explicit
        // prot checks, 802.1Q has its own handling, and anything else (LLDP included)
        // simply falls through with dl_type set and the IP fields left at zero.
        //
        // So: pass the ethertypes the matcher demonstrably handles, and keep dropping
        // the 802.3/LLC range that actually walks the payload.
        if (sdn_mode() == SDN_MODE_OPENFLOW)
        {
            uint16_t ethertype = ntohs(in_pkt->data.header.prot);
            if (ethertype < OFP_DL_TYPE_ETH2_CUTOFF)
            {
                /* 802.3/LLC: the matcher parses the payload to find a type. Not needed
                 * by any GINI lab, and the one shape with real out-of-bounds risk. */
                verbose(2, "[fromTunDev]:: Dropped 802.3/LLC frame (len/type 0x%04x)"
                        " in OpenFlow mode.", ethertype);
                free(in_pkt);
                continue;
            }
        }

        // copy fields into the message from the packet..
        in_pkt->frame.src_interface = iface->interface_id;
        COPY_MAC(in_pkt->frame.src_hw_addr, iface->mac_addr);
        COPY_IP(in_pkt->frame.src_ip_addr, iface->ip_addr);

        // check for filtering.. if the it should be filtered.. then drop
        if (filteredPacket(filter, in_pkt))
        {
            verbose(2, "[fromTunDev]:: Packet filtered..!");
            free(in_pkt);
            continue;   // skip the rest of the loop
        }

        verbose(2, "[fromTunDev]:: Packet is sent for enqueuing..");
        enqueuePacket(pcore, in_pkt, sizeof(gpacket_t), (sdn_mode() == SDN_MODE_OPENFLOW));
    }
}


/*
 * Connect to the tun interface.
 */

vpl_data_t *tun_connect(short int src_port, uchar* src_IP,
                        short int dst_port, uchar* dst_IP)
{
    int fd;
    int dst_ip;
    verbose(2, "[tun_connect]:: starting connection.. ");
    vpl_data_t *pri = (vpl_data_t *)malloc(sizeof(vpl_data_t));
    bzero(pri, sizeof(sizeof(vpl_data_t))); 
    
    COPY_IP(&dst_ip, dst_IP);
    // initialize the vpl_data structure.. much of it is unused here.
    // we are reusing vpl_data_t to minimize the changes for other code.
    pri->sock_type = "tun";
    pri->ctl_sock = NULL;
    pri->ctl_addr = NULL;
    pri->data_addr = NULL;
    pri->local_addr = NULL;
    pri->data = -1;
    pri->control = -1;

    fd = socket(AF_INET,SOCK_DGRAM,0);	
    if (fd == -1) {
            verbose(2, "[tun_connect]:: Creating UDP socket failed, error = %s", strerror(errno));		
            return NULL;
    }

    struct sockaddr_in* srcaddr = (struct sockaddr_in*)malloc(sizeof(struct sockaddr_in));
    bzero(srcaddr,sizeof(*srcaddr));
    srcaddr->sin_family = AF_INET;
    srcaddr->sin_addr.s_addr = htonl(INADDR_ANY);
    srcaddr->sin_port=htons(src_port);
    if(bind(fd,(struct sockaddr *)srcaddr,sizeof(*srcaddr))== -1)
    {
        verbose(2, "[tun_connect]:: Binding UDP Socket Failed, error = %s", strerror(errno));		
        free(srcaddr);
        return NULL;
    }

    struct sockaddr_in* dstaddr = (struct sockaddr_in*)malloc(sizeof(struct sockaddr_in));
    bzero(dstaddr,sizeof(*dstaddr));
    dstaddr->sin_family = AF_INET;
    dstaddr->sin_addr.s_addr=htonl(dst_ip);
    verbose(2, "destination IP = %s\n",  inet_ntoa(dstaddr->sin_addr));
    dstaddr->sin_port=htons(dst_port);

    pri->data = fd;
    pri->local_addr = (void*)srcaddr;
    pri->data_addr = (void*)dstaddr;

    return pri;
}

int tun_recvfrom(vpl_data_t *vpl, void *buf, int len)
{
    int n, rcv_addr_len;
    struct sockaddr_in* dstaddr = (struct sockaddr_in*)vpl->data_addr;
    struct sockaddr_in rcvaddr;
    char tmpbuf[100];
    
    rcv_addr_len = sizeof(rcvaddr);
    n=recvfrom(vpl->data,buf,len,0,(struct sockaddr *)&rcvaddr,&rcv_addr_len);
    if (n == -1)
    {
        verbose(2, "[tun_recvfrom]:: unable to receive packet, error = %s", strerror(errno));
        return EXIT_FAILURE;
    }
    /*
     * Each tun interface is a dedicated point-to-point UDP link, so only the peer
     * sends here. We deliberately do NOT reject on a sender-address mismatch: in a
     * dynamic fabric (Docker) the peer's address is resolved at startup and may differ
     * slightly from the datagram's observed source (DNS timing, NAT). Dropping on a
     * mismatch silently breaks the whole link. Note a mismatch at high verbosity only.
     */
    if ((rcvaddr.sin_addr.s_addr != dstaddr->sin_addr.s_addr) ||
        rcvaddr.sin_port != dstaddr->sin_port)
        verbose(3, "[tun_recvfrom]:: sender differs from configured peer (accepting anyway)");

    verbose(2, "[tun_recvfrom]:: Destination MAC is %s ", MAC2Colon(tmpbuf, buf));
    return EXIT_SUCCESS;
        
}

int tun_sendto(vpl_data_t *vpl, void *buf, int len)
{
    int n;
    struct sockaddr_in* dstaddr = (struct sockaddr_in*)vpl->data_addr;
    
    n = sendto(vpl->data,buf,len,0,
             (struct sockaddr*)dstaddr,sizeof(*dstaddr));
    if (n == -1) 
    {
	verbose(2, "[tun_sendto]:: unable to send packet, error = %s", strerror(errno));		
	return EXIT_FAILURE;
    }
       
    return EXIT_SUCCESS;
}


