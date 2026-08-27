/**
 * openflow_pkt_proc.c - OpenFlow packet processing
 */


#include <inttypes.h>
#include <arpa/inet.h>

#include <slack/std.h>
#include <slack/err.h>

#include "ethernet.h"   /* gpacketSize() -- real on-the-wire length */
#include "grouter.h"
#include "ip.h"
#include "openflow.h"
#include "openflow_config.h"
#include "openflow_flowtable.h"
#include "openflow_ctrl_iface.h"
#include "openflow_pkt_proc.h"
#include "protocols.h"
#include "tcp.h"
#include "udp.h"

// GNET packet core
static pktcore_t *packet_core;

/**
 * Creates a new packet identical to the specified packet but with a VLAN
 * header.
 *
 * @param packet The packet to add a VLAN header to.
 *
 * @return The packet with the VLAN header.
 */
static gpacket_t openflow_pkt_proc_add_vlan_header(gpacket_t *packet)
{
	gpacket_t new_packet;
	new_packet.frame = packet->frame;
	pkt_data_vlan_t vlan_data;
	COPY_MAC(&vlan_data.header.src, &packet->data.header.src);
	COPY_MAC(&vlan_data.header.dst, &packet->data.header.dst);
	vlan_data.header.tpid = htons(ETHERTYPE_IEEE_802_1Q);
	vlan_data.header.tci = 0;
	vlan_data.header.prot = packet->data.header.prot;
	memcpy(&vlan_data.data, &packet->data.data, DEFAULT_MTU);
	memcpy(&new_packet.data, &vlan_data, sizeof(vlan_data));
	return new_packet;
}

/**
 * Creates a new packet identical to the specified packet but without a VLAN
 * header.
 *
 * @param packet The packet to remove the VLAN header from.
 *
 * @return The packet without the VLAN header.
 */
static gpacket_t openflow_pkt_proc_remove_vlan_header(gpacket_t *packet)
{
	gpacket_t new_packet;
	new_packet.frame = packet->frame;
	pkt_data_vlan_t *vlan_data = (pkt_data_vlan_t *) &packet->data;
	COPY_MAC(&new_packet.data.header.src, &vlan_data->header.src);
	COPY_MAC(&new_packet.data.header.dst, &vlan_data->header.dst);
	new_packet.data.header.prot = vlan_data->header.prot;
	memcpy(&new_packet.data.data, &vlan_data->data, DEFAULT_MTU);
	return new_packet;
}

/**
 * Updates the IP (and, if applicable, the TCP or UDP) checksums in a packet.
 *
 * @param packet The packet with the checksums to be updated.
 */
static void openflow_pkt_proc_update_checksums(ip_packet_t *ip_packet)
{
	// IP packet
	ip_packet->ip_cksum = 0;
	ip_packet->ip_cksum = htons(
	        checksum((void *) ip_packet, ip_packet->ip_hdr_len * 2));

	if (ip_packet->ip_prot == TCP_PROTOCOL)
	{
		// TCP packet
		uint32_t ip_header_length = ip_packet->ip_hdr_len * 4;
		tcp_packet_type *tcp_packet = (tcp_packet_type *) ((uint8_t *) ip_packet
		        + ip_header_length);
		tcp_packet->checksum = tcp_checksum(ip_packet);
	}
	else if (ip_packet->ip_prot == UDP_PROTOCOL)
	{
		// UDP packet
		uint32_t ip_header_length = ip_packet->ip_hdr_len * 4;
		udp_packet_type *udp_packet = (udp_packet_type *) ((uint8_t *) ip_packet
		        + ip_header_length);
		udp_packet->checksum = udp_checksum(ip_packet);
	}
}

/**
 * Makes a copy of the specified packet and inserts it into the specified
 * queue.
 *
 * @param packet The packet to insert into the queue.
 * @param queue  The queue to insert the packet into.
 *
 * @return 0, or a negative value if an error occurred.
 */
