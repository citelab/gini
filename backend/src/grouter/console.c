/*                                                                                                               * This is the console (it creates a .port) interface for the gRouter.
 * At this time, the console gives a copy of all the packets that are
 * flowing through the router in the libpcap format.
 */

#include "grouter.h"
#include "simplequeue.h"
#include "gpcap.h"
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
#include "console.h"
#include "protocols.h"

// Forward declarations for static functions
static void write_pcapheader(int fid);
static int write_pcaprecord(int fid, void *buf, int len);

/*
 * Some global variables!
 */
int consoleid;                        // FIFO id 
simplequeue_t *consoleq;
char consolepath[MAX_NAME_LEN];
pthread_t console_threadid;


void consoleRestart(char *rpath, char *rname)
{
	int fd, status;

	sprintf(consolepath, "%s/%s.%s", rpath, rname, "port");

 	if (fifo_exists(consolepath, 1)) 
		verbose(2, "[consoleRestart]:: Existing FIFO %s removed .. creating a new one ", consolepath); 


 	if ((fd = fifo_open(consolepath, S_IRUSR | S_IWUSR | S_IWGRP | S_IWOTH, 1, &consoleid)) == -1) 
 	{ 
 		error("[consoleRestart]:: unable to create socket .. %s", consolepath); 
 		return; 
 	} 
	write_pcapheader(consoleid);
	return;
}


void consoleGetState()
{
	printf("Port (console) is always enabled \n");
}


/*
 * Write out the Gpcap header into the FIFO.
 */
static void write_pcapheader(int fid)
{
	pcap_hdr_t phdr = {0xa1b2c3d4, 2, 4, 0, 0, 65535, 1};
	int bytes;

 	if ((bytes = write(fid, &phdr, sizeof(pcap_hdr_t))) == -1)
 	{
		error("[write_pcapheader]:: error writing the pcap header ");
 		return; 
 	} 
}


static int write_pcaprecord(int fid, void *buf, int len)
{
	pcaprec_hdr_t pchdr = {.ts_sec = 0, .ts_usec = 0};
	int bytes;
	void *lbuf;

	pchdr.incl_len = pchdr.orig_len = len;
	lbuf = malloc(sizeof(pcaprec_hdr_t) + len);
	bcopy(&pchdr, lbuf, sizeof(pcaprec_hdr_t));
	bcopy(buf, (lbuf + sizeof(pcaprec_hdr_t)), len);

	if ((bytes = write(fid, lbuf, len + sizeof(pcaprec_hdr_t))) == -1)
	{
		error("[write_pcapheader]:: error writing the pcap header ");
 		return -1; 
	}
	free(lbuf);
	return 0;
}




void consoleHandler(void *ptr)
{
	unsigned char *data;
	int len;

	pthread_setcanceltype(PTHREAD_CANCEL_ASYNCHRONOUS, NULL);
	write_pcapheader(consoleid);
	while(1)
	{
		pthread_testcancel();
		// read a packet from the queue, wait if no packet
		readQueue(consoleq, (void **)&data, &len);
		pthread_testcancel();
		// write the fifo, block if FIFO not read (i.e., full)
		write_pcaprecord(consoleid, data, len);
		free(data);
	}
}



/*
 * This function basically sets up the .port (for wireshark use)
 */
void consoleInit(char *rpath, char *rname)
{
	int fd, status;


	sprintf(consolepath, "%s/%s.%s", rpath, rname, "port");

 	if (fifo_exists(consolepath, 1)) 
 	{ 
 		verbose(2, "[consoleInit]:: WARNING! existing FIFO %s removed .. creating a new one ", consolepath); 
 		remove(consolepath); 
		// remove existing console message queue - the message queue connecting to wireshark
		if (consoleq != NULL)
			destroySimpleQueue(consoleq);
		if (console_threadid != 0)
			pthread_cancel(console_threadid);
 	} 

	consoleq = createSimpleQueue("console queue", 256, 0, 1);
 	if ((fd = fifo_open(consolepath, S_IRUSR | S_IWUSR | S_IWGRP | S_IWOTH, 1, &consoleid)) == -1) 
 	{ 
 		error("[consoleInit]:: unable to create socket .. %s", consolepath); 
 		return; 
 	} 

	status = pthread_create(&(console_threadid), NULL, (void *)consoleHandler, (void *)consoleq);
	if (status != 0) 
		error("[consoleInit]:: Unable to create the console handler thread... ");
	return;
}

