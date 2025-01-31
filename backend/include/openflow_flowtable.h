/**
 * openflow_flowtable.h - OpenFlow flowtable
 */

#ifndef __OPENFLOW_FLOWTABLE_H_
#define __OPENFLOW_FLOWTABLE_H_

#include <stdint.h>
#include <time.h>

#include "openflow_defs.h"
#include "message.h"
#include "packetcore.h"
#include "simplequeue.h"

/**
 * Initializes the OpenFlow flowtable timeout thread.
 */
int openflow_flowtable_timeout_init(pthread_t *thread);

/**
 * Initializes the flowtable.
 */
void openflow_flowtable_init(void);

/**
 * Frees the flow table, avoiding memory leaks.
 */
void openflow_flowtable_release(void);

/**
 * Retrieves the matching flowtable entry for the specified packet.
 *
 * @param packet    The specified packet.
 *
 * @return The matching flowtable entry.
 */
openflow_flowtable_entry_type *openflow_flowtable_get_entry_for_packet(
        gpacket_t *packet);

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
        uint16_t *error_code);

/**
 * Gets the table statistics for the OpenFlow flowtable.
 *
 * @return The table statistics for the OpenFlow flowtable.
 */
ofp_table_stats openflow_flowtable_get_table_stats();

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
        openflow_flowtable_action_type **ptr_to_actions);

/**
 * Prints the specified OpenFlow flowtable entry to the console.
 *
 * @param index The index of the entry to print.
 */
void openflow_flowtable_print_entry(uint32_t index);

/**
 * Prints the statistics for the specified entry in the flowtable.
 *
 * @param The index of the entry to print.
 */
void openflow_flowtable_print_entry_stat(uint32_t index);

/**
 * Prints all OpenFlow flowtable entries to the console.
 */
void openflow_flowtable_print_entries();

/**
 * Prints the statistics for all entries in the flowtable.
 */
void openflow_flowtable_print_entry_stats();

/**
 * Prints the statistics for the flowtable.
 */
void openflow_flowtable_print_table_stats();

#endif // ifndef __OPENFLOW_FLOWTABLE_H_
