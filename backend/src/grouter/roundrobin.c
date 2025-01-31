#include <slack/std.h>
#include <slack/err.h>
#include <slack/map.h>
#include <slack/list.h>
#include <pthread.h>
#include <unistd.h>

#include "protocols.h"
#include "packetcore.h"
#include "message.h"
#include "grouter.h"
#include "simplequeue.h"
#include "qdisc.h"
#include "roundrobin.h"
#include "verbose.h"

/*
 * Roundrobin scheduler implementation -- when the roundrobin scheme is used, we need to use
 * the corresponding "queuer" as well.
 */

extern router_config rconfig;

void *roundRobinScheduler(void *pc)
{
	pktcore_t *pcore = (pktcore_t *)pc;
	List *keylst;
	int nextqid, qcount, rstatus, pktsize;
	char *nextqkey;
	gpacket_t *in_pkt;
	simplequeue_t *nextq;


	pthread_setcanceltype(PTHREAD_CANCEL_ASYNCHRONOUS, NULL);
	while (1)
	{
		verbose(2, "[roundRobinScheduler]:: Round robin scheduler processing... ");
		keylst = map_keys(pcore->queues);
		nextqid = pcore->lastqid;
		qcount = list_length(keylst);

		pthread_mutex_lock(&(pcore->qlock));
		if (pcore->packetcnt == 0)
			pthread_cond_wait(&(pcore->schwaiting), &(pcore->qlock));
		pthread_mutex_unlock(&(pcore->qlock));

		pthread_testcancel();
		do
		{
			nextqid = (1 + nextqid) % qcount;
			nextqkey = list_item(keylst, nextqid);
			// get the queue..
			nextq = map_get(pcore->queues, nextqkey);
			// read the queue..
			rstatus = readQueue(nextq, (void **)&in_pkt, &pktsize);

			if (rstatus == EXIT_SUCCESS)
			{
				pcore->lastqid = nextqid;
				writeQueue(pcore->workQ, in_pkt, pktsize);
			}

		} while (nextqid != pcore->lastqid && rstatus == EXIT_FAILURE);
		list_release(keylst);

		pthread_mutex_lock(&(pcore->qlock));
		if (rstatus == EXIT_SUCCESS)
			pcore->packetcnt--;
		pthread_mutex_unlock(&(pcore->qlock));

		usleep(rconfig.schedcycle);
	}
}



