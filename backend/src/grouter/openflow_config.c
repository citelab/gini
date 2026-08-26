/**
 * openflow_config.c - OpenFlow switch configuration
 */

#include "openflow_config.h"

#include <arpa/inet.h>
#include <inttypes.h>
#include <stdint.h>
#include <string.h>

#include "gnet.h"
#include "grouter.h"
#include "openflow.h"
#include "openflow_ctrl_iface.h"
#include "openflow_defs.h"

// OpenFlow physical ports
static ofp_phy_port phy_ports[OPENFLOW_MAX_PHYSICAL_PORTS];
static pthread_mutex_t phy_ports_mutex;

// OpenFlow physical port statistics
static ofp_port_stats phy_port_stats[OPENFLOW_MAX_PHYSICAL_PORTS];
static pthread_mutex_t phy_port_stats_mutex;

// OpenFlow switch configuration
static uint16_t switch_config_flags;
static pthread_mutex_t switch_config_flags_mutex;

extern router_config rconfig;

/**
 * Converts the specified GNET port number to an OpenFlow port number.
 *
 * @param gnet_port_num The GNET port number.
 *
 * @return The corresponding OpenFlow port number.
 */
uint16_t openflow_config_get_of_port_num(uint16_t gnet_port_num)
{
	return gnet_port_num + 1;
}

/**
 * Converts the specified OpenFlow port number to an GNET port number.
 *
 * @param gnet_port_num The OpenFlow port number.
 *
 * @return The corresponding GNET port number.
 */
uint16_t openflow_config_get_gnet_port_num(uint16_t openflow_port_num)
{
	return openflow_port_num - 1;
}

/**
 * Sets the OpenFlow physical port statistics structs to their default values.
 */
static void openflow_config_set_phy_port_stats_defaults()
{
	pthread_mutex_lock(&phy_port_stats_mutex);

	uint32_t i;
	for (i = 0; i < OPENFLOW_MAX_PHYSICAL_PORTS; i++)
	{
		memset(&phy_port_stats[i], 0, sizeof(ofp_port_stats));

		phy_port_stats[i].port_no = htons(openflow_config_get_of_port_num(i));

		// Unsupported counters
		phy_port_stats[i].rx_dropped = htonll(-1);
		phy_port_stats[i].tx_dropped = htonll(-1);
		phy_port_stats[i].rx_errors = htonll(-1);
		phy_port_stats[i].tx_errors = htonll(-1);
		phy_port_stats[i].rx_frame_err = htonll(-1);
		phy_port_stats[i].rx_over_err = htonll(-1);
		phy_port_stats[i].rx_crc_err = htonll(-1);
		phy_port_stats[i].collisions = htonll(-1);
	}

	pthread_mutex_unlock(&phy_port_stats_mutex);
}

/**
 * Sets the OpenFlow physical port structs to their default values. These
 * values change depending on the current state of the GNET interfaces.
 */
static void openflow_config_set_phy_port_defaults()
{
	pthread_mutex_lock(&phy_ports_mutex);

	uint32_t i;
	for (i = 0; i < OPENFLOW_MAX_PHYSICAL_PORTS; i++)
	{
		memset(&phy_ports[i], 0, sizeof(ofp_phy_port));

		phy_ports[i].port_no = htons(openflow_config_get_of_port_num(i));
		sprintf(phy_ports[i].name, "Port %d",
		        openflow_config_get_of_port_num(i));
		phy_ports[i].config = 0;
		phy_ports[i].state = 0;
		phy_ports[i].curr = 0;
		phy_ports[i].advertised = 0;
		phy_ports[i].supported = 0;
		phy_ports[i].peer = 0;

		interface_t *iface = findInterface(i);
		if (iface == NULL)
		{
			memset(phy_ports[i].hw_addr, 0, OFP_ETH_ALEN);
			phy_ports[i].state |= htonl(OFPPS_LINK_DOWN);
		}
		else
		{
			COPY_MAC(phy_ports[i].hw_addr, iface->mac_addr);
		}
	}

	pthread_mutex_unlock(&phy_ports_mutex);
}

/**
 * Initializes the OpenFlow physical ports.
 */
