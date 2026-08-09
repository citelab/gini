/*
 * packetcore.c (This is the core of the gRouter)
 * AUTHOR: Muthucumaru Maheswaran
 * DATE: June 30, 2008

 * The functions provided by this collection mimics the input queues
 * and the interconnection network in a typical router. In this software
 * implementation, we provide a collection of input queues into which the
 * packet classifier inserts the packets. For now, the packets have a
 * drop on full policy. The packet scheduler is responsible for picking a
 * packet from the collection of active input queues. The packet scheduler
 * inserts the chosen packet into a work queue that is not part of the
 * packet core. The work queue is serviced by one or more worker threads
 * (for now we have one worker thread).
 */
#define _XOPEN_SOURCE             500
#include <unistd.h>
#include <slack/std.h>
#include <slack/err.h>
#include <slack/prog.h>
#include <slack/map.h>
#include <slack/list.h>
#include <pthread.h>
#include <math.h>
#include <stdlib.h>
#include <arpa/inet.h>
#include "protocols.h"
#include "packetcore.h"
#include "message.h"
#include "classifier.h"
#include "grouter.h"
#include "openflow_pkt_proc.h"
#include "filter.h"
#include "arp.h"
#include "ip.h"
#include "sdn.h"
#include "gr_delay_ctl.h"

extern classlist_t *classifier;
extern filtertab_t *filter;
extern router_config rconfig;

/*
 * Packet core Cname Cache functions are here.
 * This is a simple cache to make fast lookups on the cnames (classes)
 * already defined for the packet queues. Because this information is
 * retrieved at each packet arrival, we should do it very fast!
 */
pktcorecnamecache_t *createPktCoreCnameCache()
{
	pktcorecnamecache_t *pcache;

	pcache = (pktcorecnamecache_t *)malloc(sizeof(pktcorecnamecache_t));
	pcache->numofentries = 0;

	return pcache;
}


void insertCnameCache(pktcorecnamecache_t *pcache, char *cname)
{
	pcache->cname[pcache->numofentries] = strdup(cname);
	pcache->numofentries++;
}


int deleteCnameCache(pktcorecnamecache_t *pcache, char *cname)
{
	int j, i, found = 0;

	for (j = 0; j < pcache->numofentries; j++)
	{
		if (!strcmp(pcache->cname[j], cname))
		{
			found = 1;
			break;
		}
	}

	if (found)
	{
		free(pcache->cname[j]);
		for (i = j; j < (pcache->numofentries-1); j++)
			pcache->cname[i] = pcache->cname[i+1];
		pcache->numofentries--;
	}

	return found;
}



pktcore_t *createPacketCore(char *rname, simplequeue_t *outQ, simplequeue_t *workQ, simplequeue_t *openflowWorkQ)
{
	pktcore_t *pcore;

	if ((pcore = (pktcore_t *) malloc(sizeof(pktcore_t))) == NULL)
	{
		fatal("[createPktCore]:: Could not allocate memory for packet core structure");
		return NULL;
	}

	pcore->pcache = createPktCoreCnameCache();


	strcpy(pcore->name, rname);
	pthread_mutex_init(&(pcore->qlock), NULL);
	pthread_mutex_init(&(pcore->wqlock), NULL);
	pthread_cond_init(&(pcore->schwaiting), NULL);
	pcore->lastqid = 0;
	pcore->packetcnt = 0;
	pcore->outputQ = outQ;
	pcore->workQ = workQ;
	if (rconfig.openflow) {
		pcore->openflowWorkQ = openflowWorkQ;
	}
	pcore->maxqsize = MAX_QUEUE_SIZE;
	pcore->qdiscs = initQDiscTable();
	addSimplePolicy(pcore->qdiscs, "taildrop");


	if (!(pcore->queues = map_create(NULL)))
	{
		fatal("[createPacketCore]:: Could not create the queues..");
		return NULL;
	}

	verbose(6, "[createPacketCore]:: packet core successfully created ...");
	return pcore;
}


int addPktCoreQueue(pktcore_t *pcore, char *qname, char *qdisc, double qweight, double delay_us, int nslots)
{
	simplequeue_t *pktq;
	qentrytype_t *qentry;


	if ((pktq = createSimpleQueue(qname, pcore->maxqsize, 0, 0)) == NULL)
	{
		error("[addPktCoreQueue]:: packet queue creation failed.. ");
		return EXIT_FAILURE;
	}

	// if 0.. let the queue size be set to default
	if (nslots != 0)
		pktq->maxsize = nslots;
	pktq->delay_us = delay_us;
	strcpy(pktq->qdisc, qdisc);
	pktq->weight = qweight;
	pktq->stime = pktq->ftime = 0.0;
	if (!strcmp(qdisc, "red"))
	{
		qentry = getqdiscEntry(pcore->qdiscs, qdisc);
		pktq->maxval = pktq->maxsize * qentry->maxval;
		pktq->minval = pktq->maxsize * qentry->minval;
		pktq->pmaxval = qentry->pmaxval;
		pktq->avgqsize = 0;
		pktq->count = -1;
		pktq->idlestart = 0;
	}

	map_add(pcore->queues, qname, pktq);
	insertCnameCache(pcore->pcache, qname);
	return EXIT_SUCCESS;
}


