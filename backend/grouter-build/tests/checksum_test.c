/*
 * checksum_test.c — standalone unit test for tcp_checksum() / udp_checksum().
 *
 * These are what OpenFlow SET_NW_SRC / SET_NW_DST call after rewriting an address, and
 * until this test existed they could not have been right: they stepped a typed pointer
 * (`ip_packet + hdr_len*4` moves hdr_len*4 whole structs), summed the bytes of the
 * pointer VARIABLE on the stack instead of the segment, and mixed network- and host-order
 * lengths. gini.samples.l4_lb's first TCP rewrite killed the switch with SIGSEGV in
 * checksum() — the switch vanished and the VIP simply never answered.
 *
 * The expected values below were produced by a separate RFC 1071 implementation in Python
 * (not by this C code), over real IPv4 packets:
 *
 *   tcp_syn    a 20-byte SYN, even length
 *   tcp_odd    a segment with an odd payload — the zero-padded last byte
 *   rewrite    a packet to the VIP whose dst is rewritten to a backend, exactly the
 *              l4_lb forward flow: the checksum must be the one for the NEW address
 *   udp_even / udp_odd
 *   udp "no checksum" (0) must stay 0, and a bogus IP length must not be followed
 *
 * Build — linked against the WHOLE gRouter, because tcp.c and udp.c carry a TCP/UDP stack
 * with dependencies everywhere; the router's own main() is renamed out of the way. Run from
 * backend/ inside the gRouter build image (it has libslack, readline and Lua):
 *
 *   F="-Iinclude -I/usr/local/include -I/usr/include/lua5.4 -DHAVE_PTHREAD_RWLOCK=1 \
 *      -DHAVE_GETOPT_LONG -DGR_LEGACY_MODULES -DGR_LUA -w -fcommon"
 *   for f in src/grouter/*.c; do n=$(basename $f .c); x=; [ $n = grouter ] && x=-Dmain=gr_main
 *     gcc -c $F $x $f -o /tmp/$n.o; done
 *   gcc -c $F grouter-build/tests/checksum_test.c -o /tmp/_t.o
 *   gcc -o /tmp/checksum_test /tmp/*.o -rdynamic -L/usr/local/lib \
 *       -lreadline -ltermcap -lslack -lpthread -lutil -lm -llua5.4 && /tmp/checksum_test
 *
 * (Leaving the rest unresolved with --unresolved-symbols=ignore-all links, then fails at
 * load time on a dangling PLT entry — that shortcut does not work.)
 */
#include <stdio.h>
#include <string.h>
#include <arpa/inet.h>
#include "grouter.h"
#include "ip.h"
#include "tcp.h"
#include "udp.h"

static unsigned char pkt_tcp_syn[] = { 0x45, 0x00, 0x00, 0x28, 0x12, 0x34, 0x40, 0x00, 0x40, 0x06, 0x12, 0x2f, 0x0a, 0x00, 0x01, 0x0a, 0x0a, 0x00, 0x01, 0x64, 0xb8, 0xd0, 0x00, 0x50, 0x00, 0x00, 0x03, 0xe8, 0x00, 0x00, 0x00, 0x00, 0x50, 0x02, 0xff, 0x00, 0x00, 0x00, 0x00, 0x00 };
#define EXP_TCP_SYN 0xdd6b
static unsigned char pkt_tcp_odd[] = { 0x45, 0x00, 0x00, 0x3b, 0x12, 0x34, 0x40, 0x00, 0x40, 0x06, 0x12, 0x1c, 0x0a, 0x00, 0x01, 0x0a, 0x0a, 0x00, 0x01, 0x64, 0xb8, 0xd0, 0x00, 0x50, 0x00, 0x00, 0x03, 0xe8, 0x00, 0x00, 0x00, 0x00, 0x50, 0x02, 0xff, 0x00, 0x00, 0x00, 0x00, 0x00, 0x47, 0x45, 0x54, 0x20, 0x2f, 0x20, 0x48, 0x54, 0x54, 0x50, 0x2f, 0x31, 0x2e, 0x30, 0x0d, 0x0a, 0x0d, 0x0a, 0x58 };
#define EXP_TCP_ODD 0xa6b8
static unsigned char pkt_udp_even[] = { 0x45, 0x00, 0x00, 0x26, 0x12, 0x34, 0x40, 0x00, 0x40, 0x11, 0x12, 0x7e, 0x0a, 0x00, 0x01, 0x0a, 0x0a, 0x00, 0x01, 0x0c, 0x14, 0xe9, 0x00, 0x35, 0x00, 0x12, 0x00, 0x00, 0x68, 0x65, 0x6c, 0x6c, 0x6f, 0x2d, 0x67, 0x69, 0x6e, 0x69 };
#define EXP_UDP_EVEN 0xbac4
static unsigned char pkt_udp_odd[] = { 0x45, 0x00, 0x00, 0x1f, 0x12, 0x34, 0x40, 0x00, 0x40, 0x11, 0x12, 0x85, 0x0a, 0x00, 0x01, 0x0a, 0x0a, 0x00, 0x01, 0x0c, 0x14, 0xe9, 0x00, 0x35, 0x00, 0x0b, 0x00, 0x00, 0x6f, 0x64, 0x64 };
#define EXP_UDP_ODD 0x0140
static unsigned char pkt_rewrite[] = { 0x45, 0x00, 0x00, 0x2b, 0x12, 0x34, 0x40, 0x00, 0x40, 0x06, 0x12, 0x2c, 0x0a, 0x00, 0x01, 0x0a, 0x0a, 0x00, 0x01, 0x64, 0xb8, 0xd0, 0x00, 0x50, 0x00, 0x00, 0x03, 0xe8, 0x00, 0x00, 0x00, 0x00, 0x50, 0x02, 0xff, 0x00, 0x00, 0x00, 0x00, 0x00, 0x47, 0x45, 0x54 };
#define EXP_REWRITE 0x427b

