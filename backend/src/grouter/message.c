#include <stdio.h>
#include <stdlib.h>
#include <netinet/in.h>
#include <netinet/ip.h>
#include "grouter.h"
#include "message.h"
#include "protocols.h"
#include "ip.h"
#include "arp.h"

#include <slack/std.h>
#include <slack/err.h>
#include "verbose.h"

// Define IPTOS macros if not defined
#ifndef IPTOS_PREC
#define IPTOS_PREC(tos)          ((tos) & 0xe0)
#endif

#ifndef IPTOS_TOS
#define IPTOS_TOS(tos)           ((tos) & 0x1e)
#endif

gpacket_t *duplicatePacket(gpacket_t *inpkt)
{
	gpacket_t *cpptr = (gpacket_t *) malloc(sizeof(gpacket_t));

	if (cpptr == NULL)
	{
		error("[duplicatePacket]:: error allocating memory for duplication.. ");
		return NULL;
	}
	memcpy(cpptr, inpkt, sizeof(gpacket_t));
	return cpptr;
}

void printSepLine(char *start, char *end, int count, char sep)
{
	int i;

	printf("%s", start);
	for (i = 0; i < count; i++)
		printf("%c", sep);
	printf("%s", end);
}

void printGPktFrame(gpacket_t *msg, char *routine)
{
	char tmpbuf[MAX_TMPBUF_LEN];

	printf("\n    P A C K E T  F R A M E  S E C T I O N of GPACKET @ %s \n", routine);
	printf(" SRC interface : \t %d\n", msg->frame.src_interface);
	printf(" SRC IP addr : \t %s\n", IP2Dot(tmpbuf, msg->frame.src_ip_addr));
	printf(" SRC HW addr : \t %s\n", MAC2Colon(tmpbuf, msg->frame.src_hw_addr));
	printf(" DST interface : \t %d\n", msg->frame.dst_interface);
	printf(" NEXT HOP addr : \t %s\n", IP2Dot(tmpbuf, msg->frame.nxth_ip_addr));
}

void printGPacket(gpacket_t *msg, int level, char *routine)
{
	printSepLine("", "\n", 70, '=');
	printGPktFrame(msg, routine);

	if (level >= 3)
		printGPktPayload(msg, level);

	printSepLine("\n", "\n", 70, '=');
}

void printGPktPayload(gpacket_t *msg, int level)
{
	int prot;

	prot = printEthernetHeader(msg);
	switch (prot)
	{
	case IP_PROTOCOL:
		prot = printIPPacket(msg);
		switch (prot)
		{
		case ICMP_PROTOCOL:
			printICMPPacket(msg);
			break;
		case UDP_PROTOCOL:
			printUDPPacket(msg);
		case TCP_PROTOCOL:
			printTCPPacket(msg);
		}
		break;
	case ARP_PROTOCOL:
		printARPPacket(msg);
		break;
	default:
		// ignore other cases for now!
		break;
	}
}

int printEthernetHeader(gpacket_t *msg)
{
	char tmpbuf[MAX_TMPBUF_LEN];
	int prot;

	printf("\n    P A C K E T  D A T A  S E C T I O N of GMESSAGE \n");
	printf(" DST MAC addr : \t %s\n", MAC2Colon(tmpbuf, msg->data.header.dst));
	printf(" SRC MAC addr : \t %s\n", MAC2Colon(tmpbuf, msg->data.header.src));
	prot = ntohs(msg->data.header.prot);
	printf(" Protocol : \t %x\n", prot);

	return prot;
}

