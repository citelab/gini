#ifndef __WFQ_H__
#define __WFQ_H__

#include "packetcore.h"
#include "message.h"

// Function declarations
void *weightedFairScheduler(void *pc);
int weightedFairQueuer(pktcore_t *pcore, gpacket_t *in_pkt, int pktsize, char *qkey);

// Helper macro for max function
#ifndef max
#define max(a,b) ((a) > (b) ? (a) : (b))
#endif

#endif 