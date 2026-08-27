/*
 * ethernet.h (header file the Ethernet driver)
 * AUTHOR: Muthucumaru Maheswaran
 *
 * VERSION:
 */



/* Self-contained: pkt_data_t / gpacket_t live here. Previously this header relied on
 * every includer having pulled in message.h first, which breaks any new consumer. */
#include "message.h"

/*
 * function prototypes
 */

int findPacketSize(pkt_data_t *pkt);
/* On-the-wire size, preferring frame.pkt_len when the producer set it. Use this on
 * send paths -- findPacketSize() pads non-IP/ARP frames to the full struct. */
int gpacketSize(gpacket_t *pkt);

void *toEthernetDev(void *arg);
void* fromEthernetDev(void *arg);
