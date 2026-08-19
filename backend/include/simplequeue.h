/*
 * simplequeue.h (include file for the Simple Queue library)
 * AUTHOR: Muthucumaru Maheswaran
 * DATE: June 6, 2008
 *
 */

#ifndef __SIMPLE_QUEUE_H__
#define __SIMPLE_QUEUE_H__


#include <slack/std.h>
#include <slack/map.h>
#include <slack/list.h>

#include "grouter.h"


typedef struct _simplewrapper_t
{
	int size;
	void *data;
} simplewrapper_t;



typedef struct _simplequeue_t
{
	char name[MAX_NAME_LEN];
	List *queue;
	pthread_cond_t qfull, qempty;
	pthread_mutex_t qlock;
	int maxsize, cursize, bytesleft;
	int blockonwrite;
	int blockonread;
	// following parameters are useful for statistics keeping
	double prevaccesstime;
	double avgbyterate;
	// following parameters are useful for queueing discipline
	char qdisc[MAX_NAME_LEN];
	double delay_us;
	// following parameters are useful for scheduling algorithms
	double weight;
	double stime, ftime;
	double deficit;                 // DRR deficit counter (bytes)
	// following parameters are useful for RED
	double minval, maxval, pmaxval;
	double avgqsize, idlestart;
	int count;
	// per-queue statistics: packets/bytes forwarded (out), enqueued (in), and dropped
	long pkts_in, pkts_out, pkts_drop;
	long bytes_in, bytes_out, bytes_drop;
} simplequeue_t;


// Function prototypes
simplequeue_t *createSimpleQueue(char *name, int maxsize, int blockonwrite, int blockonread);
int destroySimpleQueue(simplequeue_t *msgqueue);
void printSimpleQueue(simplequeue_t *msgqueue);
int writeQueue(simplequeue_t *msgqueue, void *data, int size);
int copy2Queue(simplequeue_t *msgqueue, void *data, int size);
void computeAvgByteRate(simplequeue_t *sq, int size);
double getAvgByteRate(simplequeue_t *sq);

int readQueue(simplequeue_t *msgqueue, void **data, int *size);
int peekQueue(simplequeue_t *msgqueue, void **data, int *size);

#endif
