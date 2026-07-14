/**
 * openflow_flowtable.c - OpenFlow flowtable
 */

#include "openflow_flowtable.h"

#include <inttypes.h>
#include <string.h>
#include <time.h>
#include <arpa/inet.h>

#include <slack/std.h>
#include <slack/err.h>

#include "arp.h"
#include "gnet.h"
#include "grouter.h"
#include "message.h"
#include "icmp.h"
#include "ip.h"
#include "openflow.h"
#include "openflow_config.h"
#include "openflow_ctrl_iface.h"
#include "openflow_pkt_proc.h"
#include "protocols.h"
#include "simplequeue.h"
#include "tcp.h"
#include "udp.h"

// OpenFlow flowtable
static openflow_flowtable_type *flowtable;
static pthread_mutex_t flowtable_mutex;

// Forward declaration of debugging functions
static void openflow_flowtable_print_entry_no_lock(uint32_t index);

/**
 * Set the specified ofp_flow_stats struct to its defaults.
 */
static void openflow_flowtable_set_flow_stats_defaults(ofp_flow_stats *stats)
{
	// Clear stats
	memset(stats, 0, sizeof(ofp_flow_stats));
	stats->length = htons(sizeof(ofp_flow_stats));
	stats->table_id = 0;
}

/**
 * Set flowtable defaults.
 */
static void openflow_flowtable_set_defaults(void)
{
	pthread_mutex_lock(&flowtable_mutex);

	// Clear flowtable
	memset(flowtable, 0, sizeof(openflow_flowtable_type));

	// Initialize table stats
	flowtable->stats.table_id = 0;
	strncpy(flowtable->stats.name, OPENFLOW_TABLE_NAME,
	OFP_MAX_TABLE_NAME_LEN);
	flowtable->stats.name[OFP_MAX_TABLE_NAME_LEN - 1] = '\0';
	flowtable->stats.max_entries = htonl(OPENFLOW_MAX_FLOWTABLE_ENTRIES);
	flowtable->stats.wildcards = htonl(OFPFW_ALL);

	// Default flowtable entry (send all packets to normal router processing)
	ofp_flow_mod *flow_mod = calloc(sizeof(ofp_flow_mod) +
			sizeof(ofp_action_output), 1);

	flow_mod->header.length = htons(sizeof(ofp_flow_mod) +
			sizeof(ofp_action_output));
	flow_mod->header.type = OFPT_FLOW_MOD;
	flow_mod->header.version = OFP_VERSION;
	flow_mod->header.xid = 0;

	flow_mod->command = OFPFC_ADD;
	flow_mod->match.wildcards = htonl(OFPFW_ALL);
	flow_mod->priority = htonl(1);

	flow_mod->actions[0].type = htons(OFPAT_OUTPUT);
	flow_mod->actions[0].len = htons(sizeof(ofp_action_output));
	((ofp_action_output *) &flow_mod->actions[0])->port = htons(OFPP_NORMAL);

	pthread_mutex_unlock(&flowtable_mutex);

	openflow_flowtable_modify(flow_mod, NULL, NULL);
	free(flow_mod);
}

/**
 * Initializes the flowtable.
 */
void openflow_flowtable_init(void)
{
	pthread_mutex_lock(&flowtable_mutex);
	flowtable = malloc(sizeof(openflow_flowtable_type));
	pthread_mutex_unlock(&flowtable_mutex);

	openflow_flowtable_set_defaults();
}

/**
 * Frees the flow table, avoiding memory leaks.
 */
void openflow_flowtable_release(void)
{
	pthread_mutex_lock(&flowtable_mutex);

	flowtable = malloc(sizeof(openflow_flowtable_type));
	if (flowtable)
	{
		free(flowtable);
	}
	flowtable = NULL;

	pthread_mutex_unlock(&flowtable_mutex);
}

/**
 * Compares two IP addresses.
 *
 * @param ip_1   The first IP address to compare.
 * @param ip_2   The second IP address to compare.
 * @param ip_len The number of bits in the IP address to compare.
 *
 * @return 1 if the IP addresses are the same, 0 otherwise.
 */
static uint8_t openflow_flowtable_ip_compare(uint32_t ip_1, uint32_t ip_2,
        uint8_t ip_len)
{
	return (ip_1 >> ((OFPFW_NW_DST_ALL >> OFPFW_NW_DST_SHIFT) - ip_len))
	        == (ip_2 >> ((OFPFW_NW_DST_ALL >> OFPFW_NW_DST_SHIFT) - ip_len)) ?
	        1 : 0;
}

/**
 * Determines whether the specified OpenFlow match matches the specified packet.
 *
 * @param match  The match to test the packet against.
 * @param packet The packet to test.
 *
 * @return 1 if the packet matches the match, 0 otherwise.
 */
static uint8_t openflow_flowtable_match_packet(ofp_match *match,
        gpacket_t *packet)
{
	// Default headers
	uint16_t in_port = htons(
	        openflow_config_get_of_port_num(packet->frame.src_interface));
	uint8_t dl_src[OFP_ETH_ALEN];
	uint8_t dl_dst[OFP_ETH_ALEN];
	uint16_t dl_vlan = 0;
	uint8_t dl_vlan_pcp = 0;
	uint16_t dl_type = packet->data.header.prot;
	uint8_t nw_tos = 0;
	uint8_t nw_proto = 0;
	uint32_t nw_src = 0;
	uint32_t nw_dst = 0;
	uint16_t tp_src = 0;
	uint16_t tp_dst = 0;
	memcpy(&dl_src, packet->data.header.src, OFP_ETH_ALEN);
	memcpy(&dl_dst, packet->data.header.dst, OFP_ETH_ALEN);

	// Accept match if all fields wildcard is present in match
	if (ntohl(match->wildcards) == OFPFW_ALL)
	{
		verbose(2, "[openflow_flowtable_match_packet]:: Packet matched (all"
				" field wildcard).");
		return 1;
	}

	// Set headers for IEEE 802.3 Ethernet frame
	if (ntohs(packet->data.header.prot) < OFP_DL_TYPE_ETH2_CUTOFF)
	{
		if (packet->data.data[0] == IEEE_802_2_DSAP_SNAP)
		{
			// SNAP
			if (packet->data.data[2] & IEEE_802_2_CTRL_8_BITS)
			{
				// 8-bit control field
				uint32_t oui;
				memcpy(&oui, &packet->data.data[3], sizeof(uint8_t) * 3);
				if (ntohl(oui) == 0)
				{
					memcpy(&dl_type, &packet->data.data[6],
					        sizeof(uint8_t) * 2);
				}
				else
				{
					dl_type = htons(OFP_DL_TYPE_NOT_ETH_TYPE);
				}

			}
			else
			{
				// 16-bit control field
				uint32_t oui;
				memcpy(&oui, &packet->data.data[4], sizeof(uint8_t) * 3);
				if (ntohl(oui) == 0)
				{
					memcpy(&dl_type, &packet->data.data[7],
					        sizeof(uint8_t) * 2);
				}
				else
				{
					dl_type = htons(OFP_DL_TYPE_NOT_ETH_TYPE);
				}
			}
		}
		else
		{
			// No SNAP
			dl_type = htons(OFP_DL_TYPE_NOT_ETH_TYPE);
		}
	}

	// Set headers for IEEE 802.1Q Ethernet frame
	if (ntohs(packet->data.header.prot) == ETHERTYPE_IEEE_802_1Q)
	{
		verbose(2, "[openflow_flowtable_match_packet]:: Setting headers for"
				" IEEE 802.1Q Ethernet frame.");
		pkt_data_vlan_t *vlan_data = (pkt_data_vlan_t *) &packet->data;
		dl_vlan = htons(ntohs(vlan_data->header.tci) & 0xFFF);
		dl_vlan_pcp = htons(ntohs(vlan_data->header.tci) >> 13);
		dl_type = vlan_data->header.prot;
	}
	else
	{
		dl_vlan = ntohs(OFP_VLAN_NONE);
	}

	// Set headers for ARP packet
	if (ntohs(packet->data.header.prot) == ARP_PROTOCOL)
	{
		verbose(2, "[openflow_flowtable_match_packet]:: Setting headers for"
				" ARP.");
		arp_packet_t *arp_packet = (arp_packet_t *) &packet->data.data;
		nw_proto = ntohs(arp_packet->arp_opcode);
		COPY_IP(&nw_src, &arp_packet->src_ip_addr);
		COPY_IP(&nw_dst, &arp_packet->dst_ip_addr);
	}

	// Set headers for IP packet
	if (ntohs(packet->data.header.prot) == IP_PROTOCOL)
	{
		verbose(2, "[openflow_flowtable_match_packet]:: Setting headers for"
				" IP.");
		ip_packet_t *ip_packet = (ip_packet_t *) &packet->data.data;
		nw_proto = ip_packet->ip_prot;
		COPY_IP(&nw_src, &ip_packet->ip_src);
		COPY_IP(&nw_dst, &ip_packet->ip_dst);
		nw_tos = ip_packet->ip_tos;

		if (!(ntohs(ip_packet->ip_frag_off) & 0x1fff)
		        && !(ntohs(ip_packet->ip_frag_off) & 0x2000))
		{
			// IP packet is not fragmented
			verbose(2, "[openflow_flowtable_match_packet]:: IP packet is not"
					" fragmented.");
			if (ip_packet->ip_prot == TCP_PROTOCOL)
			{
				// TCP packet
				verbose(2, "[openflow_flowtable_match_packet]:: Setting"
						" headers for TCP.");
				uint32_t ip_header_length = ip_packet->ip_hdr_len * 4;
				tcp_packet_type *tcp_packet =
				        (tcp_packet_type *) ((uint8_t *) ip_packet
				                + ip_header_length);
				tp_src = tcp_packet->src_port;
				tp_dst = tcp_packet->dst_port;
			}
			else if (ip_packet->ip_prot == UDP_PROTOCOL)
			{
				// UDP packet
				verbose(2, "[openflow_flowtable_match_packet]:: Setting"
						" headers for UDP.");
				uint32_t ip_header_length = ip_packet->ip_hdr_len * 4;
				udp_packet_type *udp_packet =
				        (udp_packet_type *) ((uint8_t *) ip_packet
				                + ip_header_length);
				tp_src = udp_packet->src_port;
				tp_dst = udp_packet->dst_port;
			}
			else if (ip_packet->ip_prot == ICMP_PROTOCOL)
			{
				// ICMP packet
				verbose(2, "[openflow_flowtable_match_packet]:: Setting"
						" headers for ICMP.");
				int ip_header_length = ip_packet->ip_hdr_len * 4;
				icmphdr_t *icmp_packet = (icmphdr_t *) ((uint8_t *) ip_packet
				        + ip_header_length);
				tp_src = htons((uint16_t) icmp_packet->type);
				tp_dst = htons((uint16_t) icmp_packet->code);
			}
		}
	}

	// Reject match on input port
	if (!(ntohl(match->wildcards) & OFPFW_IN_PORT) && in_port != match->in_port)
	{
		verbose(2, "[openflow_flowtable_match_packet]:: Packet not matched"
				" (switch input port).");
		return 0;
	}

	// Reject match on Ethernet source MAC address
	if (!(ntohl(match->wildcards) & OFPFW_DL_SRC)
	        && memcmp(dl_src, match->dl_src, OFP_ETH_ALEN))
	{
		verbose(2, "[openflow_flowtable_match_packet]:: Packet not matched"
				" (source MAC address).");
		return 0;
	}

	// Reject match on Ethernet destination MAC address
	if (!(ntohl(match->wildcards) & OFPFW_DL_DST)
	        && memcmp(dl_dst, match->dl_dst, OFP_ETH_ALEN))
	{
		verbose(2, "[openflow_flowtable_match_packet]:: Packet not matched"
				" (destination MAC address).");
		return 0;
	}

	// Reject match on Ethernet VLAN ID
	if (!(ntohl(match->wildcards) & OFPFW_DL_VLAN) && dl_vlan != match->dl_vlan)
	{
		verbose(2, "[openflow_flowtable_match_packet]:: Packet not matched"
				" (VLAN ID).");
		return 0;
	}

	// Reject match on Ethernet VLAN priority
	if (!(ntohl(match->wildcards) & OFPFW_DL_VLAN)
	        && !(ntohl(match->wildcards) & OFPFW_DL_VLAN_PCP)
	        && dl_vlan_pcp != match->dl_vlan_pcp)
	{
		verbose(2, "[openflow_flowtable_match_packet]:: Packet not matched"
				" (VLAN priority).");
		return 0;
	}

	// Reject match on Ethernet frame type
	if (!(ntohl(match->wildcards) & OFPFW_DL_TYPE) && dl_type != match->dl_type)
	{
		verbose(2, "[openflow_flowtable_match_packet]:: Packet not matched"
				" (Ethernet frame type).");
		return 0;
	}

	// Reject match on IP type of service
	if (!(ntohl(match->wildcards) & OFPFW_DL_TYPE)
	        && ntohs(match->dl_type) == IP_PROTOCOL
	        && !(ntohl(match->wildcards) & OFPFW_NW_TOS)
	        && nw_tos != match->nw_tos)
	{
		verbose(2, "[openflow_flowtable_match_packet]:: Packet not matched"
				" (IP type of service).");
		return 0;
	}

	// Reject match on IP protocol or ARP opcode
	if (!(ntohl(match->wildcards) & OFPFW_DL_TYPE)
	        && (ntohs(match->dl_type) == IP_PROTOCOL
	                || ntohs(match->dl_type) == ARP_PROTOCOL)
	        && !(ntohl(match->wildcards) & OFPFW_NW_PROTO)
	        && nw_proto != match->nw_proto)
	{
		verbose(2, "[openflow_flowtable_match_packet]:: Packet not matched"
				" (IP protocol or ARP opcode).");
		return 0;
	}

	// Reject match on IP source address
	uint8_t ip_src_len = (OFPFW_NW_SRC_ALL >> OFPFW_NW_SRC_SHIFT)
	        - ((ntohl(match->wildcards) & OFPFW_NW_SRC_MASK)
	                >> OFPFW_NW_SRC_SHIFT);
	if (!(ntohl(match->wildcards) & OFPFW_DL_TYPE)
	        && (ntohs(match->dl_type) == IP_PROTOCOL
	                || ntohs(match->dl_type) == ARP_PROTOCOL))
	{
		if (ip_src_len > 0
		        && !openflow_flowtable_ip_compare(ntohl(nw_src),
		                ntohl(match->nw_src), ip_src_len))
		{
			verbose(2, "[openflow_flowtable_match_packet]:: Packet not"
					" matched (IP source address).");
			return 0;
		}
	}

	// Reject match on IP destination address
	uint8_t ip_dst_len = (OFPFW_NW_DST_ALL >> OFPFW_NW_DST_SHIFT)
	        - ((ntohl(match->wildcards) & OFPFW_NW_DST_MASK)
	                >> OFPFW_NW_DST_SHIFT);
	if (!(ntohl(match->wildcards) & OFPFW_DL_TYPE)
	        && (ntohs(match->dl_type) == IP_PROTOCOL
	                || ntohs(match->dl_type) == ARP_PROTOCOL))
	{
		if (ip_dst_len > 0
		        && !openflow_flowtable_ip_compare(ntohl(nw_dst),
		                ntohl(match->nw_dst), ip_dst_len))
		{
			verbose(2, "[openflow_flowtable_match_packet]:: Packet not"
					" matched (IP destination address).");
			return 0;
		}
	}

	// Reject match on TCP/UDP source port or ICMP type
	if (!(ntohl(match->wildcards) & OFPFW_DL_TYPE)
	        && ntohs(match->dl_type) == IP_PROTOCOL)
	{
		if (!(ntohl(match->wildcards) & OFPFW_NW_PROTO)
		        && (match->nw_proto == ICMP_PROTOCOL
		                || match->nw_proto == TCP_PROTOCOL
		                || match->nw_proto == UDP_PROTOCOL))
		{
			if (!(ntohl(match->wildcards) & OFPFW_TP_SRC)
			        && tp_src != match->tp_src)
			{
				verbose(2, "[openflow_flowtable_match_packet]:: Packet not"
						" matched (TCP/UDP source port or ICMP type).");
				return 0;
			}
		}
	}

	// Reject match on TCP/UDP destination port or ICMP code
	if (!(ntohl(match->wildcards) & OFPFW_DL_TYPE)
	        && ntohs(match->dl_type) == IP_PROTOCOL)
	{
		if (!(ntohl(match->wildcards) & OFPFW_NW_PROTO)
		        && (match->nw_proto == ICMP_PROTOCOL
		                || match->nw_proto == TCP_PROTOCOL
		                || match->nw_proto == UDP_PROTOCOL))
		{
			if (!(ntohl(match->wildcards) & OFPFW_TP_DST)
			        && tp_dst != match->tp_dst)
			{
				verbose(2, "[openflow_flowtable_match_packet]:: Packet not"
						" matched (TCP/UDP destination port or ICMP code).");
				return 0;
			}
		}
	}

	verbose(2, "[openflow_flowtable_match_packet]:: Packet matched.");
	return 1;
}

