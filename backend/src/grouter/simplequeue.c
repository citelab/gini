/*
 * simplequeue.c (A Simple Message Queue library)
 * AUTHOR: Muthucumaru Maheswaran
 * DATE: June 25, 2008

 * This is a simple multi-thread friendly Queue. It is
 * used to store and retrieve packets inside the gRouter.
 */

#include <slack/std.h>
#include <slack/err.h>
#include <slack/map.h>
#include <slack/list.h>
#include <math.h>
#include <time.h>
#include <sys/time.h>
#include "simplequeue.h"

// For unbounded queues, set maxsize to 0.
// For bounded queues, blockonwrite could be true or false. If true,
// a write waits if the queue is full. Otherwise, the write returns failed.
// Similarly, if blockonread is true, a read on an empty queue blocks.
// For unbounded queue, blockonwrite is meaningless.
simplequeue_t *createSimpleQueue(char *name, int maxsize, int blockonwrite,
				 int blockonread)
{
	simplequeue_t *msgqueue;

	if ((msgqueue = (simplequeue_t *) malloc(sizeof(simplequeue_t))) == NULL)
	{
		fatal("[createSimpleQueue]:: Could not allocate memory for message queue structure");
		return NULL;
	}
	strcpy(msgqueue->name, name);
	msgqueue->maxsize = maxsize;
	msgqueue->cursize = 0;
	msgqueue->bytesleft = 0;
	msgqueue->avgbyterate = 0.0;
	msgqueue->deficit = 0.0;
	msgqueue->pkts_in = msgqueue->pkts_out = msgqueue->pkts_drop = 0;
	msgqueue->bytes_in = msgqueue->bytes_out = msgqueue->bytes_drop = 0;
	msgqueue->prevaccesstime = (long)time(NULL);
	msgqueue->blockonwrite = blockonwrite;
	msgqueue->blockonread = blockonread;

	pthread_mutex_init(&(msgqueue->qlock), NULL);
	pthread_cond_init(&(msgqueue->qfull), NULL);
	pthread_cond_init(&(msgqueue->qempty), NULL);

	if (!(msgqueue->queue = list_create(NULL)))
	{
		fatal("[createSimpleQueue]:: Could not create the message list..");
		return NULL;
	}

	verbose(6, "[createSimpleQueue]:: Queue created -- name %s", name);
	return msgqueue;
}


// destroys the data structures of the message and releases the memory held
// by them.. we could still have a memory leak!!
int destroySimpleQueue(simplequeue_t *msgqueue)
{
  verbose(2,"[destroySimpleQueue]:: Deleting the queue %s .. ", msgqueue->name);
  if (msgqueue != NULL)
  {
	  if (msgqueue->queue != NULL)
		  list_release(msgqueue->queue);
	  free(msgqueue);
  }
  verbose(4, "[destroySimpleQueue]:: released all the simple queue data structures.. ");
  // TODO: check the errno to see whether any problems..
  return EXIT_SUCCESS;
}


void printSimpleQueue(simplequeue_t *msgqueue)
{

	printf("Queue name: %s\n", msgqueue->name);
	printf("Queuing discipline: %s\n", msgqueue->qdisc);
	printf("Queue weight: %f\n", msgqueue->weight);
	printf("Queuing delay: %f\n", msgqueue->delay_us);
	if (msgqueue->maxsize == 0)
		printf("Queue size (maximum): Unlimited \n");
	else
		printf("Queue size (maximum): %d \n", msgqueue->maxsize);

//	printf("Block on write: %s\n", msgqueue->blockonwrite ? "enabled" : "not enabled");
//	printf("Block on read: %s\n", msgqueue->blockonread ? "enabled" : "not enabled");

	printf("Found %d elements in the queue \n", msgqueue->cursize);
	printf("The queue has %d bytes in it \n", msgqueue->bytesleft);

	printf("Previous access time: %f\n", msgqueue->prevaccesstime);
	printf("Average byte rate: %f\n", msgqueue->avgbyterate);
}


int copy2Queue(simplequeue_t *msgqueue, void *data, int size)
{
	uchar *lpkt;

	if ((lpkt = (uchar *)malloc(size)) == NULL)
	{
		fatal("[copy2Queue]:: unable to allocate memory for copy packet...");
		return EXIT_FAILURE;
	}

	memcpy(lpkt, data, size);
	if (writeQueue(msgqueue, lpkt, size) == EXIT_FAILURE)
		free(lpkt);
	return EXIT_SUCCESS;
}


