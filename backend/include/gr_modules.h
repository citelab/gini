/*
 * gr_modules.h  —  Z2 built-in inline modules (the droppable add-ons).
 *
 * Each factory returns a gr_module_t* conforming to the uniform process() ABI, so the
 * runner chains them regardless of language. Lua and native (Zig/C) student modules
 * plug in through the exact same ABI.
 */
#ifndef __GR_MODULES_H__
#define __GR_MODULES_H__

#include <stdint.h>
#include "gr_module.h"

/* ACL / firewall: DROP packets whose dst IP matches deny_cidr (e.g. "10.0.3.0/24"). */
gr_module_t *gr_mod_acl(const char *deny_cidr);

/* NAT: rewrite the source IP to snat_ip (e.g. "203.0.113.1"); CONTINUE. */
gr_module_t *gr_mod_nat(const char *snat_ip);

/* Firewall: the legacy filter table as a module (full build / GR_LEGACY_MODULES). */
gr_module_t *gr_mod_filter(void);

/* Counter / tap: counts packets and CONTINUEs (a stand-in for capture/log). */
gr_module_t *gr_mod_counter(void);
long         gr_mod_counter_value(gr_module_t *m);

/* Rate limit: token-bucket policer — DROP packets over <pps>[/<burst>], else CONTINUE. */
gr_module_t *gr_mod_rate(const char *spec);
long         gr_mod_rate_drops(gr_module_t *m);

/* QoS classifier: mark matching packets with a DSCP ("<cidr>:<dscp>" or "<dscp>"); CONTINUE. */
gr_module_t *gr_mod_classify(const char *spec);

/* Tap / capture: mirror matching packets to a .pcap file ("<path>[@<cidr>]"); CONTINUE. */
gr_module_t *gr_mod_tap(const char *spec);
long         gr_mod_tap_count(gr_module_t *m);

/* Lua scripting module (friendly tier) — only built with -Dlua=true (links liblua). */
gr_module_t *gr_mod_lua(const char *script);

/* ---- shared packet accessors (used by Lua scripts and native modules) ------ *
 * A language-neutral view of the packet, so a module never re-derives byte offsets.
 * Addresses are host-order IPv4; the IP datagram starts at pkt->data.data[0].     */
uint32_t gr_pkt_ipsrc(gpacket_t *pkt);
uint32_t gr_pkt_ipdst(gpacket_t *pkt);
int      gr_pkt_proto(gpacket_t *pkt);
int      gr_pkt_ttl(gpacket_t *pkt);
int      gr_pkt_len(gpacket_t *pkt);
int      gr_parse_ipv4(const char *s, uint32_t *out_hostorder);

/* ---- native module registry ----------------------------------------------- *
 * Build a registered module by name (`gpipe add <name> [arg]`). Returns NULL if the
 * name is unknown. gr_module_names() lists the registered names for usage messages. */
gr_module_t *gr_module_create(const char *name, const char *arg);
const char  *gr_module_names(void);
int          gr_module_needs_arg(const char *name);   /* 1 yes, 0 no, -1 unknown */

#endif /* __GR_MODULES_H__ */
