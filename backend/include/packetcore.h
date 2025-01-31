/*
 * packetcore.h (include file for the Packet Core)
 * AUTHOR: Muthucumaru Maheswaran
 * DATE: June 30, 2008
 *
 */

#ifndef __PACKET_CORE_H__
#define __PACKET_CORE_H__


#include <slack/std.h>
#include <slack/map.h>
#include <slack/list.h>
#include <pthread.h>

#include "message.h"
#include "grouter.h"
#include "simplequeue.h"
#include "qdisc.h"


typedef struct _pktcorecnamecache_t
{
	char *cname[MAX_QUEUE_SIZE];
	int numofentries;
} pktcorecnamecache_t;


typedef struct _pktcore_t
{
	char name[MAX_NAME_LEN];
	char spolicy[MAX_NAME_LEN];
	pthread_cond_t schwaiting;
	pthread_mutex_t qlock;                // lock for the main queue
	pthread_mutex_t wqlock;               // lock for work queue
	simplequeue_t *outputQ;
	simplequeue_t *workQ;
	simplequeue_t *openflowWorkQ;
	Map *queues;
	int lastqid;
	int packetcnt;
	int maxqsize;
	double vclock;
	pktcorecnamecache_t *pcache;
	qdisctable_t *qdiscs;
} pktcore_t;


// Function prototypes

pktcore_t *createPacketCore(char *rname, simplequeue_t *outQ, simplequeue_t *workQ, simplequeue_t *openflowWorkQ);
int addPktCoreQueue(pktcore_t *pcore, char *qname, char *dqisc, double qweight, double delay_us, int nslots);
simplequeue_t *getCoreQueue(pktcore_t *pcore, char *qname);
void printAllQueues(pktcore_t *pcore);
void printQueueStats(pktcore_t *pcore);
void printOneQueue(pktcore_t *pcore, char *qname);
void modifyQueueWeight(pktcore_t *pcore, char *qname, double weight);
void modifyQueueDiscipline(pktcore_t *pcore, char *qname, char *qdisc);
int delPktCoreQueue(pktcore_t *pcore, char *qname);

pthread_t PktCoreSchedulerInit(pktcore_t *pcore);
int PktCoreWorkerInit(pktcore_t *pcore, pthread_t *thread);
int PktCoreOpenflowWorkerInit(pktcore_t *pcore, pthread_t *thread);
void *openflowPacketProcessor(void *pc);
void *packetProcessor(void *pc);

int enqueuePacket(pktcore_t *pcore, gpacket_t *in_pkt, int pktsize, uint8_t openflow);

// Function prototypes from roundrobin.c and wfq.c??
void *weightedFairScheduler(void *pc);
int weightedFairQueuer(pktcore_t *pcore, gpacket_t *in_pkt, int pktsize, char *qkey);
int roundRobinQueuer(pktcore_t *pcore, gpacket_t *in_pkt, int pktsize, char *qkey);
void *roundRobinScheduler(void *pc);

int redDiscard(simplequeue_t *thisq, gpacket_t *ipkt);
#endif
