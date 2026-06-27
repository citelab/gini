/*
 * icmp.c (collection of functions that implement the ICMP)
 * AUTHOR: Original version by Weiling Xu
 *         Revised by Muthucumaru Maheswaran
 * DATE:   Last revised on June 22, 2008
 *
 */


#include "protocols.h"
#include "icmp.h"
#include "ip.h"
#include "message.h"
#include "grouter.h"
#include <slack/err.h>
#include <netinet/in.h>
#include <sys/time.h>
#include <stdio.h>
#include <string.h>

#include <unistd.h>
#include <math.h>
#include <signal.h>

// state information on outstanding ping..
pingstat_t pstat;

// state information specifying number of "echo request" packets to be attempted by the ping
// this is set to 0 if ^C is received (to kill the ping)
static int curr_retries = 0;

/*
 * *** TODO: *** complete this function by implemeting the missing handlers
 * Only ICMP Echo is processed at this point.. we need to process other ICMP packets
 * as well. This is somewhat urgent!!
 */
void ICMPProcessPacket(gpacket_t *in_pkt)
{
	ip_packet_t *ip_pkt = (ip_packet_t *)in_pkt->data.data;
	int iphdrlen = ip_pkt->ip_hdr_len *4;
	icmphdr_t *icmphdr = (icmphdr_t *)((uchar *)ip_pkt + iphdrlen);

	switch (icmphdr->type)
	{
	case ICMP_ECHO_REQUEST:
		verbose(2, "[ICMPProcessPacket]:: ICMP processing for ECHO request");
		ICMPProcessEchoRequest(in_pkt);
		break;

	case ICMP_ECHO_REPLY:
		verbose(2, "[ICMPProcessPacket]:: ICMP processing for ECHO reply");
		ICMPProcessEchoReply(in_pkt);
		break;

	case ICMP_REDIRECT:
	case ICMP_SOURCE_QUENCH:
	case ICMP_TIMESTAMP:
	case ICMP_TIMESTAMPREPLY:
	case ICMP_INFO_REQUEST:
	case ICMP_INFO_REPLY:
		verbose(2, "[ICMPProcessPacket]:: ICMP processing for type %d not implemented ", icmphdr->type);
		break;
	}
}



/*
 * send a ICMP echo request packet to the specified host/router
 * this routine does the following: send ICMP requests to the IP
 * with the destination address stored in icmp_cmd.ip_addr.
 * Sleeps for a while and tries another ping (until the number of tries).
 * If a ping is already active it ignores the current request.
 * No buffered ping requests!
 */
void ICMPDoPing(uchar *ipaddr, int pkt_size, int retries)
{
	static int ping_active = 0;

	// temporarily set SIGINT handler to ICMPPingTerminate
	redefineSignalHandler(SIGINT, ICMPPingTerminate);
	curr_retries = retries;

	int i;
	char tmpbuf[64];

	// initialize the ping statistics structure
	pstat.dst_ip = ipaddr;
	pstat.tmin = LARGE_REAL_NUMBER;
	pstat.tmax = SMALL_REAL_NUMBER;
	pstat.tsum = 0;
	pstat.ntransmitted = 0;
	pstat.nreceived = 0;

	if (ping_active == 0)
	{
		printf("Pinging IP Address [%s]\n", IP2Dot(tmpbuf, ipaddr));
		ping_active = 1;
		
		for(i=0; i < curr_retries; i++) {
			ICMPSendPingPacket(ipaddr, pkt_size, i);
			sleep(1);
		}
		ICMPDisplayPingStats();
		ping_active = 0;
	}

	// reset SIGINT handler to ignore the signal
	redefineSignalHandler(SIGINT, dummyFunctionCopy);
}



void ICMPSendPingPacket(uchar *dst_ip, int size, int seq)
{
	gpacket_t *out_pkt = (gpacket_t *) malloc(sizeof(gpacket_t));
	ip_packet_t *ipkt = (ip_packet_t *)(out_pkt->data.data);
	ipkt->ip_hdr_len = 5;                                  // no IP header options!!
	icmphdr_t *icmphdr = (icmphdr_t *)((uchar *)ipkt + ipkt->ip_hdr_len*4);
	ushort cksum;
	struct timeval *tp = (struct timeval *)((uchar *)icmphdr + 8);
	struct timezone tz;
	uchar *dataptr;
	int i;
	char tmpbuf[64];

	pstat.ntransmitted++;

	icmphdr->type = ICMP_ECHO_REQUEST;
	icmphdr->code = 0;
	icmphdr->checksum = 0;
	icmphdr->un.echo.id = getpid() & 0xFFFF;
	icmphdr->un.echo.sequence = seq;
	gettimeofday(tp, &tz);

	dataptr = ((uchar *)icmphdr + 8 +  sizeof(struct timeval));
	// pad data...
	for (i = 8; i < size; i++)
		*dataptr++ = i;

	cksum = checksum((uchar *)icmphdr, size/2);  // size = payload (given) + icmp_header
	icmphdr->checksum = htons(cksum);

	verbose(2, "[sendPingPacket]:: Sending... ICMP ping to  %s", IP2Dot(tmpbuf, dst_ip));

	// send the message to the IP routine for further processing
	// the IP should create new header .. provide needed information from here.
	// tag the message as new packet
	// IPOutgoingPacket(/context, packet, IPaddr, size, newflag, source)
	IPOutgoingPacket(out_pkt, dst_ip, size, 1, ICMP_PROTOCOL);
}




