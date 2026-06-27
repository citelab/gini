/*
 * ip.c (collection of functions that implement the IP (Internet protocol).
 * AUTHOR: Original version by Weiling Xu
 *         Revised by Muthucumaru Maheswaran
 * DATE:   Last revised on June 22, 2008
 */

#include "message.h"
#include "grouter.h"
#include "routetable.h"
#include "gr_state.h"   /* Z1: locked route/ARP accessors (race fix) */
#include "host_stack.h" /* Z1: sealed, optional lwIP host stack */
#include "gr_pipeline.h" /* Z2: inline module-graph runner */
#include "gr_control_plane.h" /* B2: control-plane module receive hook */
#include "gr_mcast.h"         /* B3: multicast membership table */
#include "mtu.h"
#include "protocols.h"
#include "ip.h"
#include "tcp.h"
#include "tcp_impl.h"
#include "udp.h"
#include "icmp.h"
#include "fragment.h"
#include "packetcore.h"
#include <stdlib.h>
#include <slack/err.h>
#include <netinet/in.h>
#include <string.h>

#include <slack/std.h>
#include <slack/prog.h>

extern pktcore_t *pcore;

void IPProcessMulticast(gpacket_t *in_pkt);                 /* B3 */
static int ip_directed_bcast_iface(uchar *dst, int *iface); /* B3 */

void IPInit()
{
	RouteTableInit(route_tbl);
	MTUTableInit(MTU_tbl);
	gr_mcast_init();                                       /* B3: multicast membership */
}


/*
 * IPIncomingPacket: Process incoming IP packet.
 * The IP packet can be destined to the local router (for example route updates).
 * Or it could be a packet meant for forwarding: either unicast or multicast/broadcast.
 * This is a wrapper routine that calls the appropriate subroutine to take
 * the appropriate function.
 */
void IPIncomingPacket(gpacket_t *in_pkt)
{
	char tmpbuf[MAX_TMPBUF_LEN];

	// get a pointer to the IP packet
    ip_packet_t *ip_pkt = (ip_packet_t *)&in_pkt->data.data;
	uchar bcast_ip[] = IP_BCAST_ADDR;

	// Is this IP packet for me??
	if (IPCheckPacket4Me(in_pkt))
	{
		verbose(2, "[IPIncomingPacket]:: got IP packet destined to this router");
		IPProcessMyPacket(in_pkt);
	} else if ((gNtohl(tmpbuf, ip_pkt->ip_dst)[0] & 0xf0) == 0xe0)
	{
		// B3: class-D destination (224.0.0.0/4) -> multicast handling
		verbose(2, "[IPIncomingPacket]:: got a multicast packet");
		IPProcessMulticast(in_pkt);
	} else if (COMPARE_IP(gNtohl(tmpbuf, ip_pkt->ip_dst), bcast_ip) == 0)
	{
		// TODO: rudimentary 'broadcast IP address' check
		verbose(2, "[IPIncomingPacket]:: not repeat broadcast (final destination %s), packet thrown",
		       IP2Dot(tmpbuf, gNtohl((tmpbuf+20), ip_pkt->ip_dst)));
		IPProcessBcastPacket(in_pkt);
	} else
	{
		// Destinated to someone else
		verbose(2, "[IPIncomingPacket]:: got IP packet destined to someone else");
		IPProcessForwardingPacket(in_pkt);
	}
}



/*
 * IPCheckPacket4Me: Return TRUE if the packet is meant for me. Otherwise return FALSE.
 * Check against all possible IPs I have to determine whether this packet
 * is meant for me.
 */
int IPCheckPacket4Me(gpacket_t *in_pkt)
{
	ip_packet_t *ip_pkt = (ip_packet_t *)&in_pkt->data.data;
	char tmpbuf[MAX_TMPBUF_LEN];
	int count, i;
	uchar iface_ip[MAX_MTU][4];
	uchar pkt_ip[4];

	COPY_IP(pkt_ip, gNtohl(tmpbuf, ip_pkt->ip_dst));
	verbose(2, "[IPCheckPacket4Me]:: looking for IP %s ", IP2Dot(tmpbuf, pkt_ip));
	if ((count = findAllInterfaceIPs(MTU_tbl, iface_ip)) > 0)
	{
		for (i = 0; i < count; i++)
		{
			if (COMPARE_IP(iface_ip[i], pkt_ip) == 0)
			{
				verbose(2, "[IPCheckPacket4Me]:: found a matching IP.. for %s ", IP2Dot(tmpbuf, pkt_ip));
				return TRUE;
			}
		}
		return FALSE;
	} else
		return FALSE;
}



