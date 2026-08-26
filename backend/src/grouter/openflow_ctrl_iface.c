/**
 * openflow_ctrl_iface.c - OpenFlow controller interface
 *
 * This interface implements the OpenFlow 1.0.2 specification.
 *
 * Notes about unsupported features:
 *   - Emergency mode is not supported; fail-secure mode is used instead. Any
 *     flow table entry with the emergency flag is treated as a normal entry.
 *   - IP fragment re-assembly is not supported.
 *   - The 802.1d spanning tree protocol is not supported.
 *   - Queues are not supported.
 *   - No vendor extensions are supported.
 *   - Certain port statistics are not supported.
 *   - TLS support is not implemented. This is the only part of the
 *     implementation that does not conform to the specification; all other
 *     unsupported features are optional.
 *
 * Other nodes:
 *   - All wildcards are supported during matching.
 *   - Matching of IP addresses in ARP packets is supported.
 *   - VLAN tag actions and matching are supported, though no other component
 *     of GINI currently supports VLAN tags.
 *   - Barrier requests and replies are supported, but are currently meaningless
 *     since only one thread is assigned to the controller-switch interface.
 *     If this changes, then the implementation for this feature will have to
 *     be updated.
 *   - Port statistics only count packets and bytes received by and transmitted
 *     from the port abstractions in the OpenFlow packet processor. This does
 *     not take into account the fact that packets may be dropped by GNET
 *     before and after the processor.
 */

#include "openflow_ctrl_iface.h"

#include <arpa/inet.h>
#include <errno.h>
#include <inttypes.h>
#include <netinet/in.h>
#include <pthread.h>
#include <slack/err.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>
#include <netdb.h>
#include <arpa/inet.h>

#include "gnet.h"
#include "grouter.h"
#include "ip.h"
#include "message.h"
#include "openflow.h"
#include "openflow_config.h"
#include "openflow_defs.h"
#include "openflow_flowtable.h"
#include "openflow_pkt_proc.h"
#include "protocols.h"
#include "tcp.h"

// Controller socket file descriptor
static int32_t ofc_socket_fd;
static pthread_mutex_t ofc_socket_mutex;

// Transaction ID counter
static uint32_t xid = 0;
static pthread_mutex_t xid_mutex;

// Connection status
static uint8_t connection_status = 0;
static pthread_mutex_t connection_status_mutex;

// Reconnect requested variable
static uint8_t reconnect = 0;
static pthread_mutex_t reconnect_mutex;

/**
 * Gets a transaction ID. This transaction ID will not have been used to send
 * data from the switch before unless more than 2^32 messages have been sent.
 *
 * @return A transaction ID that probably has not been used to send data from
 *         the switch before.
 */
static uint32_t openflow_ctrl_iface_get_xid()
{
	pthread_mutex_lock(&xid_mutex);
	uint32_t next_xid = xid++;
	pthread_mutex_unlock(&xid_mutex);
	return next_xid;
}

/**
 * Gets the current controller connection state.
 *
 * @return 1 if the switch is currently connected to the controller, 0 if not.
 */
static uint8_t openflow_ctrl_iface_get_conn_state()
{
	pthread_mutex_lock(&connection_status_mutex);
	uint8_t status = connection_status;
	pthread_mutex_unlock(&connection_status_mutex);
	return status;
}

/**
 * Sets the current controller connection state to up.
 */
static void openflow_ctrl_iface_conn_up()
{
	pthread_mutex_lock(&connection_status_mutex);
	connection_status = 1;
	pthread_mutex_unlock(&connection_status_mutex);

	pthread_mutex_lock(&reconnect_mutex);
	reconnect = 0;
	pthread_mutex_unlock(&reconnect_mutex);
}

/**
 * Sets the current controller connection state to down.
 */
static void openflow_ctrl_iface_conn_down()
{
	pthread_mutex_lock(&connection_status_mutex);
	connection_status = 0;
	pthread_mutex_unlock(&connection_status_mutex);
}

/**
 * Requests that the switch reconnect to the controller.
 */
void openflow_ctrl_iface_reconnect()
{
	pthread_mutex_lock(&reconnect_mutex);
	reconnect = 1;
	pthread_mutex_unlock(&reconnect_mutex);
}

/**
 * Gets a template for an OpenFlow message. The message is created using
 * dynamically-allocated memory and must be freed when no longer needed.
 *
 * @param type   The type of the message.
 * @param length The length of the message in host byte order.
 *
 * @return A pointer to an OpenFlow message template.
 */
static ofp_header *openflow_ctrl_iface_create_msg(uint8_t type, uint16_t length)
{
	ofp_header *msg = malloc(length);
	memset(msg, 0, length);
	msg->version = OFP_VERSION;
	msg->type = type;
	msg->length = htons(length);
	return msg;
}

/**
 * Gets a template for an OpenFlow error message. The message is created using
 * dynamically-allocated memory and must be freed when no longer needed.
 *
 * @return A pointer to an OpenFlow error message template.
 */
static ofp_error_msg *openflow_ctrl_iface_create_error_msg()
{
	ofp_error_msg *error_msg = (ofp_error_msg *) openflow_ctrl_iface_create_msg(
	        OFPT_ERROR,
	        sizeof(ofp_error_msg) + OPENFLOW_ERROR_MSG_MIN_DATA_SIZE);
	error_msg->header.length = htons(sizeof(ofp_error_msg));
	memset(error_msg->data, 0, OPENFLOW_ERROR_MSG_MIN_DATA_SIZE);
	return error_msg;
}

/**
 * Sends an OpenFlow message to the controller TCP socket.
 *
 * @param data A pointer to the message.
 * @param len  The length of the message in bytes.
 *
 * @return The number of bytes sent, or a negative value if an error occurred.
 */
static int32_t openflow_ctrl_iface_send(void *data, uint32_t len)
{
	pthread_mutex_lock(&ofc_socket_mutex);

	uint8_t counter = 0;
	uint32_t sent = 0;
	while (sent < len && counter < OPENFLOW_CTRL_IFACE_SEND_TIMEOUT)
	{
		int32_t ret = send(ofc_socket_fd, ((uint8_t *) data) + sent, len - sent,
		        0);

		if (ret < 0)
		{
			if (errno == EINTR)
				continue;                       /* interrupted, not an error */
			if (errno == EAGAIN || errno == EWOULDBLOCK)
			{
				counter += 1;                   /* buffer full: back off, retry */
				sleep(1);
				continue;
			}
			pthread_mutex_unlock(&ofc_socket_mutex);
			verbose(1, "[openflow_ctrl_iface_send]:: Unknown error occurred"
					" while sending message.");
			return OPENFLOW_CTRL_IFACE_ERR_UNKNOWN;
		}
		if (ret == 0)
		{
			counter += 1;
			sleep(1);
			continue;
		}

		/* Advance by what was ACTUALLY written -- exactly once.
		 *
		 * This used to add `ret` twice on a short write (once in a
		 * `ret < len - sent` branch, then again unconditionally below). `sent`
		 * therefore overshot, the loop exited believing the message was complete,
		 * and the message went out TRUNCATED. A truncated OpenFlow message
		 * desynchronises the controller's byte stream -- it reads the tail of one
		 * message as the header of the next -- so the controller drops the
		 * connection. That is what made switches flap connect/disconnect under
		 * load, which in turn reset topology discovery before it could ever
		 * converge. Short writes are normal on a busy socket; they are not errors. */
		sent += ret;
	}

	if (sent < len)
	{
		pthread_mutex_unlock(&ofc_socket_mutex);
		verbose(1, "[openflow_ctrl_iface_send]:: Send timeout reached"
				" while sending message.");
		return OPENFLOW_CTRL_IFACE_ERR_SEND_TIMEOUT;
	}

	pthread_mutex_unlock(&ofc_socket_mutex);
	return sent;
}