simplequeue_t *getCoreQueue(pktcore_t *pcore, char *qname)
{
	return map_get(pcore->queues, qname);
}


void printAllQueues(pktcore_t *pcore)
{
	List *keylst;
	Lister *klster;
	char *nxtkey;
	simplequeue_t *nextq;

	keylst = map_keys(pcore->queues);
	klster = lister_create(keylst);

	while (nxtkey = ((char *)lister_next(klster)))
	{
		nextq = map_get(pcore->queues, nxtkey);
		printSimpleQueue(nextq);
	}
	lister_release(klster);
	list_release(keylst);
}


void printQueueStats(pktcore_t *pcore)
{
	List *keylst;
	Lister *klster;
	char *nxtkey;
	simplequeue_t *nextq;

	keylst = map_keys(pcore->queues);
	klster = lister_create(keylst);

	printf("NOT YET IMPLEMENTED \n");
	while (nxtkey = ((char *)lister_next(klster)))
	{
		nextq = map_get(pcore->queues, nxtkey);
		printf("Stats for %s \n", nxtkey);
	}
	lister_release(klster);
	list_release(keylst);
}



void printOneQueue(pktcore_t *pcore, char *qname)
{
	List *keylst;
	Lister *klster;
	char *nxtkey;
	simplequeue_t *nextq;

	keylst = map_keys(pcore->queues);
	klster = lister_create(keylst);

	while (nxtkey = ((char *)lister_next(klster)))
	{
		if (!strcmp(qname, nxtkey))
		{
			nextq = map_get(pcore->queues, nxtkey);
			printSimpleQueue(nextq);
		}
	}
	lister_release(klster);
	list_release(keylst);
}


void modifyQueueWeight(pktcore_t *pcore, char *qname, double weight)
{
	List *keylst;
	Lister *klster;
	char *nxtkey;
	simplequeue_t *nextq;

	keylst = map_keys(pcore->queues);
	klster = lister_create(keylst);

	while (nxtkey = ((char *)lister_next(klster)))
	{
		if (!strcmp(qname, nxtkey))
		{
			nextq = map_get(pcore->queues, nxtkey);
			nextq->weight = weight;
		}
	}
	lister_release(klster);
	list_release(keylst);
}


void modifyQueueDiscipline(pktcore_t *pcore, char *qname, char *qdisc)
{
	List *keylst;
	Lister *klster;
	char *nxtkey;
	simplequeue_t *nextq;

	keylst = map_keys(pcore->queues);
	klster = lister_create(keylst);

	while (nxtkey = ((char *)lister_next(klster)))
	{
		if (!strcmp(qname, nxtkey))
		{
			nextq = map_get(pcore->queues, nxtkey);
			strcpy(nextq->qdisc, qdisc);
		}
	}
	lister_release(klster);
	list_release(keylst);
}


int delPktCoreQueue(pktcore_t *pcore, char *qname)
{
	List *keylst;
	Lister *klster;
	char *nxtkey;
	int deleted;

	keylst = map_keys(pcore->queues);
	klster = lister_create(keylst);
	deleted = 0;

	while (nxtkey = ((char *)lister_next(klster)))
	{
		if (!strcmp(qname, nxtkey))
		{
			map_remove(pcore->queues, qname);
			deleted = 1;
			deleteCnameCache(pcore->pcache, qname);
		}
	}
	lister_release(klster);
	list_release(keylst);

	if (deleted)
		return EXIT_SUCCESS;
	else
		return EXIT_FAILURE;
}



// create a thread for the scheduler. for now, hook up the Worst-case WFQ
// as the scheduler. Only the dequeue is hooked up. The enqueue part is
// in the classifier. The dequeue will put the scheduler in wait when it
// runs out of jobs in the queue. The enqueue will wake up a sleeping scheduler.
pthread_t PktCoreSchedulerInit(pktcore_t *pcore)
{
	int threadstat;
	pthread_t threadid;

	threadstat = pthread_create((pthread_t *)&threadid, NULL, (void *)roundRobinScheduler, (void *)pcore);
	if (threadstat != 0)
	{
		verbose(1, "[PKTCoreSchedulerInit]:: unable to create thread.. ");
		return -1;
	}

	return threadid;
}