/*
 * TODO: broadcast not yet implemented.. should be simple to implement.
 * read RFC 1812 and 922 ...
 */
int IPProcessBcastPacket(gpacket_t *in_pkt)
{
	/* B2: broadcast/link-local-multicast control traffic (DHCP DISCOVER to
	 * 255.255.255.255, routing-protocol hellos to 224.0.0.x) is offered to the
	 * control plane here. gr_cp_deliver copies on a filter match; it does not take
	 * ownership, so the existing (no-op) memory behaviour is unchanged. */
	gr_cp_deliver(in_pkt);
	return EXIT_SUCCESS;
}


/*
 * B3: forward a multicast (class-D) datagram. Link-local control groups (224.0.0.x, e.g.
 * IGMP membership reports) are offered to the control plane so the IGMP-snoop module can
 * learn memberships; routable groups are replicated to every interface that has a member,
 * except the one the packet arrived on. With no members, the packet is dropped.
 */
void IPProcessMulticast(gpacket_t *in_pkt)
{
	char tmpbuf[MAX_TMPBUF_LEN];
	ip_packet_t *ip_pkt = (ip_packet_t *)&in_pkt->data.data;
	uchar grp[4];
	uint32_t mask;
	int i;

	COPY_IP(grp, gNtohl(tmpbuf, ip_pkt->ip_dst));   /* group address (host order) */

	gr_cp_deliver(in_pkt);                           /* let IGMP snoop / control see it */

	mask = gr_mcast_lookup(grp);
	if (mask == 0)                                   /* no members anywhere */
	{
		free(in_pkt);
		return;
	}

	for (i = 0; i < 32; i++)
	{
		gpacket_t *cp;
		ip_packet_t *cip;
		if (!(mask & (1u << i))) continue;
		if (i == in_pkt->frame.src_interface) continue;   /* don't echo to the source LAN */
		if ((cp = duplicatePacket(in_pkt)) == NULL) continue;
		cip = (ip_packet_t *)&cp->data.data;
		cip->ip_ttl -= 1;                            /* this is a hop */
		cip->ip_cksum = 0;
		cip->ip_cksum = htons(checksum((uchar *)cip, cip->ip_hdr_len * 2));
		cp->frame.dst_interface = i;
		cp->frame.arp_bcast = TRUE;                  /* MAC set below; GNET sends as-is */
		/* IPv4 multicast MAC: 01:00:5e + low 23 bits of the group */
		cp->data.header.dst[0] = 0x01; cp->data.header.dst[1] = 0x00; cp->data.header.dst[2] = 0x5e;
		cp->data.header.dst[3] = grp[1] & 0x7f;
		cp->data.header.dst[4] = grp[2];
		cp->data.header.dst[5] = grp[3];
		cp->data.header.prot = htons(IP_PROTOCOL);
		IPSend2Output(cp);
	}
	free(in_pkt);
}


/* B3: if dst is the all-ones host address of one of our connected /24s, return 1 and the
 * interface index; used to forward a directed broadcast onto that subnet. */
static int ip_directed_bcast_iface(uchar *dst, int *iface)
{
	int i;
	uchar ip[4];
	for (i = 0; i < MAX_MTU; i++)
	{
		if (findInterfaceIP(MTU_tbl, i, ip) != EXIT_SUCCESS) continue;
		if (ip[0] == dst[0] && ip[1] == dst[1] && ip[2] == dst[2] && dst[3] == 255)
		{
			*iface = i;
			return 1;
		}
	}
	return 0;
}