/**
 * Sends an error message with the specified transaction ID, error type and
 * error code.
 *
 * @param type     The error type in host byte order.
 * @param code     The error code in host byte order.
 * @param orig_msg A pointer to the OpenFlow message which caused the error.
 *                 The transaction ID for the error message will be the same as
 *                 that of this message.
 *
 * @return 0, or a negative value if an error occurred.
 */
static int32_t openflow_ctrl_iface_send_error(uint16_t type, uint16_t code,
        ofp_header *orig_msg)
{
	ofp_error_msg* error_msg = openflow_ctrl_iface_create_error_msg();
	error_msg->header.xid = orig_msg->xid;
	error_msg->type = htons(type);
	error_msg->code = htons(code);

	if (orig_msg->length > 64)
	{
		error_msg->header.length += 64;
		memcpy(error_msg->data, orig_msg, 64);
	}
	else
	{
		error_msg->header.length += orig_msg->length;
		memcpy(error_msg->data, orig_msg, orig_msg->length);
	}

	int32_t ret = openflow_ctrl_iface_send(error_msg, sizeof(ofp_error_msg));
	free(error_msg);
	return ret;
}

/**
 * Gets the next OpenFlow message from the controller TCP socket. The message
 * is created using dynamically-allocated memory and must be freed when no
 * longer needed.
 *
 * @param ptr_to_msg A pointer to a void pointer. The void pointer will be
 *                   replaced with a pointer to the message if one is received.
 *
 * @return The number of bytes in the message, 0 if no message was received,
 *         or a negative value if an error occurred.
 */
static int32_t openflow_ctrl_iface_recv(ofp_header **ptr_to_msg)
{
	pthread_mutex_lock(&ofc_socket_mutex);

	ofp_header header;
	int32_t ret = recv(ofc_socket_fd, &header, sizeof(header), MSG_DONTWAIT);
	if (ret == 0)
	{
		pthread_mutex_unlock(&ofc_socket_mutex);
		verbose(1, "[openflow_ctrl_iface_recv]:: Controller connection"
				" closed while receiving message header.");
		return OPENFLOW_CTRL_IFACE_ERR_CONN_CLOSED;
	}
	else if (ret == -1)
	{
		pthread_mutex_unlock(&ofc_socket_mutex);
		if (errno == EAGAIN) return 0;
		verbose(1, "[openflow_ctrl_iface_recv]:: Unknown error occurred"
				" while receiving message header.");
		return OPENFLOW_CTRL_IFACE_ERR_UNKNOWN;
	}

	if (header.version != OFP_VERSION)
	{
		pthread_mutex_unlock(&ofc_socket_mutex);
		verbose(1, "[openflow_ctrl_iface_recv]:: Bad OpenFlow version number"
				" found in message header.");
		int32_t ret = openflow_ctrl_iface_send_error(OFPET_BAD_REQUEST,
		        OFPBRC_BAD_VERSION, &header);
		if (ret < 0) return ret;
		return OPENFLOW_CTRL_IFACE_ERR_OPENFLOW;
	}
	else if (header.type > OPENFLOW_MAX_MSG_TYPE)
	{
		pthread_mutex_unlock(&ofc_socket_mutex);
		verbose(1, "[openflow_ctrl_iface_recv]:: Unsupported message type"
				" found in message header.");
		int32_t ret = openflow_ctrl_iface_send_error(OFPET_BAD_REQUEST,
		        OFPBRC_BAD_TYPE, &header);
		if (ret < 0) return ret;
		return OPENFLOW_CTRL_IFACE_ERR_OPENFLOW;
	}

	void *msg = malloc(ntohs(header.length));
	memcpy(msg, &header, sizeof(header));

	if (ntohs(header.length) - sizeof(header) > 0)
	{
		ret = recv(ofc_socket_fd, msg + sizeof(header),
		        ntohs(header.length) - sizeof(header), 0);
		if (ret == 0)
		{
			pthread_mutex_unlock(&ofc_socket_mutex);
			verbose(1, "[openflow_ctrl_iface_recv]:: Controller connection"
					" closed while receiving message body.");
			return OPENFLOW_CTRL_IFACE_ERR_CONN_CLOSED;
		}
		else if (ret == -1)
		{
			pthread_mutex_unlock(&ofc_socket_mutex);
			if (errno == EAGAIN) return 0;
			verbose(1, "[openflow_ctrl_iface_recv]:: Unknown error occurred"
					" while receiving message body.");
			return OPENFLOW_CTRL_IFACE_ERR_UNKNOWN;
		}
	}

	*ptr_to_msg = msg;
	pthread_mutex_unlock(&ofc_socket_mutex);
	return ntohs(header.length);
}

/**
 * Sends a hello message to the OpenFlow controller.
 *
 * @return The number of bytes sent, or a negative value if an error occurred.
 */
static int32_t openflow_ctrl_iface_send_hello()
{
	uint16_t msg_len = sizeof(ofp_hello);
	ofp_hello *msg = (ofp_hello *) openflow_ctrl_iface_create_msg(OFPT_HELLO,
	        msg_len);
	msg->header.xid = htonl(openflow_ctrl_iface_get_xid());

	int32_t ret = openflow_ctrl_iface_send(msg, msg_len);
	free(msg);
	return ret;
}

/**
 * Processes a hello message from the OpenFlow controller.
 *
 * @param msg The hello message received from the OpenFlow controller.
 *
 * @return 0, or a negative value if an error occurred.
 */
static int32_t openflow_ctrl_iface_recv_hello(ofp_hello *msg)
{
	if (msg->header.version < OFP_VERSION || msg->header.type != OFPT_HELLO
	        || ntohs(msg->header.length) < 8)
	{
		printf("%u\n", ntohs(msg->header.length));
		if (msg->header.version < OFP_VERSION)
		{
			verbose(1, "[openflow_ctrl_iface_recv_hello]:: Incompatible"
					" OpenFlow controller version found in hello message from"
					" controller.");
		}
		else if (msg->header.type != OFPT_HELLO)
		{
			verbose(1, "[openflow_ctrl_iface_recv_hello]:: Unexpected message"
					" type found in hello message from controller.");
		}
		else if (ntohs(msg->header.length) < 8)
		{
			verbose(1, "[openflow_ctrl_iface_recv_hello]:: Unexpected message"
					" length found in hello message from controller.");
		}

		int32_t ret = openflow_ctrl_iface_send_error(OFPET_HELLO_FAILED,
		        OFPHFC_INCOMPATIBLE, &msg->header);
		if (ret < 0) return ret;
		return OPENFLOW_CTRL_IFACE_ERR_OPENFLOW;
	}

	return 0;
}

/**
 * Performs the initial OpenFlow controller connection setup, which consists
 * of the hello request-reply exchange.
 *
 * @return The number of bytes sent, or a negative value if an error occurred.
 */
static int32_t openflow_ctrl_iface_hello_req_rep()
{
	verbose(2, "[openflow_ctrl_iface_hello_req_rep]:: Performing controller"
			" hello request-reply exchange.");
	int32_t ret = openflow_ctrl_iface_send_hello();
	if (ret < 0) return ret;

	ofp_hello* msg;
	do
	{
		ret = openflow_ctrl_iface_recv((ofp_header **) &msg);
	}
	while (ret == 0);
	if (ret < 0) return ret;

	ret = openflow_ctrl_iface_recv_hello(msg);
	free(msg);
	return ret;
}