/**
 * Retrieves the matching flowtable entry for the specified packet. Increments
 * the packet and byte count statistics for that entry.
 *
 * This function dynamically allocates memory that must be freed manually
 * afterwards for its return value.
 *
 * @param packet The specified packet.
 *
 * @return The matching flowtable entry.
 */
openflow_flowtable_entry_type *openflow_flowtable_get_entry_for_packet(
        gpacket_t *packet)
{
	pthread_mutex_lock(&flowtable_mutex);

	uint32_t current_priority = 0;
	openflow_flowtable_entry_type *current_entry = NULL;
	uint32_t i;
	for (i = 0; i < OPENFLOW_MAX_FLOWTABLE_ENTRIES; i++)
	{
		// Reject match for inactive entry
		if (!flowtable->entries[i].active) continue;

		openflow_flowtable_entry_type *entry = &flowtable->entries[i];
		ofp_match *match = &entry->match;
		uint8_t is_match = openflow_flowtable_match_packet(match, packet);
		if (is_match)
		{
			if (match->wildcards == 0)
			{
				// Exact match
				verbose(2, "[openflow_flowtable_get_entry_for_packet]::"
						" Found exact match at index %" PRIu32 ".", i);
				current_entry = entry;
				break;
			}
			else if (entry->priority >= current_priority)
			{
				// Possible wildcard match, but wait to see if there
				// are any other wildcard matches with higher priority
				verbose(2, "[openflow_flowtable_get_entry_for_packet]::"
						" Found possible match at index %" PRIu32 ".", i);
				current_entry = entry;
				current_priority = entry->priority;
			}
		}
	}

	flowtable->stats.lookup_count = htonll(
	        ntohll(flowtable->stats.lookup_count) + 1);

	if (current_entry == NULL)
	{
		verbose(2, "[openflow_flowtable_get_entry_for_packet]::"
				" No entry found.");
		pthread_mutex_unlock(&flowtable_mutex);
		return NULL;
	}
	else
	{
		// Increment stats
		flowtable->stats.matched_count = htonll(
		        ntohll(flowtable->stats.matched_count) + 1);
		current_entry->stats.packet_count = htonll(
		        ntohll(current_entry->stats.packet_count) + 1);
		current_entry->stats.byte_count = htonll(
		        ntohll(current_entry->stats.byte_count) + sizeof(pkt_data_t));
		time(&current_entry->last_matched);

		// Make copy of entry for use outside this function
		openflow_flowtable_entry_type *return_entry = malloc(
		        sizeof(openflow_flowtable_entry_type));
		memcpy(return_entry, current_entry,
		        sizeof(openflow_flowtable_entry_type));
		pthread_mutex_unlock(&flowtable_mutex);
		return return_entry;
	}
}

/**
 * Determines whether there is an entry in the flowtable that overlaps the
 * specified entry. An entry overlaps another entry if a single packet may
 * match both, and both entries have the same priority.
 *
 * @param flow_mod A pointer to an ofp_flow_mod struct containing the specified
 *                 entry.
 * @param index    A pointer to a variable used to store the index of the
 *                 overlapping entry, if any.
 *
 * @return 1 if a match is found, 0 otherwise.
 */