/*
 * process an IP packet destined to someone else...
 * ARGUMENT: in_pkt - pointer to incoming packet
 *
 * Error processing: Check for conditions that generate ICMP packets.
 * For example, TTL expired, redirect, mulformed packets, ...
 * DF set and fragment,.. etc.
 *
 * Fragment processing: Check whether fragment is necessary .. condition already checked.
 *
 * Forward packet and fragments (could be multicasting)
 */
int IPProcessForwardingPacket(gpacket_t *in_pkt)
{
	gpacket_t *pkt_frags[MAX_FRAGMENTS];
	ip_packet_t *ip_pkt = (ip_packet_t *)in_pkt->data.data;
	int num_frags, i, need_frag;
	char tmpbuf[MAX_TMPBUF_LEN];

	verbose(2, "[IPProcessForwardingPacket]:: checking for any IP errors..");
	// all the validation and ICMP generation, processing is
	// done in this function...
	if (IPCheck4Errors(in_pkt) == EXIT_FAILURE)
		return EXIT_FAILURE;


	// Z2: run the inline module pipeline (ACL / NAT / QoS / Lua / native — the Router
	// Lab "drops") after parse, before route lookup. Empty pipeline -> CONTINUE ->
	// base forwarding unchanged. (Legacy / NORMAL path; OpenFlow mode is the ingress
	// front door per sdn.h.)
	{
		gr_verdict_t _gv = gr_pipeline_run(gr_default_pipeline(), in_pkt);
		if (_gv.action == GR_DROP)
			return EXIT_FAILURE;
		if (_gv.action != GR_CONTINUE)
			return EXIT_SUCCESS;   /* a module took ownership of the packet */
	}

	// B3: directed broadcast — dst is the all-ones host address of a connected /24. Forward
	// it onto that subnet as a link-layer broadcast (unless it arrived from there). This is
	// what makes the cross-subnet smurf experiment work; real edge routers disable it.
	{
		int dbif;
		if (ip_directed_bcast_iface(gNtohl(tmpbuf, ip_pkt->ip_dst), &dbif) &&
		    dbif != in_pkt->frame.src_interface)
		{
			ip_pkt->ip_cksum = 0;
			ip_pkt->ip_cksum = htons(checksum((uchar *)ip_pkt, ip_pkt->ip_hdr_len * 2));
			in_pkt->frame.dst_interface = dbif;
			in_pkt->frame.arp_bcast = TRUE;
			memset(in_pkt->data.header.dst, 0xff, 6);   /* L2 broadcast */
			in_pkt->data.header.prot = htons(IP_PROTOCOL);
			IPSend2Output(in_pkt);
			return EXIT_SUCCESS;
		}
	}

	// find the route... if it does not exist, should we send a
	// ICMP network/host unreachable message -- CHECK??
	if (gr_route_lookup(gNtohl(tmpbuf, ip_pkt->ip_dst),
			   in_pkt->frame.nxth_ip_addr,
			   &(in_pkt->frame.dst_interface)) == EXIT_FAILURE)
		return EXIT_FAILURE;

	// check for redirection?? -- the output interface is already found
	// by the previous command.. if needed the following routine sends the
	// redirects but the packet is sent to destination..
	// TODO: Check the RFC for conformance??
	IPCheck4Redirection(in_pkt);

	// check for fragmentation -- this should return three conditions:
	// FRAGS_NONE, FRAGS_ERROR, MORE_FRAGS
	need_frag = IPCheck4Fragmentation(in_pkt);

	switch (need_frag)
	{
	case FRAGS_NONE:
		verbose(2, "[IPProcessForwardingPacket]:: sending packet to GNET..");
		// compute the checksum before sending out.. the fragmentation routine does this inside it.
		ip_pkt->ip_cksum = 0;
		ip_pkt->ip_cksum = htons(checksum((uchar *)ip_pkt, ip_pkt->ip_hdr_len *2));
		if (IPSend2Output(in_pkt) == EXIT_FAILURE)
		{
			verbose(1, "[IPProcessForwardingPacket]:: WARNING: IPProcessForwardingPacket(): Could not forward packets ");
			return EXIT_FAILURE;
		}
		break;

	case FRAGS_ERROR:
		verbose(2, "[IPProcessForwardingPacket]:: unreachable on packet from %s",
			IP2Dot(tmpbuf, gNtohl((tmpbuf+20), ip_pkt->ip_src)));
		int int_mtu = findMTU(MTU_tbl, in_pkt->frame.dst_interface);
		ICMPProcessFragNeeded(in_pkt, int_mtu);
		break;

	case MORE_FRAGS:
		// fragment processing...
		num_frags = fragmentIPPacket(in_pkt, pkt_frags);

		verbose(2, "[IPProcessForwardingPacket]:: IP packet needs fragmentation");
		// forward each fragment
		for (i = 0; i < num_frags; i++)
		{
			if (IPSend2Output(pkt_frags[i]) == EXIT_FAILURE)
			{
				verbose(1, "[IPProcessForwardingPacket]:: processForwardIPPacket(): Could not forward packets ");
				return EXIT_FAILURE;
			}
		}
		deallocateFragments(pkt_frags, num_frags);
		break;
	default:
		return EXIT_FAILURE;
	}
	return EXIT_SUCCESS;
}