/**
 * Processes an echo request message from the OpenFlow controller.
 *
 * @param message          The echo request message received from the OpenFlow
 *                         controller.
 * @param ptr_to_echo_data A pointer to a unsigned 8-bit integer pointer. The
 *                         inner pointer will be replaced with a pointer to the
 *                         raw data associated with the echo message.
 *
 * @return 0, or a negative value if an error occurred.
 */
static int32_t openflow_ctrl_iface_recv_echo_req(ofp_header *msg,
        uint8_t **ptr_to_echo_data)
{
	verbose(2, "[openflow_ctrl_iface_parse_message]:: Received"
			" message from controller of type OFPT_ECHO_REQUEST.");
	if (ntohs(msg->length) < 8)
	{
		verbose(1, "[openflow_ctrl_iface_recv_echo_req]:: Unexpected message"
				" length found in message of type OFPT_ECHO_REQUEST from"
				" controller.");
		int32_t ret = openflow_ctrl_iface_send_error(OFPET_BAD_REQUEST,
		        OFPBRC_BAD_LEN, msg);
		if (ret < 0) return ret;
		return OPENFLOW_CTRL_IFACE_ERR_OPENFLOW;
	}

	if (ntohs(msg->length) > 8) *ptr_to_echo_data = ((uint8_t *) msg) + 8;

	return 0;
}

/**
 * Sends an echo reply to the OpenFlow controller.
 *
 * @param echo_data     A pointer to the data to echo back to the OpenFlow
 *                      controller.
 * @param echo_data_len The length of the echo data in host byte order.
 * @param xid           The transaction ID of the original request.
 *
 * @return The number of bytes sent, or a negative value if an error occurred.
 */
static int32_t openflow_ctrl_iface_send_echo_rep(uint8_t *echo_raw,
        uint16_t echo_raw_len, uint32_t xid)
{
	verbose(2, "[openflow_ctrl_iface_parse_message]:: Sending"
			" message to controller of type OFPT_ECHO_REPLY.");
	uint16_t msg_len = sizeof(ofp_header) + echo_raw_len;
	ofp_header *msg = openflow_ctrl_iface_create_msg(OFPT_ECHO_REPLY, msg_len);
	msg->xid = xid;
	memcpy(msg + sizeof(ofp_header), echo_raw, echo_raw_len);

	int32_t ret = openflow_ctrl_iface_send(msg, msg_len);
	free(msg);
	return ret;
}

/**
 * Processes a features request message from the OpenFlow controller.
 *
 * @param msg The features request message received from the OpenFlow
 *            controller.
 *
 * @return 0, or a negative value if an error occurred.
 */
static int32_t openflow_ctrl_iface_recv_features_req(ofp_header *msg)
{
	if (msg->type != OFPT_FEATURES_REQUEST)
	{
		verbose(1, "[openflow_ctrl_iface_recv_features_req]:: Unexpected"
				" message type found in features request message from"
				" controller.");
		int32_t ret = openflow_ctrl_iface_send_error(OFPET_BAD_REQUEST,
		        OFPBRC_BAD_TYPE, msg);
		if (ret < 0) return ret;
		return OPENFLOW_CTRL_IFACE_ERR_OPENFLOW;
	}
	if (ntohs(msg->length) != 8)
	{
		verbose(1, "[openflow_ctrl_iface_recv_features_req]:: Unexpected"
				" message length found in features request message from"
				" controller.");
		int32_t ret = openflow_ctrl_iface_send_error(OFPET_BAD_REQUEST,
		        OFPBRC_BAD_LEN, msg);
		if (ret < 0) return ret;
		return OPENFLOW_CTRL_IFACE_ERR_OPENFLOW;
	}
	return 0;
}

/**
 * Sends a features reply message to the OpenFlow controller.
 *
 * @param xid The transaction ID of the original request.
 *
 * @return The number of bytes sent, or a negative value if an error occurred.
 */
static int32_t openflow_ctrl_iface_send_features_rep(uint32_t xid)
{
	// Advertise only the ports that actually EXIST, not all
	// OPENFLOW_MAX_PHYSICAL_PORTS (1024) slots.
	//
	// Advertising every slot is not merely wasteful (a 49KB features reply for a
	// switch with two links), it breaks topology discovery: a controller running
	// openflow.discovery registers every advertised port and probes each one with
	// LLDP, so a four-switch topology yields 4096 phantom ports and the discovery
	// timer degenerates to send_cycle_time/4096. The real ports are then never
	// probed in practice, no LLDP is ever exchanged, no links are found, and any
	// app that needs a network-wide view (forwarding.l2_multi, openflow.spanning_tree)
	// silently does nothing.
	//
	// A port "exists" if GNET has an interface behind it. Ports that exist but are
	// administratively or physically down are still advertised, with their state
	// bits set -- that is what a controller expects to see.
	uint32_t i;
	uint32_t n_ports = 0;
	for (i = 0; i < OPENFLOW_MAX_PHYSICAL_PORTS; i++)
	{
		if (findInterface(i) != NULL) n_ports++;
	}

	uint16_t msg_len = sizeof(ofp_switch_features)
	        + (n_ports * sizeof(ofp_phy_port));
	ofp_header *msg = openflow_ctrl_iface_create_msg(OFPT_FEATURES_REPLY,
	        msg_len);
	msg->xid = xid;

	ofp_switch_features switch_features = openflow_config_get_switch_features();
	memcpy(((uint8_t *) msg) + sizeof(ofp_header),
	        ((uint8_t *) &switch_features) + sizeof(ofp_header),
	        sizeof(ofp_switch_features) - sizeof(ofp_header));

	uint32_t slot = 0;
	for (i = 0; i < OPENFLOW_MAX_PHYSICAL_PORTS; i++)
	{
		if (findInterface(i) == NULL) continue;

		ofp_phy_port *src = openflow_config_get_phy_port(
		        openflow_config_get_of_port_num(i));
		if (src == NULL) continue;

		uint8_t *dest = ((uint8_t *) msg) + sizeof(switch_features)
		        + (slot * sizeof(ofp_phy_port));
		memcpy(dest, src, sizeof(ofp_phy_port));
		free(src);
		slot++;
	}

	verbose(2, "[openflow_ctrl_iface_send_features_rep]:: Advertising %u port(s)"
			" in features reply (%u bytes).", slot, (unsigned) msg_len);

	int32_t ret = openflow_ctrl_iface_send(msg, msg_len);
	free(msg);
	return ret;
}

/**
 * Performs the initial OpenFlow features request-reply exchange.
 *
 * @return The number of bytes sent, or a negative value if an error occurred.
 */
static int32_t openflow_ctrl_iface_features_req_rep()
{
	verbose(2, "[openflow_ctrl_iface_features_req_rep]:: Performing controller"
			" features request-reply exchange.");
	ofp_header *msg;
	int32_t ret;
	do
	{
		ret = openflow_ctrl_iface_recv(&msg);
	}
	while (ret == 0);
	if (ret < 0) return ret;

	ret = openflow_ctrl_iface_recv_features_req(msg);
	;
	if (ret < 0) return ret;

	ret = openflow_ctrl_iface_send_features_rep(msg->xid);
	free(msg);
	return ret;
}

/**
 * Processes a get configuration request from the OpenFlow controller.
 *
 * @param msg A pointer to the get configuration message received from the
 *            OpenFlow controller.
 *
 * @return 0, or a negative value if an error occurred.
 */
