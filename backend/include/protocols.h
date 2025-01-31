/*
 * protocols.h (include file for protocol definitions)
 * AUTHOR: Muthucumaru Maheswaran
 * DATE: December 16, 2004
 * VERSION: 1.0
 *
 */

#ifndef __PROTOCOLS_H__
#define __PROTOCOLS_H__

#include <netinet/in.h>
#include <stdint.h>

// Network byte order conversion for 64-bit integers
#ifndef htonll
#define htonll(x) ((((uint64_t)htonl(x)) << 32) + htonl((x) >> 32))
#endif

#ifndef ntohll
#define ntohll(x) ((((uint64_t)ntohl(x)) << 32) + ntohl((x) >> 32))
#endif

// Ethernet protocol types
#define ETHERTYPE_IEEE_802_1Q  0x8100
#define OFP_DL_TYPE_ETH2_CUTOFF 0x0600
#define OFP_DL_TYPE_NOT_ETH_TYPE 0x05ff

// IEEE 802.2 definitions
#define IEEE_802_2_DSAP_SNAP    0xAA
#define IEEE_802_2_CTRL_8_BITS  0x03

// Protocol numbers
#define IP_PROTOCOL    0x0800
#define ARP_PROTOCOL   0x0806
#define TCP_PROTOCOL   6
#define UDP_PROTOCOL   17
#define ICMP_PROTOCOL  1
#define ETHERNET_PROTOCOL 0x0001  // Hardware type for Ethernet

#endif // __PROTOCOLS_H__