static uint8_t openflow_flowtable_find_overlapping_entry(ofp_flow_mod *flow_mod,
        uint32_t *index)
{
	uint32_t i;
	for (i = 0; i < OPENFLOW_MAX_FLOWTABLE_ENTRIES; i++)
	{
		openflow_flowtable_entry_type *entry = &flowtable->entries[i];

		// Reject overlap if entry inactive
		if (!entry->active) continue;

		// Reject overlap if priorities are not the same
		if (entry->priority != flow_mod->priority) continue;

		ofp_match *flow_mod_match = &flow_mod->match;
		ofp_match *entry_match = &entry->match;

		// Accept overlap if either entry uses the all fields wildcard
		if (ntohl(flow_mod_match->wildcards) == OFPFW_ALL
		        || ntohl(entry_match->wildcards) == OFPFW_ALL)
		{
			*index = i;
			return 1;
		}

		// In general, reject overlap if both entries do not use a wildcard
		// and have differing values for a particular field

		// Reject overlap on input port
		if (!(ntohl(flow_mod_match->wildcards) & OFPFW_IN_PORT)
		        && !(ntohl(entry_match->wildcards) & OFPFW_IN_PORT))
		{
			if (flow_mod_match->in_port != entry_match->in_port)
			{
				continue;
			}
		}

		// Reject overlap on Ethernet source MAC address
		if (!(ntohl(flow_mod_match->wildcards) & OFPFW_DL_SRC)
		        && !(ntohl(entry_match->wildcards) & OFPFW_DL_SRC))
		{
			if (memcmp(flow_mod_match->dl_src, entry_match->dl_src,
			OFP_ETH_ALEN))
			{
				continue;
			}
		}

		// Reject overlap on Ethernet destination MAC address
		if (!(ntohl(flow_mod_match->wildcards) & OFPFW_DL_DST)
		        && !(ntohl(entry_match->wildcards) & OFPFW_DL_DST))
		{
			if (memcmp(flow_mod_match->dl_dst, entry_match->dl_dst,
			OFP_ETH_ALEN))
			{
				continue;
			}
		}

		// Reject overlap on Ethernet VLAN ID
		if (!(ntohl(flow_mod_match->wildcards) & OFPFW_DL_VLAN)
		        && !(ntohl(entry_match->wildcards) & OFPFW_DL_VLAN))
		{
			if (flow_mod_match->dl_vlan != entry_match->dl_vlan)
			{
				continue;
			}
		}

		// Reject overlap on Ethernet VLAN priority
		if (!(ntohl(flow_mod_match->wildcards) & OFPFW_DL_VLAN)
		        && !(ntohl(entry_match->wildcards) & OFPFW_DL_VLAN))
		{
			if (ntohs(flow_mod_match->dl_vlan) != OFP_VLAN_NONE
			        && ntohs(entry_match->dl_vlan) != OFP_VLAN_NONE)
			{
				// In addition to general rules, reject overlap only if
				// both entries do not use a wildcard or the value
				// OFP_VLAN_NONE for VLAN IDs
				if (!(ntohl(flow_mod_match->wildcards) & OFPFW_DL_VLAN_PCP)
				        && !(ntohl(entry_match->wildcards) & OFPFW_DL_VLAN_PCP))
				{
					if (flow_mod_match->dl_vlan_pcp != entry_match->dl_vlan_pcp)
					{
						continue;
					}
				}
			}
		}

		// Reject overlap on Ethernet frame type
		if (!(ntohl(flow_mod_match->wildcards) & OFPFW_DL_TYPE)
		        && !(ntohl(entry_match->wildcards) & OFPFW_DL_TYPE))
		{
			if (flow_mod_match->dl_type != entry_match->dl_type)
			{
				continue;
			}
		}

		// Reject overlap on IP type of service
		if (ntohs(flow_mod_match->dl_type) == IP_PROTOCOL
		        && flow_mod_match->dl_type == entry_match->dl_type)
		{
			// In addition to general rules, reject overlap only if
			// both entries match the IP protocol
			if (!(ntohl(flow_mod_match->wildcards) & OFPFW_NW_TOS)
			        && !(ntohl(entry_match->wildcards) & OFPFW_NW_TOS))
			{
				if (flow_mod_match->nw_tos != entry_match->nw_tos)
				{
					continue;
				}
			}
		}

		// Reject overlap on IP protocol or ARP opcode
		if ((ntohs(flow_mod_match->dl_type) == IP_PROTOCOL
		        || ntohs(flow_mod_match->dl_type) == ARP_PROTOCOL)
		        && flow_mod_match->dl_type == entry_match->dl_type)
		{
			// In addition to general rules, reject overlap only if
			// both entries match the IP or ARP protocol
			if (!(ntohl(flow_mod_match->wildcards) & OFPFW_NW_TOS)
			        && !(ntohl(entry_match->wildcards) & OFPFW_NW_TOS))
			{
				if (flow_mod_match->nw_tos != entry_match->nw_tos)
				{
					continue;
				}
			}
		}

		// Match on IP source address
		if ((ntohs(flow_mod_match->dl_type) == IP_PROTOCOL
		        || ntohs(flow_mod_match->dl_type) == ARP_PROTOCOL)
		        && flow_mod_match->dl_type == entry_match->dl_type)
		{
			// In addition to general rules, reject overlap only if
			// both entries match the IP or ARP protocol
			uint8_t ip_len_flow = (OFPFW_NW_SRC_ALL >> OFPFW_NW_SRC_SHIFT)
			        - ((ntohl(flow_mod_match->wildcards) & OFPFW_NW_SRC_MASK)
			                >> OFPFW_NW_SRC_SHIFT);
			uint8_t ip_len_entry = (OFPFW_NW_SRC_ALL >> OFPFW_NW_SRC_SHIFT)
			        - ((ntohl(entry_match->wildcards) & OFPFW_NW_SRC_MASK)
			                >> OFPFW_NW_SRC_SHIFT);
			if (ip_len_flow > 0 && ip_len_entry > 0)
			{
				// For the purposes of checking whether the IP source addresses
				// differ, only compare the bits that are not wildcarded by
				// either entry
				if (!openflow_flowtable_ip_compare(
				        ntohl(flow_mod_match->nw_src),
				        ntohl(entry_match->nw_src),
				        ip_len_flow < ip_len_entry ?
				                ip_len_flow : ip_len_entry))
				{
					continue;
				}
			}
		}

		// Match on IP destination address
		if ((ntohs(flow_mod_match->dl_type) == IP_PROTOCOL
		        || ntohs(flow_mod_match->dl_type) == ARP_PROTOCOL)
		        && flow_mod_match->dl_type == entry_match->dl_type)
		{
			// In addition to general rules, reject overlap only if
			// both entries match the IP or ARP protocol
			uint8_t ip_len_flow = (OFPFW_NW_DST_ALL >> OFPFW_NW_DST_SHIFT)
			        - ((ntohl(flow_mod_match->wildcards) & OFPFW_NW_DST_MASK)
			                >> OFPFW_NW_DST_SHIFT);
			uint8_t ip_len_entry = (OFPFW_NW_DST_ALL >> OFPFW_NW_DST_SHIFT)
			        - ((ntohl(entry_match->wildcards) & OFPFW_NW_DST_MASK)
			                >> OFPFW_NW_DST_SHIFT);
			if (ip_len_flow > 0 && ip_len_entry > 0)
			{
				// For the purposes of checking whether the IP destination
				// addresses differ, only compare the bits that are not
				// wildcarded by either entry
				if (!openflow_flowtable_ip_compare(
				        ntohl(flow_mod_match->nw_dst),
				        ntohl(entry_match->nw_dst),
				        ip_len_flow < ip_len_entry ?
				                ip_len_flow : ip_len_entry))
				{
					continue;
				}
			}
		}

		// Reject overlap on TCP/UDP source port or ICMP type
		if (ntohs(flow_mod_match->dl_type) == IP_PROTOCOL
		        && flow_mod_match->dl_type == entry_match->dl_type)
		{
			if ((flow_mod_match->nw_proto == ICMP_PROTOCOL
			        || flow_mod_match->nw_proto == TCP_PROTOCOL
			        || flow_mod_match->nw_proto == UDP_PROTOCOL)
			        && flow_mod_match->nw_proto == entry_match->nw_proto)
			{
				// In addition to general rules, reject overlap only if
				// both entries match the IP protocol, as well as either the
				// ICMP, TCP, or UDP protocol
				if (!(ntohl(flow_mod_match->wildcards) & OFPFW_TP_SRC)
				        && !(ntohl(entry_match->wildcards) & OFPFW_TP_SRC))
				{
					if (flow_mod_match->tp_src != entry_match->tp_src)
					{
						continue;
					}
				}
			}
		}

		// Reject overlap on TCP/UDP destination port or ICMP code
		if (ntohs(flow_mod_match->dl_type) == IP_PROTOCOL
		        && flow_mod_match->dl_type == entry_match->dl_type)
		{
			if ((flow_mod_match->nw_proto == ICMP_PROTOCOL
			        || flow_mod_match->nw_proto == TCP_PROTOCOL
			        || flow_mod_match->nw_proto == UDP_PROTOCOL)
			        && flow_mod_match->nw_proto == entry_match->nw_proto)
			{
				// In addition to general rules, reject overlap only if
				// both entries match the IP protocol, as well as either the
				// ICMP, TCP, or UDP protocol
				if (!(ntohl(flow_mod_match->wildcards) & OFPFW_TP_DST)
				        && !(ntohl(entry_match->wildcards) & OFPFW_TP_DST))
				{
					if (flow_mod_match->tp_dst != entry_match->tp_dst)
					{
						continue;
					}
				}
			}
		}

		*index = i;
		return 1;
	}

	return 0;
}

/**
 * Determines whether there is an entry in the flowtable that matches the
 * specified entry. An entry matches another entry if the ofp_match struct in
 * the second entry is identical to or more specific than the ofp_match struct
 * in the second entry.
 *
 * @param flow_mod    A pointer to an ofp_flow_mod struct containing the
 * 					  specified entry.
 * @param index       A pointer to a variable used to store the index of the
 *                    matching entry, if any.
 * @param start_index The index at which to begin matching comparisons.
 * @param out_port    The output port which entries are required to have an
 *                    action for to be matched, in host byte order.
 *
 * @return 1 if a match is found, 0 otherwise.
 */