/*
 * Send TTL expired message.
 */
void ICMPProcessTTLExpired(gpacket_t *in_pkt)
{
	ip_packet_t *ipkt = (ip_packet_t *)in_pkt->data.data;
	int iphdrlen = ipkt->ip_hdr_len *4;
	icmphdr_t *icmphdr = (icmphdr_t *)((uchar *)ipkt + iphdrlen);
	ushort cksum;
	char tmpbuf[MAX_TMPBUF_LEN];
	int iprevlen = iphdrlen + 8;  // IP header + 64 bits
	uchar prevbytes[MAX_IPREVLENGTH_ICMP];

	memcpy(prevbytes, (uchar *)ipkt, iprevlen);

	/*
	 * form an ICMP TTL expired message and fill in ICMP
	 * header ...
	 */
	icmphdr->type = ICMP_TTL_EXPIRED;
	icmphdr->code = 0; 
	icmphdr->checksum = 0;
	bzero((void *)&(icmphdr->un), sizeof(icmphdr->un));
	memcpy(((uchar *)icmphdr + 8), prevbytes, iprevlen);    /* ip header + 64 bits of original pkt */
	cksum = checksum((uchar *)icmphdr, (8 + iprevlen)/2 );
	icmphdr->checksum = htons(cksum);

	verbose(2, "[ICMPProcessTTLExpired]:: Sending... ICMP TTL expired message ");
	printf("Checksum at ICMP routine (TTL expired):  %x\n", cksum);

	// send the message back to the IP module for further processing ..
	// set the messsage as REPLY_PACKET
	IPOutgoingPacket(in_pkt, gNtohl(tmpbuf, ipkt->ip_src), 8+iprevlen, 1, ICMP_PROTOCOL);
}



/*
 * send a PING reply in response to the incoming REQUEST
 */
void ICMPProcessEchoRequest(gpacket_t *in_pkt)
{
	ip_packet_t *ipkt = (ip_packet_t *)in_pkt->data.data;
	int iphdrlen = ipkt->ip_hdr_len *4;
	icmphdr_t *icmphdr = (icmphdr_t *)((uchar *)ipkt + iphdrlen);
	uchar *icmppkt_b = (uchar *)icmphdr;

	ushort cksum;
	int ilen = ntohs(ipkt->ip_pkt_len) - iphdrlen;


	icmphdr->type = ICMP_ECHO_REPLY;
	icmphdr->checksum = 0;
	if (IS_ODD(ilen))
	{
		// pad with a zero byte.. IP packet length remains the same 
		icmppkt_b[ilen] = 0x0;
		ilen++;
	}
	cksum = checksum(icmppkt_b, (ilen/2));
	icmphdr->checksum = htons(cksum);
	
	// send the message back to the IP routine for further processing ..
	// set the messsage as REPLY_PACKET..
	// destination IP and size need not be set. they can be obtained from the original packet
	
	IPOutgoingPacket(in_pkt, NULL, 0, 0, ICMP_PROTOCOL);
}



/*
 * process incoming ECHO REPLY .. by printing it...
 * ASSUMPTION: only one ping is active -- this is true because the
 * router is not multitasking from the CLI -- only one ping command can be
 * active at a given time. the reply should correspond to the active request.
 */
void ICMPProcessEchoReply(gpacket_t *in_pkt)
{
	ip_packet_t *ipkt = (ip_packet_t *)in_pkt->data.data;
	int iphdrlen = ipkt->ip_hdr_len *4;
	icmphdr_t *icmphdr = (icmphdr_t *)((uchar *)ipkt + iphdrlen);
	uchar *icmppkt_b = (uchar *)icmphdr;

	struct timeval tv;
	struct timezone tz;
	char tmpbuf[MAX_TMPBUF_LEN];
	double elapsed_time;

	if (icmphdr->type == ICMP_ECHO_REPLY)
	{

		gettimeofday(&tv, &tz);
		elapsed_time = subTimeVal(&tv, (struct timeval *)(icmppkt_b + 8));

		// update ping statistics structure
		pstat.nreceived++;
		pstat.tmin = fmin(pstat.tmin, elapsed_time);
		pstat.tmax = fmax(pstat.tmax, elapsed_time);
		pstat.tsum += elapsed_time;

		printf("%d bytes from %s: icmp_seq=%d ttl=%d time=%6.3f ms\n", 
		       (ntohs(ipkt->ip_pkt_len) - iphdrlen - 8),
		       IP2Dot(tmpbuf, gNtohl((tmpbuf+20), ipkt->ip_src)), 
		       icmphdr->un.echo.sequence, ipkt->ip_ttl, elapsed_time);
	}
}


