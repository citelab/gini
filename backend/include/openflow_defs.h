/**
 * openflow_defs.h - OpenFlow definitions
 */

#ifndef __OPENFLOW_DEFS_H_
#define __OPENFLOW_DEFS_H_

#include <stdint.h>
#include <time.h>

#include "gnet.h"
#include "openflow.h"

// OpenFlow struct typedefs
typedef struct ofp_header ofp_header;
typedef struct ofp_hello ofp_hello;
typedef struct ofp_switch_config ofp_switch_config;
typedef struct ofp_phy_port ofp_phy_port;
typedef struct ofp_switch_features ofp_switch_features;
typedef struct ofp_port_status ofp_port_status;
typedef struct ofp_port_mod ofp_port_mod;
typedef struct ofp_packet_in ofp_packet_in;
typedef struct ofp_action_output ofp_action_output;
typedef struct ofp_action_vlan_vid ofp_action_vlan_vid;
typedef struct ofp_action_vlan_pcp ofp_action_vlan_pcp;
typedef struct ofp_action_dl_addr ofp_action_dl_addr;
typedef struct ofp_action_nw_addr ofp_action_nw_addr;
typedef struct ofp_action_tp_port ofp_action_tp_port;
typedef struct ofp_action_nw_tos ofp_action_nw_tos;
typedef struct ofp_action_header ofp_action_header;
typedef struct ofp_packet_out ofp_packet_out;
typedef struct ofp_match ofp_match;
typedef struct ofp_flow_mod ofp_flow_mod;
typedef struct ofp_flow_removed ofp_flow_removed;
typedef struct ofp_error_msg ofp_error_msg;
typedef struct ofp_stats_request ofp_stats_request;
typedef struct ofp_stats_reply ofp_stats_reply;
typedef struct ofp_desc_stats ofp_desc_stats;
typedef struct ofp_flow_stats_request ofp_flow_stats_request;
typedef struct ofp_flow_stats ofp_flow_stats;
typedef struct ofp_aggregate_stats_request ofp_aggregate_stats_request;
typedef struct ofp_aggregate_stats_reply ofp_aggregate_stats_reply;
typedef struct ofp_table_stats ofp_table_stats;
typedef struct ofp_port_stats_request ofp_port_stats_request;
typedef struct ofp_port_stats ofp_port_stats;

// OpenFlow error codes
#define OPENFLOW_CTRL_IFACE_ERR_UNKNOWN          ((int32_t) -1)
#define OPENFLOW_CTRL_IFACE_ERR_CONN_CLOSED      ((int32_t) -2)
#define OPENFLOW_CTRL_IFACE_ERR_SEND_TIMEOUT     ((int32_t) -3)
#define OPENFLOW_CTRL_IFACE_ERR_OPENFLOW         ((int32_t) -4)
#define OPENFLOW_PKT_PROC_ERR_ACTION_INVALID     ((int32_t) -5)
#define OPENFLOW_PKT_PROC_ERR_QUEUE              ((int32_t) -6)

// OpenFlow constants
#define OPENFLOW_MFR_DESC                        "McGill University Advanced Networking Research Laboratory (ANRL)"
#define OPENFLOW_HW_DESC                         "GINI"
#define OPENFLOW_SW_DESC                         "gRouter"

#define OPENFLOW_TABLE_NAME                      "Standard"

#define OPENFLOW_NUM_TABLES                      ((uint32_t) 1)
#define OPENFLOW_ERROR_MSG_MIN_DATA_SIZE         64
#define OPENFLOW_CTRL_IFACE_SEND_TIMEOUT         10

#define OPENFLOW_MAX_PHYSICAL_PORTS              MAX_INTERFACES
#define OPENFLOW_MAX_FLOWTABLE_ENTRIES           ((uint32_t) 100)
#define OPENFLOW_MAX_ACTIONS                     ((uint32_t) 25)
#define OPENFLOW_MAX_ACTION_SIZE                 ((uint32_t) 16)
#define OPENFLOW_MAX_MSG_TYPE                    OFPT_QUEUE_GET_CONFIG_REPLY

/**
 * Represents an OpenFlow action.
 */
typedef struct
{
	// 1 if this entry is active (i.e. not empty), 0 otherwise
	uint8_t active;
	// Action header
	ofp_action_header header;
	// Padding
	uint8_t pad[OPENFLOW_MAX_ACTION_SIZE - sizeof(ofp_action_header)];
} openflow_flowtable_action_type;

/**
 * Represents an entry in an OpenFlow flowtable.
 */
typedef struct
{
	// 1 if this entry is active (i.e. not empty), 0 otherwise
	uint8_t active;
	// Match headers
	ofp_match match;
	// Cookie (opaque data) from controller
	uint64_t cookie;
	// The last time this entry was matched against a packet
	time_t last_matched;
	// The last time this entry was modified by the controller
	time_t last_modified;
	// The time this entry was added
	time_t added;
	// Number of seconds since last match before expiration of this entry;
	// stored in network byte format
	uint16_t idle_timeout;
	// Number of seconds since last modification before expiration of this
	// entry; stored in network byte format
	uint16_t hard_timeout;
	// Entry priority (only relevant for wildcards); stored in network byte
	// format.
	//
	// uint16_t, matching the wire (ofp_flow_mod.priority) and the stats struct.
	// This was uint32_t, and openflow_flowtable_modify_entry_at_index assigns the
	// wire field straight in -- so a 16-bit NETWORK-order value was widened to 32
	// bits and then compared numerically in get_entry_for_packet. On a
	// little-endian host that inverts the ordering: priority 1 stored as 0x0100
	// (256) and priority 32768 as 0x0080 (128), so the boot-time wildcard default
	// rule outranked every rule the controller installed.
	uint16_t priority;
	// Entry flags (see ofp_flow_mod_flags); stored in network byte format
	uint16_t flags;
	// Entry actions
	openflow_flowtable_action_type actions[OPENFLOW_MAX_ACTIONS];
	// Entry stats
	ofp_flow_stats stats;
} openflow_flowtable_entry_type;

/**
 * Represents an OpenFlow flowtable.
 */
typedef struct
{
	// Table entries
	openflow_flowtable_entry_type entries[OPENFLOW_MAX_FLOWTABLE_ENTRIES];
	// Table stats
	ofp_table_stats stats;
} openflow_flowtable_type;

#endif // ifndef __OPENFLOW_DEFS_H_
