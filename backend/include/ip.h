/*
 * ip.h (header file for Internet protocol (IP)
 * AUTHOR: Originally written by Weiling Xu
 *         Revised by Muthucumaru Maheswaran
 * DATE: December 24, 2004
 *
 */


#ifndef __IP_H_
#define __IP_H_

#include <stdint.h>
#include <endian.h>
#include "mtu.h"
#include "routetable.h"
#include "grouter.h"
#include "message.h"
#include "err.h"
#include "pbuf.h"

#define IP_HLEN 20

route_entry_t route_tbl[MAX_ROUTES];       	// routing table
mtu_entry_t MTU_tbl[MAX_MTU];		        // MTU table

/** Gets an IP pcb option (SOF_* flags) */
#define ip_get_option(pcb, opt)   ((pcb)->so_options & (opt))
/** Sets an IP pcb option (SOF_* flags) */
#define ip_set_option(pcb, opt)   ((pcb)->so_options |= (opt))
/** Resets an IP pcb option (SOF_* flags) */
#define ip_reset_option(pcb, opt) ((pcb)->so_options &= ~(opt))


/*
 * Option flags per-socket. These are the same like SO_XXX.
 */
#define SOF_ACCEPTCONN    0x02U  /* socket has had listen() */
#define SOF_REUSEADDR     0x04U  /* allow local address reuse */
#define SOF_KEEPALIVE     0x08U  /* keep connections alive */
#define SOF_BROADCAST     0x20U  /* permit to send and to receive broadcast messages (see IP_SOF_BROADCAST option) */
#define SOF_LINGER        0x80U  /* linger on close if data present */

/* These flags are inherited (e.g. from a listen-pcb to a connection-pcb): */
#define SOF_INHERITED   (SOF_REUSEADDR|SOF_KEEPALIVE|SOF_LINGER/*|SOF_DEBUG|SOF_DONTROUTE|SOF_OOBINLINE*/)

typedef unsigned   char    u8_t;


/* This is the common part of all PCB types. It needs to be at the
   beginning of a PCB type definition. It is located here so that
   changes to this common part are made in one location instead of
   having to change all PCB structs. */
#define IP_PCB_ADDRHINT

#define IP_PCB \
  /* ip addresses in network byte order */ \
  uchar local_ip[4]; \
  uchar remote_ip[4]; \
   /* Socket options */  \
  u8_t so_options;      \
   /* Type Of Service */ \
  u8_t tos;              \
  /* Time To Live */     \
  u8_t ttl               \
  /* link layer address resolution hint */ \
  IP_PCB_ADDRHINT

struct ip_pcb {
/* Common members of all PCB types */
  IP_PCB;
};


/*
 * Private definitions: only used within the IP module
 */

/*
 * IP packet definition.. taken from <netinet/ip.h>
 * Made verbose for readability...
 * Definitions for internet protocol version 4.
 * Per RFC 791, September 1981.
 */
typedef struct _ip_packet_t
{
#if __BYTE_ORDER == __LITTLE_ENDIAN
	uint8_t ip_hdr_len:4;                   // header length 
	uint8_t ip_version:4;                   // version 
#endif
#if __BYTE_ORDER == __BIG_ENDIAN
	uint8_t ip_version:4;                   // version 
	uint8_t ip_hdr_len:4;                   // header length 
#endif
	uint8_t ip_tos;                         // type of service 
	uint16_t ip_pkt_len;                    // total length 
	uint16_t ip_identifier;                 // identification 
	uint16_t ip_frag_off;                   // fragment offset field 
#define IP_RF 0x8000                            // reserved fragment flag 
#define IP_DF 0x4000                            // dont fragment flag 
#define IP_MF 0x2000                            // more fragments flag 
#define IP_OFFMASK 0x1fff                       // mask for fragmenting bits 
	uint8_t ip_ttl;                         // time to live 
	uint8_t ip_prot;                        // protocol 
	uint16_t ip_cksum;                      // checksum 
	uchar ip_src[4], ip_dst[4];             // source and dest address 
} ip_packet_t;

#define TEST_DF_BITS(X)                 ( (X & IP_DF) >> 14)
#define TEST_MF_BITS(X)                 ( (X & IP_MF) >> 13)
#define SET_DF_BITS(X)                  X = ( X | (0x00001 << 14) )
#define SET_MF_BITS(X)                  X = ( X | (0x00001 << 13) )
#define RESET_DF_BITS(X)                X = ( X & (~(0x00001 << 14)) )
#define RESET_MF_BITS(X)                X = ( X & (~(0x00001 << 13)) )

#define IPH_HL(hdr) ((hdr)->ip_hdr_len & 0x0f)

// function prototypes...

void IPInit();
void IPIncomingPacket(gpacket_t *in_pkt);
int IPCheckPacket4Me(gpacket_t *in_pkt);
int IPProcessBcastPacket(gpacket_t *in_pkt);
int IPProcessForwardingPacket(gpacket_t *in_pkt);
int IPCheck4Errors(gpacket_t *in_pkt);
int IPCheck4Fragmentation(gpacket_t *in_pkt);
int IPCheck4Redirection(gpacket_t *in_pkt);
int IPProcessMyPacket(gpacket_t *in_pkt);
int UDPProcess(gpacket_t *in_pkt);
int TCPProcess(gpacket_t *in_pkt);
int IPOutgoingPacket(gpacket_t *pkt, uchar *dst_ip, int size, int newflag, int src_prot);
int send2Output(gpacket_t *pkt);
int IPVerifyPacket(ip_packet_t *ip_pkt);
int isInSameNetwork(uchar *ip_addr1, uchar *ip_addr2);

int IPSend2Output(gpacket_t *pkt);

uchar ip_addr_isany(uchar *addr);
uchar ip_addr_cmp(uchar *addr1, uchar *addr2);
uchar ip_addr_netcmp(uchar *addr1, uchar *addr2, uchar *mask);
void print_ip4(uchar *addr);
void ip_addr_set(uchar *dest, uchar *src);
err_t ip_output(struct pbuf *p, uchar *src_ip, uchar *dst_ip, u8_t ttl, u8_t tos, int src_prot);
u32_t ip4_addr_get_u32(uchar *src);

#endif