static int32_t openflow_ctrl_iface_recv_get_config_req(ofp_header *msg)
{
	verbose(2, "[openflow_ctrl_iface_parse_message]:: Received"
			" message from controller of type OFPT_GET_CONFIG_REQUEST.");

	if (ntohs(msg->length) < 8)
	{
		verbose(1, "[openflow_ctrl_iface_recv_get_config_req]:: Unexpected"
				" message length found in message of type"
				" OFPT_GET_CONFIG_REQUEST from controller.");
		int32_t ret = openflow_ctrl_iface_send_error(OFPET_BAD_REQUEST,
		        OFPBRC_BAD_LEN, msg);
		if (ret < 0) return ret;
		return OPENFLOW_CTRL_IFACE_ERR_OPENFLOW;
	}
	return 0;
}

/**
 * Sends a get configuration reply to the OpenFlow controller.
 *
 * @param xid The transaction ID of the original request.
 *
 * @return The number of bytes sent, or a negative value if an error occurred.
 */
static int32_t openflow_ctrl_iface_send_get_config_rep(uint32_t xid)
{
	verbose(2, "[openflow_ctrl_iface_parse_message]:: Sending"
			" message to controller of type OFPT_GET_CONFIG_REPLY.");

	uint16_t msg_len = sizeof(ofp_switch_config);
	ofp_switch_config *msg =
	        (ofp_switch_config *) openflow_ctrl_iface_create_msg(
	                OFPT_GET_CONFIG_REPLY, msg_len);
	msg->header.xid = xid;
	msg->flags = openflow_config_get_switch_config_flags();
	// This OpenFlow implementation has no buffers, so miss_send_len is not
	// stored and is always zero
	msg->miss_send_len = 0;

	int32_t ret = openflow_ctrl_iface_send(msg, msg_len);
	free(msg);
	return ret;
}

/**
 * Processes a set configuration message from the OpenFlow controller.
 *
 * @param msg A pointer to the set configuration message received from the
 *            OpenFlow controller.
 *
 * @return 0, or a negative value if an error occurred.
 */
static int32_t openflow_ctrl_iface_recv_set_config(ofp_switch_config *msg)
{
	verbose(2, "[openflow_ctrl_iface_parse_message]:: Received"
			" message from controller of type OFPT_SET_CONFIG.");

	if (ntohs(msg->header.length) != 12)
	{
		verbose(1, "[openflow_ctrl_iface_recv_set_config]:: Unexpected"
				" message length found in message of type OFPT_SET_CONFIG from"
				" controller.");
		int32_t ret = openflow_ctrl_iface_send_error(OFPET_BAD_REQUEST,
		        OFPBRC_BAD_LEN, &msg->header);
		if (ret < 0) return ret;
		return OPENFLOW_CTRL_IFACE_ERR_OPENFLOW;
	}

	openflow_config_set_switch_config_flags(msg->flags);

	return 0;
}

/**
 * Processes a packet out message from the OpenFlow controller.
 *
 * @param msg A pointer to the packet out message received from the OpenFlow
 *            controller.
 *
 * @return 0, or a negative value if an error occurred.
 */
static int32_t openflow_ctrl_iface_recv_packet_out(ofp_packet_out *msg)
{
	verbose(2, "[openflow_ctrl_iface_parse_message]:: Received"
			" message from controller of type OFPT_PACKET_OUT.");

	if (ntohs(msg->header.length) < 16)
	{
		verbose(1, "[openflow_ctrl_iface_recv_packet_out]:: Unexpected"
				" message length found in message of type OFPT_PACKET_OUT from"
				" controller.");
		int32_t ret = openflow_ctrl_iface_send_error(OFPET_BAD_REQUEST,
		        OFPBRC_BAD_LEN, &msg->header);
		if (ret < 0) return ret;
		return OPENFLOW_CTRL_IFACE_ERR_OPENFLOW;
	}
	if (ntohl(msg->buffer_id) != -1)
	{
		verbose(1, "[openflow_ctrl_iface_recv_packet_out]:: Unexpected"
				" message length found in message of type OFPT_PACKET_OUT from"
				" controller.");
		int32_t ret = openflow_ctrl_iface_send_error(OFPET_BAD_REQUEST,
		        OFPBRC_BUFFER_UNKNOWN, &msg->header);
		if (ret < 0) return ret;
		return OPENFLOW_CTRL_IFACE_ERR_OPENFLOW;
	}

	// bzero, not an uninitialised stack struct: only src_interface was ever assigned
	// (and only conditionally), so every other frame field -- including the pkt_len
	// added below -- would otherwise be whatever was on the stack.
	gpacket_t packet;
	bzero(&packet, sizeof(gpacket_t));

	uint16_t in_port = openflow_config_get_gnet_port_num(ntohs(msg->in_port));
	if (in_port < MAX_INTERFACES && findInterface(in_port) != NULL)
	{
		packet.frame.src_interface = in_port;
	}

	// Length of the frame the controller handed us. Record it: without it the send
	// path cannot derive a size for a non-IP/ARP frame and pads it to 1518 bytes,
	// which is what stopped openflow.discovery's LLDP from ever arriving.
	int data_len = (int) ntohs(msg->header.length) - (int) sizeof(ofp_packet_out)
	        - (int) ntohs(msg->actions_len);
	if (data_len < 0) data_len = 0;
	if (data_len > (int) sizeof(pkt_data_t)) data_len = (int) sizeof(pkt_data_t);
	packet.frame.pkt_len = data_len;

	memcpy(&packet.data, ((uint8_t *) msg->actions) + ntohs(msg->actions_len),
	        data_len);

	uint32_t actions = ntohs(msg->actions_len) / sizeof(ofp_action_header);
	uint32_t i;
	for (i = 0; i < actions; i++)
	{
		ofp_action_header *action = &msg->actions[i];
		openflow_pkt_proc_perform_action(action, &packet);
	}
	return 0;
}

/**
 * Processes a flow modification message from the OpenFlow controller.
 *
 * @param msg A pointer to the flow modification message received from the
 *            OpenFlow controller.
 *
 * @return 0, or a negative value if an error occurred.
 */
static int32_t openflow_ctrl_iface_recv_flow_mod(ofp_flow_mod *msg)
{
	verbose(2, "[openflow_ctrl_iface_parse_message]:: Received"
			" message from controller of type OFPT_FLOW_MOD.");

	int32_t ret;
	if (ntohs(msg->header.length) < 72)
	{
		verbose(1, "[openflow_ctrl_iface_recv_flow_mod]:: Unexpected message"
				" length found in message of type OFPT_FLOW_MOD from"
				" controller.");
		int32_t ret = openflow_ctrl_iface_send_error(OFPET_BAD_REQUEST,
		        OFPBRC_BAD_LEN, &msg->header);
		if (ret < 0) return ret;
		return OPENFLOW_CTRL_IFACE_ERR_OPENFLOW;
	}

	uint16_t error_type;
	uint16_t error_code;
	ret = openflow_flowtable_modify(msg, &error_type, &error_type);
	if (ret < 0)
	{
		int32_t ret = openflow_ctrl_iface_send_error(error_type, error_code,
		        &msg->header);
		if (ret < 0) return ret;
		return OPENFLOW_CTRL_IFACE_ERR_OPENFLOW;
	}
	return 0;
}

/**
 * Processes a port modification message from the OpenFlow controller.
 *
 * @param msg A pointer to the port modification message received from the
 *            OpenFlow controller.
 *
 * @return 0, or a negative value if an error occurred.
 */
