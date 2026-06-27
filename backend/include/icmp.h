/*
 * icmp.h (header file for the ICMP module)
 * AUTHOR: Original version written by Weiling Xu
 *         Revised by Muthucumaru Maheswaran
 * VERSION: Beta
 */

#ifndef __ICMP_H_
#define __ICMP_H_

#include <sys/types.h>
#include "grouter.h"
#include "message.h"



#define ICMP_ECHO_REPLY          0      /* Echo Reply                   */
#define ICMP_ECHO_REQUEST        8      /* Echo Request                 */
#define ICMP_DEST_UNREACH        3      /* Destination Unreachable      */
#define ICMP_SOURCE_QUENCH       4      /* Source Quench                */
#define ICMP_REDIRECT            5      /* Redirect (change route)      */
#define ICMP_TTL_EXPIRED        11      /* Time Exceeded                */
#define ICMP_PARAMETERPROB      12      /* Parameter Problem            */
#define ICMP_TIMESTAMP          13      /* Timestamp Request            */
#define ICMP_TIMESTAMPREPLY     14      /* Timestamp Reply              */
#define ICMP_INFO_REQUEST       15      /* Information Request          */
#define ICMP_INFO_REPLY         16      /* Information Reply            */


/* Codes for UNREACH. */
#define ICMP_NET_UNREACH        0       /* Network Unreachable          */
#define ICMP_HOST_UNREACH       1       /* Host Unreachable             */
#define ICMP_PROT_UNREACH       2       /* Protocol Unreachable         */
#define ICMP_PORT_UNREACH       3       /* Port Unreachable             */
#define ICMP_FRAG_NEEDED        4       /* Fragmentation Needed/DF set  */
#define ICMP_SR_FAILED          5       /* Source Route failed          */
#define ICMP_NET_UNKNOWN        6
#define ICMP_HOST_UNKNOWN       7

                                                                                                  
/* Codes for REDIRECT. */
#define ICMP_REDIR_NET          0       /* Redirect Net                 */
#define ICMP_REDIR_HOST         1       /* Redirect Host                */
#define ICMP_REDIR_NETTOS       2       /* Redirect Net for TOS         */
#define ICMP_REDIR_HOSTTOS      3       /* Redirect Host for TOS        */
                                                                                                  
/* Codes for TIME_EXCEEDED. */
#define ICMP_EXC_TTL            0       /* TTL count exceeded           */
#define ICMP_EXC_FRAGTIME       1       /* Fragment Reass time exceeded */
                                                                                                  

// icmp structure definitions go here...

typedef struct _icmphdr_t
{
	uchar type;                  /* message type */
	uchar code;                  /* type sub-code */
	ushort checksum;
	union _un
	{
		struct
		{
			ushort id;
			ushort sequence;
		} echo;                     /* echo datagram */
		uint   gateway;        /* gateway address */
		struct
		{
			ushort __unused;
			ushort mtu;
		} frag;                     /* path mtu discovery */
	} un;
} icmphdr_t;


// structure used to collect ping statistics
typedef struct _pingstat_t
{
	uchar* dst_ip;
	float tmin;
	float tmax;
	float tsum;
	int ntransmitted;
	int nreceived;
} pingstat_t;



// function prototypes should go here....
void ICMPSendPingPacket(uchar *dst_ip, int size, int seq);
void ICMPProcessEchoRequest(gpacket_t *in_pkt);
void ICMPProcessEchoReply(gpacket_t *in_pkt);

void ICMPProcessPacket(gpacket_t *in_pkt);

void ICMPDoPing(uchar *ipaddr, int pkt_size, int retries);

void ICMPProcessTTLExpired(gpacket_t *in_pkt);
void ICMPProcessFragNeeded(gpacket_t *in_pkt, int interface_mtu);
void ICMPProcessRedirect(gpacket_t *in_pkt, uchar *gw_addr);
void ICMPDisplayPingStats();
void dummyFunctionCopy();
void ICMPPingTerminate();

#endif
