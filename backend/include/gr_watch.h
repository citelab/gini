/*
 * gr_watch.h — packet watch: what happened to each packet, for the Router Lab to draw.
 *
 * The Router Lab's packet visualizer needs to know, per packet, where it came in, where it went
 * out, and what stopped it if something did. Nothing recorded that: `set verbose` and a Lua
 * module's log() print into the container log, and a tap writes pcap for Wireshark. This is a
 * small ring of packet FATES the Lab polls over the control socket (`watch dump <since>`).
 *
 * OFF BY DEFAULT, and free while off — every hook is `if (gr_watch_enabled)`, one load and a
 * branch — so a router nobody is watching forwards exactly as fast as before. A student turns it
 * on from the Lab.
 *
 * Fates, one character so a dump line stays short:
 *   F  forwarded out an interface        D  dropped by a pipeline module (which one is recorded)
 *   N  no route to the destination       T  TTL expired here
 *   L  addressed to the router itself    A  an ARP arrived (answered / learned / ignored)
 *   S  SENT by the router itself — its own ARP requests and replies, and the IP it originates
 *      (echo replies, TTL exceeded, unreachables). Without these a ping TO the router showed the
 *      request arriving and never the reply, and the first ping after a Run was slow for a reason
 *      nobody could see: the router was busy resolving the next hop.
 *
 * ARP is recorded as protocol 2054 (its EtherType — no IP protocol has that number) with the
 * opcode in the source-port field; ICMP carries its type and code in the two port fields.
 */
#ifndef __GR_WATCH_H__
#define __GR_WATCH_H__

#include "message.h"

#define GR_WATCH_N      512     /* ring slots; the Lab polls often enough not to need more */

#define GW_FWD          'F'
#define GW_DROP         'D'
#define GW_NOROUTE      'N'
#define GW_TTL          'T'
#define GW_LOCAL        'L'
#define GW_ARP          'A'
#define GW_SENT         'S'

#define GW_PROTO_ARP    2054            /* 0x0806 */

extern volatile int gr_watch_enabled;

/* Record one packet's fate. `out_if` is -1 unless forwarded; `module` is the pipeline index that
 * dropped it, or -1. Call only through GR_WATCH so a disabled watch costs one branch. */
void gr_watch_note(gpacket_t *pkt, char fate, int out_if, int module);

#define GR_WATCH(pkt, fate, out_if, module) \
    do { if (gr_watch_enabled) gr_watch_note((pkt), (fate), (out_if), (module)); } while (0)

/* Record an ARP: `sender`/`target` are the 4 address bytes in WIRE order (as in the ARP packet),
 * `op` is 1 (request) or 2 (reply), `outcome` is "answered" / "learned" / "ignored" or "". */
void gr_watch_arp(char fate, int in_if, int out_if, int op, const unsigned char *sender,
                  const unsigned char *target, const char *outcome);

#define GR_WATCH_ARP(fate, in_if, out_if, op, sender, target, outcome) \
    do { if (gr_watch_enabled) \
             gr_watch_arp((fate), (in_if), (out_if), (op), (sender), (target), (outcome)); \
    } while (0)

/* CLI handlers (registered in cli.c). */
void watchCmd(void);
void ifstatCmd(void);

#endif
