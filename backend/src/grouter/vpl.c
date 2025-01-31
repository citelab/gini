/*
 * This is the low level virtual physical layer for GINI. It interconnects
 * with the sockets created by other elements of a GINI virtual topology.

 * This driver code is based on code borrowed from different portions
 * of the UML source code. The original code is copyrighted as follows:

 * Copyright (C) 2001 Lennert Buytenhek (buytenh@gnu.org) and
 * James Leu (jleu@mindspring.net).
 * Copyright (C) 2001 by various other people who didn't put their name here.
 * Licensed under the GPL.
 */

#include "grouter.h"
#include "vpl.h"
#include "simplequeue.h"
#include <string.h>
#include <stdlib.h>
#include <errno.h>
#include <unistd.h>
#include <slack/std.h>
#include <slack/err.h>
#include <slack/fio.h>
#include <sys/stat.h>
#include "gpcap.h"
#include "verbose.h"
#include "message.h"
#include <sys/socket.h>
#include <sys/un.h>

/*
 * Some global variables! These global variables are used for visualizing the
 * packets. For wireshark and graphing tool interfaces. May be we need to find
 * a better structure.. so global variables can be removed?
 */
int consoleid;                        // FIFO id
char consolepath[MAX_NAME_LEN];
pthread_t console_threadid;
simplequeue_t *consoleq;
int infoid;
char infopath[MAX_NAME_LEN];
pthread_t info_threadid;
simplequeue_t *infoq;


/*
 * Local support routines...
 * I don't expect you to use these! These routines are used by other
 * routines within this file.
 */

int __vpl_sendto(int fd, void *buf, int len, void *to, int sock_len)
{
	int n;

	while(((n = sendto(fd, buf, len, 0, (struct sockaddr *) to,
                           sock_len)) < 0) && (errno == EINTR)) ;
	if(n < 0)
	{
		if(errno == EAGAIN) return(0);
        	return(-errno);
	}
	else if(n == 0) return(-ENOTCONN);
	return(n);
}



/*
 * This function basically sets up the .port (for wireshark use)
 */
void vpl_init(char *rpath, char *rname)
{
  	consoleInit(rpath, rname);
}




/*
 * Virtual physical layer connect routine. This routine connects to
 * the HUB or Switch that is already running in daemon mode.
 * returns non NULL pointer if success. Otherwise returns NULL.
 */

vpl_data_t *vpl_connect(char *vsock_name)
{
	struct timeval temp_wtime;
	struct sockaddr_un *sun;
	struct request_v3 req;
	int n, fd;

	struct name_t  		// temporary structure for providing local address
	{
		char zero;
		int pid;
		int usecs;
	} name;

	verbose(2, "[vpl_connect]:: starting connection.. ");
	vpl_data_t *pri = (vpl_data_t *)calloc(1, sizeof(vpl_data_t));
	pri->sock_type = "unix";
	pri->ctl_sock = (struct sockaddr_un *)strdup(vsock_name);
	pri->ctl_addr = (struct sockaddr_un *)new_addr(pri->ctl_sock,
				 strlen(vsock_name) + 1);
	pri->data_addr = NULL;
	pri->data = -1;
	pri->control = -1;
	name.zero = 0;
	name.pid = getpid();
	gettimeofday(&temp_wtime, NULL);
	name.usecs = temp_wtime.tv_usec;
	pri->local_addr = (struct sockaddr_un *)new_addr(&name, sizeof(struct name_t));

	if((pri->control = socket(AF_UNIX, SOCK_STREAM, 0)) < 0){
		verbose(2, "[vpl_connect]:: control socket failed, error = %s", strerror(errno));
		return NULL;
	}

	if(connect(pri->control, (struct sockaddr *) pri->ctl_addr,
		   sizeof(struct sockaddr_un)) < 0){
		verbose(2, "[vpl_connect]:: control connect failed, error = %s", strerror(errno));

		close(pri->control);
		return NULL;
	}

	verbose(2, "[vpl_connect]:: made primary connection.. ");
	if((fd = socket(AF_UNIX, SOCK_DGRAM, 0)) < 0){
		verbose(2, "[vpl_connect]:: data socket failed, error = %s", strerror(errno));

		close(pri->control);
		return NULL;
	}
	if(bind(fd, (struct sockaddr *) pri->local_addr, sizeof(struct sockaddr_un)) < 0){
		verbose(2, "[vpl_connect]:: data bind failed, error = %s", strerror(errno));
		close(fd);
		close(pri->control);
		return NULL;
	}

	sun = malloc(sizeof(struct sockaddr_un));
	if(sun == NULL){
		verbose(2, "[vpl_connect]:: new_addr: allocation of sockaddr_un failed");

		close(fd);
		close(pri->control);
		return NULL;
	}

	verbose(2, "[vpl_connect]:: writing local address.. ");
	req.magic = SWITCH_MAGIC;
	req.version = SWITCH_VERSION;
	req.type = REQ_NEW_CONTROL;
	memcpy(&(req.sock), pri->local_addr, sizeof(struct sockaddr_un));
	n = write(pri->control, &req, sizeof(req));
	if (n != sizeof(req))
	{
		verbose(2, "[vpl_connect]:: control setup request returned %d, error = %s", n, strerror(errno));
		close(pri->control);
		return NULL;
	}

	verbose(2, "[vpl_connect]:: reading remote address.. ");
	n = read(pri->control, sun, sizeof(*sun));
	if (n != sizeof(*sun))
	{
		verbose(2, "[vpl_connect]:: read of data socket returned %d, error = %s", n, strerror(errno));
		close(fd);
		close(pri->control);
		return NULL;
	}
	pri->data_addr = (struct sockaddr_un *)sun;
	pri->data = fd;

	return pri;
}