static uint8_t openflow_flowtable_find_matching_entry(ofp_match *flow_mod_match,
        uint32_t *index, uint32_t start_index, uint16_t out_port)
{
	uint32_t i, j;
	for (i = start_index; i < OPENFLOW_MAX_FLOWTABLE_ENTRIES; i++)
	{
		// Reject match for inactive entries
		if (!flowtable->entries[i].active) continue;

		// Verify that this entry contains an output action for the specified
		// port, if one was specified; if there is no such action, then we can
		// reject the match
		uint8_t out_port_match = 0;
		if (out_port != OFPP_NONE)
		{
			for (j = 0; j < OPENFLOW_MAX_ACTIONS; j++)
			{
				if (flowtable->entries[i].actions[j].active)
				{
					ofp_action_output *action =
					        (ofp_action_output *) &flowtable->entries[i].actions[j].header;
					if (ntohs(action->type) == OFPAT_OUTPUT
					        && ntohs(action->port) == out_port)
					{
						out_port_match = 1;
						break;
					}
				}
			}
		}
		if (out_port != OFPP_NONE && !out_port_match)
		{
			continue;
		}

		// In general, reject the match if the specified field is not
		// wildcarded in the query and either the query does not match the
		// entry or the field is wildcarded in the entry

		ofp_match *entry_match = &flowtable->entries[i].match;

		// Accept match if query entry has the all fields wildcard
		if (ntohl(flow_mod_match->wildcards) == OFPFW_ALL)
		{
			*index = i;
			return 1;
		}

		// Reject match on input port
		if (!(ntohl(flow_mod_match->wildcards) & OFPFW_IN_PORT))
		{
			if ((ntohl(entry_match->wildcards) & OFPFW_IN_PORT)
			        || entry_match->in_port != flow_mod_match->in_port)
			{
				continue;
			}
		}

		// Reject match on Ethernet source MAC address
		if (!(ntohl(flow_mod_match->wildcards) & OFPFW_DL_SRC))
		{
			if ((ntohl(entry_match->wildcards) & OFPFW_DL_SRC)
			        || memcmp(flow_mod_match->dl_src, entry_match->dl_src,
			        OFP_ETH_ALEN))
			{
				continue;
			}
		}

		// Reject match on Ethernet destination MAC address
		if (!(ntohl(flow_mod_match->wildcards) & OFPFW_DL_DST))
		{
			if ((ntohl(entry_match->wildcards) & OFPFW_DL_DST)
			        || memcmp(flow_mod_match->dl_dst, entry_match->dl_dst,
			        OFP_ETH_ALEN))
			{
				continue;
			}
		}

		// Reject match on Ethernet VLAN ID
		if (!(ntohl(flow_mod_match->wildcards) & OFPFW_DL_VLAN))
		{
			if ((ntohl(entry_match->wildcards) & OFPFW_DL_VLAN)
			        || entry_match->dl_vlan != flow_mod_match->dl_vlan)
			{
				continue;
			}
		}

		// Reject match on Ethernet VLAN priority
		if (!(ntohl(flow_mod_match->wildcards) & OFPFW_DL_VLAN))
		{
			if (ntohs(flow_mod_match->dl_vlan) != OFP_VLAN_NONE)
			{
				// In addition to general rule, reject match only if VLAN
				// ID is not wildcarded in the query entry and the VLAN ID
				// is not set to OFP_VLAN_NONE in the query entry
				if (!(ntohl(flow_mod_match->wildcards) & OFPFW_DL_VLAN_PCP))
				{
					if ((ntohl(entry_match->wildcards) & OFPFW_DL_VLAN)
					        || ntohs(entry_match->dl_vlan) == OFP_VLAN_NONE
					        || (ntohl(entry_match->wildcards)
					                & OFPFW_DL_VLAN_PCP)
					        || entry_match->dl_vlan_pcp
					                != flow_mod_match->dl_vlan_pcp)
					{
						// In addition to general rule, reject match if VLAN ID
						// is wildcarded in the entry or if VLAN ID is set to
						// OFP_VLAN_NONE in the entry
						continue;
					}
				}
			}
		}

		// Reject match on Ethernet frame type
		if (!(ntohl(flow_mod_match->wildcards) & OFPFW_DL_TYPE))
		{
			if ((ntohl(entry_match->wildcards) & OFPFW_DL_TYPE)
			        || entry_match->dl_type != flow_mod_match->dl_type)
			{
				continue;
			}
		}

		// Reject match on IP type of service
		if (!(ntohl(flow_mod_match->wildcards) & OFPFW_NW_TOS))
		{
			if (!(ntohl(flow_mod_match->wildcards) & OFPFW_DL_TYPE)
			        && ntohs(flow_mod_match->dl_type) == IP_PROTOCOL)
			{
				// In addition to general rule, reject match only if the query
				// entry matches the IP protocol
				if ((ntohl(entry_match->wildcards) & OFPFW_NW_TOS)
				        || entry_match->nw_tos != flow_mod_match->nw_tos
				        || (ntohl(entry_match->wildcards) & OFPFW_DL_TYPE)
				        || entry_match->dl_type != flow_mod_match->dl_type)
				{
					// In addition to general rule, reject match if the entry
					// does not match the query entry protocol
					continue;
				}
			}
		}

		// Reject match IP protocol or ARP opcode
		if (!(ntohl(flow_mod_match->wildcards) & OFPFW_NW_PROTO))
		{
			if (!(ntohl(flow_mod_match->wildcards) & OFPFW_DL_TYPE)
			        && (ntohs(flow_mod_match->dl_type) == IP_PROTOCOL
			                || ntohs(flow_mod_match->dl_type) == ARP_PROTOCOL))
			{
				// In addition to general rule, reject match only if the query
				// entry matches the IP or ARP protocol
				if ((ntohl(entry_match->wildcards) & OFPFW_NW_PROTO)
				        || entry_match->nw_proto != flow_mod_match->nw_proto
				        || (ntohl(entry_match->wildcards) & OFPFW_DL_TYPE)
				        || entry_match->dl_type != flow_mod_match->dl_type)
				{
					// In addition to general rule, reject match if the entry
					// does not match the query entry protocol
					continue;
				}
			}
		}

		// Reject match on IP source address
		if (!(ntohl(flow_mod_match->wildcards) & OFPFW_DL_TYPE)
		        && (ntohs(flow_mod_match->dl_type) == IP_PROTOCOL
		                || ntohs(flow_mod_match->dl_type) == ARP_PROTOCOL))
		{
			// In addition to general rule, reject match only if the query
			// entry matches the IP or ARP protocol
			uint8_t ip_len_flow = (OFPFW_NW_SRC_ALL >> OFPFW_NW_SRC_SHIFT)
			        - ((ntohl(flow_mod_match->wildcards) & OFPFW_NW_SRC_MASK)
			                >> OFPFW_NW_SRC_SHIFT);
			if (ip_len_flow > 0)
			{
				uint8_t ip_len_entry = (OFPFW_NW_SRC_ALL >> OFPFW_NW_SRC_SHIFT)
				        - ((ntohl(entry_match->wildcards) & OFPFW_NW_SRC_MASK)
				                >> OFPFW_NW_SRC_SHIFT);
				// In addition to general rule, reject match if the entry
				// does not match the query entry protocol or if the query
				// entry has fewer wildcarded bits than the entry
				if ((ntohl(entry_match->wildcards) & OFPFW_DL_TYPE)
				        || entry_match->dl_type != flow_mod_match->dl_type
				        || ip_len_entry < ip_len_flow
				        || !openflow_flowtable_ip_compare(
				                ntohl(entry_match->nw_src),
				                ntohl(flow_mod_match->nw_src), ip_len_flow))
				{
					// For the purposes of checking whether the IP source
					// addresses differ, compare the bits that are not
					// wildcarded by the query entry
					continue;
				}
			}
		}

		// Reject match on IP destination address
		if (!(ntohl(flow_mod_match->wildcards) & OFPFW_DL_TYPE)
		        && (ntohs(flow_mod_match->dl_type) == IP_PROTOCOL
		                || ntohs(flow_mod_match->dl_type) == ARP_PROTOCOL))
		{
			// In addition to general rule, reject match only if the query
			// entry matches the IP or ARP protocol
			uint8_t ip_len_flow = (OFPFW_NW_DST_ALL >> OFPFW_NW_DST_SHIFT)
			        - ((ntohl(flow_mod_match->wildcards) & OFPFW_NW_DST_MASK)
			                >> OFPFW_NW_DST_SHIFT);
			if (ip_len_flow > 0)
			{
				uint8_t ip_len_entry = (OFPFW_NW_DST_ALL >> OFPFW_NW_DST_SHIFT)
				        - ((ntohl(entry_match->wildcards) & OFPFW_NW_DST_MASK)
				                >> OFPFW_NW_DST_SHIFT);
				// In addition to general rule, reject match if the entry
				// does not match the query entry protocol or if the query
				// entry has fewer wildcarded bits than the entry
				if ((ntohl(entry_match->wildcards) & OFPFW_DL_TYPE)
				        || entry_match->dl_type != flow_mod_match->dl_type
				        || ip_len_entry < ip_len_flow
				        || !openflow_flowtable_ip_compare(
				                ntohl(entry_match->nw_dst),
				                ntohl(flow_mod_match->nw_dst), ip_len_flow))
				{
					// For the purposes of checking whether the IP destination
					// addresses differ, compare the bits that are not
					// wildcarded by the query entry
					continue;
				}
			}
		}

		// Reject match on TCP/UDP source port or ICMP type
		if (!(ntohl(flow_mod_match->wildcards) & OFPFW_TP_SRC))
		{
			if (!(ntohl(flow_mod_match->wildcards) & OFPFW_DL_TYPE)
			        && ntohs(flow_mod_match->dl_type) == IP_PROTOCOL
			        && !(ntohl(flow_mod_match->wildcards) & OFPFW_NW_PROTO)
			        && (flow_mod_match->nw_proto == ICMP_PROTOCOL
			                || flow_mod_match->nw_proto == TCP_PROTOCOL
			                || flow_mod_match->nw_proto == UDP_PROTOCOL))
			{
				// In addition to general rule, reject match only if the query
				// entry matches the IP protocol, as well as the ICMP, TCP or
				// UDP protocol
				if ((ntohl(entry_match->wildcards) & OFPFW_DL_TYPE)
				        || entry_match->dl_type != flow_mod_match->dl_type
				        || (ntohl(entry_match->wildcards) & OFPFW_NW_PROTO)
				        || entry_match->nw_proto != flow_mod_match->nw_proto
				        || (ntohl(entry_match->wildcards) & OFPFW_TP_SRC)
				        || entry_match->tp_src != flow_mod_match->tp_src)
				{
					// In addition to general rule, reject match if the entry
					// does not match the query entry protocol
					continue;
				}
			}
		}

		// Reject match on TCP/UDP destination port or ICMP code
		if (!(ntohl(flow_mod_match->wildcards) & OFPFW_TP_DST))
		{
			if (!(ntohl(flow_mod_match->wildcards) & OFPFW_DL_TYPE)
			        && ntohs(flow_mod_match->dl_type) == IP_PROTOCOL
			        && !(ntohl(flow_mod_match->wildcards) & OFPFW_NW_PROTO)
			        && (flow_mod_match->nw_proto == ICMP_PROTOCOL
			                || flow_mod_match->nw_proto == TCP_PROTOCOL
			                || flow_mod_match->nw_proto == UDP_PROTOCOL))
			{
				// In addition to general rule, reject match only if the query
				// entry matches the IP protocol, as well as the ICMP, TCP or
				// UDP protocol
				if ((ntohl(entry_match->wildcards) & OFPFW_DL_TYPE)
				        || entry_match->dl_type != flow_mod_match->dl_type
				        || (ntohl(entry_match->wildcards) & OFPFW_NW_PROTO)
				        || entry_match->nw_proto != flow_mod_match->nw_proto
				        || (ntohl(entry_match->wildcards) & OFPFW_TP_DST)
				        || entry_match->tp_dst != flow_mod_match->tp_dst)
				{
					// In addition to general rule, reject match if the entry
					// does not match the query entry protocol
					continue;
				}
			}
		}

		*index = i;
		return 1;
	}

	return 0;
}

/**
 * Determines whether there is an entry in the flowtable that is identical to
 * the specified entry. An entry is identical to another entry if they share
 * the same priority and have identical header fields.
 *
 * @param flow_mod An ofp_flow_mod struct containing the specified entry.
 * @param index    A pointer to a variable used to store the index of the
 *                 identical entry, if any.
 *
 * @return 1 if a match is found, 0 otherwise.
 */
static uint8_t openflow_flowtable_find_identical_entry(ofp_flow_mod* flow_mod,
        uint32_t *index)
{
	uint32_t i;
	for (i = 0; i < OPENFLOW_MAX_FLOWTABLE_ENTRIES; i++)
	{
		// Reject match if entry inactive
		if (!flowtable->entries[i].active) continue;

		// Check if the entries' priorities are the same and whether their
		// header fields are identical (i.e. they have identical matches)
		if (flowtable->entries[i].priority == flow_mod->priority
		        && !memcmp(&flowtable->entries[i].match, &flow_mod->match,
		                sizeof(ofp_match)))
		{
			*index = i;
			return 1;
		}
	}

	return 0;
}

/**
 * Deletes the entry with the specified index from the flowtable.
 *
 * @param i      The index of the entry to remove.
 * @param reason The reason for the flow removal.
 */
static void openflow_flowtable_delete_entry_at_index(uint32_t i,uint8_t reason)
{
	if (ntohs(flowtable->entries[i].flags) & OFPFF_SEND_FLOW_REM)
	{
		openflow_ctrl_iface_send_flow_removed(&flowtable->entries[i],
		        reason);
	}

	flowtable->stats.active_count = htonl(
	        ntohl(flowtable->stats.active_count) - 1);
	memset(&flowtable->entries[i], 0, sizeof(openflow_flowtable_entry_type));
}

/**
 * Applies the specified flow modification to the flowtable entry at the
 * specified index.
 *
 * @param flow_mod   The struct containing the flow modification.
 * @param index      The index of the flowtable entry to modify.
 * @param error_type A pointer to an error type variable that will be
 *                   populated (in network byte order) if an error occurs.
 * @param error_code A pointer to an error code variable that will be
 *                   populated (in network byte order) if an error occurs.
 * @param reset      If 1, resets the statistics associated with the entry.
 */