static int32_t openflow_ctrl_iface_recv_port_mod(ofp_port_mod *msg)
{
	verbose(2, "[openflow_ctrl_iface_parse_message]:: Received"
			" message from controller of type OFPT_PORT_MOD.");

	if (ntohs(msg->header.length) != 32)
	{
		verbose(1, "[openflow_ctrl_iface_recv_port_mod]:: Unexpected"
				" message length found in message of type OFPT_PORT_MOD from"
				" controller.");
		int32_t ret = openflow_ctrl_iface_send_error(OFPET_BAD_REQUEST,
		        OFPBRC_BAD_LEN, &msg->header);
		if (ret < 0) return ret;
		return OPENFLOW_CTRL_IFACE_ERR_OPENFLOW;
	}

	ofp_phy_port *phy_port = openflow_config_get_phy_port(ntohs(msg->port_no));
	if (phy_port == NULL)
	{
		verbose(1, "[openflow_ctrl_iface_recv_port_mod]:: Unexpected"
				" port number found in message of type OFPT_PORT_MOD from"
				" controller.");
		int32_t ret = openflow_ctrl_iface_send_error(OFPET_PORT_MOD_FAILED,
		        OFPPMFC_BAD_PORT, &msg->header);
		if (ret < 0) return ret;
		return OPENFLOW_CTRL_IFACE_ERR_OPENFLOW;
	}
	if (memcmp(phy_port->hw_addr, msg->hw_addr, 6))
	{
		verbose(1, "[openflow_ctrl_iface_recv_port_mod]:: Unexpected"
				" MAC address found in message of type OFPT_PORT_MOD from"
				" controller.");
		int32_t ret = openflow_ctrl_iface_send_error(OFPET_PORT_MOD_FAILED,
		        OFPPMFC_BAD_PORT, &msg->header);
		if (ret < 0) return ret;
		return OPENFLOW_CTRL_IFACE_ERR_OPENFLOW;
	}

	uint32_t i;
	for (i = 0; i < 32; i++)
	{
		if ((msg->mask & (1 << i)))
		{
			if (msg->config & (1 << i)) phy_port->config |= 1 << i;
			else phy_port->config &= ~(1 << i);
		}
	}
	if (msg->advertise != 0) phy_port->advertised = msg->advertise;

	openflow_config_set_phy_port(ntohs(msg->port_no), phy_port);
	free(phy_port);

	return 0;
}

/**
 * Sends a packet in message to the OpenFlow controller containing the
 * specified packet.
 *
 * @param packet A pointer to the packet to send to the OpenFlow controller.
 * @param reason The reason the packet is being sent to the controller.
 *
 * @return The number of bytes sent, or a negative value if an error occurred.
 */
int32_t openflow_ctrl_iface_send_packet_in(gpacket_t *packet, uint8_t reason)
{
	if (openflow_ctrl_iface_get_conn_state())
	{
		uint16_t msg_len = sizeof(ofp_packet_in) + sizeof(pkt_data_t)
		        - (sizeof(ofp_packet_in) - offsetof(ofp_packet_in, data));
		ofp_packet_in *msg = (ofp_packet_in *) openflow_ctrl_iface_create_msg(
		        OFPT_PACKET_IN, msg_len);
		msg->header.xid = htonl(openflow_ctrl_iface_get_xid());
		msg->buffer_id = htonl(-1);
		msg->total_len = htons(sizeof(pkt_data_t));
		msg->in_port = htons(
		        openflow_config_get_of_port_num(packet->frame.src_interface));
		msg->reason = reason;
		memcpy(msg->data, &packet->data, sizeof(pkt_data_t));

		int32_t ret = openflow_ctrl_iface_send(msg, msg_len);
		free(msg);
		return ret;
	}
	else
	{
		return 0;
	}
}

/**
 * Sends a flow removed message to the OpenFlow controller.
 *
 * @param entry  A pointer to the flowtable entry that is being removed from
 *               the flowtable.
 * @param reason The reason the flow is being removed.
 *
 * @return The number of bytes sent, or a negative value if an error occurred.
 */
int32_t openflow_ctrl_iface_send_flow_removed(
        openflow_flowtable_entry_type *entry, uint8_t reason)
{
	if (openflow_ctrl_iface_get_conn_state())
	{
		uint16_t msg_len = sizeof(ofp_flow_removed);
		ofp_flow_removed *msg =
		        (ofp_flow_removed *) openflow_ctrl_iface_create_msg(
		                OFPT_FLOW_REMOVED, msg_len);
		msg->header.xid = htonl(openflow_ctrl_iface_get_xid());
		msg->match = entry->match;
		msg->cookie = entry->cookie;
		msg->priority = entry->priority;
		msg->reason = reason;
		msg->duration_sec = entry->stats.duration_sec;
		msg->duration_nsec = entry->stats.duration_nsec;
		msg->idle_timeout = entry->idle_timeout;
		msg->packet_count = entry->stats.packet_count;
		msg->byte_count = entry->stats.byte_count;

		int32_t ret = openflow_ctrl_iface_send(msg, msg_len);
		free(msg);
		return ret;
	}
	else
	{
		return 0;
	}
}

/**
 * Sends a port status message to the OpenFlow controller.
 *
 * @param phy_port A pointer to the physical port that was added, removed or
 *                 changed.
 * @param reason   A value indicating what has changed about the physical port.
 *
 * @return The number of bytes sent, or a negative value if an error occurred.
 */
int32_t openflow_ctrl_iface_send_port_status(ofp_phy_port *phy_port,
        uint8_t reason)
{
	if (openflow_ctrl_iface_get_conn_state())
	{
		uint16_t msg_len = sizeof(ofp_port_status);
		ofp_port_status *msg =
		        (ofp_port_status *) openflow_ctrl_iface_create_msg(
		                OFPT_PORT_STATUS, msg_len);
		msg->header.xid = htonl(openflow_ctrl_iface_get_xid());
		msg->reason = reason;
		msg->desc = *phy_port;

		int32_t ret = openflow_ctrl_iface_send(msg, msg_len);
		free(msg);
		return ret;
	}
	else
	{
		return 0;
	}
}

/**
 * Processes a stats request message from the OpenFlow controller.
 *
 * @param msg A pointer to the stats request message received from the OpenFlow
 *            controller.
 *
 * @return 0, or a negative value if an error occurred.
 */
static int32_t openflow_ctrl_iface_recv_stats_req(ofp_stats_request *msg)
{
	verbose(2, "[openflow_ctrl_iface_parse_message]:: Received"
			" message from controller of type OFPT_STATS_REQUEST.");

	uint16_t type = ntohs(msg->type);
	uint16_t expected_length;
	if (type == OFPST_DESC)
	{
		expected_length = sizeof(ofp_stats_request);
	}
	else if (type == OFPST_FLOW)
	{
		expected_length = sizeof(ofp_stats_request)
		        + sizeof(ofp_flow_stats_request);
	}
	else if (type == OFPST_AGGREGATE)
	{
		expected_length = sizeof(ofp_stats_request)
		        + sizeof(ofp_aggregate_stats_request);
	}
	else if (type == OFPST_TABLE)
	{
		expected_length = sizeof(ofp_stats_request) + sizeof(ofp_table_stats);
	}
	else if (type == OFPST_PORT)
	{
		expected_length = sizeof(ofp_stats_request)
		        + sizeof(ofp_port_stats_request);
	}
	else
	{
		verbose(1, "[openflow_ctrl_iface_recv_stats_req]:: Unexpected"
				" stats type found in message of type OFPT_STATS_REQUEST"
				" from controller.");
		if (type == OFPST_VENDOR)
		{
			int32_t ret = openflow_ctrl_iface_send_error(OFPET_BAD_REQUEST,
			        OFPBRC_BAD_VENDOR, &msg->header);
			if (ret < 0) return ret;
			return OPENFLOW_CTRL_IFACE_ERR_OPENFLOW;
		}
		else
		{
			int32_t ret = openflow_ctrl_iface_send_error(OFPET_BAD_REQUEST,
			        OFPBRC_BAD_TYPE, &msg->header);
			if (ret < 0) return ret;
			return OPENFLOW_CTRL_IFACE_ERR_OPENFLOW;
		}
	}

	if (expected_length != ntohs(msg->header.length))
	{
		verbose(1, "[openflow_ctrl_iface_recv_stats_req]:: Unexpected"
				" message length found in message of type OFPT_STATS_REQUEST"
				" (stats type " PRIu16 ") from controller.", type);
		int32_t ret = openflow_ctrl_iface_send_error(OFPET_BAD_REQUEST,
		        OFPBRC_BAD_LEN, &msg->header);
		if (ret < 0) return ret;
		return OPENFLOW_CTRL_IFACE_ERR_OPENFLOW;
	}

	return 0;
}

