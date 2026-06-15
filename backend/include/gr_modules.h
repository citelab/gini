/*
 * gr_modules.h  —  Z2 built-in inline modules (the droppable add-ons).
 *
 * Each factory returns a gr_module_t* conforming to the uniform process() ABI, so the
 * runner chains them regardless of language. Lua and native (Zig/C) student modules
 * plug in through the exact same ABI.
 */
#ifndef __GR_MODULES_H__
#define __GR_MODULES_H__

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

/* Lua scripting module (friendly tier) — only built with -Dlua=true (links liblua). */
gr_module_t *gr_mod_lua(const char *script);

#endif /* __GR_MODULES_H__ */