int printIPPacket(gpacket_t *msg)
{
	ip_packet_t *ip_pkt;
	char tmpbuf[MAX_TMPBUF_LEN];
	int tos;

	ip_pkt = (ip_packet_t *)msg->data.data;
	printf("IP: ----- IP Header -----\n");
	printf("IP: Version        : %d\n", ip_pkt->ip_version);
	printf("IP: Header Length  : %d Bytes\n", ip_pkt->ip_hdr_len*4);
	printf("IP: Total Length   : %d Bytes\n", ntohs(ip_pkt->ip_pkt_len));
	printf("IP: Type of Service: 0x%02X\n", ip_pkt->ip_tos);
	printf("IP:      xxx. .... = 0x%02X (Precedence)\n", IPTOS_PREC(ip_pkt->ip_tos));
	tos = IPTOS_TOS(ip_pkt->ip_tos);
	if (tos ==  IPTOS_LOWDELAY)
		printf("IP:      ...1 .... = Minimize Delay\n");
	else
		printf("IP:      ...0 .... = Normal Delay\n");
	if (tos == IPTOS_THROUGHPUT)
		printf("IP:      .... 1... = Maximize Throughput\n");
	else
		printf("IP:      .... 0... = Normal Throughput\n");
	if (tos == IPTOS_RELIABILITY)
		printf("IP:      .... .1.. = Maximize Reliability\n");
	else
		printf("IP:      .... .0.. = Normal Reliability\n");
	if (tos == IPTOS_MINCOST)
		printf("IP:      .... ..1. = Minimize Cost\n");
	else
		printf("IP:      .... ..0. = Normal Cost\n");
	printf("IP: Identification : %d\n", ntohs(ip_pkt->ip_identifier));
	printf("IP: Flags          : 0x%02X\n", ((ntohs(ip_pkt->ip_frag_off) & ~IP_OFFMASK)>>13));
	if ((ntohs(ip_pkt->ip_frag_off) & IP_DF) == IP_DF)
		printf("IP:      .1.. .... = do not fragment\n");
	else
		printf("IP:      .0.. .... = can fragment\n");
	if ((ntohs(ip_pkt->ip_frag_off) & IP_MF) == IP_MF)
		printf("IP:      ..1. .... = more fragment\n");
	else
		printf("IP:      ..0. .... = last fragment\n");
	printf("IP: Fragment Offset: %d Bytes\n", (ntohs(ip_pkt->ip_frag_off) & IP_OFFMASK));
	printf("IP: Time to Live   : %d sec/hops\n", ip_pkt->ip_ttl);

	printf("IP: Protocol       : %d", ip_pkt->ip_prot);
	printf("IP: Checksum       : 0x%X\n", ntohs(ip_pkt->ip_cksum));
	{
		uchar tmp[4];
		printf("IP: Source         : %s", IP2Dot(tmpbuf, gNtohl(tmp, ip_pkt->ip_src)));
		printf("IP: Destination    : %s", IP2Dot(tmpbuf, gNtohl(tmp, ip_pkt->ip_dst)));
	}

	return ip_pkt->ip_prot;
}

void printARPPacket(gpacket_t *msg)
{
	arp_packet_t *apkt;
	char tmpbuf[MAX_TMPBUF_LEN];

	apkt = (arp_packet_t *) msg->data.data;

	printf(" ARP hardware addr type %x \n", ntohs(apkt->hw_addr_type));
	printf(" ARP protocol %x \n", ntohs(apkt->arp_prot));
	printf(" ARP hardware addr len %d \n", apkt->hw_addr_len);
	printf(" ARP protocol len %d \n", apkt->arp_prot_len);
	printf(" ARP opcode %x \n", ntohs(apkt->arp_opcode));
	printf(" ARP src hw addr %s \n", MAC2Colon(tmpbuf, apkt->src_hw_addr));
	printf(" ARP src ip addr %s \n", IP2Dot(tmpbuf, gNtohl((uchar *)tmpbuf, apkt->src_ip_addr)));
	printf(" ARP dst hw addr %s \n", MAC2Colon(tmpbuf, apkt->dst_hw_addr));
	printf(" ARP dst ip addr %s \n", IP2Dot(tmpbuf, gNtohl((uchar *)tmpbuf, apkt->dst_ip_addr)));
}

void printICMPPacket(gpacket_t *msg)
{

	printf("\n ICMP PACKET display NOT YET IMPLEMENTED !! \n");
}

void printUDPPacket(gpacket_t *msg)
{

	printf("\n UDP PACKET display NOT YET IMPLEMENTED !! \n");
}

void printTCPPacket(gpacket_t *msg)
{

	printf("\n TCP PACKET display NOT YET IMPLEMENTED !! \n");
}