void openflow_config_init_phy_ports()
{
	openflow_config_set_phy_port_defaults();
	openflow_config_set_phy_port_stats_defaults();
}

/**
 * Updates the specified OpenFlow physical port struct to match the state
 * of the corresponding GNET interface.
 *
 * @param The OpenFlow port number corresponding to the port to update.
 */
void openflow_config_update_phy_port(int32_t openflow_port_num)
{
	pthread_mutex_lock(&phy_ports_mutex);

	int32_t gnet_port_num = openflow_config_get_gnet_port_num(
	        openflow_port_num);

	interface_t *iface = findInterface(gnet_port_num);
	if (iface == NULL)
	{
		memset(phy_ports[gnet_port_num].hw_addr, 0, OFP_ETH_ALEN);
		phy_ports[gnet_port_num].state |= htonl(OFPPS_LINK_DOWN);
	}
	else
	{
		COPY_MAC(phy_ports[gnet_port_num].hw_addr, iface->mac_addr);
		// Clear LINK_DOWN as well as setting it. Without this the bit is
		// sticky: a port marked down (at init, or when a link drops) could
		// never come back up, so the controller would keep seeing a dead
		// port and openflow_pkt_proc_forward_packet_to_port() would go on
		// silently discarding everything sent to it.
		phy_ports[gnet_port_num].state &= ~htonl(OFPPS_LINK_DOWN);
	}

	openflow_ctrl_iface_send_port_status(&phy_ports[gnet_port_num],
	        OFPPR_MODIFY);

	pthread_mutex_unlock(&phy_ports_mutex);
}

/**
 * Gets the OpenFlow ofp_phy_port struct corresponding to the specified
 * OpenFlow port number.
 *
 * This function uses dynamically allocated memory that must be manually
 * freed afterwards.
 *
 * @param openflow_port_num The specified OpenFlow port number.
 *
 * @return The OpenFlow physical port struct corresponding to the
 *         specified port number.
 */
ofp_phy_port *openflow_config_get_phy_port(uint16_t openflow_port_num)
{
	if (openflow_port_num > 0
	        && openflow_port_num < OPENFLOW_MAX_PHYSICAL_PORTS + 1)
	{
		pthread_mutex_lock(&phy_ports_mutex);

		ofp_phy_port *port = malloc(sizeof(ofp_phy_port));
		memcpy(port,
		        &phy_ports[openflow_config_get_gnet_port_num(openflow_port_num)],
		        sizeof(ofp_phy_port));

		pthread_mutex_unlock(&phy_ports_mutex);
		return port;
	}
	else
	{
		return NULL;
	}
}

/**
 * Sets the OpenFlow ofp_phy_port struct corresponding to the specified
 * OpenFlow port number.
 *
 * @param openflow_port_num The specified OpenFlow port number.
 * @param port              The new ofp_phy_port struct.
 */
void openflow_config_set_phy_port(uint16_t openflow_port_num,
        ofp_phy_port *port)
{
	if (openflow_port_num > 0
	        && openflow_port_num < OPENFLOW_MAX_PHYSICAL_PORTS + 1)
	{
		pthread_mutex_lock(&phy_ports_mutex);
		phy_ports[openflow_config_get_gnet_port_num(openflow_port_num)] = *port;
		pthread_mutex_unlock(&phy_ports_mutex);
	}
}

/**
 * Prints information associated with the specified OpenFlow physical port.
 *
 * @param index The index of the port to print.
 */
