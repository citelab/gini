/*
 * host_stack.h  —  Z1 seal around the lwIP host stack.
 *
 * lwIP terminates UDP/TCP addressed TO the router (management, endpoints). Today
 * ip.c's IPProcessMyPacket calls UDPProcess()/TCPProcess() inline and lwIP reaches
 * into the global route_tbl[] directly. This boundary makes lwIP:
 *   - SEALED  : the only place that knows about UDPProcess/TCPProcess, and
 *   - OPTIONAL: a pure forwarder can build with -DGR_NO_HOST_STACK and not link lwIP
 *               (smaller binary, fewer threads). ICMP-to-self stays in the core.
 *
 * In the Router UI this is a TOGGLE ("terminate traffic here"), not a droppable node.
 */
#ifndef __HOST_STACK_H__
#define __HOST_STACK_H__

#include "message.h"   /* gpacket_t */

int host_stack_init(void);
int host_stack_enabled(void);

/* Hand a router-addressed UDP/TCP packet to the host stack.
 * Returns 1 if the host stack consumed it, 0 otherwise (caller handles, e.g. ICMP). */
int host_stack_input(gpacket_t *pkt);

#endif /* __HOST_STACK_H__ */