/**
 * Sends a stats reply message to the OpenFlow controller.
 *
 * @param orig_msg A pointer to the stats request message received from the
 *                 OpenFlow controller.
 *
 * @return The number of bytes sent, or a negative value if an error occurred.
 */
static int32_t openflow_ctrl_iface_send_stats_rep(ofp_stats_request *orig_msg)
{
	verbose(2, "[openflow_ctrl_iface_parse_message]:: Sending"
			" message to controller of type OFPT_STATS_REPLY.");

	uint16_t type = ntohs(orig_msg->type);
	if (type == OFPST_DESC)
	{
		uint16_t msg_len = sizeof(ofp_stats_reply) + sizeof(ofp_desc_stats);
		ofp_stats_reply *msg =
		        (ofp_stats_reply *) openflow_ctrl_iface_create_msg(
		                OFPT_STATS_REPLY, msg_len);
		msg->header.xid = orig_msg->header.xid;
		msg->type = htons(OFPST_DESC);
		msg->flags = 0;

		ofp_desc_stats stats = openflow_config_get_desc_stats();
		memcpy(msg->body, &stats, sizeof(stats));

		int32_t ret = openflow_ctrl_iface_send(msg, msg_len);
		free(msg);
		return ret;
	}
	else if (type == OFPST_FLOW)
	{
		ofp_flow_stats_request *orig_body =
		        (ofp_flow_stats_request *) orig_msg->body;

		int32_t bytes_sent = 0;
		uint32_t i = 0;
		ofp_flow_stats *stats;
		openflow_flowtable_action_type *actions;

		while (i < OPENFLOW_MAX_FLOWTABLE_ENTRIES)
		{
			if (openflow_flowtable_get_entry_stats(&orig_body->match,
			        orig_body->out_port, i, &i, orig_body->table_id, &stats,
			        &actions))
			{
				uint16_t msg_len = sizeof(ofp_stats_reply)
				        + sizeof(ofp_flow_stats);

				uint32_t j = 0;
				for (j = 0; j < OPENFLOW_MAX_ACTIONS; j++)
				{
					if (actions[j].active)
					{
						msg_len += ntohs(actions[j].header.len);
						stats->length = htons(
						        ntohs(stats->length)
						                + ntohs(actions[j].header.len));
					}
				}

				ofp_stats_reply *msg =
				        (ofp_stats_reply *) openflow_ctrl_iface_create_msg(
				                OFPT_STATS_REPLY, msg_len);
				msg->header.xid = orig_msg->header.xid;
				msg->type = htons(OFPST_FLOW);
				msg->flags = htons(OFPSF_REPLY_MORE);

				memcpy(msg->body, stats, sizeof(ofp_flow_stats));

				uint32_t len_index = 0;
				for (j = 0; i < OPENFLOW_MAX_ACTIONS; j++)
				{
					if (actions[j].active)
					{
						memcpy(
						        (uint8_t *) (((uint8_t *) msg->body) + len_index),
						        &actions[j].header,
						        ntohs(actions[j].header.len));
						len_index += ntohs(actions[j].header.len);
					}
				}

				int32_t ret = openflow_ctrl_iface_send(msg, msg_len);
				free(msg);
				free(stats);
				free(actions);
				if (ret < 0) return ret;
				bytes_sent += ret;

				i += 1;
			}
			else
			{
				break;
			}
		}

		uint16_t msg_len = sizeof(ofp_stats_reply);
		ofp_stats_reply *msg =
		        (ofp_stats_reply *) openflow_ctrl_iface_create_msg(
		                OFPT_STATS_REPLY, msg_len);
		msg->header.xid = orig_msg->header.xid;
		msg->type = htons(OFPST_FLOW);
		msg->flags = 0;

		int32_t ret = openflow_ctrl_iface_send(msg, msg_len);
		free(msg);
		if (ret < 0) return ret;
		return ret + bytes_sent;
	}
	else if (type == OFPST_AGGREGATE)
	{
		ofp_flow_stats_request *orig_body =
		        (ofp_flow_stats_request *) orig_msg->body;

		uint32_t i = 0;
		ofp_flow_stats *stats;
		openflow_flowtable_action_type *actions;

		ofp_aggregate_stats_reply body;
		body.byte_count = 0;
		body.packet_count = 0;
		body.flow_count = 0;

		while (i < OPENFLOW_MAX_FLOWTABLE_ENTRIES)
		{
			if (openflow_flowtable_get_entry_stats(&orig_body->match,
			        orig_body->out_port, i, &i, orig_body->table_id, &stats,
			        &actions))
			{
				body.byte_count = ntohll(
				        htonll(body.byte_count) + stats->byte_count);
				body.packet_count = ntohll(
				        htonll(body.packet_count) + stats->packet_count);
				body.flow_count = ntohl(htonl(body.flow_count) + 1);

				i += 1;
			}
		}

		uint16_t msg_len = sizeof(ofp_stats_reply)
		        + sizeof(ofp_aggregate_stats_reply);
		ofp_stats_reply *msg =
		        (ofp_stats_reply *) openflow_ctrl_iface_create_msg(
		                OFPT_STATS_REPLY, msg_len);
		msg->header.xid = orig_msg->header.xid;
		msg->type = htons(OFPST_AGGREGATE);
		msg->flags = 0;

		memcpy(msg->body, &body, sizeof(ofp_aggregate_stats_reply));

		int32_t ret = openflow_ctrl_iface_send(msg, msg_len);
		free(msg);
		return ret;
	}
	else if (type == OFPST_TABLE)
	{
		uint32_t msg_len = sizeof(ofp_stats_reply) + (sizeof(ofp_table_stats));
		ofp_stats_reply *msg =
		        (ofp_stats_reply *) openflow_ctrl_iface_create_msg(
		                OFPT_STATS_REPLY, msg_len);
		msg->header.xid = orig_msg->header.xid;
		msg->type = htons(OFPST_TABLE);
		msg->flags = 0;

		ofp_table_stats stats = openflow_flowtable_get_table_stats();
		memcpy(msg->body, &stats, sizeof(ofp_table_stats));

		int32_t ret = openflow_ctrl_iface_send(msg, msg_len);
		free(msg);
		return ret;
	}
	else if (type == OFPST_PORT)
	{
		ofp_port_stats_request *orig_body =
		        (ofp_port_stats_request *) orig_msg->body;

		ofp_port_stats *port_stats = openflow_config_get_port_stats(
		        ntohs(orig_body->port_no));
		if (port_stats != NULL)
		{
			uint16_t msg_len = sizeof(ofp_stats_reply) + sizeof(ofp_port_stats);
			ofp_stats_reply *msg =
			        (ofp_stats_reply *) openflow_ctrl_iface_create_msg(
			                OFPT_STATS_REPLY, msg_len);
			msg->header.xid = orig_msg->header.xid;
			msg->type = htons(OFPST_PORT);
			msg->flags = 0;

			memcpy(msg->body, &port_stats, sizeof(ofp_port_stats));

			int32_t ret = openflow_ctrl_iface_send(msg, msg_len);
			free(msg);
			free(port_stats);
			return ret;
		}
		else
		{
			uint16_t msg_len = sizeof(ofp_stats_reply);
			ofp_stats_reply *msg =
			        (ofp_stats_reply *) openflow_ctrl_iface_create_msg(
			                OFPT_STATS_REPLY, msg_len);
			msg->header.xid = orig_msg->header.xid;
			msg->type = htons(OFPST_PORT);
			msg->flags = 0;

			int32_t ret = openflow_ctrl_iface_send(msg, msg_len);
			free(msg);
			return ret;
		}
	}

	return OPENFLOW_CTRL_IFACE_ERR_UNKNOWN;
}