// need g_sockname - a temporary copy of name??

/*
 * create a vpl server socket
 * data_addr is still not set!!
 */
vpl_data_t *vpl_create_server(char *name)
{
	struct timeval temp_wtime;
	vpl_data_t *vdata;
	int one = 1;
	struct name_t  					// temporary structure for providing local address
	{
		char zero;
		int pid;
		int usecs;
	} sname;

	if ((vdata = (vpl_data_t *)malloc(sizeof(vpl_data_t))) == NULL)
	{
		verbose(2, "[vpl_create_server]:: memory allocation error ");
		return NULL;
	}
	vdata->sock_type = "unix";
	vdata->ctl_sock = (struct sockaddr_un *)strdup(name);
	vdata->data_addr = NULL;

	// setup control socket
	if((vdata->control = socket(PF_UNIX, SOCK_STREAM, 0)) <0)
	{
		verbose(2, "[vpl_create_server]:: cannot create a socket ");
		return NULL;
	}
	if(setsockopt(vdata->control, SOL_SOCKET,
		      SO_REUSEADDR, (char *) &one, sizeof(one)) <0)
	{
		verbose(2, "[vpl_create_server]:: cannot set socket options");
		return NULL;
	}

	vdata->ctl_addr = (struct sockaddr_un *)new_addr(name, strlen(name)+1);
	if(bind(vdata->control, (struct sockaddr *)vdata->ctl_addr,
		sizeof(struct sockaddr_un)) < 0)
	{
		if(errno == EADDRINUSE)
		{
			int test_fd;
			verbose(2, "[vpl_create_server]:: socket %s already exists.", name);
			if((test_fd = socket(PF_UNIX, SOCK_STREAM, 0)) < 0)
				verbose(2, "[vpl_create_server]:: cannot create a socket ");
			if(connect(test_fd, (struct sockaddr *)vdata->ctl_addr,
				   sizeof(struct sockaddr_un)) < 0)
			{
				if(errno == ECONNREFUSED)
				{
					verbose(2, "[vpl_create_server]:: removing unused socket %s.", name);
					if(unlink(name) < 0)
						verbose(2, "[vpl_create_server]:: unlink failed on socket");
				}
				else close(test_fd);
			}
		}
		if(bind(vdata->control, (struct sockaddr *)vdata->ctl_addr,
			sizeof(struct sockaddr_un)) < 0)
			verbose(2, "[vpl_create_server]:: error binding socket");
	}

	if(listen(vdata->control, 15) < 0)
		verbose(2, "[vpl_create_server]:: error executing listen");

	if((vdata->data = socket(PF_UNIX, SOCK_DGRAM, 0)) < 0)
	{
		verbose(2, "[vpl_create_server]:: unable to create socket ");
		return NULL;
	}

	sname.zero = 0;
	sname.pid = getpid();
	gettimeofday(&temp_wtime, NULL);
	sname.usecs = temp_wtime.tv_usec;
	vdata->local_addr = (struct sockaddr_un *)new_addr(&sname, sizeof(struct name_t));

	if(bind(vdata->data, (struct sockaddr *)vdata->local_addr,
		sizeof(struct sockaddr_un)) < 0)
	{
		verbose(2, "[vpl_create_server]:: bind error ...");
		return NULL;
	}
	return vdata;
}