int IPCheck4Errors(gpacket_t *in_pkt)
{
	char tmpbuf[MAX_TMPBUF_LEN];
	ip_packet_t *ip_pkt = (ip_packet_t *)in_pkt->data.data;

	// check for valid version and checksum.. silently drop the packet if not.
	if (IPVerifyPacket(ip_pkt) == EXIT_FAILURE)
		return EXIT_FAILURE;

	// Decrement TTL, if TTL <= 0, send to ICMP module with TTL-expired command
	// return EXIT_FAILURE
	if (--ip_pkt->ip_ttl <= 0)
	{
		verbose(2, "[processIPErrors]:: TTL expired on packet from %s",
		       IP2Dot(tmpbuf, gNtohl((tmpbuf+20), ip_pkt->ip_src)));

		ICMPProcessTTLExpired(in_pkt);
		return EXIT_FAILURE;
	}

	return EXIT_SUCCESS;
}



/*
 * check for MTU sizes and DF flag..
 * first get the MTU value for the next hop interface.
 * if the current packet size is greater than the next hop MTU, then
 * fragmentation is needed. If the DF is set and fragmentation is
 * needed, an error condition occurs.
 * FRAGS_NONE - no fragmentation;
 * FRAGS_ERROR - fragmentation error;
 * MORE_FRAGS - fragmentation is required.
 * GENERAL_ERROR - mtu not found.
 */
int IPCheck4Fragmentation(gpacket_t *in_pkt)
{
	int link_mtu;
	char tmpbuf[MAX_TMPBUF_LEN];
	ip_packet_t *ip_pkt = (ip_packet_t *)in_pkt->data.data;

	verbose(2, "[IPCheck4Fragmentation]:: .. checking mtu for next hop %s and interface %d ",
		IP2Dot(tmpbuf, in_pkt->frame.nxth_ip_addr), in_pkt->frame.dst_interface);

	if ((link_mtu = findMTU(MTU_tbl, in_pkt->frame.dst_interface)) < 0)
		return GENERAL_ERROR;

	if (link_mtu < ntohs(ip_pkt->ip_pkt_len))                 // need fragmentation
	{
		if (TEST_DF_BITS(ip_pkt->ip_frag_off))    // DF is set: destination unreachable
			return FRAGS_ERROR;
		return MORE_FRAGS;
	} else
		return FRAGS_NONE;
}



/*
 * check for redirection condition. This function always returns
 * success. That is no matter whether redirection was sent or not
 * it returns success!
 */