/**
 * Processes a barrier request message from the OpenFlow controller.
 *
 * @param msg The barrier request message received from the OpenFlow
 *            controller.
 *
 * @return 0, or a negative value if an error occurred.
 */
static int32_t openflow_ctrl_iface_recv_barrier_req(ofp_header *msg)
{
	verbose(2, "[openflow_ctrl_iface_parse_message]:: Received"
			" message from controller of type OFPT_BARRIER_REQUEST.");

	if (ntohs(msg->length) != 8)
	{
		verbose(1, "[openflow_ctrl_iface_recv_barrier_req]:: Unexpected"
				" message length found in message of type OFPT_BARRIER_REQUEST"
				" from controller.");
		int32_t ret = openflow_ctrl_iface_send_error(OFPET_BAD_REQUEST,
		        OFPBRC_BAD_LEN, msg);
		if (ret < 0) return ret;
		return OPENFLOW_CTRL_IFACE_ERR_OPENFLOW;
	}
	return 0;
}

/**
 * Sends a barrier reply message to the OpenFlow controller.
 *
 * @param xid The transaction ID of the barrier request.
 *
 * @return The number of bytes sent, or a negative value if an error occurred.
 */
static int32_t openflow_ctrl_iface_send_barrier_rep(uint32_t xid)
{
	verbose(2, "[openflow_ctrl_iface_parse_message]:: Sending"
			" message to controller of type OFPT_BARRIER_REPLY.");

	uint16_t msg_len = 8;
	ofp_header *msg = openflow_ctrl_iface_create_msg(OFPT_BARRIER_REPLY,
	        msg_len);
	msg->xid = xid;

	int32_t ret = openflow_ctrl_iface_send(msg, msg_len);
	free(msg);
	return ret;
}

/**
 * Prints an error message to the console.
 *
 * @param msg The error message to print to the console.
 */
static void openflow_ctrl_iface_print_error(ofp_error_msg *msg)
{
	if (ntohs(msg->header.length) < 12)
	{
		verbose(1, "[openflow_ctrl_iface_print_error]:: Unexpected message"
				" length found in message of type OFPT_ERROR from"
				" controller.");
		return;
	}

	verbose(1, "[openflow_ctrl_iface_print_error]:: Unexpected"
			" error message received from controller of type " PRIu16
	" and code " PRIu16 ".", ntohs(msg->type), ntohs(msg->code));
}

/**
 * Parses a message from the OpenFlow controller.
 *
 * @param message The message to parse.
 *
 * @return 0, or a negative value if an error occurred.
 */
static int32_t openflow_ctrl_iface_parse_message(ofp_header *msg)
{
	int32_t ret = 0;
	switch (msg->type)
	{
		case OFPT_ERROR:
		{
			openflow_ctrl_iface_print_error((ofp_error_msg *) msg);
			break;
		}
		case OFPT_ECHO_REQUEST:
		{
			uint8_t *echo_data;
			ret = openflow_ctrl_iface_recv_echo_req(msg, &echo_data);
			if (ret < 0) return ret;
			ret = openflow_ctrl_iface_send_echo_rep(echo_data,
			        msg->length - sizeof(ofp_header), msg->xid);
			break;
		}
		case OFPT_GET_CONFIG_REQUEST:
		{
			ret = openflow_ctrl_iface_recv_get_config_req(msg);
			if (ret < 0) return ret;
			ret = openflow_ctrl_iface_send_get_config_rep(msg->xid);
			break;
		}
		case OFPT_SET_CONFIG:
		{
			ofp_switch_config *switch_config = (ofp_switch_config *) msg;
			ret = openflow_ctrl_iface_recv_set_config(switch_config);
			break;
		}
		case OFPT_PACKET_OUT:
		{
			ofp_packet_out *packet_out = (ofp_packet_out *) msg;
			ret = openflow_ctrl_iface_recv_packet_out(packet_out);
			break;
		}
		case OFPT_FLOW_MOD:
		{
			ofp_flow_mod *flow_mod = (ofp_flow_mod *) msg;
			ret = openflow_ctrl_iface_recv_flow_mod(flow_mod);
			break;
		}
		case OFPT_PORT_MOD:
		{
			ofp_port_mod *port_mod = (ofp_port_mod *) msg;
			ret = openflow_ctrl_iface_recv_port_mod(port_mod);
			break;
		}
		case OFPT_STATS_REQUEST:
		{
			ofp_stats_request *stats_request = (ofp_stats_request *) msg;
			ret = openflow_ctrl_iface_recv_stats_req(stats_request);
			if (ret < 0) return ret;
			ret = openflow_ctrl_iface_send_stats_rep(stats_request);
			break;
		}
		case OFPT_BARRIER_REQUEST:
		{
			ofp_header *barrier_request = msg;
			ret = openflow_ctrl_iface_recv_barrier_req(barrier_request);
			if (ret < 0) return ret;
			ret = openflow_ctrl_iface_send_barrier_rep(barrier_request->xid);
			break;
		}
		case OFPT_FEATURES_REQUEST:
		{
			// A features request is legal AT ANY TIME, not only during the opening
			// handshake -- and controllers do re-ask. openflow.spanning_tree sends one
			// after every port modification (see its _invalidate_ports: the port config
			// bits it just changed make the controller's cached copy stale, so it
			// re-reads them). Falling through to `default` here answered a routine
			// request with OFPBRC_BAD_TYPE and dropped the connection, so the switch
			// flapped connect/disconnect for as long as spanning_tree kept adjusting
			// ports -- which looked like "spanning_tree doesn't work" while every other
			// app, none of which touches ports, was fine.
			ret = openflow_ctrl_iface_recv_features_req(msg);
			if (ret < 0) return ret;
			ret = openflow_ctrl_iface_send_features_rep(msg->xid);
			break;
		}
		default:
		{
			// NOTE the "%": this read `" message type " PRIu8 "found..."`, so the
			// format specifier was a bare "hhu"/"u" with no argument marker -- the type
			// was never printed and the line came out as "message type ufound". An
			// unknown-message log that hides the message number is worse than useless.
			verbose(1, "[openflow_ctrl_iface_parse_message]:: Unexpected"
					" message type %" PRIu8 " found in message from controller.",
			        msg->type);
			if (msg->type == OFPT_VENDOR)
			{
				ret = openflow_ctrl_iface_send_error(OFPET_BAD_REQUEST,
				        OFPBRC_BAD_VENDOR, msg);
			}
			else
			{
				ret = openflow_ctrl_iface_send_error(OFPET_BAD_REQUEST,
				        OFPBRC_BAD_TYPE, msg);
			}
			break;
		}
	}

	if (ret < 0) return ret;
	return 0;
}

