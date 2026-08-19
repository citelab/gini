#define _XOPEN_SOURCE 500        /* for usleep() from <unistd.h> */
#include <unistd.h>
#include <slack/std.h>
#include <slack/err.h>
#include <slack/map.h>
#include <slack/list.h>
#include <pthread.h>
#include <arpa/inet.h>

#include "protocols.h"
#include "packetcore.h"
#include "message.h"
#include "grouter.h"
#include "ip.h"

/*
 * roundrobin.c -- the gRouter packet scheduler.
 *
 * A single scheduler thread (packetScheduler) drains the per-class input queues and
 * hands packets to the work queue. The policy in rconfig.schedpolicy selects between:
 *
 *   SCHED_RR  -- round robin: one packet from the next non-empty queue, in turn.
 *                Simple, but weight-blind and per-packet (a queue of large packets
 *                gets more bandwidth than a queue of small ones).
 *   SCHED_DRR -- deficit round robin: each queue is granted a byte quantum in
 *                proportion to its weight and keeps a running deficit; it dequeues
 *                while the deficit lasts, charged by each packet's real byte length.
 *                This divides a congested link by weight, in bytes, regardless of
 *                packet size.
 *
 * The scheduler forwards at most one packet per `sched-cycle` microseconds (CLI:
 * `set sched-cycle`), so the forwarding rate is a knob independent of the policy --
 * that keeps rr and drr comparable at the same service rate.
 *
 * (The former worst-case WFQ scheduler in wfq.c was never wired in and has been
 * removed; DRR is the weighted fair-queuing scheduler now.)
 */

extern router_config rconfig;

#define DRR_BASE_QUANTUM  1500.0   /* bytes granted to a weight-1 queue each round */
#define GR_ETH_HDR_LEN    14       /* dst(6) + src(6) + ethertype(2) */

/*
 * Real on-the-wire byte length of a packet: Ethernet header + IP total length.
 * Every queue slot carries a fixed-size gpacket_t, so the wrapper size is useless
 * for byte accounting; we read the true length out of the IP header. ARP and any
 * non-IP packet get a small nominal length.
 */
int gpktByteLen(gpacket_t *p)
{
	if (p == NULL)
		return 64;
	if (ntohs(p->data.header.prot) == IP_PROTOCOL)
	{
		ip_packet_t *ip = (ip_packet_t *)&p->data.data;
		int len = ntohs(ip->ip_pkt_len);
		if (len < 20 || len > DEFAULT_MTU)
			len = 64;                       /* malformed: fall back */
		return GR_ETH_HDR_LEN + len;
	}
	return 64;
}

/* Move one packet from a class queue to the work queue, update stats, and throttle. */
static void dispatchOne(pktcore_t *pcore, simplequeue_t *q, gpacket_t *pkt, int pktsize)
{
	q->pkts_out++;
	q->bytes_out += gpktByteLen(pkt);
	writeQueue(pcore->workQ, pkt, pktsize);

	pthread_mutex_lock(&(pcore->qlock));
	pcore->packetcnt--;
	pthread_mutex_unlock(&(pcore->qlock));

	if (rconfig.schedcycle > 0)
		usleep(rconfig.schedcycle);         /* fixed service rate, policy-independent */
}

/* Round robin: dispatch a single packet from the next non-empty queue. */
static void rrStep(pktcore_t *pcore)
{
	List *keylst = map_keys(pcore->queues);
	int qcount = list_length(keylst);
	int nextqid = pcore->lastqid;
	int rstatus = EXIT_FAILURE;
	char *nextqkey;
	simplequeue_t *nextq;
	gpacket_t *in_pkt;
	int pktsize;

	if (qcount == 0)
	{
		list_release(keylst);
		return;
	}

	do
	{
		nextqid = (1 + nextqid) % qcount;
		nextqkey = list_item(keylst, nextqid);
		nextq = map_get(pcore->queues, nextqkey);
		rstatus = readQueue(nextq, (void **)&in_pkt, &pktsize);
		if (rstatus == EXIT_SUCCESS)
		{
			pcore->lastqid = nextqid;
			dispatchOne(pcore, nextq, in_pkt, pktsize);
		}
	} while (nextqid != pcore->lastqid && rstatus == EXIT_FAILURE);

	list_release(keylst);
}

/* Deficit round robin: one weighted pass over all queues. */
static void drrStep(pktcore_t *pcore)
{
	List *keylst = map_keys(pcore->queues);
	Lister *klster = lister_create(keylst);
	char *key;
	simplequeue_t *q;
	gpacket_t *pkt;
	int pktsize;

	while ((key = (char *)lister_next(klster)))
	{
		q = map_get(pcore->queues, key);
		if (q->cursize == 0)
		{
			q->deficit = 0.0;               /* idle queue keeps no credit */
			continue;
		}
		q->deficit += q->weight * DRR_BASE_QUANTUM;

		while (q->deficit > 0.0)
		{
			if (readQueue(q, (void **)&pkt, &pktsize) != EXIT_SUCCESS)
				break;                      /* queue drained */
			q->deficit -= gpktByteLen(pkt);
			dispatchOne(pcore, q, pkt, pktsize);
		}
	}
	lister_release(klster);
	list_release(keylst);
}

/*
 * The scheduler thread. Waits until there is at least one queued packet, then runs
 * one step of the selected policy. Switching policy (spolicy set rr|drr) takes effect
 * on the next step, since the branch is read each iteration.
 */
void *packetScheduler(void *pc)
{
	pktcore_t *pcore = (pktcore_t *)pc;

	pthread_setcanceltype(PTHREAD_CANCEL_ASYNCHRONOUS, NULL);
	while (1)
	{
		pthread_mutex_lock(&(pcore->qlock));
		if (pcore->packetcnt == 0)
			pthread_cond_wait(&(pcore->schwaiting), &(pcore->qlock));
		pthread_mutex_unlock(&(pcore->qlock));

		pthread_testcancel();

		if (rconfig.schedpolicy == GR_SCHED_DRR)
			drrStep(pcore);
		else
			rrStep(pcore);
	}
	return NULL;
}
