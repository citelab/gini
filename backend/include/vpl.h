/*
 * This example code shows how you can connect to a port of the
 * mach_virtual_switch when it is running in HUB mode. To put the
 * virtual switch in hub mode just include the word "hub" in the
 * config file for the switch in a line by itself. 

 * This example code is based on code borrowed from different portions
 * of the UML source code. The original code is copyrighted as follows:

 * Copyright (C) 2001 Lennert Buytenhek (buytenh@gnu.org) and 
 * James Leu (jleu@mindspring.net).
 * Copyright (C) 2001 by various other people who didn't put their name here.
 * Licensed under the GPL.
 */

#ifndef __VPL_H__
#define __VPL_H__


#include <errno.h>
#include <unistd.h>
#include <stdint.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/time.h>
#include <stdlib.h>
#include <stdio.h>

#define SWITCH_VERSION           3
#define CONSOLE_PACKET           269               // arbitary number .. least likely to clash!

typedef struct _vpl_data_t {
	char *sock_type;
	char *ctl_sock;
	void *ctl_addr;
	void *data_addr;
	void *local_addr;
	int data;
	int control;
} vpl_data_t;



enum request_type { REQ_NEW_CONTROL };

#define SWITCH_MAGIC 0xfeedface

struct request_v3 {
	uint32_t magic;
	uint32_t version;
	enum request_type type;
	struct sockaddr_un sock;
};


/* function prototypes for internal routines */
int __vpl_sendto(int fd, void *buf, int len, void *to, int sock_len);


/* function prototypes for external routines */
struct sockaddr_un *new_addr(void *name, int len);
struct sockaddr_un *dup_addr(struct sockaddr_un *sock);
void vpl_init(char *rpath, char *rname);
void consoleHandler(void *ptr);
vpl_data_t *vpl_connect(char *sock_name);
vpl_data_t *vpl_create_server(char *name);
int vpl_accept_connect(vpl_data_t *v);
int vpl_recvfrom(vpl_data_t *vpl, void *buf, int len);
int vpl_sendto(vpl_data_t *vpl, void *buf, int len);

#endif
