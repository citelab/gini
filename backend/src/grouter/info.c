/*
 * This is the information port for the gRouter. It implements a ".info" port
 * that can be used by an external program to retrieve information from the
 * gRouter. This information provides a oneway information flow (from gRouter
 * to the external program). This is useful for performance visualization front
 * ends..
 */

#include "grouter.h"
#include "cli.h"
#include "simplequeue.h"
#include "info.h"
#include <string.h>
#include <stdlib.h>
#include <errno.h>
#include <unistd.h>
#include <slack/std.h>
#include <slack/err.h>
#include <slack/fio.h>
#include <sys/stat.h>
#include "verbose.h"
#include "message.h"
#include "openflow_config.h"
#include <pthread.h>
#include <sys/types.h>

/*
 * Some global variables!
 */
info_config_t  iconf;



void infoGetState()
{
	printf("Info port is *always* enabled \n");
}



int write_to_fifo(info_config_t *iconf, message_t *msg)
{
	int wsize;

	// write the header first
	if ((wsize = write(iconf->sock, (void *)&(msg->header), sizeof(msg_header_t))) < 0)
	{
		error("[write_to_fifo]:: error writing the header ");
		return EXIT_FAILURE;
	}

	// write the payload next
	if (msg->header.length > 0)
	{
		if ((wsize = write(iconf->sock, (void *)msg->data, msg->header.length)) < 0)
		{
			error("[write_to_fifo]:: error writing the payload ");
			return EXIT_FAILURE;
		}
	}
	return EXIT_SUCCESS;
}




void *infoHandler(void *arg)
{
	info_config_t *iconf = (info_config_t *)arg;
	queue_target_t *tptr;
	simplequeue_t *qptr;
	char linebuf[MAX_LINE_LEN], timestr[MAX_TMPBUF_LEN];
	int len;
	time_t tval;
	Lister *lster;

	pthread_setcanceltype(PTHREAD_CANCEL_ASYNCHRONOUS, NULL);
	while(1)
	{
		sleep(iconf->updateinterval);
		pthread_testcancel();

		tval = time(NULL);
		if (iconf->rawtimemode)
			sprintf(timestr, "%ld ", (long)tval);
		else
		{
			sprintf(timestr, "%s ", ctime(&tval));
			timestr[strlen(timestr)-2] = 0;
		}

		for(lster = lister_create(iconf->qtargets); lister_has_next(lster) == 1; )
		{
			tptr = (queue_target_t *)lister_next(lster);
			qptr = tptr->queue;
			message_t msg;
			msg.header.type = 1;  // Queue info type
			msg.data = linebuf;
			sprintf(linebuf, "%s\t%s\t%d\t%f\n", timestr, qptr->name, qptr->cursize, getAvgByteRate(qptr));
			msg.header.length = strlen(linebuf);
			write_to_fifo(iconf, &msg);
		}
		lister_release(lster);
	}
	return NULL;
}


void addTarget(char *name, simplequeue_t *tgrt)
{
	queue_target_t *qtrgt;

	if ((qtrgt = (queue_target_t *) malloc(sizeof(queue_target_t))) == NULL)
	{
		fatal("[addTarget]:: unable to allocate memory..");
		return;
	}

	qtrgt->active = TRUE;
	strcpy(qtrgt->targetname, name);
	qtrgt->queue = tgrt;

	list_push(iconf.qtargets, qtrgt);

}


void activeTarget(char *name)
{
	Lister *lster;
	queue_target_t *qptr;

	for(lster = lister_create(iconf.qtargets); lister_has_next(lster) == 1; )
	{
		qptr = (queue_target_t *)lister_next(lster);
		if (strcmp(name, qptr->targetname) == 0)
			qptr->active = TRUE;
	}
	lister_release(lster);
}


void deactiveTarget(char *name)
{
	Lister *lster;
	queue_target_t *qptr;

	for(lster = lister_create(iconf.qtargets); lister_has_next(lster) == 1; )
	{
		qptr = (queue_target_t *)lister_next(lster);
		if (strcmp(name, qptr->targetname) == 0)
			qptr->active = FALSE;
	}

	lister_release(lster);
}



void printTimeMode()
{
	if (iconf.rawtimemode)
		printf("\nTime is displayed in seconds from the Epoch\n");
	else
		printf("\nTime is displayed in human readable form (e.g., Wed Jun 30 21:49:08 1993)\n");
}


void setTimeMode(int rawmode)
{
	iconf.rawtimemode = rawmode;
}

int getTimeMode()
{
	return iconf.rawtimemode;
}


void setUpdateInterval(int interval)
{
	iconf.updateinterval = interval;
}


int getUpdateInterval()
{
	return iconf.updateinterval;
}


/*
 * List the information targets...
 */
void infoList()
{
	Lister *lster;
	queue_target_t *qptr;


	printf("\n\n");
	printf("Information output device (FIFO path): %s\n", iconf.path);
	printf("Information update interval: %d (sec)\n", iconf.updateinterval);
	printf("Information updated on the following targets (queues): - \n\n");
	printf("Target name\tState\tQueue Address\n");

	for(lster = lister_create(iconf.qtargets); lister_has_next(lster) == 1; )
	{
		qptr = (queue_target_t *)lister_next(lster);
		if (qptr->active)
			printf("%s\tEnabled\t%p\n", qptr->targetname, qptr->queue);
		else
			printf("%s\tDisabled\t%p\n", qptr->targetname, qptr->queue);
	}
	printf("\n");
	lister_release(lster);
}






/*
 * This function basically sets up the .port (for wireshark use)
 */
int infoInit(char *rpath, char *rname)
{
	struct stat sbuf;
	char tmpbuf[MAX_NAME_LEN];
	pthread_t ptid;

	// construct the FIFO pathname
	bzero(tmpbuf, MAX_NAME_LEN);
	sprintf(tmpbuf, "%s/%s.fifo", rpath, rname);
	strcpy(iconf.path, tmpbuf);

	// check whether the FIFO exists.. remove if it exists
	if (stat(iconf.path, &sbuf) == 0)
	{
		verbose(2, "[infoInit]:: WARNING! existing FIFO %s removed .. creating a new one ", iconf.path);
		// if the FIFO exists, delete it..
		unlink(iconf.path);
		// if there is a thread using it, cancel it
		if (iconf.status == INFO_ACTIVE)
			pthread_cancel(iconf.id);
	}

	// create the FIFO with read/write permissions for user
	if (mkfifo(iconf.path, 0666) < 0)
	{
		error("[infoInit]:: unable to create socket .. %s", iconf.path);
		return EXIT_FAILURE;
	}

	// create the handler thread
	if (pthread_create(&ptid, NULL, infoHandler, &iconf) != 0)
	{
		error("Unable to create the info handler thread... ");
		return EXIT_FAILURE;
	}

	iconf.id = ptid;
	iconf.status = INFO_ACTIVE;
	return EXIT_SUCCESS;
}