/*
 * send an ICMP Redirect
 */
void ICMPProcessRedirect(gpacket_t *in_pkt, uchar *gw_addr)
{
	ip_packet_t *ipkt = (ip_packet_t *)in_pkt->data.data;
	int iphdrlen = ipkt->ip_hdr_len * 4;
	icmphdr_t *icmphdr = (icmphdr_t *)((uchar *)ipkt + iphdrlen);
	int iprevlen = iphdrlen + 8;  // IP header + 64 bits
	ushort cksum;
	char tmpbuf[MAX_TMPBUF_LEN];
	uchar prevbytes[MAX_IPREVLENGTH_ICMP];
	memcpy(prevbytes, (uchar *)ipkt, iprevlen);


	// form an ICMP Redirect message
	icmphdr->type = ICMP_REDIRECT;
	icmphdr->code = 0; 
	icmphdr->checksum = 0;
	COPY_IP((char *)&icmphdr->un.gateway, gw_addr);
	memcpy(((uchar *)icmphdr + 8), prevbytes, iprevlen);    /* ip header + 64 bits of original pkt */
	cksum = checksum((uchar *)icmphdr, (8 + iprevlen)/2 );
	icmphdr->checksum = htons(cksum);

	verbose(2, "[ICMPProcessRedirect]:: Sending... ICMP Redirect message ");

	// send the message back to the IP routine for further processing ..
	// set the messsage as REPLY_PACKET
	IPOutgoingPacket(in_pkt, gNtohl(tmpbuf, ipkt->ip_src), (8 + iprevlen), 0, ICMP_PROTOCOL);
}




/*
 * send a Fragmentation needed from previous router
 */
void ICMPProcessFragNeeded(gpacket_t *in_pkt, int interface_mtu)
{
	ip_packet_t *ipkt = (ip_packet_t *)in_pkt->data.data;
	int iphdrlen = ipkt->ip_hdr_len *4;
	icmphdr_t *icmphdr = (icmphdr_t *)((uchar *)ipkt + iphdrlen);
	int iprevlen = iphdrlen + 8;  // IP header + 64 bits
	ushort cksum;
	char tmpbuf[MAX_TMPBUF_LEN];
	uchar prevbytes[MAX_IPREVLENGTH_ICMP];
	memcpy(prevbytes, (uchar *)ipkt, iprevlen);            // save OLD portions of IP packet

	// form an ICMP Fragmentation needed message
	icmphdr->type = ICMP_DEST_UNREACH;
	icmphdr->code = ICMP_FRAG_NEEDED; 
	icmphdr->checksum = 0;
	icmphdr->un.frag.mtu = interface_mtu;
	memcpy(((uchar *)icmphdr + 8), prevbytes, iprevlen);    // OLD ip header + 64 bits of original pkt 
	cksum = checksum((uchar *)icmphdr, (8 + iprevlen)/2 );
	icmphdr->checksum = htons(cksum);

	verbose(2, "[processFragNeeded]:: Sending... ICMP Frag needed message ");

	// send the message back to the IP module for further processing ..
	// set the messsage as REPLY_PACKET
	IPOutgoingPacket(in_pkt, gNtohl(tmpbuf, ipkt->ip_src), (8 + iprevlen), 0, ICMP_PROTOCOL);
}

/*
 * display ping statistics after ping command is terminated
 */
void ICMPDisplayPingStats() {
	char tmpbuf[64];
	printf("\n");
	printf("--- %s ping statistics ---\n", IP2Dot(tmpbuf, pstat.dst_ip));
	printf("%d packets transmitted, %d packets received, %.1f%% packet loss\n", pstat.ntransmitted, pstat.nreceived, 1.0 - pstat.ntransmitted/(double)pstat.nreceived);
	printf("round-trip min/avg/max = %.3f/%.3f/%.3f ms\n", pstat.tmin, pstat.tsum/pstat.nreceived, pstat.tmax);
}

/*
 * set current retries to 0, so if ^C was received then ping will terminate
 */
void ICMPPingTerminate() {
	curr_retries = 0;
}

/*
 * copy of the dummy function
 */
void dummyFunctionCopy(int sign)
{
	printf("Signal [%d] is ignored \n", sign);
}
