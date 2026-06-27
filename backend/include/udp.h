/*
 * Copyright (c) 2001-2004 Swedish Institute of Computer Science.
 * All rights reserved. 
 * 
 * Redistribution and use in source and binary forms, with or without modification, 
 * are permitted provided that the following conditions are met:
 *
 * 1. Redistributions of source code must retain the above copyright notice,
 *    this list of conditions and the following disclaimer.
 * 2. Redistributions in binary form must reproduce the above copyright notice,
 *    this list of conditions and the following disclaimer in the documentation
 *    and/or other materials provided with the distribution.
 * 3. The name of the author may not be used to endorse or promote products
 *    derived from this software without specific prior written permission. 
 *
 * THIS SOFTWARE IS PROVIDED BY THE AUTHOR ``AS IS'' AND ANY EXPRESS OR IMPLIED 
 * WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF 
 * MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT 
 * SHALL THE AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, 
 * EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT 
 * OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS 
 * INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN 
 * CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING 
 * IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY 
 * OF SUCH DAMAGE.
 *
 * This file is part of the lwIP TCP/IP stack.
 * 
 * Author: Adam Dunkels <adam@sics.se>
 *
 */
#ifndef __UDP_H_
#define __UDP_H_

#include <stdint.h>
#include "ip.h"
#include "err.h"
#include "pbuf.h"

/**
 * IP_DEFAULT_TTL: Default value for Time-To-Live used by transport layers.
 */
#ifndef IP_DEFAULT_TTL
#define IP_DEFAULT_TTL                  255
#endif

/**
 * UDP_TTL: Default Time-To-Live value.
 */
#ifndef UDP_TTL
#define UDP_TTL                         (IP_DEFAULT_TTL)
#endif

/**
 * UDP packet definition adapted from <netinet/udp.h>.
 */
typedef struct {
	// Source port
	uint16_t	src_port;
	// Destination port
	uint16_t	dst_port;
	// UDP length (in bytes)
	uint16_t	length;
	// UDP checksum
	uint16_t	checksum;
} udp_packet_type;

/**
 * UDP pseudo-header used to calculate checksums.
 */
typedef struct {
	// IP source address
	uint32_t	ip_src;
	// IP destination address
	uint32_t	ip_dst;
	// Reserved
	uint8_t		reserved;
	// IP protocol
	uint8_t		ip_prot;
	// UDP length (in bytes)
	uint16_t	udp_length;
} udp_pseudo_header_type;

/**
 * Calculates the UDP checksum for the specified UDP packet.
 *
 * @param ip_packet The IP packet containing the UDP packet.
 *
 * @return The UDP checksum for the specified UDP packet.
 */
uint16_t udp_checksum(ip_packet_t *ip_packet);

#ifdef __cplusplus
extern "C" {
#endif

#define UDP_HLEN 8

/* Fields are (of course) in network byte order. */
#define PACK_STRUCT_FIELD(x) x
struct udp_hdr {
  PACK_STRUCT_FIELD(uint16_t src);
  PACK_STRUCT_FIELD(uint16_t dest);  /* src/dest UDP ports */
  PACK_STRUCT_FIELD(uint16_t len);
  PACK_STRUCT_FIELD(uint16_t chksum);
};
#define UDP_FLAGS_NOCHKSUM       0x01U
#define UDP_FLAGS_UDPLITE        0x02U
#define UDP_FLAGS_CONNECTED      0x04U
#define UDP_FLAGS_MULTICAST_LOOP 0x08U

struct udp_pcb;

/** Function prototype for udp pcb receive callback functions
 * addr and port are in same byte order as in the pcb
 * The callback is responsible for freeing the pbuf
 * if it's not used any more.
 *
 * ATTENTION: Be aware that 'addr' points into the pbuf 'p' so freeing this pbuf
 *            makes 'addr' invalid, too.
 *
 * @param arg user supplied argument (udp_pcb.recv_arg)
 * @param pcb the udp_pcb which received data
 * @param p the packet buffer that was received
 * @param addr the remote IP address from which the packet was received
 * @param port the remote port from which the packet was received
 */
typedef void (*udp_recv_fn)(void *arg, struct udp_pcb *pcb, struct pbuf *p,
    uchar *addr, uint16_t port);

struct udp_pcb {
/* Common members of all PCB types */
  IP_PCB;

/* Protocol specific PCB members */

  struct udp_pcb *next;

  uint8_t flags;
  /** ports are in host byte order */
  uint16_t local_port, remote_port;

  /** receive callback function */
  udp_recv_fn recv;
  /** user-supplied argument for the recv callback */
  void *recv_arg;  
};
/* udp_pcbs export for exernal reference (e.g. SNMP agent) */
extern struct udp_pcb *udp_pcbs;

/* The following functions is the application layer interface to the
   UDP code. */
struct udp_pcb * udp_new        (void);
void             udp_remove     (struct udp_pcb *pcb);
err_t            udp_bind       (struct udp_pcb *pcb, uchar *ipaddr,
                                 uint16_t port);
err_t            udp_connect    (struct udp_pcb *pcb, uchar *ipaddr,
                                 uint16_t port);
void             udp_disconnect (struct udp_pcb *pcb);
void             udp_recv       (struct udp_pcb *pcb, udp_recv_fn recv,
                                 void *recv_arg);
err_t            udp_sendto_if  (struct udp_pcb *pcb, struct pbuf *p,
                                 uchar *dst_ip, uint16_t dst_port);
err_t            udp_sendto     (struct udp_pcb *pcb, struct pbuf *p,
                                 uchar *dst_ip, uint16_t dst_port);
err_t            udp_send       (struct udp_pcb *pcb, struct pbuf *p);

err_t            udp_sendto_chksum(struct udp_pcb *pcb, struct pbuf *p, uchar *dst_ip, uint16_t dst_port, uint8_t have_chksum, uint16_t chksum);
err_t            udp_send_chksum(struct udp_pcb *pcb, struct pbuf *p, uint8_t have_chksum, uint16_t chksum);

#define          udp_flags(pcb) ((pcb)->flags)
#define          udp_setflags(pcb, f)  ((pcb)->flags = (f))

/* The following functions are the lower layer interface to UDP. */
void             udp_input      (struct pbuf *p, gpacket_t *in_pkt, uchar *netmask, uchar *network);
void             udp_init       (void);

#if UDP_DEBUG
void udp_debug_print(struct udp_hdr *udphdr);
#else
#define udp_debug_print(udphdr)
#endif

#ifdef __cplusplus
}
#endif

#endif // ifndef __UDP_H_