int PktCoreWorkerInit(pktcore_t *pcore)
{
	int threadstat, threadid;

	threadstat = pthread_create((pthread_t *)&threadid, NULL, (void *)packetProcessor, (void *)pcore);

	if (threadstat != 0)
	{
		verbose(1, "[PKTCoreWorkerInit]:: unable to create thread.. ");
		return -1;
	}

	return threadid;
}


void *packetProcessor(void *pc)
{
	pktcore_t *pcore = (pktcore_t *)pc;
	gpacket_t *in_pkt;
	int pktsize;

	pthread_setcanceltype(PTHREAD_CANCEL_ASYNCHRONOUS, NULL);
	while (1)
	{
		verbose(2, "[packetProcessor]:: Waiting for a packet...");
		readQueue(pcore->workQ, (void **)&in_pkt, &pktsize);
		pthread_testcancel();
		verbose(2, "[packetProcessor]:: Got a packet for further processing..");

		// get the protocol field within the packet... and switch it accordingly
		switch (ntohs(in_pkt->data.header.prot))
		{
		case IP_PROTOCOL:
			verbose(2, "[packetProcessor]:: Packet sent to IP routine for further processing.. ");

			IPIncomingPacket(in_pkt);
			break;
		case ARP_PROTOCOL:
			verbose(2, "[packetProcessor]:: Packet sent to ARP module for further processing.. ");
			ARPProcess(in_pkt);
			break;
		default:
			verbose(1, "[packetProcessor]:: Packet discarded: Unknown protocol protocol");
			// TODO: should we generate ICMP errors here.. check router RFCs
			free(in_pkt);
			break;
		}
	}
}

int PktCoreOpenflowWorkerInit(pktcore_t *pcore)
{
	int threadstat, threadid;

	threadstat = pthread_create((pthread_t *)&threadid, NULL,
	                            (void *)openflowPacketProcessor,
								(void *)pcore);
	if (threadstat != 0)
	{
		verbose(1, "[PktCoreOpenflowWorkerInit]:: unable to create thread.. ");
		return -1;
	}

	return threadid;
}

void *openflowPacketProcessor(void *pc) {
	pktcore_t *pcore = (pktcore_t *)pc;
	gpacket_t *in_pkt;
	int pktsize;

	pthread_setcanceltype(PTHREAD_CANCEL_ASYNCHRONOUS, NULL);

	// NOTE: openflow_pkt_proc_init(pcore) is now called from main() BEFORE this thread
	// and the flowtable-timeout thread start, so the flowtable is fully allocated up
	// front. Re-initializing here would race the timeout thread and leak the flowtable.
	while (1)
	{
		verbose(2, "[openflowPacketProcessor]:: Waiting for a packet...");
		readQueue(pcore->openflowWorkQ, (void **)&in_pkt, &pktsize);
		pthread_testcancel();
		verbose(2, "[openflowPacketProcessor]:: Got a packet for further"
			" processing..");

		openflow_pkt_proc_handle_packet(in_pkt);
		free(in_pkt);
	}
}

/*
 * Checks if a given packets matches any of the classifier definitions
 * associated with existing queues.
 *
 * TODO: Performance issue; we are caching the cname.. should we actually
 * cache the classification rule itself? This will actually reduce the
 * lookup time. However, changes to the rules made will not be reflected
 * unless the cache is invalidated.
 */
char *tagPacket(pktcore_t *pcore, gpacket_t *in_pkt)
{
	classdef_t *cdef;
	int j, found = FALSE;
	char *qname;
	static char *defaultstr = "default";

	verbose(2, "[tagPacket]:: Entering the packet tagging function.. ");

	for (j = 0; j < pcore->pcache->numofentries; j++)
	{

		qname = pcore->pcache->cname[j];
		if (!strcmp(qname, "default"))
			continue;
		if ((cdef = getClassDef(classifier, qname)))
		{
			if (isRuleMatching(cdef, in_pkt))
			{
				found = TRUE;
				break;
			}
		}
	}

	if (found == TRUE)
		return cdef->cname;
	else
		return defaultstr;
}


