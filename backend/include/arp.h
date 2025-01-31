/*
 * arp.h (header file for ARP (address resolution protocol)
 * AUTHOR: Weiling Xu (original version)
 * REVISED: Muthucumaru Maheswaran
 * REVISED ON: December 19, 2004
 *
 * All declarations related to ARP are here
 *
 */

#ifndef __ARP_H_
#define __ARP_H_

#include <sys/types.h>

#include "grouter.h"
#include "simplequeue.h"
#include "message.h"
#include <stdint.h>

/*
 * Private definitions: only used within the ARP module
 */
#define MAX_ARP 			20      // max. number of ARP entries
#define MAX_ARP_BUFFERS 		50	// max number of entries in message buffer
#define ARP_CACHE_SIZE                  31      // ARP cache size

/*
 * ARP protocol definitions.. used for ARP processing.
 */
#define ARP_REQUEST 			1       // From RFC 790
#define ARP_REPLY 			2       // ""


/*
 * TODO: Expand this entry?? Should we not include other parameters such
 * as
 */
typedef struct _arp_entry_t
{
	bool is_empty;                          // entry used or not
	uchar ip_addr[4];
	uchar mac_addr[6];
} arp_entry_t;


typedef struct _arp_buffer_entry_t
{
	bool is_empty;                          // entry used or not
	gpacket_t *wait_msg;                    // message that is waiting for ARP resolution
} arp_buffer_entry_t;


/*
 * structure of an ARP request.. taken from net/if_arp.h and modified
 */
typedef struct _arp_packet_t
{
	uint16_t hw_addr_type;                   // Format of hardware address
	uint16_t arp_prot;                       // Format of protocol address
	uint8_t hw_addr_len;                     // Length of hardware address
	uint8_t arp_prot_len;                    // Length of protocol address
	uint16_t arp_opcode;                     // ARP opcode (command)
	uchar src_hw_addr[6];	                 // Sender hardware address
	uchar src_ip_addr[4];                    // Sender IP address
	uchar dst_hw_addr[6];                    // Target hardware address
	uchar dst_ip_addr[4];                    // Target IP address
} arp_packet_t;


int ARPResolve(gpacket_t *in_pkt);
void ARPProcess(gpacket_t *pkt);

void ARPInitTable();
void ARPReInitTable();

int ARPFindEntry(uchar *ip_addr, uchar *mac_addr);
void ARPAddEntry(const char *ip_addr, const char *mac_addr);
void ARPPrintTable(void);
void ARPDeleteEntry(const char *ip_addr);
void ARPSendRequest(gpacket_t *pkt);

// ARP Buffer functions..
void ARPInitBuffer();
void ARPAddBuffer(gpacket_t *in_pkt);
int ARPGetBuffer(gpacket_t **out_pkt, uchar *nexthop);
void ARPFlushBuffer(const char *next_hop, const char *mac_addr);

void ARPInit(void);

#endif
