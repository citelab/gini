/*
 * This file provides the set of functions to open, read, and write the tap
 * interface. The tap interface is used to connect the virtual network created
 * by GINI to the Internet.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <netinet/in.h>
#include <sys/types.h>
#include <sys/socket.h>
#include "vpl.h"
#ifdef __APPLE__
#include <net/if.h>
#include <sys/kern_control.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <net/if_utun.h>
#define UTUN_CONTROL_NAME "com.apple.net.utun_control"
#else
#include <linux/if.h>
#include <linux/if_tun.h>
#endif
#include <fcntl.h>
#include <arpa/inet.h>
#include <sys/ioctl.h>
#include "message.h"
#include "grouter.h"
#include "tapio.h"

#include <slack/std.h>
#include <slack/err.h>
#include <slack/fio.h>
#include <sys/stat.h>
#include <unistd.h>
#include <syslog.h>

/*
 * You don't need to read this file unless you
 * are debugging the gRouter for transmission problems to and from the
 * Internet.
 */



/*
 * Connect to the tap interface. We already have the tap0 interface setup
 * using an external script.
 */

vpl_data_t *tap_connect(char *dev)
{
	struct ifreq ifr;
	int fd, err;
	vpl_data_t *vpl;

#ifdef __APPLE__
	// macOS tap device creation
	struct ctl_info ctlInfo;
	struct sockaddr_ctl sc;
	
	memset(&ctlInfo, 0, sizeof(ctlInfo));
	strlcpy(ctlInfo.ctl_name, UTUN_CONTROL_NAME, sizeof(ctlInfo.ctl_name));
	
	fd = socket(PF_SYSTEM, SOCK_DGRAM, SYSPROTO_CONTROL);
	if (fd < 0) {
		perror("socket(SYSPROTO_CONTROL)");
		return NULL;
	}
	
	if (ioctl(fd, CTLIOCGINFO, &ctlInfo) < 0) {
		perror("ioctl(CTLIOCGINFO)");
		close(fd);
		return NULL;
	}
	
	sc.sc_id = ctlInfo.ctl_id;
	sc.sc_len = sizeof(sc);
	sc.sc_family = AF_SYSTEM;
	sc.ss_sysaddr = AF_SYS_CONTROL;
	sc.sc_unit = 0;  // Zero means first available unit
	
	if (connect(fd, (struct sockaddr *)&sc, sizeof(sc)) < 0) {
		perror("connect(AF_SYS_CONTROL)");
		close(fd);
		return NULL;
	}
#else
	// Linux tap device creation
	if ((fd = open("/dev/net/tun", O_RDWR)) < 0)
	{
		printf("Error: Cannot open TUN/TAP dev\n");
		exit(0);
	}

	memset(&ifr, 0, sizeof(ifr));
	ifr.ifr_flags = IFF_TAP | IFF_NO_PI;
	if (*dev)
		strncpy(ifr.ifr_name, dev, IFNAMSIZ);

	if ((err = ioctl(fd, TUNSETIFF, (void *)&ifr)) < 0)
	{
		printf("Error: Could not ioctl tun");
		close(fd);
		return NULL;
	}
#endif

	vpl = (vpl_data_t *)malloc(sizeof(vpl_data_t));
	vpl->read = tapRead;
	vpl->write = tapWrite;
	vpl->data = fd;

	return vpl;
}



/*
 * Receive a packet from the vpl. You can use this with a "select"
 * function to multiplex between different interfaces or you can use
 * it in a multi-processed/multi-threaded server. The example code
 * given here should work in either mode.
 */
int tap_recvfrom(vpl_data_t *vpl, void *buf, int len)
{
	int n;
	uchar localbuf[MAX_MESSAGE_SIZE];

	while (((n = vpl->read(&vpl->data, localbuf, len)) < 0) && (errno == EINTR))
		;

	if (n < 0) {
		if (errno == EAGAIN)
			return (0);
		return (-errno);
	} else if (n == 0)
		return (-ENOTCONN);

	// strip the 4 bytes prepended to the packet..
	bcopy((localbuf+4), buf, n-4);

	return (n-4);
}


/*
 * Send packet through the tap interface pointed by the vpl data structure..
 */

int tap_sendto(vpl_data_t *vpl, void *buf, int len)
{
	int n;
	uchar localbuf[MAX_MESSAGE_SIZE];

	bzero(localbuf, MAX_MESSAGE_SIZE);
	bcopy(buf, (localbuf+4), len);

	while(((n = vpl->write(&vpl->data, localbuf, len+4)) < 0) && (errno == EINTR)) ;
	if(n < 0)
	{
		if(errno == EAGAIN) return(0);
		return(-errno);
	}
	else if(n == 0) return(-ENOTCONN);
	return(n);
}

size_t tapRead(void *arg, void *buf, size_t len)
{
	int tap_fd = *((int *)arg);
#ifdef __APPLE__
	// macOS needs to handle the 4-byte header
	uint32_t family;
	ssize_t rlen = read(tap_fd, &family, sizeof(family));
	if (rlen < 0) return rlen;
	rlen = read(tap_fd, buf, len);
	return rlen;
#else
	return read(tap_fd, buf, len);
#endif
}

size_t tapWrite(void *arg, void *buf, size_t len)
{
	int tap_fd = *((int *)arg);
#ifdef __APPLE__
	// macOS needs to handle the 4-byte header
	uint32_t family = htonl(AF_INET);
	write(tap_fd, &family, sizeof(family));
	return write(tap_fd, buf, len);
#else
	return write(tap_fd, buf, len);
#endif
}