int writeQueue(simplequeue_t *msgqueue, void *data, int size)
{
	simplewrapper_t *swrap;

	if ((swrap = (simplewrapper_t *)malloc(sizeof(simplewrapper_t))) == NULL)
	{
		fatal("[writeQueue]:: unable to allocate memory for packet wrapper ");
		return EXIT_FAILURE;
	}
	swrap->size = size;
	swrap->data = data;

	pthread_mutex_lock(&(msgqueue->qlock));           // lock the queue..

	if (msgqueue->cursize >= msgqueue->maxsize)
	{
		// finite queue size and it is already full..
		if (msgqueue->blockonwrite)
			// wait if blockonwrite .. otherwise just quit
			pthread_cond_wait(&(msgqueue->qfull), &(msgqueue->qlock));
		else
		{
			pthread_mutex_unlock(&(msgqueue->qlock));
			free(swrap);
			return EXIT_FAILURE;
		}
	}
	list_push(msgqueue->queue, swrap);
	msgqueue->cursize++;
	msgqueue->bytesleft += size;

	if ((msgqueue->cursize == 1) && (msgqueue->blockonread))
		pthread_cond_signal(&(msgqueue->qempty));

	pthread_mutex_unlock(&(msgqueue->qlock));
	return EXIT_SUCCESS;
}


// TODO: We need to shorten this function.. it is bit long!
// Just reorganize the loops .. lots of redundant statements.
int readQueue(simplequeue_t *msgqueue, void **data, int *size)
{
	simplewrapper_t *swrap;
	int rvalue;

	pthread_mutex_lock(&(msgqueue->qlock));
	if (msgqueue->cursize <= 0)
	{
		if (msgqueue->blockonread)
		{
			pthread_cond_wait(&(msgqueue->qempty), &(msgqueue->qlock));
			swrap = list_shift(msgqueue->queue);
			*size = swrap->size;
			*data = swrap->data;
			swrap->data = NULL;
			msgqueue->cursize--;
			msgqueue->bytesleft -= *size;
			rvalue = EXIT_SUCCESS;
		} else
		{
			*data = NULL;
			*size = 0;
			rvalue = EXIT_FAILURE;
		}

	} else if (msgqueue->cursize <= msgqueue->maxsize)
	{
		msgqueue->cursize--;
		swrap = list_shift(msgqueue->queue);
		*size = swrap->size;
		*data = swrap->data;
		swrap->data = NULL;
		msgqueue->bytesleft -= *size;
		if ((msgqueue->blockonwrite) && (msgqueue->cursize >= (msgqueue->maxsize-1)))
			pthread_cond_signal(&(msgqueue->qfull));
		rvalue = EXIT_SUCCESS;
	}
	pthread_mutex_unlock(&(msgqueue->qlock));
	if (rvalue == EXIT_SUCCESS)
		free(swrap);

	if (rvalue == EXIT_SUCCESS)
		computeAvgByteRate(msgqueue, *size);
	return rvalue;
}


void computeAvgByteRate(simplequeue_t *sq, int size)
{
	double curraccesstime, tinterval, mfactor;
	struct timeval tval;

	gettimeofday(&tval, NULL);
	curraccesstime = tval.tv_usec * 0.000001;
	curraccesstime += tval.tv_sec;
	tinterval = curraccesstime - sq->prevaccesstime;
	sq->prevaccesstime = curraccesstime;
	mfactor = exp(-1.0 * tinterval);
	sq->avgbyterate = (1 - mfactor)* (size/tinterval) + mfactor * sq->avgbyterate;
}


double getAvgByteRate(simplequeue_t *sq)
{
	double curraccesstime;
	struct timeval tval;

	gettimeofday(&tval, NULL);
	curraccesstime = tval.tv_usec * 0.000001;
	curraccesstime += tval.tv_sec;
	if ((curraccesstime - sq->prevaccesstime) > 1.0)
		computeAvgByteRate(sq, 0);

	return sq->avgbyterate;
}





// get the next element without actually removing it from the queeue.
int peekQueue(simplequeue_t *msgqueue, void **data, int *size)
{
	simplewrapper_t *swrap;

	pthread_mutex_lock(&(msgqueue->qlock));

	if (msgqueue->cursize <= 0)
	{
		*size = 0;
		*data = NULL;
		pthread_mutex_unlock(&(msgqueue->qlock));
		return EXIT_FAILURE;
	} else
	{
		swrap = list_shift(msgqueue->queue);
		*size = swrap->size;
		*data = &(swrap->data);
		list_unshift(msgqueue->queue, swrap);
		pthread_mutex_unlock(&(msgqueue->qlock));
		return EXIT_SUCCESS;
	}
}