static int failures;

static void check(const char *what, unsigned got, unsigned want)
{
	if (got != want) {
		printf("FAIL  %-34s got 0x%04x want 0x%04x\n", what, got, want);
		failures++;
	} else {
		printf("ok    %s\n", what);
	}
}

/* The checksum field as it sits in the packet, read back in wire order. */
static unsigned field(unsigned char *pkt, int off)
{
	return (pkt[20 + off] << 8) | pkt[20 + off + 1];
}

static void run_tcp(const char *what, unsigned char *pkt, unsigned want)
{
	ip_packet_t *ip = (ip_packet_t *) pkt;
	tcp_packet_type *tcp = (tcp_packet_type *) (pkt + 20);
	tcp->checksum = htons(0xBEEF);                  /* stale value: must be ignored */
	tcp->checksum = tcp_checksum(ip);
	check(what, field(pkt, 16), want);
}

static void run_udp(const char *what, unsigned char *pkt, unsigned want)
{
	ip_packet_t *ip = (ip_packet_t *) pkt;
	udp_packet_type *udp = (udp_packet_type *) (pkt + 20);
	udp->checksum = htons(0xBEEF);                  /* present, so it must be recomputed */
	udp->checksum = udp_checksum(ip);
	check(what, field(pkt, 6), want);
}

int main(void)
{
	run_tcp("tcp: even-length SYN", pkt_tcp_syn, EXP_TCP_SYN);
	run_tcp("tcp: odd-length segment", pkt_tcp_odd, EXP_TCP_ODD);

	/* The l4_lb forward flow: rewrite dst 10.0.1.100 -> 10.0.1.12, then recompute. */
	{
		unsigned char backend[4] = { 10, 0, 1, 12 };
		ip_packet_t *ip = (ip_packet_t *) pkt_rewrite;
		COPY_IP(ip->ip_dst, backend);
		run_tcp("tcp: after SET_NW_DST rewrite", pkt_rewrite, EXP_REWRITE);
	}

	run_udp("udp: even length", pkt_udp_even, EXP_UDP_EVEN);
	run_udp("udp: odd length", pkt_udp_odd, EXP_UDP_ODD);

	/* A sender that did not checksum (0) must not suddenly get one. */
	{
		udp_packet_type *udp = (udp_packet_type *) (pkt_udp_even + 20);
		udp->checksum = 0;
		udp->checksum = udp_checksum((ip_packet_t *) pkt_udp_even);
		check("udp: 0 (none) stays 0", ntohs(udp->checksum), 0);
	}

	/* An IP total length far beyond the buffer must be refused, not summed. */
	{
		ip_packet_t *ip = (ip_packet_t *) pkt_tcp_syn;
		tcp_packet_type *tcp = (tcp_packet_type *) (pkt_tcp_syn + 20);
		ip->ip_pkt_len = htons(60000);
		tcp->checksum = htons(0x1234);
		tcp->checksum = tcp_checksum(ip);
		check("tcp: bogus length left alone", ntohs(tcp->checksum), 0x1234);
	}

	printf("%s\n", failures ? "FAILED" : "all checksum tests passed");
	return failures ? 1 : 0;
}
