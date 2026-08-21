/*
 * multicast.h — provided starter code for the Multicast File Distribution capstone
 * (GINI book, "Network Multicasting" chapter). A thin wrapper over the IPv4 multicast
 * socket calls plus a checksum helper, so the socket plumbing is not the assignment.
 *
 * Typical use:
 *     sender:    mcast_t *m = multicast_init("239.1.1.1", 5000, 5000);
 *                multicast_send(m, buf, len);
 *     receiver:  mcast_t *m = multicast_init("239.1.1.1", 5000, 5000);
 *                multicast_setup_recv(m);            // joins the group
 *                if (multicast_check_receive(m) > 0)
 *                    n = multicast_receive(m, buf, sizeof buf);
 */
#ifndef GINI_MULTICAST_H
#define GINI_MULTICAST_H

#include <stdint.h>
#include <stddef.h>

typedef struct mcast mcast_t;

/* Create the socket, bound to a group and a pair of ports (send-to and receive-on).
 * Returns NULL on failure. TTL is set high enough to cross the routers of a GINI
 * multi-LAN (the multicast tree, not the TTL, bounds where the traffic goes). */
mcast_t *multicast_init(const char *group, int send_port, int recv_port);

/* Receiver side: bind the receive port and join the group on every interface. */
void multicast_setup_recv(mcast_t *m);

/* >0 when a datagram is waiting (non-blocking poll), 0 when not, <0 on error. */
int multicast_check_receive(mcast_t *m);

/* Blocking receive of one datagram into buf (at most len bytes). Returns the number
 * of bytes received, or <0 on error. */
int multicast_receive(mcast_t *m, void *buf, int len);

/* Send one datagram of len bytes to the group. Returns len, or <0 on error. */
int multicast_send(mcast_t *m, const void *buf, int len);

/* Close the socket and free the handle. */
void multicast_close(mcast_t *m);

/* Checksum helper (FNV-1a, 32-bit): deterministic across machines. Use it for both
 * chunk checksums and whole-file checksums. */
uint32_t mc_checksum(const void *buf, size_t len);

/* ---- the packet header every capstone packet starts with ---------------------- */

#define MC_META 1                 /* payload: the file table                        */
#define MC_DATA 2                 /* payload: one chunk of one file                 */

typedef struct {
    uint8_t  type;                /* MC_META or MC_DATA                             */
    uint8_t  file_id;             /* which file (DATA)                              */
    uint16_t chunk_len;           /* payload bytes in this packet                   */
    uint32_t seq;                 /* chunk number within the file (DATA)            */
    uint32_t total_chunks;        /* chunks in this file (DATA and META)            */
    uint32_t checksum;            /* mc_checksum of the payload                     */
} mc_hdr_t;                       /* payload follows the header in the datagram     */

#define MC_MAX_CHUNK 8192         /* upper bound on -c; keeps datagrams < 9 KB      */
#define MC_MAX_PKT   (sizeof(mc_hdr_t) + MC_MAX_CHUNK)

#endif /* GINI_MULTICAST_H */