static int enqueuePacket_now(pktcore_t *pcore, gpacket_t *in_pkt, int pktsize,
	uint8_t openflow)
{
	if (openflow)
	{
		writeQueue(pcore->openflowWorkQ, in_pkt, pktsize);
	}
	else
	{
		char *qkey;
		simplequeue_t *thisq;

		// check for filtering.. if the it should be filtered.. then drop
		if (filteredPacket(filter, in_pkt))
		{
			verbose(2, "[enqueuePacket]:: Packet filtered..!");
			free(in_pkt);
			return EXIT_FAILURE;
		}

		/*
		 * invoke the packet classifier to get the packet tag at the very minimum,
		 * we get the "default" tag!
		 */
		qkey = tagPacket(pcore, in_pkt);

		verbose(2, "[enqueuePacket]:: simple packet queuer ..");
		if (prog_verbosity_level() >= 3)
			printGPacket(in_pkt, 6, "QUEUER");

		pthread_mutex_lock(&(pcore->qlock));

		thisq = map_get(pcore->queues, qkey);
		if (thisq == NULL)
		{
			fatal("[enqueuePacket]:: Invalid %s key presented for queue retrieval", qkey);
			pthread_mutex_unlock(&(pcore->qlock));
			free(in_pkt);
			return EXIT_FAILURE;             // packet dropped..
		}

		// with queue size full.. we should drop the packet for taildrop or red
		// TODO: Need to change if we include other buffer management policies (e.g., dropfront)
		if (thisq->cursize >= thisq->maxsize)
		{
			verbose(2, "[enqueuePacket]:: Packet dropped.. Queue for [%s] is full.. cursize %d..  ", qkey, thisq->cursize);
			free(in_pkt);
			pthread_mutex_unlock(&(pcore->qlock));
			return EXIT_FAILURE;
		}

		if ( (!strcmp(thisq->qdisc, "red")) && (redDiscard(thisq, in_pkt)) )
		{
			verbose(2, "[enqueuePacket]:: RED Discarded Packet .. ");
			free(in_pkt);
			pthread_mutex_unlock(&(pcore->qlock));
			return EXIT_FAILURE;
		}

		pcore->packetcnt++;
		if (pcore->packetcnt == 1)
			pthread_cond_signal(&(pcore->schwaiting)); // wake up scheduler if it was waiting..
		pthread_mutex_unlock(&(pcore->qlock));
		verbose(2, "[enqueuePacket]:: Adding packet.. ");
		writeQueue(thisq, in_pkt, pktsize);
		return EXIT_SUCCESS;
	}
}


/*
 * Ingress link delay (optional). When an ingress delay line is configured, every arriving
 * packet is parked in the holding queue and re-injected -- in order -- when its sampled hold
 * time expires. When no line is set, this is a plain call through to the queuer, so the
 * forwarding path is unchanged. See gr_delay_ctl.h.
 */
static pktcore_t *g_ingress_pcore = NULL;

int enqueuePacket(pktcore_t *pcore, gpacket_t *in_pkt, int pktsize, uint8_t openflow)
{
	gr_delayline_t *dl = gr_ingress_line;   /* read once off the fast path */
	if (dl != NULL)
	{
		g_ingress_pcore = pcore;
		if (gr_delay_push(dl, in_pkt))
			return EXIT_SUCCESS;            /* held; re-injected later, in order */
		free(in_pkt);                       /* holding queue full -> drop */
		return EXIT_FAILURE;
	}
	return enqueuePacket_now(pcore, in_pkt, pktsize, openflow);
}

/* Release callback: the delay line calls this (on its own thread) when a held packet is due. */
void gr_ingress_emit(void *pkt)
{
	enqueuePacket_now(g_ingress_pcore, (gpacket_t *)pkt, sizeof(gpacket_t),
			  (sdn_mode() == SDN_MODE_OPENFLOW));
}


/*
 * RED function: evaluate the Random early drop algorithm and return
 * 1 (true) if the packet should be dropped. Return 0 otherwise.
 */
int redDiscard(simplequeue_t *thisq, gpacket_t *ipkt)
{
	double m;
	double curraccesstime, pb, pa;
	struct timeval tval;
	int discarded = 0;

	gettimeofday(&tval, NULL);
	curraccesstime = tval.tv_usec * 0.000001;
	curraccesstime += tval.tv_sec;

	// calculate queue average..
	if (thisq->cursize > 0)
		thisq->avgqsize = thisq->avgqsize + 0.9 * (thisq->cursize - thisq->avgqsize);
	else
	{
		m = (curraccesstime - thisq->prevaccesstime)/0.0001;
		thisq->avgqsize = pow(0.1, m) * thisq->avgqsize;
	}

	if ((thisq->minval < thisq->avgqsize) && (thisq->avgqsize < thisq->maxval))
	{
		thisq->count++;
		pb = thisq->pmaxval *  ( (thisq->avgqsize - thisq->minval) / (thisq->maxval - thisq->avgqsize) );
		pa = pb/ ( 1 - thisq->count * pb );
		if (drand48() > pa)
		{
			discarded = 1;
			thisq->count = 0;
		}
	} else if (thisq->maxval < thisq->avgqsize)
	{
		discarded = 1;
		thisq->count = 0;
	} else
		thisq->count = -1;

	return discarded;
}


