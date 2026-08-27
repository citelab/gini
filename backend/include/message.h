/*
 * message.h (header for the messaging layer that goes with the modules)
 * AUTHOR: Muthucumaru Maheswaran
 * VERSION: Beta
 * DATE: December 19, 2004
 *
 */

#ifndef __MESSAGE_H__
#define __MESSAGE_H__

#include <sys/types.h>
#include "grouter.h"
#include <stdint.h>


#define MAX_IPREVLENGTH_ICMP            50       // maximum previous header sent back


#define MAX_MESSAGE_SIZE                sizeof(gpacket_t)



// this is just an ethernet frame with
// a maximum payload definition.
// (TODO: revise it to use standard structures)
typedef struct _pkt_data_t
{
	struct
	{
		uchar dst[6];                // destination host's MAC address (filled by gnet)
		uchar src[6];                // source host's MAC address (filled by gnet)
		ushort prot;                // protocol field
	} header;
	uchar data[DEFAULT_MTU];             // payload (limited to maximum MTU)
	int8_t pad[4];					// VLAN padding
} pkt_data_t;

/**
 * Ethernet frame with VLAN (802.1Q) support
 */
typedef struct
{
	struct
	{
		uint8_t dst[6];
		uint8_t src[6];
		uint16_t tpid;
		uint16_t tci;
		uint16_t prot;
	} header;
	uint8_t data[DEFAULT_MTU];
} pkt_data_vlan_t;

// frame wrapping every packet... GINI specific (GINI metadata)
typedef struct _pkt_frame_t
{
	int src_interface;               // incoming interface number; filled in by gnet?
	uchar src_ip_addr[4];            // source IP address; required for ARP, IP, gnet
	uchar src_hw_addr[6];            // source MAC address; required for ARP, filled by gnet
	int dst_interface;               // outgoing interface, required by gnet; filled in by IP, ARP
	uchar nxth_ip_addr[4];           // destination interface IP address; required by ARP, filled IP
	int arp_valid;
	int arp_bcast;
	int openflow;
	// True on-the-wire byte count of `data`, when the producer knows it (frames read
	// off an interface, and frames injected by an OpenFlow controller via PACKET_OUT).
	// 0 = unknown, in which case the send path falls back to findPacketSize().
	//
	// This exists because findPacketSize() can only derive a length for IP and ARP and
	// returns sizeof(pkt_data_t) — 1518 bytes — for anything else. That made every
	// non-IP frame go out padded to the full struct, which both put ~1450 bytes of
	// uninitialized heap on the wire and broke LLDP: the oversized datagrams never
	// arrived, so openflow.discovery found no links and every network-wide controller
	// app (forwarding.l2_multi, openflow.spanning_tree) silently did nothing.
	// Never transmitted — only `data` goes on the wire.
	int pkt_len;
} pkt_frame_t;


typedef struct _gpacket_t
{
	pkt_frame_t frame;
	pkt_data_t data;
} gpacket_t;


gpacket_t *duplicatePacket(gpacket_t *inpkt);
void printSepLine(char *start, char *end, int count, char sep);
void printGPktFrame(gpacket_t *msg, char *routine);
void printGPacket(gpacket_t *msg, int level, char *routine);
void printGPktPayload(gpacket_t *msg, int level);
int printEthernetHeader(gpacket_t *msg);

int printIPPacket(gpacket_t *msg);
void printARPPacket(gpacket_t *msg);
void printICMPPacket(gpacket_t *msg);
void printUDPPacket(gpacket_t *msg);
void printTCPPacket(gpacket_t *msg);

#endif