static int32_t openflow_flowtable_modify_entry_at_index(ofp_flow_mod *flow_mod,
        uint32_t index, uint16_t *error_type, uint16_t *error_code,
        uint8_t reset)
{
	openflow_flowtable_action_type actions[OPENFLOW_MAX_ACTIONS];
	uint32_t i;
	uint16_t action_block_index = sizeof(ofp_flow_mod);
	uint16_t actions_index = 0;

	ofp_action_header *action_header;
	while (action_block_index < ntohs(flow_mod->header.length))
	{
		// Use temporary pointer to increment pointer address by bytes
		char *tmp_ptr = (char *) flow_mod;
		tmp_ptr += action_block_index;
		action_header = (ofp_action_header *) tmp_ptr;

		memcpy(&actions[actions_index].header, action_header,
		        ntohs(action_header->len));

		if (ntohs(actions[actions_index].header.type) == OFPAT_ENQUEUE)
		{
			verbose(2, "[openflow_flowtable_modify_entry_at_index]:: "
					"OFPAT_ENQUEUE action not supported. Not modifying"
					" entry.");
			*error_type = htons(OFPET_FLOW_MOD_FAILED);
			*error_code = htons(OFPFMFC_UNSUPPORTED);
			return -1;
		}
		if (ntohs(actions[actions_index].header.type) == OFPAT_VENDOR)
		{
			verbose(2, "[openflow_flowtable_modify_entry_at_index]::"
					" OFPAT_VENDOR action not supported. Not modifying entry.");
			*error_type = htons(OFPET_FLOW_MOD_FAILED);
			*error_code = htons(OFPFMFC_UNSUPPORTED);
			return -1;
		}
		if ((ntohs(actions[actions_index].header.type) > OFPAT_ENQUEUE)
		        && (ntohs(actions[actions_index].header.type) < OFPAT_VENDOR))
		{
			verbose(2, "[openflow_flowtable_modify_entry_at_index]::"
					" Unrecognized action. Not modifying entry.");
			*error_type = htons(OFPET_BAD_ACTION);
			*error_code = htons(OFPBAC_BAD_TYPE);
			return -1;
		}

		if (ntohs(actions[actions_index].header.type) == OFPAT_OUTPUT)
		{
			ofp_action_output *output_action =
			        (ofp_action_output *) action_header;
			if (ntohs(output_action->len) != 8)
			{
				verbose(2, "[openflow_flowtable_modify_entry_at_index]::"
						" OFPAT_OUTPUT action not of length 8. Not modifying"
						" entry.");
				*error_type = htons(OFPET_BAD_ACTION);
				*error_code = htons(OFPBAC_BAD_LEN);
				return -1;
			}
			if (ntohs(output_action->port) > OPENFLOW_MAX_PHYSICAL_PORTS
			        && ntohs(output_action->port) < OFPP_IN_PORT)
			{
				verbose(2, "[openflow_flowtable_modify_entry_at_index]::"
						" OFPAT_OUTPUT action uses invalid port. Not modifying"
						" entry.");
				*error_type = htons(OFPET_BAD_ACTION);
				*error_code = htons(OFPBAC_BAD_OUT_PORT);
				return -1;
			}
		}
		else if (ntohs(actions[actions_index].header.type)
		        == OFPAT_SET_VLAN_VID)
		{
			if (ntohs(actions[actions_index].header.len) != 8)
			{
				verbose(2, "[openflow_flowtable_modify_entry_at_index]::"
						" OFPAT_SET_VLAN_VID action not of length 8. Not"
						" modifying entry.");
				*error_type = htons(OFPET_BAD_ACTION);
				*error_code = htons(OFPBAC_BAD_LEN);
				return -1;
			}
		}
		else if (ntohs(actions[actions_index].header.type)
		        == OFPAT_SET_VLAN_PCP)
		{
			if (ntohs(actions[actions_index].header.len) != 8)
			{
				verbose(2, "[openflow_flowtable_modify_entry_at_index]::"
						" OFPAT_SET_VLAN_PCP action not of length 8. Not"
						" modifying entry.");
				*error_type = htons(OFPET_BAD_ACTION);
				*error_code = htons(OFPBAC_BAD_LEN);
				return -1;
			}
		}
		else if (ntohs(actions[actions_index].header.type) == OFPAT_SET_DL_SRC
		        || ntohs(actions[actions_index].header.type)
		                == OFPAT_SET_DL_DST)
		{
			if (ntohs(actions[actions_index].header.len) != 16)
			{
				verbose(2, "[openflow_flowtable_modify_entry_at_index]::"
						" OFPAT_SET_DL_SRC or OFPAT_SET_DL_DST action not of"
						" length 16. Not modifying entry.");
				*error_type = htons(OFPET_BAD_ACTION);
				*error_code = htons(OFPBAC_BAD_LEN);
				return -1;
			}
		}
		else if (ntohs(actions[actions_index].header.type) == OFPAT_SET_NW_SRC
		        || ntohs(actions[actions_index].header.type)
		                == OFPAT_SET_NW_DST)
		{
			if (ntohs(actions[actions_index].header.len) != 8)
			{
				verbose(2, "[openflow_flowtable_modify_entry_at_index]::"
						" OFPAT_SET_NW_SRC or OFPAT_SET_NW_DST action not of"
						" length 8. Not modifying entry.");
				*error_type = htons(OFPET_BAD_ACTION);
				*error_code = htons(OFPBAC_BAD_LEN);
				return -1;
			}
		}
		else if (ntohs(actions[actions_index].header.type) == OFPAT_SET_NW_TOS)
		{
			if (ntohs(actions[actions_index].header.len) != 8)
			{
				verbose(2, "[openflow_flowtable_modify_entry_at_index]::"
						" OFPAT_SET_NW_TOS action not of length 8. Not"
						" modifying entry.");
				*error_type = htons(OFPET_BAD_ACTION);
				*error_code = htons(OFPBAC_BAD_LEN);
				return -1;
			}
		}
		else if (ntohs(actions[actions_index].header.type) == OFPAT_SET_TP_SRC
		        || ntohs(actions[actions_index].header.type)
		                == OFPAT_SET_TP_DST)
		{
			if (ntohs(actions[actions_index].header.len) != 8)
			{
				verbose(2, "[openflow_flowtable_modify_entry_at_index]::"
						" OFPAT_SET_TP_SRC or OFPAT_SET_TP_DST action not of"
						" length 8. Not modifying entry.");
				*error_type = htons(OFPET_BAD_ACTION);
				*error_code = htons(OFPBAC_BAD_LEN);
				return -1;
			}
		}

		action_block_index += ntohs(action_header->len);
		actions_index += 1;
		if (actions_index > OPENFLOW_MAX_ACTIONS)
		{
			verbose(2, "[openflow_flowtable_modify_entry_at_index]::"
					" Too many actions. Not modifying entry.");
			*error_type = htons(OFPET_BAD_ACTION);
			*error_code = htons(OFPBAC_TOO_MANY);
			return -1;
		}
	}

	if (reset)
	{
		memset(&flowtable->entries[index].stats, 0, sizeof(ofp_flow_stats));
		openflow_flowtable_set_flow_stats_defaults(
		        &flowtable->entries[index].stats);
	}

	flowtable->entries[index].active = 1;

	flowtable->entries[index].match = flow_mod->match;
	flowtable->entries[index].stats.match = flow_mod->match;

	flowtable->entries[index].cookie = flow_mod->cookie;
	flowtable->entries[index].stats.cookie = flow_mod->cookie;

	time(&flowtable->entries[index].last_matched);
	time(&flowtable->entries[index].last_modified);

	flowtable->entries[index].idle_timeout = flow_mod->idle_timeout;
	flowtable->entries[index].stats.idle_timeout = flow_mod->idle_timeout;

	flowtable->entries[index].hard_timeout = flow_mod->hard_timeout;
	flowtable->entries[index].stats.hard_timeout = flow_mod->hard_timeout;

	flowtable->entries[index].priority = flow_mod->priority;
	flowtable->entries[index].stats.priority = flow_mod->priority;

	flowtable->entries[index].flags = flow_mod->flags;
	for (i = 0; i < OPENFLOW_MAX_ACTIONS; i++)
	{
		if (i < actions_index)
		{
			flowtable->entries[index].actions[i] = actions[i];
			flowtable->entries[index].actions[i].active = 1;
		}
		else
		{
			flowtable->entries[index].actions[i].active = 0;
		}
	}

	verbose(2, "[openflow_flowtable_modify_entry_at_index]:: Modified entry"
			" at index %" PRIu32 ".", index);

	return 0;
}

/**
 * Adds the specified entry to the flowtable.
 *
 * @param flow_mod   The struct containing the entry to add to the flowtable.
 * @param error_type A pointer to an error type variable that will be
 *                   populated (in network byte order) if an error occurs.
 * @param error_code A pointer to an error code variable that will be
 *                   populated (in network byte order) if an error occurs.
 *
 * @return 0 if no error occurred, -1 otherwise.
 */
static int32_t openflow_flowtable_add(ofp_flow_mod* flow_mod,
        uint16_t *error_type, uint16_t *error_code)
{
	uint32_t i;
	uint16_t flags = ntohs(flow_mod->flags);

	if (flags & OFPFF_CHECK_OVERLAP)
	{
		verbose(2, "[openflow_flowtable_add]:: OFPFF_CHECK_OVERLAP flag set.");
		if (openflow_flowtable_find_overlapping_entry(flow_mod, &i))
		{
			verbose(2, "[openflow_flowtable_add]:: Overlapping entry found at"
					" index %" PRIu32 ". Not adding to table.", i);
			*error_type = htons(OFPET_FLOW_MOD_FAILED);
			*error_code = htons(OFPFMFC_OVERLAP);
			return -1;
		}
	}

	if (openflow_flowtable_find_identical_entry(flow_mod, &i))
	{
		verbose(2, "[openflow_flowtable_add]:: Replacing flowtable entry at"
				" index %" PRIu32 ".", i);
		memset(&flowtable->entries[i], 0,
		        sizeof(openflow_flowtable_entry_type));
		return openflow_flowtable_modify_entry_at_index(flow_mod, i, error_type,
		        error_code, 0);
	}

	for (i = 0; i < OPENFLOW_MAX_FLOWTABLE_ENTRIES; i++)
	{
		if (!flowtable->entries[i].active)
		{
			flowtable->stats.active_count = htonl(
			        ntohl(flowtable->stats.active_count) + 1);

			verbose(2, "[openflow_flowtable_add]:: Adding flowtable entry at"
					" index %" PRIu32 ".", i);
			memset(&flowtable->entries[i], 0,
			        sizeof(openflow_flowtable_entry_type));
			time(&flowtable->entries[i].added);
			return openflow_flowtable_modify_entry_at_index(flow_mod, i,
			        error_type, error_code, 1);
		}
	}

	verbose(2, "[openflow_flowtable_add]:: No room in flowtable to add entry.");
	*error_type = htons(OFPET_FLOW_MOD_FAILED);
	*error_code = htons(OFPFMFC_ALL_TABLES_FULL);
	return -1;
}

/**
 * Modifies the specified entry in the flowtable.
 *
 * @param flow_mod   The struct containing the entry to edit in the flowtable.
 * @param error_type A pointer to an error type variable that will be
 *                   populated (in network byte order) if an error occurs.
 * @param error_code A pointer to an error code variable that will be
 *                   populated (in network byte order) if an error occurs.
 *
 * @return 0 if no error occurred, -1 otherwise.
 */