int IPCheck4Redirection(gpacket_t *in_pkt)
{
	char tmpbuf[MAX_TMPBUF_LEN];
	gpacket_t *cp_pkt;
	ip_packet_t *ip_pkt = (ip_packet_t *)in_pkt->data.data;

	// check for redirect condition and send an ICMP back... let the current packet
	// go as well (check the specification??)
	if (isInSameNetwork(gNtohl(tmpbuf, ip_pkt->ip_src), in_pkt->frame.nxth_ip_addr) == EXIT_SUCCESS)
	{
		verbose(2, "[processIPErrors]:: redirect message sent on packet from %s",
		       IP2Dot(tmpbuf, gNtohl((tmpbuf+20), ip_pkt->ip_src)));

		cp_pkt = duplicatePacket(in_pkt);

		ICMPProcessRedirect(cp_pkt, cp_pkt->frame.nxth_ip_addr);
	}

	// IP packet is verified to be good. This packet should be
	// further processed to carry out forwarding.
	return EXIT_SUCCESS;
}



/*
 * process an IP packet destined to the router itself
 * ARGUMENT: in_pkt points to the message containing the packet
 * RETURNS: EXIT_FAILURE or EXIT_SUCCESS;
 *
 * Processing flow is as follows:
 *      Error processing: A similar routine as the "forwarding mode"
 *      Control packet processing: ICMP processing.. send it to the
 *                                 ICMP module which is going to decode the
 *                                 packet further.
 *      Information packet processing: These are UDP/TCP packets destined
 *                                 to the router. They contain route
 *                                 updates.. mainly driven by routing algorithms
 */
int IPProcessMyPacket(gpacket_t *in_pkt)
{
	ip_packet_t *ip_pkt = (ip_packet_t *)in_pkt->data.data;

	if (IPVerifyPacket(ip_pkt) == EXIT_SUCCESS)
	{
		// B2: offer router-bound packets to the control plane (a routing protocol's
		// packets, DHCP unicast renewals). gr_cp_deliver copies on a filter match and
		// does not consume, so the existing ICMP/host-stack handling below is unchanged.
		gr_cp_deliver(in_pkt);

		// Is packet ICMP? send it to the ICMP module
		// further processing with appropriate type code

		if (ip_pkt->ip_prot == ICMP_PROTOCOL) {
			ICMPProcessPacket(in_pkt);
		  return EXIT_SUCCESS;
        }

		// UDP/TCP addressed to the router -> the (optional) host stack (lwIP),
		// sealed behind host_stack_input(). Pure forwarder: -DGR_NO_HOST_STACK.
		if (host_stack_input(in_pkt))
			return EXIT_SUCCESS;

	}
	return EXIT_FAILURE;
}


/* UDPProcess/TCPProcess moved to host_stack.c (Z1: lwIP sealed + optional via -DGR_NO_HOST_STACK) */


/*
 * this function processes the IP packets that are reinjected into the
 * IP layer by ICMP, UDP, and other higher-layers.
 * There can be two scenarios. The packet can be a reply for an original
 * query OR it can be a new one. The processing performed by this function depends
 * on the packet type..
 * IMPORTANT: src_prot is the source protocol number.
 */
