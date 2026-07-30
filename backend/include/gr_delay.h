/*
 * gr_delay.h  —  link-delay lines for the gRouter (ingress / egress holding queues).
 *
 * A "delay line" is a FIFO holding queue with a dedicated release thread. Every packet
 * pushed into it is held for a sampled amount of time and then handed to an emit callback,
 * IN ORDER. It models the propagation/queueing delay of a link so GINI can build topologies
 * with realistic latency (and jitter), turning it from a functional-topology emulator into a
 * performance emulator.
 *
 * Design (see the book's element-ladder discussion):
 *   - Placed at the pipeline EDGES, not inline: an ingress line in front of enqueuePacket(),
 *     an egress line in front of the interface todev(). The forwarding core is untouched
 *     except for one hook call at each seam.
 *   - The release thread NEVER blocks the forwarding path: producers just append and return.
 *   - Order-preserving: releases are kept monotonic, so a plain FIFO is correct (the head is
 *     always the earliest release). A backstop clamp guarantees order even under a large swing.
 *   - Correlated jitter (AR(1)): the delay wanders slowly rather than jumping per packet, so
 *     order is preserved naturally (the clamp almost never fires) and the jitter stays real.
 *     This is netem's delay/jitter/correlation model.
 *   - Bounded: the holding queue has a max depth (the link's buffer); overflow drops, and the
 *     caller keeps ownership of a dropped packet so it can free/count it as it would normally.
 *
 * The line is generic: it holds `void *pkt` and knows nothing about gpacket_t, so it is unit
 * testable with libc + pthreads alone.
 */
#ifndef __GR_DELAY_H__
#define __GR_DELAY_H__

/* Called on the release thread when a held packet's time is up. The line has already given
 * up ownership of `pkt`; the callback is responsible for it thereafter (forward or free). */
typedef void (*gr_delay_emit_fn)(void *pkt);

typedef struct gr_delayline gr_delayline_t;

/* Create a delay line.
 *   emit      : release callback (required).
 *   base_ms   : mean delay in milliseconds (>= 0).
 *   jitter_ms : std-dev of the jitter component in milliseconds (>= 0; 0 => constant delay).
 *   corr      : AR(1) correlation of the jitter, in [0,1). 0 => IID jitter; higher => burstier.
 *   limit     : max packets held at once (<= 0 => GR_DELAY_DEFAULT_LIMIT).
 * Returns NULL on bad args or allocation failure. Starts the release thread. */
gr_delayline_t *gr_delay_create(gr_delay_emit_fn emit,
                                double base_ms, double jitter_ms, double corr, int limit);

/* Retune an existing line at run time (thread-safe). Same argument meaning as create. */
void gr_delay_config(gr_delayline_t *dl,
                     double base_ms, double jitter_ms, double corr, int limit);

/* Push a packet into the line.
 *   returns 1  : accepted — the line now owns `pkt` and will emit it later, in order.
 *   returns 0  : rejected (queue full) — the caller STILL OWNS `pkt` (free/count it). */
int gr_delay_push(gr_delayline_t *dl, void *pkt);

/* Introspection (for `delay show` and tests). */
long gr_delay_held(gr_delayline_t *dl);      /* packets currently in the holding queue */
long gr_delay_passed(gr_delayline_t *dl);    /* total released so far                  */
long gr_delay_dropped(gr_delayline_t *dl);   /* total dropped for a full queue         */
double gr_delay_base_ms(gr_delayline_t *dl);
double gr_delay_jitter_ms(gr_delayline_t *dl);
double gr_delay_corr(gr_delayline_t *dl);
int gr_delay_limit(gr_delayline_t *dl);

/* Stop the release thread (flushing/emitting nothing more) and free the line. */
void gr_delay_destroy(gr_delayline_t *dl);

#define GR_DELAY_DEFAULT_LIMIT 4096

#endif /* __GR_DELAY_H__ */