static int32_t openflow_flowtable_edit(ofp_flow_mod *flow_mod,
        uint16_t *error_type, uint16_t *error_code)
{
	uint32_t i;
	uint32_t start_index = 0;

	uint8_t found_match = 0;
	while (start_index < OPENFLOW_MAX_FLOWTABLE_ENTRIES)
	{
		if (openflow_flowtable_find_matching_entry(&flow_mod->match, &i,
		        start_index, OFPP_NONE))
		{
			verbose(2, "[openflow_flowtable_edit]:: Editing flowtable entry at"
					" index %" PRIu32 ".", i);
			int32_t ret = openflow_flowtable_modify_entry_at_index(flow_mod, i,
			        error_type, error_code, 0);
			if (ret < 0)
			{
				return ret;
			}
			start_index = i + 1;
			found_match = 1;
		}
		else
		{
			break;
		}
	}

	if (!found_match)
	{
		return openflow_flowtable_add(flow_mod, error_type, error_code);
	}
	else
	{
		return 0;
	}
}

/**
 * Strictly modifies the specified entry in the flowtable.
 *
 * @param flow_mod   The struct containing the entry to edit in the flowtable.
 * @param error_type A pointer to an error type variable that will be
 *                   populated (in network byte order) if an error occurs.
 * @param error_code A pointer to an error code variable that will be
 *                   populated (in network byte order) if an error occurs.
 *
 * @return 0 if no error occurred, -1 otherwise.
 */
static int32_t openflow_flowtable_edit_strict(ofp_flow_mod *flow_mod,
        uint16_t *error_type, uint16_t *error_code)
{
	uint32_t i;

	if (openflow_flowtable_find_identical_entry(flow_mod, &i))
	{
		verbose(2, "[openflow_flowtable_edit_strict]:: Editing flowtable entry"
				" at index %" PRIu32 ".", i);
		return openflow_flowtable_modify_entry_at_index(flow_mod, i, error_type,
		        error_code, 0);
	}

	return openflow_flowtable_add(flow_mod, error_type, error_code);
}

/**
 * Deletes the specified entry or entries in the flowtable.
 *
 * @param flow_mod   The struct containing the entry to edit in the flowtable.
 * @param error_type A pointer to an error type variable that will be
 *                   populated (in network byte order) if an error occurs.
 * @param error_code A pointer to an error code variable that will be
 *                   populated (in network byte order) if an error occurs.
 *
 * @return 0 if no error occurred, -1 otherwise.
 */
static int32_t openflow_flowtable_delete(ofp_flow_mod *flow_mod,
        uint16_t *error_type, uint16_t *error_code)
{
	uint32_t i;
	uint32_t start_index = 0;

	uint8_t found_match = 0;
	while (start_index < OPENFLOW_MAX_FLOWTABLE_ENTRIES)
	{
		if (openflow_flowtable_find_matching_entry(&flow_mod->match, &i,
		        start_index, ntohs(flow_mod->out_port)))
		{
			verbose(2, "[openflow_flowtable_delete]:: Deleting flowtable entry"
					" at index %" PRIu32 ".", i);
			openflow_flowtable_delete_entry_at_index(i, OFPRR_DELETE);
			start_index = i + 1;
		}
		else
		{
			break;
		}
	}

	return 0;
}

/**
 * Strictly deletes the specified entry or entries in the flowtable.
 *
 * @param flow_mod   The struct containing the entry to edit in the flowtable.
 * @param error_type A pointer to an error type variable that will be
 *                   populated (in network byte order) if an error occurs.
 * @param error_code A pointer to an error code variable that will be
 *                   populated (in network byte order) if an error occurs.
 *
 * @return 0 if no error occurred, -1 otherwise.
 */
static int32_t openflow_flowtable_delete_strict(ofp_flow_mod *flow_mod,
        uint16_t *error_type, uint16_t *error_code)
{
	uint32_t i;

	if (openflow_flowtable_find_identical_entry(flow_mod, &i))
	{
		verbose(2, "[openflow_flowtable_delete_strict]:: Deleting flowtable"
				" entry at index %" PRIu32 ".", i);
		openflow_flowtable_delete_entry_at_index(i, OFPRR_DELETE);
	}

	return 0;
}

/**
 * Applies the specified modification to the flowtable.
 *
 * @param modify_info The modification to apply to the flowtable.
 * @param error_type  A pointer to an error type variable that will be
 *                    populated (in network byte order) if an error occurs.
 * @param error_code  A pointer to an error code variable that will be
 *                    populated (in network byte order) if an error occurs.
 *
 * @return 0 if no error occurred, -1 otherwise.
 */
int32_t openflow_flowtable_modify(ofp_flow_mod *flow_mod, uint16_t *error_type,
        uint16_t *error_code)
{
	pthread_mutex_lock(&flowtable_mutex);

	uint16_t command = ntohs(flow_mod->command);
	int32_t status;
	if (command == OFPFC_ADD)
	{
		verbose(2, "[openflow_flowtable_modify]:: Modify command is"
				" OFPFC_ADD.");
		status = openflow_flowtable_add(flow_mod, error_type, error_code);
	}
	else if (command == OFPFC_MODIFY)
	{
		verbose(2, "[openflow_flowtable_modify]:: Modify command is"
				" OFPFC_MODIFY.");
		status = openflow_flowtable_edit(flow_mod, error_type, error_code);
	}
	else if (command == OFPFC_MODIFY_STRICT)
	{
		verbose(2, "[openflow_flowtable_modify]:: Modify command is"
				" OFPFC_MODIFY_STRICT.");
		status = openflow_flowtable_edit_strict(flow_mod, error_type,
		        error_code);
	}
	else if (command == OFPFC_DELETE)
	{
		verbose(2, "[openflow_flowtable_modify]:: Modify command is"
				" OFPFC_DELETE.");
		status = openflow_flowtable_delete(flow_mod, error_type, error_code);
	}
	else if (command == OFPFC_DELETE_STRICT)
	{
		verbose(2, "[openflow_flowtable_modify]:: Modify command is"
				" OFPFC_DELETE_STRICT.");
		status = openflow_flowtable_delete_strict(flow_mod, error_type,
		        error_code);
	}
	else
	{
		verbose(2, "[openflow_flowtable_modify]:: Modify command not"
				" recognized.");
		*error_type = htons(OFPET_FLOW_MOD_FAILED);
		*error_code = htons(OFPFMFC_BAD_COMMAND);
		status = -1;
	}

	pthread_mutex_unlock(&flowtable_mutex);
	return status;
}

/**
 * Updates the duration statistics for the specified entry.
 *
 * @param index The index of the entry to update.
 */
static void openflow_flowtable_update_entry_stats(uint32_t index)
{
	time_t now;
	time(&now);

	double duration = difftime(now, flowtable->entries[index].added);
	flowtable->entries[index].stats.duration_sec = htonl((uint32_t) duration);
	flowtable->entries[index].stats.duration_nsec = 0;
}

/**
 * Retrieves the flow statistics for the first matching flow.
 *
 * This function returns dynamically allocated memory referenced by
 * ptr_to_flow_stats and ptr_to_actions which must be manually free.
 *
 * @param match             A pointer to the match to match entries against.
 *                          The flow statistics for the first matching entry
 *                          will be returned.
 * @param out_port          The output port which entries are required to have
 *                          an action for to be matched, in network byte order.
 * @param index             The index at which to begin searching the
 *                          flowtable.
 * @param match_index       A pointer to a variable used to store the index of
 *                          the matching entry, if any.
 * @param table_index       The index of the table to read from.
 * @param ptr_to_flow_stats A pointer to an ofp_flow_stats struct pointer. The
 *                          inner pointer will be replaced by a pointer to the
 *                          matching ofp_flow_stats struct if one is found.
 * @param ptr_to_actions    A pointer to an openflow_flowtable_action_type
 *                          struct pointer. The inner pointer will be replaced
 *                          by a pointer to the matching
 *                          openflow_flowtable_action_type struct if one is
 *                          found.
 *
 * @return 1 if a match is found, 0 otherwise.
 */
int32_t openflow_flowtable_get_entry_stats(ofp_match *match, uint16_t out_port,
        uint32_t index, uint32_t *match_index, uint8_t table_index,
        ofp_flow_stats **ptr_to_flow_stats,
        openflow_flowtable_action_type **ptr_to_actions)
{
	if (table_index != 0 && table_index != 255) return 0;

	pthread_mutex_lock(&flowtable_mutex);

	if (openflow_flowtable_find_matching_entry(match, match_index, index,
	        ntohs(out_port)))
	{
		verbose(2, "[openflow_flowtable_get_flow_stats]:: Retrieving"
				" flow statistics for entry at index %" PRIu32 ".",
		        *match_index);

		openflow_flowtable_update_entry_stats(*match_index);
		*ptr_to_flow_stats = malloc(sizeof(ofp_flow_stats));
		memcpy(*ptr_to_flow_stats, &flowtable->entries[*match_index].stats,
		        sizeof(ofp_flow_stats));

		*ptr_to_actions = malloc(sizeof(openflow_flowtable_action_type));
		memcpy(*ptr_to_actions, &flowtable->entries[*match_index].actions,
		        sizeof(openflow_flowtable_action_type));

		pthread_mutex_unlock(&flowtable_mutex);
		return 1;
	}

	pthread_mutex_unlock(&flowtable_mutex);
	return 0;
}

/**
 * Gets the standard table statistics for the OpenFlow flowtable.
 *
 * @return The standard table statistics for the OpenFlow flowtable.
 */
ofp_table_stats openflow_flowtable_get_table_stats()
{
	pthread_mutex_lock(&flowtable_mutex);
	ofp_table_stats *stats = &flowtable->stats;
	pthread_mutex_unlock(&flowtable_mutex);
	return *stats;
}

/**
 * Prints the specified match to the console.
 *
 * @param match A pointer to the match to print.
 */
static void openflow_flowtable_print_wildcards(uint32_t wildcards)
{
	if (ntohl(wildcards) == OFPFW_ALL)
	{
		printf("\t\tAll fields\n");
		return;
	}

	if (ntohl(wildcards) & OFPFW_IN_PORT)
	{
		printf("\t\tInput port\n");
	}

	if (ntohl(wildcards) & OFPFW_DL_SRC)
	{
		printf("\t\tEthernet source MAC address\n");
	}

	if (ntohl(wildcards) & OFPFW_DL_DST)
	{
		printf("\t\tEthernet destination MAC address\n");
	}

	if (ntohl(wildcards) & OFPFW_DL_VLAN)
	{
		printf("\t\tEthernet VLAN ID\n");
	}
	else if ((ntohl(wildcards) & OFPFW_DL_VLAN_PCP))
	{
		printf("\t\tEthernet VLAN priority\n");
	}

	if (ntohl(wildcards) & OFPFW_DL_TYPE)
	{
		printf("\t\tEthernet frame type\n");
	}
	else
	{
		if (ntohl(wildcards) & OFPFW_NW_TOS)
		{
			printf("\t\tIP type of service\n");
		}

		if (ntohl(wildcards) & OFPFW_NW_PROTO)
		{
			printf("\t\tIP protocol or ARP opcode\n");
		}

		uint8_t ip_src_len = (ntohl(wildcards) & OFPFW_NW_DST_MASK)
		        >> OFPFW_NW_SRC_SHIFT;
		printf("\t\tIP source address wildcard bit count: %" PRIu8 " LSBs\n",
		        ip_src_len);

		uint8_t ip_dst_len = (ntohl(wildcards) & OFPFW_NW_DST_MASK)
		        >> OFPFW_NW_DST_SHIFT;
		printf("\t\tIP destination address wildcard bit count: %" PRIu8
		" LSBs\n", ip_dst_len);

		if (!(ntohl(wildcards) & OFPFW_NW_PROTO))
		{
			if (ntohl(wildcards) & OFPFW_TP_SRC)
			{
				printf("\t\tTCP/UDP source port or ICMP type\n");
			}

			if (ntohl(wildcards) & OFPFW_TP_DST)
			{
				printf("\t\tTCP/UDP destination port or ICMP code\n");
			}
		}
	}
}