void openflow_config_print_port(uint32_t index)
{
	pthread_mutex_lock(&phy_ports_mutex);

	if (index <= 0 || index > OPENFLOW_MAX_PHYSICAL_PORTS)
	{
		printf("Port index invalid\n");
		pthread_mutex_unlock(&phy_ports_mutex);
		return;
	}

	printf("\n");
	printf("=========\n");
	printf("Port %d\n", index);
	printf("=========\n");
	printf("\n");

	ofp_phy_port *port = &phy_ports[openflow_config_get_gnet_port_num(index)];

	printf("Name: %s\n", port->name);

	char hw_addr[50];
	MAC2Colon(hw_addr, port->hw_addr);
	printf("Hardware address: %s\n", hw_addr);

	if (ntohl(port->config) == 0)
	{
		printf("Configuration: None\n");
	}
	else
	{
		printf("Configuration:\n");
		if (ntohl(port->config) & OFPPC_PORT_DOWN)
		{
			printf("\tOFPPC_PORT_DOWN");
		}
		if (ntohl(port->config) & OFPPC_NO_FLOOD)
		{
			printf("\tOFPPC_NO_FLOOD");
		}
		if (ntohl(port->config) & OFPPC_NO_PACKET_IN)
		{
			printf("\tOFPPC_NO_PACKET_IN");
		}
	}

	if (ntohl(port->state) & OFPPS_LINK_DOWN)
	{
		printf("State: Down\n");
	}
	else
	{
		printf("State: Up\n");
	}

	pthread_mutex_unlock(&phy_ports_mutex);
}

/**
 * Prints information associated with all OpenFlow physical ports.
 */
void openflow_config_print_ports()
{
	uint32_t i;
	for (i = 1; i <= OPENFLOW_MAX_PHYSICAL_PORTS; i++)
	{
		openflow_config_print_port(i);
	}
}

/**
 * Gets the OpenFlow ofp_port_stats struct corresponding to the specified
 * OpenFlow port number.
 *
 * This function uses dynamically allocated memory that must be manually
 * freed afterwards.
 *
 * @param openflow_port_num The specified OpenFlow port number.
 *
 * @return The OpenFlow ofp_port_stats struct corresponding to the
 *         specified port number.
 */
ofp_port_stats *openflow_config_get_port_stats(uint16_t openflow_port_num)
{
	if (openflow_port_num > 0
	        && openflow_port_num < OPENFLOW_MAX_PHYSICAL_PORTS + 1)
	{
		pthread_mutex_lock(&phy_port_stats_mutex);

		ofp_port_stats *stats = malloc(sizeof(ofp_port_stats));
		memcpy(stats,
		        &phy_port_stats[openflow_config_get_gnet_port_num(
		                openflow_port_num)], sizeof(ofp_port_stats));

		pthread_mutex_unlock(&phy_port_stats_mutex);
		return stats;
	}
	else
	{
		return NULL;
	}
}

/**
 * Sets the OpenFlow ofp_port_stats struct corresponding to the specified
 * OpenFlow port number.
 *
 * @param openflow_port_num The specified OpenFlow port number.
 * @param stats             The new ofp_port_stats struct.
 */
void openflow_config_set_port_stats(uint16_t openflow_port_num,
        ofp_port_stats *stats)
{
	if (openflow_port_num > 0
	        && openflow_port_num < OPENFLOW_MAX_PHYSICAL_PORTS + 1)
	{
		pthread_mutex_lock(&phy_port_stats_mutex);
		phy_port_stats[openflow_config_get_gnet_port_num(openflow_port_num)] =
		        *stats;
		pthread_mutex_unlock(&phy_port_stats_mutex);
	}
}

/**
 * Prints the statistics for the specified OpenFlow physical port.
 *
 * @param index The index of the port to print.
 */
void openflow_config_print_port_stat(uint32_t index)
{
	pthread_mutex_lock(&phy_port_stats_mutex);

	if (index <= 0 || index > OPENFLOW_MAX_PHYSICAL_PORTS)
	{
		printf("Port index invalid\n");
		pthread_mutex_unlock(&phy_port_stats_mutex);
		return;
	}

	ofp_port_stats *stats = &phy_port_stats[openflow_config_get_gnet_port_num(
	        index)];

	printf("\n");
	printf("=========\n");
	printf("Port %d\n", index);
	printf("=========\n");
	printf("\n");

	printf("RX packets: %" PRIu64 "\n", ntohll(stats->rx_packets));
	printf("RX bytes: %" PRIu64 "\n", ntohll(stats->rx_bytes));

	printf("TX packets: %" PRIu64 "\n", ntohll(stats->tx_packets));
	printf("TX bytes: %" PRIu64 "\n", ntohll(stats->tx_bytes));

	pthread_mutex_unlock(&phy_port_stats_mutex);
}