static int32_t openflow_pkt_proc_send_packet_to_queue(gpacket_t *packet,
        simplequeue_t *queue)
{
	gpacket_t *new_packet = malloc(sizeof(gpacket_t));
	memcpy(new_packet, packet, sizeof(gpacket_t));
	int32_t ret = writeQueue(queue, new_packet, sizeof(gpacket_t));
	if (ret == 1)
	{
		verbose(1, "[openflow_pkt_proc_send_packet_to_queue]:: Failed to"
				" write packet to queue.");
		return OPENFLOW_PKT_PROC_ERR_QUEUE;
	}
	return 0;
}

/**
 * Forwards the specified packet to the specified output port.
 *
 * @param packet  The packet to forward to the output port.
 * @param of_port The OpenFlow port number to send the packet to.
 * @param flood   1 if the packet is being sent as part of a flood operation;
 *                otherwise 0.
 *
 * @return 0, or a negative value if an error occurred.
 */
static int32_t openflow_pkt_proc_forward_packet_to_port(gpacket_t *packet,
        uint16_t of_port, uint8_t flood)
{
	ofp_phy_port *port = openflow_config_get_phy_port(of_port);

	// Return if port does not exist
	if (port == NULL) return 0;

	// Return if port is administratively down or packet is a flood packet but
	// flooding is disabled for this port
	uint32_t config = ntohl(port->config);
	if (config & OFPPC_PORT_DOWN)
	{
		free(port);
		return 0;
	}
	if (flood && (config & OFPPC_NO_FLOOD))
	{
		free(port);
		return 0;
	}

	// Return if port is physically down
	uint32_t state = ntohl(port->state);
	if (state & OFPPS_LINK_DOWN)
	{
		free(port);
		return 0;
	}

	free(port);

	ofp_port_stats *stats = openflow_config_get_port_stats(of_port);
	stats->tx_packets = htonll(ntohll(stats->tx_packets) + 1);
	/* real length, not sizeof(pkt_data_t): the hardcoded struct size made every
	 * port read exactly 1518 bytes/packet, which is actively misleading when
	 * these counters are used to diagnose the packet path. */
	stats->tx_bytes = htonll(ntohll(stats->tx_bytes) + gpacketSize(packet));
	openflow_config_set_port_stats(of_port, stats);
	free(stats);

	uint32_t gnet_port_num = openflow_config_get_gnet_port_num(of_port);
	packet->frame.dst_interface = gnet_port_num;
	packet->frame.openflow = 1;
	return openflow_pkt_proc_send_packet_to_queue(packet, packet_core->outputQ);
}

/**
 * Initializes the OpenFlow packet processor.
 */
void openflow_pkt_proc_init(pktcore_t *core)
{
	packet_core = core;
	openflow_flowtable_init();
}

/**
 * Processes the specified packet using the OpenFlow packet processor.
 *
 * @param packet The packet to be handled using the OpenFlow packet processor.
 *
 * @return 0, or a negative value if an error occurred.
 */