/**
 * Prints the specified match to the CLI.
 *
 * @param match A pointer to the match to print.
 */
static void openflow_flowtable_print_match(ofp_match *match)
{
	printf("\tWildcards: %" PRIX32 "\n", ntohl(match->wildcards));
	openflow_flowtable_print_wildcards(match->wildcards);

	if (!(ntohl(match->wildcards) & OFPFW_IN_PORT))
	{
		printf("\tInput port: %" PRIu16 "\n", ntohs(match->in_port));
	}

	if (!(ntohl(match->wildcards) & OFPFW_DL_SRC))
	{
		char dl_src[50];
		MAC2Colon(dl_src, match->dl_src);
		printf("\tEthernet source MAC address: %s\n", dl_src);
	}

	if (!(ntohl(match->wildcards) & OFPFW_DL_DST))
	{
		char dl_dst[50];
		MAC2Colon(dl_dst, match->dl_dst);
		printf("\tEthernet destination MAC address: %s\n", dl_dst);
	}

	if (!(ntohl(match->wildcards) & OFPFW_DL_VLAN))
	{
		if (ntohs(match->dl_vlan) == OFP_VLAN_NONE)
		{
			printf("\tEthernet VLAN ID: OFP_VLAN_NONE\n");
		}
		else
		{
			printf("\tEthernet VLAN ID: %" PRIu16 "\n", ntohs(match->dl_vlan));
		}

		if (match->dl_vlan != OFP_VLAN_NONE
		        && !(ntohl(match->wildcards) & OFPFW_DL_VLAN_PCP))
		{
			printf("\tEthernet VLAN priority: %" PRIu8 "\n",
			        match->dl_vlan_pcp);
		}
	}

	if (!(ntohl(match->wildcards) & OFPFW_DL_TYPE))
	{
		if (ntohs(match->dl_type) == IP_PROTOCOL)
		{
			printf("\tEthernet frame type: IP\n");
		}
		else if (ntohs(match->dl_type) == ARP_PROTOCOL)
		{
			printf("\tEthernet frame type: ARP\n");
		}
		else
		{
			printf("\tEthernet frame type: %" PRIu16 "\n",
			        ntohs(match->dl_type));
		}
	}

	if (!(ntohl(match->wildcards) & OFPFW_DL_TYPE)
	        && ntohs(match->dl_type) == IP_PROTOCOL)
	{
		if (!(ntohl(match->wildcards) & OFPFW_NW_TOS))
		{
			printf("\tIP type of service: %" PRIu8 "\n", match->nw_tos);
		}
	}

	if (!(ntohl(match->wildcards) & OFPFW_DL_TYPE)
	        && (ntohs(match->dl_type) == IP_PROTOCOL
	                || ntohs(match->dl_type) == ARP_PROTOCOL))
	{
		if (!(ntohl(match->wildcards) & OFPFW_NW_PROTO))
		{
			if (ntohs(match->dl_type) == IP_PROTOCOL
			        && match->nw_proto == ICMP_PROTOCOL)
			{
				printf("\tIP protocol: ICMP\n");
			}
			else if (ntohs(match->dl_type) == IP_PROTOCOL
			        && match->nw_proto == TCP_PROTOCOL)
			{
				printf("\tIP protocol: TCP\n");
			}
			else if (ntohs(match->dl_type) == IP_PROTOCOL
			        && match->nw_proto == UDP_PROTOCOL)
			{
				printf("\tIP protocol: UDP\n");
			}
			else
			{
				printf("\tIP protocol or ARP opcode: %" PRIu8 "\n",
				        match->nw_proto);
			}
		}

		uint8_t ip_src_len = (ntohl(match->wildcards) & OFPFW_NW_SRC_MASK)
		        >> OFPFW_NW_SRC_SHIFT;
		if (ip_src_len < OFPFW_NW_SRC_ALL >> OFPFW_NW_SRC_SHIFT)
		{
			char nw_src[50];
			uint32_t nw_src_raw = ntohl(match->nw_src);
			IP2Dot(nw_src, (uint8_t *) &nw_src_raw);
			printf("\tIP source address: %s\n", nw_src);
		}

		uint8_t ip_dst_len = (ntohl(match->wildcards) & OFPFW_NW_DST_MASK)
		        >> OFPFW_NW_DST_SHIFT;
		if (ip_dst_len < OFPFW_NW_DST_ALL >> OFPFW_NW_DST_SHIFT)
		{
			char nw_dst[50];
			uint32_t nw_dst_raw = ntohl(match->nw_dst);
			IP2Dot(nw_dst, (uint8_t *) &nw_dst_raw);
			printf("\tIP destination address: %s\n", nw_dst);
		}
	}

	if (!(ntohl(match->wildcards) & OFPFW_DL_TYPE)
	        && ntohs(match->dl_type) == IP_PROTOCOL)
	{
		if (!(ntohl(match->wildcards) & OFPFW_NW_PROTO)
		        && match->nw_proto == TCP_PROTOCOL)
		{
			if (!(ntohl(match->wildcards) & OFPFW_TP_SRC))
			{
				printf("\tTCP source port: %" PRIu16 "\n",
				        ntohs(match->tp_src));
			}
			if (!(ntohl(match->wildcards) & OFPFW_TP_DST))
			{
				printf("\tTCP destination port: %" PRIu16 "\n",
				        ntohs(match->tp_dst));
			}
		}
		else if (!(ntohl(match->wildcards) & OFPFW_NW_PROTO)
		        && match->nw_proto == UDP_PROTOCOL)
		{
			if (!(ntohl(match->wildcards) & OFPFW_TP_SRC))
			{
				printf("\tUDP source port: %" PRIu16 "\n",
				        ntohs(match->tp_src));
			}
			if (!(ntohl(match->wildcards) & OFPFW_TP_DST))
			{
				printf("\tUDP destination port: %" PRIu16 "\n",
				        ntohs(match->tp_dst));
			}
		}
		else if (!(ntohl(match->wildcards) & OFPFW_NW_PROTO)
		        && match->nw_proto == ICMP_PROTOCOL)
		{
			if (!(ntohl(match->wildcards) & OFPFW_TP_SRC))
			{
				printf("\tICMP type: %" PRIu16 "\n", ntohs(match->tp_src));
			}
			if (!(ntohl(match->wildcards) & OFPFW_TP_DST))
			{
				printf("\tICMP code: %" PRIu16 "\n", ntohs(match->tp_dst));
			}
		}
	}
}

/**
 * Prints the specified action to the CLI.
 *
 * @param action A pointer to the action to print.
 */
static void openflow_flowtable_print_action(ofp_action_header *action)
{
	if (ntohs(action->type) == OFPAT_OUTPUT)
	{
		printf("\t\tType: OFPAT_OUTPUT\n");
		uint16_t port = ntohs(((ofp_action_output *) action)->port);
		if (port == OFPP_IN_PORT)
		{
			printf("\t\tOutput port: OFPP_IN_PORT\n");
		}
		else if (port == OFPP_TABLE)
		{
			printf("\t\tOutput port: OFPP_TABLE\n");
		}
		else if (port == OFPP_NORMAL)
		{
			printf("\t\tOutput port: OFPP_NORMAL\n");
		}
		else if (port == OFPP_FLOOD)
		{
			printf("\t\tOutput port: OFPP_FLOOD\n");
		}
		else if (port == OFPP_ALL)
		{
			printf("\t\tOutput port: OFPP_ALL\n");
		}
		else if (port == OFPP_CONTROLLER)
		{
			printf("\t\tOutput port: OFPP_CONTROLLER\n");
		}
		else if (port == OFPP_LOCAL)
		{
			printf("\t\tOutput port: OFPP_LOCAL\n");
		}
		else if (port == OFPP_NONE)
		{
			printf("\t\tOutput port: OFPP_NONE\n");
		}
		else if (port > OFPP_MAX)
		{
			printf("\t\tOutput port: %" PRIu16
			" (invalid physical port)\n", port);
		}
		else
		{
			printf("\t\tOutput port: %" PRIu16 "\n", port);
		}
		printf("\t\tMaximum length to send to controller (bytes): %"
		PRIu16 "\n", ntohs(((ofp_action_output *) action)->max_len));
	}
	else if (ntohs(action->type) == OFPAT_SET_VLAN_VID)
	{
		printf("\t\tType: OFPAT_SET_VLAN_VID\n");
		if (ntohs(((ofp_action_vlan_vid *) action)->vlan_vid) == OFP_VLAN_NONE)
		{
			printf("\t\tEthernet VLAN ID: OFP_VLAN_NONE\n");
		}
		else
		{
			printf("\t\tEthernet VLAN ID: %" PRIu16 "\n",
			        ntohs(((ofp_action_vlan_vid *) action)->vlan_vid));
		}
	}
	else if (ntohs(action->type) == OFPAT_SET_VLAN_PCP)
	{
		printf("\t\tType: OFPAT_SET_VLAN_PCP\n");
		printf("\t\tEthernet VLAN priority: %" PRIu16 "\n",
		        ntohs(((ofp_action_vlan_pcp *) action)->vlan_pcp));
	}
	else if (ntohs(action->type) == OFPAT_STRIP_VLAN)
	{
		printf("\t\tType: OFPAT_STRIP_VLAN\n");
	}
	else if (ntohs(action->type) == OFPAT_SET_DL_SRC)
	{
		printf("\t\tType: OFPAT_SET_DL_SRC\n");
		char dl_src[50];
		MAC2Colon(dl_src, ((ofp_action_dl_addr *) action)->dl_addr);
		printf("\t\tEthernet source MAC address: %s\n", dl_src);
	}
	else if (ntohs(action->type) == OFPAT_SET_DL_DST)
	{
		printf("\t\tType: OFPAT_SET_DL_DST\n");
		char dl_dst[50];
		MAC2Colon(dl_dst, ((ofp_action_dl_addr *) action)->dl_addr);
		printf("\t\tEthernet destination MAC address: %s\n", dl_dst);
	}
	else if (ntohs(action->type) == OFPAT_SET_NW_SRC)
	{
		printf("\t\tType: OFPAT_SET_NW_SRC\n");
		char nw_src[50];
		uint32_t nw_src_raw = ntohl(((ofp_action_nw_addr *) action)->nw_addr);
		IP2Dot(nw_src, (uint8_t *) &nw_src_raw);
		printf("\t\tIP source address: %s\n", nw_src);
	}
	else if (ntohs(action->type) == OFPAT_SET_NW_DST)
	{
		printf("\t\tType: OFPAT_SET_NW_DST\n");
		char nw_dst[50];
		uint32_t nw_dst_raw = ntohl(((ofp_action_nw_addr *) action)->nw_addr);
		IP2Dot(nw_dst, (uint8_t *) &nw_dst_raw);
		printf("\t\tIP source address: %s\n", nw_dst);
	}
	else if (ntohs(action->type) == OFPAT_SET_NW_TOS)
	{
		printf("\t\tType: OFPAT_SET_NW_TOS\n");
		printf("\t\tIP type of service: %" PRIu8 "\n",
		        ((ofp_action_nw_tos *) action)->nw_tos);
	}
	else if (ntohs(action->type) == OFPAT_SET_TP_SRC)
	{
		printf("\t\tType: OFPAT_SET_TP_SRC\n");
		printf("\t\tTCP/UDP source port or ICMP type: %" PRIu16 "\n",
		        ntohs(((ofp_action_tp_port *) action)->tp_port));
	}
	else if (ntohs(action->type) == OFPAT_SET_TP_DST)
	{
		printf("\t\tType: OFPAT_SET_TP_DST\n");
		printf("\t\tTCP/UDP destination port or ICMP code: %" PRIu16 "\n",
		        ntohs(((ofp_action_tp_port *) action)->tp_port));
	}
	else
	{
		printf("\t\tType: %" PRIu16 "\n", ntohs(action->type));
		printf("\t\tLength: %" PRIu16 "\n", ntohs(action->len));
	}
}

