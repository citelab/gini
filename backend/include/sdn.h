/*
 * sdn.h  —  Z1 seal: OpenFlow as the INGRESS MODE, not a pipeline branch.
 *
 * In OpenFlow mode the flow table owns ingress (the front door); its NORMAL action
 * delegates to the legacy pipeline. A router/port is legacy OR openflow — the stream
 * is not split mid-pipeline. This boundary keeps the core from reaching into the flow
 * table directly (today the `frame.openflow` bit + the openflow worker do this).
 *
 * In the Router UI this is a MODE switch (legacy/openflow), not a droppable node.
 */
#ifndef __SDN_H__
#define __SDN_H__

#include "message.h"   /* gpacket_t */

typedef enum
{
    SDN_MODE_LEGACY = 0,   /* classic pipeline is the front door */
    SDN_MODE_OPENFLOW      /* flow table is the front door       */
} sdn_mode_t;

sdn_mode_t sdn_mode(void);
void       sdn_set_mode(sdn_mode_t m);

typedef enum
{
    SDN_CONSUMED = 0,   /* flow table handled it (forward/drop/controller/NORMAL) */
    SDN_NORMAL          /* hand to the legacy pipeline (reserved; see sdn.c)       */
} sdn_result_t;

/* Front-door entry at ingress when in OpenFlow mode. Wraps the flow table. */
sdn_result_t sdn_ingress(gpacket_t *pkt);

#endif /* __SDN_H__ */