/**
 * Parses a packet as though it came from the OpenFlow controller.
 *
 * @param packet The packet to parse.
 */
int32_t openflow_ctrl_iface_parse_packet(gpacket_t *packet)
{
	if (ntohs(packet->data.header.prot) == IP_PROTOCOL)
	{
		ip_packet_t *ip_packet = (ip_packet_t *) &packet->data.data;
		if (!(ntohs(ip_packet->ip_frag_off) & 0x1fff)
		        && !(ntohs(ip_packet->ip_frag_off) & 0x2000))
		{
			if (ip_packet->ip_prot == TCP_PROTOCOL)
			{
				uint32_t ip_header_length = ip_packet->ip_hdr_len * 4;
				tcp_packet_type *tcp_packet =
				        (tcp_packet_type *) ((uint8_t *) ip_packet
				                + ip_header_length);

				ofp_header *message = (ofp_header *) (tcp_packet
				        + sizeof(tcp_packet_type));
				return openflow_ctrl_iface_parse_message(message);
			}
		}
	}
	return 0;
}

/**
 * OpenFlow controller thread. Connects to controller and passes incoming
 * packets to handlers.
 *
 * @param port Pointer to the controller TCP port number.
 */
static void openflow_ctrl_iface(void *port)
{
	int32_t *port_num = (int32_t *) port;
	if (*port_num < 1 || *port_num > 65535)
	{
		fatal("[openflow_ctrl_iface]:: Invalid controller TCP port number"
				" %" PRId32 ".", *port_num);
		exit(1);
	}

	// Where is the controller? Historically hardwired to 127.0.0.1 (POX ran in the
	// same namespace). For containerized SDN the POX controller runs in its own
	// container, so read GINI_OF_CONTROLLER ("host" or "host:port"); the host may be
	// a Docker service name, hence DNS resolution below. Defaults preserve the old
	// localhost behaviour. GINI_OF_CONNECT_DELAY overrides the pre-connect wait.
	const char *ctrl_env = getenv("GINI_OF_CONTROLLER");
	if (ctrl_env == NULL || ctrl_env[0] == '\0')
	{
		ctrl_env = "127.0.0.1";
	}
	char ctrl_host[256];
	strncpy(ctrl_host, ctrl_env, sizeof(ctrl_host) - 1);
	ctrl_host[sizeof(ctrl_host) - 1] = '\0';
	int32_t ctrl_port = *port_num;
	char *colon = strrchr(ctrl_host, ':');
	if (colon != NULL)
	{
		*colon = '\0';
		int32_t parsed = atoi(colon + 1);
		if (parsed > 0 && parsed < 65536)
		{
			ctrl_port = parsed;
		}
	}

	int32_t conn_delay = 30;
	const char *delay_env = getenv("GINI_OF_CONNECT_DELAY");
	if (delay_env != NULL && delay_env[0] != '\0')
	{
		int32_t v = atoi(delay_env);
		if (v >= 0)
		{
			conn_delay = v;
		}
	}
	verbose(2, "[openflow_ctrl_iface]:: Sleeping for %" PRId32 " seconds to allow"
			" controller %s:%" PRId32 " to be configured before connecting.",
			conn_delay, ctrl_host, ctrl_port);
	sleep(conn_delay);

	openflow_config_init_phy_ports();

	while (1)
	{
		verbose(2, "[openflow_ctrl_iface]:: Connecting to controller %s:%"
				PRId32 ".", ctrl_host, ctrl_port);

		struct sockaddr_in ofc_sock_addr;
		memset(&ofc_sock_addr, 0, sizeof(ofc_sock_addr));
		ofc_sock_addr.sin_family = AF_INET;
		ofc_sock_addr.sin_port = htons(ctrl_port);
		if (inet_aton(ctrl_host, &ofc_sock_addr.sin_addr) == 0)
		{
			// not a dotted-quad: resolve as a hostname (e.g. a Docker service name)
			struct hostent *he = gethostbyname(ctrl_host);
			if (he == NULL || he->h_addr_list[0] == NULL)
			{
				verbose(2, "[openflow_ctrl_iface]:: Cannot resolve controller"
						" host %s. Retrying...", ctrl_host);
				sleep(1);
				continue;
			}
			memcpy(&ofc_sock_addr.sin_addr, he->h_addr_list[0],
			        sizeof(struct in_addr));
		}

		pthread_mutex_lock(&ofc_socket_mutex);
		ofc_socket_fd = socket(AF_INET, SOCK_STREAM, 0);
		int32_t status = connect(ofc_socket_fd,
		        (struct sockaddr*) &ofc_sock_addr, sizeof(ofc_sock_addr));
		if (status != 0)
		{
			verbose(2, "[openflow_ctrl_iface]:: Failed to connect to"
					" controller %s:%" PRId32 ". Retrying...", ctrl_host, ctrl_port);
			// MUST close the fd before retrying — otherwise each spin leaks a
			// socket and we quickly exhaust the fd table, after which socket()
			// returns -1 and we can NEVER connect (even once the controller is up).
			if (ofc_socket_fd >= 0)
			{
				close(ofc_socket_fd);
			}
			ofc_socket_fd = -1;
			pthread_mutex_unlock(&ofc_socket_mutex);
			sleep(1);          // back off so we patiently wait for the controller
			continue;
		}
		pthread_mutex_unlock(&ofc_socket_mutex);

		openflow_ctrl_iface_hello_req_rep();
		openflow_ctrl_iface_features_req_rep();
		openflow_ctrl_iface_conn_up();

		verbose(2, "[openflow_ctrl_iface]:: Receiving messages.");

		while (1)
		{
			// Receive a message from controller
			ofp_header *msg = NULL;
			int32_t ret;
			do
			{
				ret = openflow_ctrl_iface_recv(&msg);
				if (ret == 0)
				{
					pthread_mutex_lock(&reconnect_mutex);
					if (reconnect == 1)
					{
						msg = NULL;
						pthread_mutex_unlock(&reconnect_mutex);
						break;
					}
					pthread_mutex_unlock(&reconnect_mutex);

					sleep(1);
				}
			}
			while (ret == 0);

			if (msg == NULL)
			{
				// Controller connection lost or reconnect requested
				pthread_mutex_lock(&ofc_socket_mutex);
				close(ofc_socket_fd);
				pthread_mutex_unlock(&ofc_socket_mutex);
				openflow_ctrl_iface_conn_down();
				break;
			}
			else
			{
				// Process received message
				openflow_ctrl_iface_parse_message(msg);
				free(msg);
			}
		}
	}

	free(port);
}

/**
 * Initializes the OpenFlow controller-switch interface.
 *
 * @param port_num The TCP port number of the OpenFlow controller.
 *
 * @return The thread associated with the controller interface.
 */
pthread_t openflow_ctrl_iface_init(int32_t port_num)
{
	int32_t threadstat;
	pthread_t threadid;

	int32_t *pn = malloc(sizeof(int));
	*pn = port_num;
	threadstat = pthread_create((pthread_t *) &threadid, NULL,
	        (void *) openflow_ctrl_iface, pn);
	return threadid;
}