int32_t openflow_pkt_proc_handle_packet(gpacket_t *packet)
{
	// Update statistics for input port
	uint16_t of_port = openflow_config_get_of_port_num(
	        packet->frame.src_interface);
	ofp_port_stats *stats = openflow_config_get_port_stats(of_port);
	stats->rx_packets = htonll(ntohll(stats->rx_packets) + 1);
	stats->rx_bytes = htonll(ntohll(stats->rx_bytes) + gpacketSize(packet));
	openflow_config_set_port_stats(of_port, stats);
	free(stats);

	if (ntohs(packet->data.header.prot) == IP_PROTOCOL)
	{
		ip_packet_t *ip_packet = (ip_packet_t *) &packet->data.data;
		if (!(ntohs(ip_packet->ip_frag_off) & 0x1fff)
		        && !(ntohs(ip_packet->ip_frag_off) & 0x2000))
		{
			// Fragmented IP packet
			uint16_t flags = ntohs(openflow_config_get_switch_config_flags());
			if (flags & OFPC_FRAG_DROP)
			{
				// Switch configured to drop fragmented IP packets
				verbose(2, "[openflow_pkt_proc_handle_packet]::"
						" Dropping fragmented IP packet.");
				return 0;
			}
		}
	}

	openflow_flowtable_entry_type *matching_entry =
	        openflow_flowtable_get_entry_for_packet(packet);
	if (matching_entry != NULL)
	{
		verbose(2, "[openflow_pkt_proc_handle_packet]:: Performing actions"
				" on packet with flowtable match.");
		uint8_t action_performed = 0;
		uint32_t i;
		for (i = 0; i < OPENFLOW_MAX_ACTIONS; i++)
		{
			if (matching_entry->actions[i].active)
			{
				int32_t ret = openflow_pkt_proc_perform_action(
				        &matching_entry->actions[i].header, packet);
				if (ret >= 0) action_performed = 1;
			}
		}

		if (!action_performed)
		{
			verbose(2, "[openflow_pkt_proc_handle_packet]:: Dropping packet"
					" with no valid actions.");
		}

		free(matching_entry);
		return 0;
	}
	else
	{
		verbose(2, "[openflow_pkt_proc_handle_packet]:: Forwarding packet"
				" with no flowtable match to controller.");
		int32_t ret = openflow_ctrl_iface_send_packet_in(packet, OFPR_NO_MATCH);
		return ret;
	}
}

/**
 * Performs the specified action on the specified packet.
 *
 * @param header The action to perform on the packet.
 * @param packet The packet on which the action should be performed.
 *
 * @return 0, or a negative value if an error occurred.
 */
