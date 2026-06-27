/*
 * gr_module.h  —  Z1 module interfaces (over gpacket_t).
 *
 * Two contracts that the modularized gRouter is built on:
 *   1. gr_module_t  — the uniform pipeline-module ABI: process(gpacket) -> verdict.
 *                     This is the contract every DROPPABLE node in the Router Lab
 *                     conforms to (ACL, NAT, QoS, rate-limit, Lua, native, tap).
 *   2. gr_device_ops_t — the interface (port) driver contract, formalizing the
 *                     existing device_t fromdev/todev vtable (tun/tap/raw/eth).
 *
 * Additive: declaring these does not change existing behaviour. Modules are made to
 * conform incrementally in Z2 (the graph runner), guarded by the test harness.
 */
#ifndef __GR_MODULE_H__
#define __GR_MODULE_H__

#include "message.h"   /* gpacket_t */
#include "device.h"    /* device_t (the existing fromdev/todev vtable) */

/* Verdict a pipeline module returns for a packet — the SERIES composition contract. */
typedef enum
{
    GR_CONTINUE = 0,   /* pass to the next module in the pipeline                 */
    GR_DROP,           /* discard the packet                                      */
    GR_CONSUMED,       /* module took ownership (queued / replied / re-injected)  */
    GR_TO_HOST,        /* dst == self -> hand to the host stack (BRANCH)          */
    GR_FORWARD         /* forwarding decided; out_iface set                       */
} gr_action_t;

typedef struct
{
    gr_action_t action;
    int         out_iface;   /* valid when action == GR_FORWARD */
} gr_verdict_t;

/* A pipeline module — the droppable node. */
typedef struct gr_module
{
    const char  *type;        /* "acl", "nat", "rate", "lua", "native", "tap" ... */
    void        *state;       /* module-private state                             */
    int          (*init)(struct gr_module *self, const char *params);
    gr_verdict_t (*process)(struct gr_module *self, gpacket_t *pkt);
    void         (*destroy)(struct gr_module *self);
} gr_module_t;

/* The interface/port driver contract — formalizes the existing device_t vtable
 * (void *(*fromdev)(void*), void *(*todev)(void*)). tun = userspace UDP. */
typedef device_t gr_device_ops_t;

#endif /* __GR_MODULE_H__ */