int IPOutgoingPacket(gpacket_t *pkt, uchar *dst_ip, int size, int newflag, int src_prot)
{
    ip_packet_t *ip_pkt = (ip_packet_t *)pkt->data.data;
	ushort cksum;
	char tmpbuf[MAX_TMPBUF_LEN];
	uchar iface_ip_addr[4];
	int status;


	ip_pkt->ip_ttl = 64;                        // set TTL to default value
	ip_pkt->ip_cksum = 0;                       // reset the checksum field
	ip_pkt->ip_prot = src_prot;  // set the protocol field

	if (newflag == 0)
	{
		COPY_IP(ip_pkt->ip_dst, ip_pkt->ip_src); 		    // set dst to original src
		COPY_IP(ip_pkt->ip_src, gHtonl(tmpbuf, pkt->frame.src_ip_addr));    // set src to me

		// find the nexthop and interface and fill them in the "meta" frame
		// NOTE: the packet itself is not modified by this lookup!
		if (gr_route_lookup(gNtohl(tmpbuf, ip_pkt->ip_dst),
				   pkt->frame.nxth_ip_addr, &(pkt->frame.dst_interface)) == EXIT_FAILURE)
				   return EXIT_FAILURE;

	} else if (newflag == 1)
	{
		// non REPLY PACKET -- this is a new packet; set all fields
		ip_pkt->ip_version = 4;
		ip_pkt->ip_hdr_len = 5;
		ip_pkt->ip_tos = 0;
		ip_pkt->ip_identifier = IP_OFFMASK & random();
		RESET_DF_BITS(ip_pkt->ip_frag_off);
		RESET_MF_BITS(ip_pkt->ip_frag_off);
		ip_pkt->ip_frag_off = 0;

		COPY_IP(ip_pkt->ip_dst, gHtonl(tmpbuf, dst_ip));
		ip_pkt->ip_pkt_len = htons(size + ip_pkt->ip_hdr_len * 4);

		verbose(2, "[IPOutgoingPacket]:: lookup next hop ");
		// find the nexthop and interface and fill them in the "meta" frame
		// NOTE: the packet itself is not modified by this lookup!
		if (gr_route_lookup(gNtohl(tmpbuf, ip_pkt->ip_dst), pkt->frame.nxth_ip_addr, &(pkt->frame.dst_interface)) == EXIT_FAILURE) {
            return EXIT_FAILURE;
        }

		verbose(2, "[IPOutgoingPacket]:: lookup MTU of nexthop");
		// lookup the IP address of the destination interface..
		if ((status = findInterfaceIP(MTU_tbl, pkt->frame.dst_interface,
					      iface_ip_addr)) == EXIT_FAILURE)
					      return EXIT_FAILURE;
		// the outgoing packet should have the interface IP as source
		COPY_IP(ip_pkt->ip_src, gHtonl(tmpbuf, iface_ip_addr));
		verbose(2, "[IPOutgoingPacket]:: almost one processing the IP header.");
	} else
	{
		error("[IPOutgoingPacket]:: unknown outgoing packet action.. packet discarded ");
		return EXIT_FAILURE;
	}

	//	compute the new checksum
	cksum = checksum((uchar *)ip_pkt, ip_pkt->ip_hdr_len*2);
	ip_pkt->ip_cksum = htons(cksum);
	pkt->data.header.prot = htons(IP_PROTOCOL);

	IPSend2Output(pkt);
	verbose(2, "[IPOutgoingPacket]:: IP packet sent to output queue.. ");
	return EXIT_SUCCESS;
}



/*
 * IPSend2Output - write to the output Queue..
 */
int IPSend2Output(gpacket_t *pkt)
{
	int vlevel;

	if (pkt == NULL)
	{
		verbose(1, "[IPSend2Output]:: NULL pointer error... nothing sent");
		return EXIT_FAILURE;
	}

	vlevel = prog_verbosity_level();
	if (vlevel >= 3)
		printGPacket(pkt, vlevel, "IP_ROUTINE");

	return writeQueue(pcore->outputQ, (void *)pkt, sizeof(gpacket_t));
}



/*
 * check whether the IP packet has correct checksum and
 * version number... this router is hard coded for IP version 4!
 * NOTE: we don't send any ICMP error messages to the source - instead
 * we silently drop the packet. It seems (should check carefully) that
 * ICMP does not have a facility to report this kind of condition.
 * May be this condition is not likely to happen???
 */
int IPVerifyPacket(ip_packet_t *ip_pkt)
{
	char tmpbuf[MAX_TMPBUF_LEN];
	int hdr_len = ip_pkt->ip_hdr_len;

	// verify the header checksum
	if (checksum((void *)ip_pkt, hdr_len *2) != 0)
	{
		verbose(2, "[IPVerifyPacket]:: packet from %s failed checksum, packet thrown",
		       IP2Dot(tmpbuf, gNtohl((tmpbuf+20), ip_pkt->ip_src)));
		return EXIT_FAILURE;
	}

	// Check correct IP version
	if (ip_pkt->ip_version != 4)
	{
		verbose(2, "[IPVerifyPacket]:: from %s failed checksum, packet thrown",
		       IP2Dot(tmpbuf, gNtohl((tmpbuf + 20), ip_pkt->ip_src)));
		return EXIT_FAILURE;
	}

	return EXIT_SUCCESS;
}


/*
 * Checks if two IP addresses are on the same network
 * returns: EXIT_FAILURE if not and EXIT_SUCCESS if they are
 */
