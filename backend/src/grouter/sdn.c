/*
 * sdn.c  —  Z1 OpenFlow adapter. The boundary between the core and the flow table.
 *
 * Today, when openflow is enabled the device drivers set the frame.openflow bit and
 * enqueuePacket() shunts the packet to the openflow worker, whose handler forwards,
 * drops, sends PACKET_IN, or re-injects NORMAL packets back into the legacy pipeline
 * (via enqueuePacket(..., openflow=0)). So the handler already does NORMAL itself;
 * sdn_ingress() therefore reports SDN_CONSUMED. SDN_NORMAL is reserved for the cleaner
 * future where the front door returns and the caller runs the legacy pipeline inline.
 */
#include "sdn.h"
#include "grouter.h"             /* router_config rconfig */
#include "openflow_pkt_proc.h"   /* openflow_pkt_proc_handle_packet */

extern router_config rconfig;

sdn_mode_t sdn_mode(void)
{
    return rconfig.openflow ? SDN_MODE_OPENFLOW : SDN_MODE_LEGACY;
}

void sdn_set_mode(sdn_mode_t m)
{
    rconfig.openflow = (m == SDN_MODE_OPENFLOW) ? 1 : 0;
}

sdn_result_t sdn_ingress(gpacket_t *pkt)
{
    (void)openflow_pkt_proc_handle_packet(pkt);  /* handles forward/drop/PACKET_IN/NORMAL */
    return SDN_CONSUMED;
}