/*
 * accept connections on vpl socket.
 * the data_addr field is set afterwards!
 */
int vpl_accept_connect(vpl_data_t *v)
{
	int insock, rbytes;
	struct sockaddr addr;
	struct request_v3 req;
	socklen_t len;

	len = sizeof(struct sockaddr);
	if ((v == NULL) || (v->control < 0))
	{
		verbose(2, "[vpl_accept_connect]:: ERROR!! invalid vpl_data.. ");
		return -1;
	}

	insock = accept(v->control, &addr, &len);
	if (insock < 0)
	{
		verbose(2, "[vpl_accept_connect]:: ERROR!! %s ", strerror(errno));
		return -1;
	}

	verbose(2, "[vpl_accept_connect]:: writing local address.. ");
	// send the local address to the remote side through insock
	write(insock, v->local_addr, sizeof(struct sockaddr_un));

	verbose(2, "[vpl_accept_connect]:: reading remote address..");
	rbytes = read(insock, &req, sizeof(struct request_v3));
	if ((rbytes < sizeof(struct request_v3)) &&
	    (req.magic != SWITCH_MAGIC) &&
	    (req.type != REQ_NEW_CONTROL))
	{
		verbose(2, "[vpl_accept_connect]:: malformed request packet ");
		return -1;
	}

	verbose(2, "[vpl_accept_connect]:: done reading remote address");
	// read the above into data addr
	v->data_addr = (struct sockaddr_un *)dup_addr((struct sockaddr_un *)&req.sock);


	return 1;
}


/*
 * Receive a packet from the vpl. You can use this with a "select"
 * function to multiplex between different interfaces or you can use
 * it in a multi-processed/multi-threaded server. The example code
 * given here should work in either mode.
 */
int vpl_recvfrom(vpl_data_t *vpl, void *buf, int len)
{
        int n;

        while(((n = recvfrom(vpl->data,  buf,  len, 0, NULL, NULL)) < 0) &&
              (errno == EINTR)) ;

        if(n < 0){
                if(errno == EAGAIN) return(0);
                return(-errno);
        }
        else if(n == 0) return(-ENOTCONN);
		copy2Queue(consoleq, buf, n);
        return(n);
}


int vpl_sendto(vpl_data_t *vpl, void *buf, int len)
{
	struct sockaddr_un *data_addr = vpl->data_addr;

	copy2Queue(consoleq, buf, len);
	return(__vpl_sendto(vpl->data, buf, len, data_addr, sizeof(*data_addr)));
}



/*
 * Cast the address in appropriate format for the socket.
 */
struct sockaddr_un *new_addr(void *name, int len)
{
	struct sockaddr_un *sun;

	sun = malloc(sizeof(struct sockaddr_un));
	if(sun == NULL){
		verbose(2, "[new_addr]:: allocation of sockaddr_un failed");
		return(NULL);
	}
	sun->sun_family = AF_UNIX;
	memcpy(sun->sun_path, name, len);

	return(sun);
}


/*
 * make a duplicate of the socket sock
 */
struct sockaddr_un *dup_addr(struct sockaddr_un *sock)
{
	struct sockaddr_un *sun;

	sun = malloc(sizeof(struct sockaddr_un));
	if (sun == NULL)
	{
		verbose(2, "[dup_addr]:: allocation of sockaddr_un failed");
		return NULL;
	}
	memcpy(sun, sock, sizeof(struct sockaddr_un));

	return sun;
}