/**
 * Prints the specified OpenFlow flowtable entry to the console without locking
 * the flowtable.
 *
 * @param index The index of the entry to print.
 */
static void openflow_flowtable_print_entry_no_lock(uint32_t index)
{
	printf("\n");
	printf("=========\n");
	printf("Entry %d\n", index);
	printf("=========\n");
	printf("\n");

	openflow_flowtable_entry_type entry = flowtable->entries[index];
	if (entry.active)
	{
		printf("Match:\n");
		openflow_flowtable_print_match(&entry.match);

		printf("Cookie: %" PRIu64 "\n", ntohll(entry.cookie));

		char last_matched_str[100];
		struct tm *last_matched = localtime(&entry.last_matched);
		strftime(last_matched_str, 100, "%Y-%m-%d %H:%M:%S", last_matched);
		printf("Last matched time: %s\n", last_matched_str);

		char last_modified_str[100];
		struct tm *last_modified = localtime(&entry.last_modified);
		strftime(last_modified_str, 100, "%Y-%m-%d %H:%M:%S", last_modified);
		printf("Last modified time: %s\n", last_modified_str);

		printf("Last matched timeout (seconds): %" PRIu16 "\n",
		        ntohs(entry.idle_timeout));
		printf("Last modified timeout (seconds): %" PRIu16 "\n",
		        ntohs(entry.hard_timeout));

		printf("Priority: %" PRIu32 "\n", ntohs(entry.hard_timeout));

		printf("Entry flags: %" PRIu16 "\n", ntohs(entry.flags));
		if (ntohs(entry.flags) & OFPFF_SEND_FLOW_REM)
		{
			printf("\tOFPFF_SEND_FLOW_REM\n");
		}
		if (ntohs(entry.flags) & OFPFF_CHECK_OVERLAP)
		{
			printf("\tOFPFF_CHECK_OVERLAP\n");
		}

		printf("Actions:\n");
		uint32_t i;
		for (i = 0; i < OPENFLOW_MAX_ACTIONS; i++)
		{
			if (entry.actions[i].active)
			{
				printf("\tAction %" PRIu32 ":\n", i);
				openflow_flowtable_print_action(&entry.actions[i].header);
			}
		}
	}
	else
	{
		printf("Entry inactive\n");
	}
}

/**
 * Prints the specified OpenFlow flowtable entry to the console.
 *
 * @param index The index of the entry to print.
 */
void openflow_flowtable_print_entry(uint32_t index)
{
	pthread_mutex_lock(&flowtable_mutex);
	openflow_flowtable_print_entry_no_lock(index);
	pthread_mutex_unlock(&flowtable_mutex);
}

/**
 * Prints the OpenFlow flowtable entries to the CLI.
 */
void openflow_flowtable_print_entries()
{
	uint32_t i;
	for (i = 0; i < OPENFLOW_MAX_FLOWTABLE_ENTRIES; i++)
	{
		openflow_flowtable_print_entry(i);
	}
}

/**
 * Prints the statistics for the specified entry in the flowtable.
 *
 * @param The index of the entry to print.
 */
void openflow_flowtable_print_entry_stat(uint32_t index)
{
	pthread_mutex_lock(&flowtable_mutex);

	if (index < 0 || index >= OPENFLOW_MAX_FLOWTABLE_ENTRIES)
	{
		printf("Entry index invalid\n");
		pthread_mutex_unlock(&flowtable_mutex);
		return;
	}

	printf("\n");
	printf("=========\n");
	printf("Entry %d\n", index);
	printf("=========\n");
	printf("\n");

	openflow_flowtable_entry_type *entry = &flowtable->entries[index];
	if (entry->active)
	{
		openflow_flowtable_update_entry_stats(index);

		printf("Table ID: %" PRIu8 "\n", entry->stats.table_id);

		printf("Match:\n");
		openflow_flowtable_print_match(&entry->stats.match);

		printf("Duration (seconds): %" PRIu32 "\n",
		        ntohl(entry->stats.duration_sec));
		printf("Duration after seconds (nanoseconds): %" PRIu32 "\n",
		        ntohl(entry->stats.duration_nsec));
		printf("Last matched timeout (seconds): %" PRIu16 "\n",
		        ntohs(entry->stats.idle_timeout));
		printf("Last modified timeout (seconds): %" PRIu16 "\n",
		        ntohs(entry->stats.hard_timeout));
		printf("Cookie: %" PRIu64 "\n", ntohll(entry->stats.cookie));
		printf("Packet count: %" PRIu64 "\n",
		        ntohll(entry->stats.packet_count));
		printf("Byte count: %" PRIu64 "\n", ntohll(entry->stats.byte_count));
	}
	else
	{
		printf("Entry inactive\n");
	}

	pthread_mutex_unlock(&flowtable_mutex);
}

/**
 * Prints the statistics for all entries in the flowtable.
 */
void openflow_flowtable_print_entry_stats()
{
	uint32_t i;
	for (i = 0; i < OPENFLOW_MAX_FLOWTABLE_ENTRIES; i++)
	{
		openflow_flowtable_print_entry_stat(i);
	}
}

/**
 * Prints the statistics for the flowtable.
 */
void openflow_flowtable_print_table_stats()
{
	pthread_mutex_lock(&flowtable_mutex);
	printf("\n");
	printf("=========\n");
	printf("Table %d\n", flowtable->stats.table_id);
	printf("=========\n");
	printf("\n");

	printf("Name: %s\n", flowtable->stats.name);

	if (ntohl(flowtable->stats.wildcards) == OFPFW_ALL)
	{
		printf("Wildcards: All fields\n");
	}
	else
	{
		printf("Wildcards:\n");
		if (ntohl(flowtable->stats.wildcards) & OFPFW_IN_PORT)
		{
			printf("\tInput port\n");
		}
		if (ntohl(flowtable->stats.wildcards) & OFPFW_DL_SRC)
		{
			printf("\tEthernet source MAC address\n");
		}
		if (ntohl(flowtable->stats.wildcards) & OFPFW_DL_DST)
		{
			printf("\tEthernet destination MAC address\n");
		}
		if (ntohl(flowtable->stats.wildcards) & OFPFW_DL_VLAN)
		{
			printf("\tEthernet VLAN ID\n");
		}
		if ((ntohl(flowtable->stats.wildcards) & OFPFW_DL_VLAN_PCP))
		{
			printf("\tEthernet VLAN priority\n");
		}
		if (ntohl(flowtable->stats.wildcards) & OFPFW_DL_TYPE)
		{
			printf("\tEthernet frame type\n");
		}
		if (ntohl(flowtable->stats.wildcards) & OFPFW_NW_TOS)
		{
			printf("\t\tIP type of service\n");
		}
		if (ntohl(flowtable->stats.wildcards) & OFPFW_NW_PROTO)
		{
			printf("\t\tIP protocol or ARP opcode\n");
		}
		if (ntohl(flowtable->stats.wildcards) & OFPFW_TP_SRC)
		{
			printf("\t\tTCP/UDP source port or ICMP type\n");
		}
		if (ntohl(flowtable->stats.wildcards) & OFPFW_TP_DST)
		{
			printf("\t\tTCP/UDP destination port or ICMP code\n");
		}
	}

	printf("Maximum number of supported entries: %" PRIu32 "\n",
	        ntohl(flowtable->stats.max_entries));
	printf("Number of active entries: %" PRIu32 "\n",
	        ntohl(flowtable->stats.active_count));
	printf("Number of packets looked up in tables: %" PRIu64 "\n",
	        ntohll(flowtable->stats.lookup_count));
	printf("Number of packets that hit table: %" PRIu64 "\n",
	        ntohll(flowtable->stats.matched_count));

	pthread_mutex_unlock(&flowtable_mutex);
}

/**
 * OpenFlow timeout thread.
 */
static void openflow_flowtable_timeout()
{
	while (1)
	{
		pthread_mutex_lock(&flowtable_mutex);

		if (flowtable == NULL)      // not yet allocated (or released): nothing to expire
		{
			pthread_mutex_unlock(&flowtable_mutex);
			sleep(1);
			continue;
		}

		uint32_t i;
		for (i = 0; i < OPENFLOW_MAX_FLOWTABLE_ENTRIES; i++)
		{
			if (flowtable->entries[i].active)
			{
				time_t now;
				time(&now);

				double idle_diff = difftime(now,
				        flowtable->entries[i].last_matched);
				if (flowtable->entries[i].idle_timeout != 0)
				{
					if (idle_diff > ntohs(flowtable->entries[i].idle_timeout))
					{
						verbose(2, "[openflow_flowtable_timeout]:: Entry"
								" %d idle timeout.", i);
						openflow_flowtable_delete_entry_at_index(i,
								OFPRR_IDLE_TIMEOUT);
						continue;
					}
				}

				double hard_diff = difftime(now,
				        flowtable->entries[i].last_modified);
				if (flowtable->entries[i].hard_timeout != 0)
				{
					if (hard_diff > htons(flowtable->entries[i].hard_timeout))
					{
						verbose(2, "[openflow_flowtable_timeout]:: Entry"
								" %d hard timeout.", i);
						openflow_flowtable_delete_entry_at_index(i,
								OFPRR_HARD_TIMEOUT);
						continue;
					}
				}
			}
		}

		pthread_mutex_unlock(&flowtable_mutex);
		sleep(1);
	}
}

/**
 * Initializes the OpenFlow flowtable timeout thread.
 */
pthread_t openflow_flowtable_timeout_init()
{
	int32_t threadstat;
	pthread_t threadid;

	threadstat = pthread_create((pthread_t *) &threadid, NULL,
	        (void *) openflow_flowtable_timeout, NULL);
	return threadid;
}