/**
 * Prints the statistics for all OpenFlow physical ports.
 */
void openflow_config_print_port_stats()
{
	uint32_t i;
	for (i = 1; i <= OPENFLOW_MAX_PHYSICAL_PORTS; i++)
	{
		openflow_config_print_port(i);
	}
}

/**
 * Gets the current switch configuration flags in network byte order.
 *
 * @return The current switch configuration flags.
 */
uint16_t openflow_config_get_switch_config_flags()
{
	pthread_mutex_lock(&switch_config_flags_mutex);
	uint16_t flags = switch_config_flags;
	pthread_mutex_unlock(&switch_config_flags_mutex);
	return flags;
}

/**
 * Sets the current switch configuration flags in network byte order.
 *
 * @param flags The switch configuration flags to set.
 */
void openflow_config_set_switch_config_flags(uint16_t flags)
{
	pthread_mutex_lock(&switch_config_flags_mutex);
	switch_config_flags = flags;
	pthread_mutex_unlock(&switch_config_flags_mutex);
}

/**
 * Gets the OpenFlow switch features.
 *
 * @return The OpenFlow switch features.
 */
ofp_switch_features openflow_config_get_switch_features()
{
	ofp_switch_features switch_features;

	switch_features.datapath_id = 0;
	uint32_t i;
	for (i = 0; i < MAX_INTERFACES; i++)
	{
		interface_t *iface = findInterface(i);
		if (iface != NULL)
		{
			COPY_MAC(((uint8_t * )&switch_features.datapath_id) + 2,
			        iface->mac_addr);
			break;
		}
	}

	switch_features.n_buffers = htonl(0);
	switch_features.n_tables = 1;
	switch_features.capabilities = htonl(
	        OFPC_FLOW_STATS | OFPC_TABLE_STATS | OFPC_PORT_STATS
	                | OFPC_ARP_MATCH_IP);
	switch_features.actions = htonl(
	        1 << OFPAT_OUTPUT | 1 << OFPAT_SET_VLAN_VID
	                | 1 << OFPAT_SET_VLAN_PCP | 1 << OFPAT_STRIP_VLAN
	                | 1 << OFPAT_SET_DL_SRC | 1 << OFPAT_SET_DL_DST
	                | 1 << OFPAT_SET_NW_SRC | 1 << OFPAT_SET_NW_DST
	                | 1 << OFPAT_SET_NW_TOS | 1 << OFPAT_SET_TP_SRC
	                | 1 << OFPAT_SET_TP_DST);

	return switch_features;
}

/**
 * Gets the OpenFlow description stats.
 *
 * @return The OpenFlow description stats.
 */
ofp_desc_stats openflow_config_get_desc_stats()
{
	ofp_desc_stats stats;
	strncpy(stats.mfr_desc, OPENFLOW_MFR_DESC, DESC_STR_LEN);
	stats.mfr_desc[DESC_STR_LEN - 1] = '\0';
	strncpy(stats.hw_desc, OPENFLOW_HW_DESC, DESC_STR_LEN);
	stats.hw_desc[DESC_STR_LEN - 1] = '\0';
	strncpy(stats.sw_desc, OPENFLOW_SW_DESC, DESC_STR_LEN);
	stats.sw_desc[DESC_STR_LEN - 1] = '\0';
	strncpy(stats.serial_num, rconfig.router_name, SERIAL_NUM_LEN);
	stats.serial_num[SERIAL_NUM_LEN - 1] = '\0';
	strncpy(stats.dp_desc, rconfig.router_name, DESC_STR_LEN);
	stats.dp_desc[DESC_STR_LEN - 1] = '\0';

	return stats;
}

/**
 * Prints the OpenFlow description statistics to the console.
 */
void openflow_config_print_desc_stats()
{
	ofp_desc_stats stats = openflow_config_get_desc_stats();
	printf("Manufacturer description: %s\n", stats.mfr_desc);
	printf("Hardware description: %s\n", stats.hw_desc);
	printf("Software description: %s\n", stats.sw_desc);
	printf("Serial number: %s\n", stats.serial_num);
	printf("Datapath description: %s\n", stats.dp_desc);
}