int isInSameNetwork(uchar *ip_addr1, uchar *ip_addr2)
{
	char tmpbuf[MAX_TMPBUF_LEN];
	int i, j;
	uchar net1[4], net2[4];

	for (i = 0; i < MAX_ROUTES; i++)
	{
		if (route_tbl[i].is_empty == TRUE) continue;
		// Skip the default route (netmask 0.0.0.0): it masks every address to 0.0.0.0,
		// which would make ANY two IPs look "on the same network" and trigger spurious
		// ICMP redirects on every forwarded packet. Only real subnets count here.
		if ((route_tbl[i].netmask[0] | route_tbl[i].netmask[1] |
		     route_tbl[i].netmask[2] | route_tbl[i].netmask[3]) == 0) continue;
		for (j = 0; j < 4; j++)
		{
			net1[j] = ip_addr1[j] & route_tbl[i].netmask[j];
			net2[j] = ip_addr2[j] & route_tbl[i].netmask[j];
		}
		if (COMPARE_IP(net1, net2) == 0)
		{
			verbose(2, "[isInSameNetwork]:: IPs %s and %s are on the same network %s",
			       IP2Dot(tmpbuf, ip_addr1), IP2Dot((tmpbuf+20), ip_addr2), IP2Dot((tmpbuf+40), route_tbl[i].network));

			return EXIT_SUCCESS;
		}
	}

	verbose(2, "[isInSameNetwork]:: IPs %s and %s are not on the same network",
	       IP2Dot(tmpbuf, ip_addr1), IP2Dot((tmpbuf+20), ip_addr2));

	return EXIT_FAILURE;
}

uchar ip_addr_isany(uchar *addr)
{
  if (addr == NULL) return 1;
  return((addr[0] | addr[1] | addr[2] | addr[3]) == 0);
}

uchar ip_addr_cmp(uchar *addr1, uchar *addr2)
{
  return(addr1[0] == addr2[0] &&
         addr1[1] == addr2[1] &&
         addr1[2] == addr2[2] &&
         addr1[3] == addr2[3]);
}

uchar ip_addr_netcmp(uchar *addr1, uchar *addr2, uchar *mask)
{
  return((addr1[0] & mask[0]) == (addr2[0] & mask[0]) &&
         (addr1[1] & mask[1]) == (addr2[1] & mask[1]) &&
         (addr1[2] & mask[2]) == (addr2[2] & mask[2]) &&
         (addr1[3] & mask[3]) == (addr2[3] & mask[3]));
}

void print_ip4(uchar *addr) {
    unsigned char bytes[4];
    bytes[0] = addr[0] & 0xFF;
    bytes[1] = addr[1] & 0xFF;
    bytes[2] = addr[2] & 0xFF;
    bytes[3] = addr[3] & 0xFF;
    printf("%d.%d.%d.%d\n", bytes[3], bytes[2], bytes[1], bytes[0]);
}

void ip_addr_set(uchar *dest, uchar *src) {
    memcpy(dest, src, 4 * sizeof(unsigned short));
}

/*
 * convert uchar[4] to int
 */
u32_t ip4_addr_get_u32(uchar *src) {
    return (src[0] << 24) | (src[1] << 16) | (src[2] << 8) | src[3];
}

/*
 * converts LWIP's ip_output() function to GINI's IPOutgoingPacket()
 */
err_t
ip_output(struct pbuf *p, uchar *src_ip, uchar *dst_ip, u8_t ttl, u8_t tos, int src_prot) {
    // create GINI's gpacket_t
	gpacket_t *out_pkt = (gpacket_t *) malloc(sizeof(gpacket_t));
    if (out_pkt == NULL) {
        printf("could not allocate gpacket_t\n");
        return ERR_MEM;
    }

    // write pbuf's payload to GINI's gpacket_t, at the correct offset
    int offset = sizeof(ip_packet_t);
    memcpy((void*)((uchar*)out_pkt->data.data + offset), p->payload, p->len);

    // call IP function
    int res = IPOutgoingPacket(out_pkt, dst_ip, p->len, 1, src_prot);
    return res;
}
