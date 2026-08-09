/*
 * gr_delay_ctl.h  —  runtime control for the router's two link-delay lines.
 *
 * The gRouter carries at most one ingress line (in front of enqueuePacket) and one egress line
 * (in front of the interface todev). They are per-router and shared across all interfaces, which
 * matches the Router Lab's one-view-per-router model: a delayed router is a long hop. Set them
 * from the CLI (`delay ...`) or from ROUTER_CONFIG; the forwarding path only ever reads the two
 * pointers below and, if non-NULL, hands the packet to the line.
 */
#ifndef __GR_DELAY_CTL_H__
#define __GR_DELAY_CTL_H__

#include "gr_delay.h"

/* The active lines (NULL == that direction has no delay). Read on the fast path. */
extern gr_delayline_t *gr_ingress_line;
extern gr_delayline_t *gr_egress_line;

/* egress==0 configures the ingress line, egress==1 the egress line. Creates the line on first
 * use (wiring the right emit callback) or retunes it if it already exists. */
void gr_delay_set(int egress, double base_ms, double jitter_ms, double corr, int limit);

/* Turn a direction off and free its line (drops anything still held). */
void gr_delay_off(int egress);

/* Human-readable status for `delay show`; returns bytes written. */
int gr_delay_describe(char *buf, int n);

/* Release callbacks — defined where the seam lives (packetcore.c / gnet.c). */
void gr_ingress_emit(void *pkt);   /* re-injects into the packet core */
void gr_egress_emit(void *pkt);    /* sends out the resolved interface */

#endif /* __GR_DELAY_CTL_H__ */
