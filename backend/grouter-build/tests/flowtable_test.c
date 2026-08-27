/*
 * flowtable_test.c — standalone unit test for openflow_flowtable.c.
 *
 * Drives the REAL flow table through its PUBLIC API only (the table itself is file-static,
 * which is right, so nothing here reaches inside). Covers the two behaviours that were
 * silently wrong and are easy to get wrong again:
 *
 *   1. LRU eviction — a full table accepts new rules by evicting the least recently
 *      matched entry, instead of refusing every further rule.
 *   2. Priority byte order — priorities survive a round trip, so a controller rule
 *      outranks the boot-time wildcard default.
 *
 * Why these matter. The table holds OPENFLOW_MAX_FLOWTABLE_ENTRIES (100) and the POX
 * forwarding apps match with ofp_match.from_packet(), i.e. ONE ENTRY PER MICROFLOW. A
 * traceroute sends ~90 probes each with a fresh UDP source port, so it filled the table and
 * every subsequent rule was refused with OFPFMFC_ALL_TABLES_FULL — the switch then punted
 * those packets to the controller forever with no way to recover. Separately, `priority`
 * was a uint32_t receiving a 16-bit NETWORK-order value, so on a little-endian host
 * priority 1 stored as 256 and priority 32768 as 128: the default rule outranked everything
 * the controller installed.
 *
 * Build:  gcc -O2 -I../../include flowtable_test.c ../../src/grouter/openflow_flowtable.c \
 *             -lpthread -o flowtable_test
 * Run:    ./flowtable_test      (exit 0 = all pass)
 *
 * Links libc and pthreads only, like delay_test.c. The stubs below stand in for the rest of
 * the gRouter -- including `verbose` (libslack) and htonll/ntohll (utils.c) -- so this test
 * links ONLY the flow table and a failure here is a flow-table failure and nothing else.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <arpa/inet.h>

#include "openflow_flowtable.h"

/* -- stubs: everything openflow_flowtable.c calls out to ------------------------------- */
int verbose(int level, const char *fmt, ...) { (void) level; (void) fmt; return 0; }
void error(const char *fmt, ...) { (void) fmt; }

/* utils.c; reimplemented here so the test links libc only */
uint64_t ntohll(uint64_t arg)
{
	return ((uint64_t) ntohl((uint32_t) arg) << 32) | ntohl((uint32_t) (arg >> 32));
}
uint64_t htonll(uint64_t arg) { return ntohll(arg); }

char *IP2Dot(char *buf, uchar *ip) { (void) ip; if (buf) buf[0] = 0; return buf; }
char *MAC2Colon(char *buf, uchar *mac) { (void) mac; if (buf) buf[0] = 0; return buf; }
uint16_t openflow_config_get_of_port_num(uint16_t n) { return n + 1; }
void openflow_ctrl_iface_send_flow_removed(openflow_flowtable_entry_type *e, uint8_t r)
{ (void) e; (void) r; }
int gpacketSize(gpacket_t *pkt) { (void) pkt; return 64; }

/* -- helpers -------------------------------------------------------------------------- */
static ofp_flow_mod *mk(uint16_t priority, uint16_t sport, uint16_t out_port)
{
	static char buf[sizeof(ofp_flow_mod) + sizeof(ofp_action_output)];
	ofp_flow_mod *fm = (ofp_flow_mod *) buf;
	memset(buf, 0, sizeof(buf));
	fm->header.length = htons(sizeof(ofp_flow_mod) + sizeof(ofp_action_output));
	fm->header.type = OFPT_FLOW_MOD;
	fm->command = OFPFC_ADD;
	/* an exact match distinguished by source port: one entry per microflow, which is what
	 * ofp_match.from_packet() produces and what fills the table in practice */
	fm->match.wildcards = htonl(0);
	fm->match.tp_src = htons(sport);
	fm->priority = htons(priority);
	ofp_action_output *a = (ofp_action_output *) &fm->actions[0];
	a->type = htons(OFPAT_OUTPUT);
	a->len = htons(sizeof(ofp_action_output));
	a->port = htons(out_port);
	return fm;
}

static int add_rule(uint16_t priority, uint16_t sport, uint16_t out_port)
{
	uint16_t error_type = 0, error_code = 0;
	return openflow_flowtable_modify(mk(priority, sport, out_port),
	                                 &error_type, &error_code);
}

static int failures = 0;
static void check(const char *what, int cond)
{
	printf("  %s  %s\n", cond ? "PASS" : "FAIL", what);
	if (!cond) failures++;
}

int main(void)
{
	const uint32_t cap = OPENFLOW_MAX_FLOWTABLE_ENTRIES;
	printf("flow table capacity: %" PRIu32 "\n\n", cap);

	printf("LRU eviction: a full table keeps accepting rules\n");
	openflow_flowtable_init();
	int accepted = 0, refused = 0;
	for (uint32_t i = 0; i < cap + 50; i++)
		(add_rule(32768, (uint16_t) (1000 + i), 2) == 0) ? accepted++ : refused++;
	printf("    %" PRIu32 " installs attempted: %d accepted, %d refused\n",
	       cap + 50, accepted, refused);
	check("no rule is refused once the table is full",
	      refused == 0 && accepted == (int) (cap + 50));

	printf("\npriority survives the round trip\n");
	openflow_flowtable_init();
	check("a low-priority rule installs", add_rule(1, 500, 2) == 0);
	check("a controller-priority rule installs", add_rule(32768, 501, 3) == 0);
	/* Read them back through the public dump. Before the fix this printed hard_timeout
	 * under the Priority label, so every reported priority was meaningless. */
	printf("    (entries below should report Priority 1 and 32768, not 0)\n");
	for (uint32_t i = 0; i < 3; i++)
		openflow_flowtable_print_entry(i);

	printf("\n%s (%d failing)\n", failures ? "FAILURES" : "all checks passed", failures);
	return failures != 0;
}