int32_t openflow_pkt_proc_perform_action(ofp_action_header *header,
        gpacket_t *packet)
{
	uint16_t header_type = ntohs(header->type);
	if (header_type == OFPAT_OUTPUT)
	{
		// Send packet to output port
		ofp_action_output *output_action = (ofp_action_output *) header;
		uint16_t port = ntohs(output_action->port);
		if (port == OFPP_IN_PORT)
		{
			// Send packet to input interface
			verbose(2, "[openflow_pkt_proc_perform_action]:: Performing"
					" OFPAT_OUTPUT action with OFPP_IN_PORT.");
			uint16_t openflow_port_num = openflow_config_get_of_port_num(
			        packet->frame.src_interface);
			return openflow_pkt_proc_forward_packet_to_port(packet,
			        openflow_port_num, 0);
		}
		else if (port == OFPP_TABLE)
		{
			// OpenFlow pipeline handling
			verbose(2, "[openflow_pkt_proc_perform_action]:: Performing"
					" OFPAT_OUTPUT action with OFPP_TABLE.");
			return openflow_pkt_proc_handle_packet(packet);
		}
		else if (port == OFPP_NORMAL)
		{
			// Normal router handling
			verbose(2, "[openflow_pkt_proc_perform_action]:: Performing"
					" OFPAT_OUTPUT action with OFPP_NORMAL.");
			gpacket_t *new_packet = malloc(sizeof(gpacket_t));
			memcpy(new_packet, packet, sizeof(gpacket_t));
			int32_t ret = enqueuePacket(packet_core, new_packet,
			        sizeof(gpacket_t), 0);
			if (ret == 1)
			{
				verbose(1, "[openflow_pkt_proc_perform_action]:: Failed to"
						" enqueue packet for OFPP_NORMAL action.");
				return OPENFLOW_PKT_PROC_ERR_QUEUE;
			}
			return 0;
		}
		else if (port == OFPP_FLOOD)
		{
			// Forward packet to all ports with flooding enabled except source
			// port
			verbose(2, "[openflow_pkt_proc_perform_action]:: Performing"
					" OFPAT_OUTPUT action with OFPP_FLOOD.");
			uint32_t i;
			for (i = 1; i <= OPENFLOW_MAX_PHYSICAL_PORTS; i++)
			{
				uint16_t gnet_port_num = openflow_config_get_gnet_port_num(i);
				if (gnet_port_num != packet->frame.src_interface)
				{
					int32_t ret = openflow_pkt_proc_forward_packet_to_port(
					        packet, i, 1);
					if (ret < 0) return ret;
				}
			}
			return 0;
		}
		else if (port == OFPP_ALL)
		{
			// Forward packet to all ports except source port
			verbose(2, "[openflow_pkt_proc_perform_action]:: Performing"
					" OFPAT_OUTPUT action with OFPP_ALL.");
			uint32_t i;
			for (i = 1; i <= OPENFLOW_MAX_PHYSICAL_PORTS; i++)
			{
				uint16_t gnet_port_num = openflow_config_get_gnet_port_num(i);
				if (gnet_port_num != packet->frame.src_interface)
				{
					int32_t ret = openflow_pkt_proc_forward_packet_to_port(
					        packet, i, 0);
					if (ret < 0) return ret;
				}
			}
			return 0;
		}
		else if (port == OFPP_CONTROLLER)
		{
			// Forward packet to controller
			verbose(2, "[openflow_pkt_proc_perform_action]:: Performing"
					" OFPAT_OUTPUT action with OFPP_CONTROLLER.");
			return openflow_ctrl_iface_send_packet_in(packet, OFPR_ACTION);
		}
		else if (port == OFPP_LOCAL)
		{
			// Forward packet to controller packet processing
			verbose(2, "[openflow_pkt_proc_perform_action]:: Performing"
					" OFPAT_OUTPUT action with OFPP_LOCAL.");
			return openflow_ctrl_iface_parse_packet(packet);
		}
		else
		{
			// Forward packet to specified port
			verbose(2, "[openflow_pkt_proc_perform_action]:: Performing"
					" OFPAT_OUTPUT action with port %" PRIu16 ".", port);
			return openflow_pkt_proc_forward_packet_to_port(packet, port, 0);
		}
	}
	else if (header_type == OFPAT_SET_VLAN_VID)
	{
		// Modify VLAN ID
		verbose(2, "[openflow_pkt_proc_perform_action]:: Performing"
				" OFPAT_SET_VLAN_VID action.");
		ofp_action_vlan_vid *vlan_vid_action = (ofp_action_vlan_vid *) header;
		if (ntohs(packet->data.header.prot) == ETHERTYPE_IEEE_802_1Q)
		{
			// Existing VLAN header
			pkt_data_vlan_t *vlan_data = (pkt_data_vlan_t *) &packet->data;
			vlan_data->header.tci = vlan_vid_action->vlan_vid;
		}
		else
		{
			// No VLAN header
			gpacket_t new_packet = openflow_pkt_proc_add_vlan_header(packet);
			memcpy(packet, &new_packet, sizeof(gpacket_t));
			pkt_data_vlan_t *vlan_data = (pkt_data_vlan_t *) &packet->data;
			vlan_data->header.tci = vlan_vid_action->vlan_vid;
		}
		return 0;
	}
	else if (header_type == OFPAT_SET_VLAN_PCP)
	{
		// Modify VLAN priority
		verbose(2, "[openflow_pkt_proc_perform_action]:: Performing"
				" OFPAT_SET_VLAN_PCP action.");
		ofp_action_vlan_pcp *vlan_pcp_action = (ofp_action_vlan_pcp *) header;
		if (ntohs(packet->data.header.prot) == ETHERTYPE_IEEE_802_1Q)
		{
			// Existing VLAN header
			pkt_data_vlan_t *vlan_data = (pkt_data_vlan_t *) &packet->data;
			vlan_data->header.tci = htons(
			        ntohs(vlan_data->header.tci) & 0x1fff);
			vlan_data->header.tci = htons(
			        (ntohs(vlan_pcp_action->vlan_pcp) << 13)
			                | ntohs(vlan_data->header.prot));
		}
		else
		{
			// No VLAN header
			gpacket_t new_packet = openflow_pkt_proc_add_vlan_header(packet);
			memcpy(packet, &new_packet, sizeof(gpacket_t));
			pkt_data_vlan_t *vlan_data = (pkt_data_vlan_t *) &packet->data;
			vlan_data->header.tci = htons(
			        ntohs(vlan_pcp_action->vlan_pcp) << 13);
		}
		return 0;
	}
	else if (header_type == OFPAT_STRIP_VLAN)
	{
		// Remove VLAN header
		verbose(2, "[openflow_pkt_proc_perform_action]:: Performing"
				" OFPAT_STRIP_VLAN action.");
		if (ntohs(packet->data.header.prot) == ETHERTYPE_IEEE_802_1Q)
		{
			gpacket_t new_packet = openflow_pkt_proc_remove_vlan_header(packet);
			memcpy(packet, &new_packet, sizeof(gpacket_t));
		}
		return 0;
	}
	else if (header_type == OFPAT_SET_DL_SRC)
	{
		// Modify Ethernet source MAC address
		verbose(2, "[openflow_pkt_proc_perform_action]:: Performing"
				" OFPAT_SET_DL_SRC action.");
		ofp_action_dl_addr *dl_addr_action = (ofp_action_dl_addr *) header;
		COPY_MAC(&packet->data.header.src, &dl_addr_action->dl_addr);
		return 0;
	}
	else if (header_type == OFPAT_SET_DL_DST)
	{
		// Modify Ethernet destination MAC address
		verbose(2, "[openflow_pkt_proc_perform_action]:: Performing"
				" OFPAT_SET_DL_DST action.");
		ofp_action_dl_addr *dl_addr_action = (ofp_action_dl_addr *) header;
		COPY_MAC(&packet->data.header.dst, &dl_addr_action->dl_addr);
		return 0;
	}
	else if (header_type == OFPAT_SET_NW_SRC)
	{
		// Modify IP source address
		verbose(2, "[openflow_pkt_proc_perform_action]:: Performing"
				" OFPAT_SET_NW_SRC action.");
		ofp_action_nw_addr *nw_addr_action = (ofp_action_nw_addr *) header;
		if (ntohs(packet->data.header.prot) == IP_PROTOCOL)
		{
			ip_packet_t *ip_packet = (ip_packet_t *) &packet->data.data;
			if (!(ntohs(ip_packet->ip_frag_off) & 0x1fff)
			        && !(ntohs(ip_packet->ip_frag_off) & 0x2000))
			{
				// IP packet is not fragmented
				COPY_IP(&ip_packet->ip_src, &nw_addr_action->nw_addr);
				openflow_pkt_proc_update_checksums(ip_packet);
			}
		}
		return 0;
	}
	else if (header_type == OFPAT_SET_NW_DST)
	{
		// Modify IP destination address
		verbose(2, "[openflow_pkt_proc_perform_action]:: Performing"
				" OFPAT_SET_NW_DST action.");
		ofp_action_nw_addr *nw_addr_action = (ofp_action_nw_addr *) header;
		if (ntohs(packet->data.header.prot) == IP_PROTOCOL)
		{
			ip_packet_t *ip_packet = (ip_packet_t *) &packet->data.data;
			if (!(ntohs(ip_packet->ip_frag_off) & 0x1fff)
			        && !(ntohs(ip_packet->ip_frag_off) & 0x2000))
			{
				// IP packet is not fragmented
				COPY_IP(&ip_packet->ip_dst, &nw_addr_action->nw_addr);
				openflow_pkt_proc_update_checksums(ip_packet);
			}
		}
		return 0;
	}
	else if (header_type == OFPAT_SET_NW_TOS)
	{
		// Modify IP type of service
		verbose(2, "[openflow_pkt_proc_perform_action]:: Performing"
				" OFPAT_SET_NW_TOS action.");
		ofp_action_nw_tos *nw_tos_action = (ofp_action_nw_tos *) header;
		if (ntohs(packet->data.header.prot) == IP_PROTOCOL)
		{
			ip_packet_t *ip_packet = (ip_packet_t *) &packet->data.data;
			if (!(ntohs(ip_packet->ip_frag_off) & 0x1fff)
			        && !(ntohs(ip_packet->ip_frag_off) & 0x2000))
			{
				// IP packet is not fragmented
				ip_packet->ip_tos = nw_tos_action->nw_tos;
				openflow_pkt_proc_update_checksums(ip_packet);
			}
		}
		return 0;
	}
	else if (header_type == OFPAT_SET_TP_SRC)
	{
		// Modify TCP/UDP source port
		verbose(2, "[openflow_pkt_proc_perform_action]:: Performing"
				" OFPAT_SET_TP_SRC action.");
		ofp_action_tp_port *tp_port_action = (ofp_action_tp_port *) header;
		if (ntohs(packet->data.header.prot) == IP_PROTOCOL)
		{
			ip_packet_t *ip_packet = (ip_packet_t *) &packet->data.data;
			if (!(ntohs(ip_packet->ip_frag_off) & 0x1fff)
			        && !(ntohs(ip_packet->ip_frag_off) & 0x2000))
			{
				// IP packet is not fragmented
				if (ip_packet->ip_prot == TCP_PROTOCOL)
				{
					uint32_t ip_header_length = ip_packet->ip_hdr_len * 4;
					tcp_packet_type *tcp_packet =
					        (tcp_packet_type *) ((uint8_t *) ip_packet
					                + ip_header_length);
					tcp_packet->src_port = tp_port_action->tp_port;
					openflow_pkt_proc_update_checksums(ip_packet);
				}
				else if (ip_packet->ip_prot == UDP_PROTOCOL)
				{
					uint32_t ip_header_length = ip_packet->ip_hdr_len * 4;
					udp_packet_type *udp_packet =
					        (udp_packet_type *) ((uint8_t *) ip_packet
					                + ip_header_length);
					udp_packet->src_port = tp_port_action->tp_port;
					openflow_pkt_proc_update_checksums(ip_packet);
				}
			}
		}
		return 0;
	}
	else if (header_type == OFPAT_SET_TP_DST)
	{
		// Modify TCP/UDP destination port
		verbose(2, "[openflow_pkt_proc_perform_action]:: Performing"
				" OFPAT_SET_TP_DST action.");
		ofp_action_tp_port *tp_port_action = (ofp_action_tp_port *) header;
		if (ntohs(packet->data.header.prot) == IP_PROTOCOL)
		{
			ip_packet_t *ip_packet = (ip_packet_t *) &packet->data.data;
			if (!(ntohs(ip_packet->ip_frag_off) & 0x1fff)
			        && !(ntohs(ip_packet->ip_frag_off) & 0x2000))
			{
				// IP packet is not fragmented
				if (ip_packet->ip_prot == TCP_PROTOCOL)
				{
					uint32_t ip_header_length = ip_packet->ip_hdr_len * 4;
					tcp_packet_type *tcp_packet =
					        (tcp_packet_type *) ((uint8_t *) ip_packet
					                + ip_header_length);
					tcp_packet->dst_port = tp_port_action->tp_port;
					openflow_pkt_proc_update_checksums(ip_packet);
				}
				else if (ip_packet->ip_prot == UDP_PROTOCOL)
				{
					uint32_t ip_header_length = ip_packet->ip_hdr_len * 4;
					udp_packet_type *udp_packet =
					        (udp_packet_type *) ((uint8_t *) ip_packet
					                + ip_header_length);
					udp_packet->dst_port = tp_port_action->tp_port;
					openflow_pkt_proc_update_checksums(ip_packet);
				}
			}
		}
		return 0;
	}
	else
	{
		verbose(1, "[openflow_pkt_proc_perform_action]:: Unrecognized"
				" action %" PRIu16 ".", header_type);
		return OPENFLOW_PKT_PROC_ERR_ACTION_INVALID;
	}
}
