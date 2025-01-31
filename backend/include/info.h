/*
 * info.h (header file for information display subsystem)
 * These routines control what information is sent to the external performance
 * visualizer and how the information is formatted.
 *
 */


#ifndef __INFO_H__
#define __INFO_H__

#include <pthread.h>
#include "simplequeue.h"

#define INFO_ACTIVE 1
#define INFO_INACTIVE 0

typedef struct _message_header_t {
	int type;
	int length;
} msg_header_t;

typedef struct _message_t {
	msg_header_t header;
	void *data;
} message_t;

typedef struct _info_config_t
{
	char path[MAX_NAME_LEN];
	int sock;                   // FIFO file descriptor
	pthread_t id;               // Thread ID
	int status;                // Active/Inactive status
	int updateinterval;        // Update interval in seconds
	int rawtimemode;           // Raw time mode flag
	List *qtargets;            // List of queue targets
} info_config_t;

typedef struct _queue_target_t
{
	int active;
	char targetname[MAX_NAME_LEN];
	simplequeue_t *queue;
} queue_target_t;

// Function declarations
void infoGetState();
int write_to_fifo(info_config_t *iconf, message_t *msg);
void *infoHandler(void *arg);
void addTarget(char *name, simplequeue_t *tgrt);
void activeTarget(char *name);
void deactiveTarget(char *name);
void printTimeMode();
void setTimeMode(int rawmode);
int getTimeMode();
void setUpdateInterval(int interval);
int getUpdateInterval();
void infoList();
int infoInit(char *rpath, char *rname);  // Changed return type to int

#endif




