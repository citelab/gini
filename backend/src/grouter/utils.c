/*
 * utils.c (some utilities for processing IP functions)
 * AUTHOR: Muthucumaru Maheswaran
 * VERSION: Beta
 */

#include "grouter.h"
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <signal.h>
#include <time.h>
#include <slack/err.h>
#include <netinet/in.h>
#include <stdint.h>
#include <arpa/inet.h>
#include "verbose.h"
#include "message.h"
#include <sys/types.h>
#include <unistd.h>


int netMaskLen(uchar nmask[])
{
	int len = 0;
	for (int i = 0; i < 4; i++)
	{
		if (nmask[i] > 0)
			len++;
	}

	return len;
}


/*
 * determine whether the given IP address is in the network given the
 * netmask
 * RETURNS 0 if IP address is in the given network, -1 otherwise.
 */
int compareIPUsingMask(uchar *ip_addr, uchar *network, uchar *netmask)
{
	uchar bnet[4];
	int i;
	char tmpbuf[64];

	COPY_IP(bnet, ip_addr);
	for (i = 0; i < 4; i++)
		bnet[i] = ip_addr[i] & netmask[i];

	if (COMPARE_IP(network, bnet) == 0)
		return 0;
	else
		return -1;
}


// This should be using the host-byte-order of x386. The x386 uses the least significant
// byte first. For ip_addr, ip_addr[3] is the least significant byte, it should be stored
// first in the representation.
char *IP2Dot(char *buf, uchar ip_addr[])
{
	sprintf(buf, "%u.%u.%u.%u", ip_addr[3], ip_addr[2], ip_addr[1], ip_addr[0]);
	return buf;
}



int Dot2IP(char *buf, uchar ip_addr[])
{
        unsigned int iip_addr[4];
        int i;

        sscanf(buf, "%u.%u.%u.%u", &(iip_addr[3]), &(iip_addr[2]), &(iip_addr[1]), &(iip_addr[0]));

        for (i=0; i < 4; i++) ip_addr[i] = iip_addr[i];
        return EXIT_SUCCESS;
}


// This is unaltered by the endianess. We treat the MAC as a string.
int Colon2MAC(char *buf, uchar mac_addr[])
{
        unsigned int imac_addr[6];
        int i;

        sscanf(buf, "%x:%x:%x:%x:%x:%x", &(imac_addr[0]), &(imac_addr[1]), &(imac_addr[2]),
	       &(imac_addr[3]), &(imac_addr[4]), &(imac_addr[5]));

        for (i=0; i < 6; i++) mac_addr[i] = imac_addr[i];
        return EXIT_SUCCESS;
}



char *MAC2Colon(char *buf, uchar mac_addr[])
{

	sprintf(buf, "%02x:%02x:%02x:%02x:%02x:%02x", mac_addr[0], mac_addr[1], mac_addr[2],
		mac_addr[3], mac_addr[4], mac_addr[5]);
	return buf;
}


int gAtoi(char *str)
{
	int val = 0;
	int indx = 1, i;

	for (i = strlen(str); i >= 0; i--)
	{
		if ((str[i] <= '9') && (str[i] >= '0'))
		{
			val += indx * (str[i] - '0');
			indx *= 10;
		}
	}
	return val;
}





unsigned char *gHtonl(unsigned char tbuf[], unsigned char val[])
{
	long inpara, outpara;

	memcpy(&inpara, val, 4);
	outpara = htonl(inpara);
	memcpy(tbuf, &outpara, 4);

	return tbuf;
}


unsigned char *gNtohl(unsigned char tbuf[], unsigned char val[])
{
	long inpara, outpara;

	memcpy(&inpara, val, 4);
	outpara = ntohl(inpara);
	memcpy(tbuf, &outpara, 4);

	return tbuf;
}


/*
 * Redefine signal handlers
 */
void redefineSignalHandler(int sigid, void (*my_func)(int signum))
{
	struct sigaction handler, old_handler;

	handler.sa_handler = my_func;
	sigemptyset(&handler.sa_mask);
	handler.sa_flags = 0;

	sigaction(sigid, NULL, &old_handler);
	if (old_handler.sa_handler != SIG_IGN)
		sigaction(sigid, &handler, NULL);
	else
		verbose(1, "[redefineSignalHandler]:: signal %d is already ignored.. redefinition ignored ", sigid);

}



/*
 * compute the checksum of a buffer, by adding 2-byte words
 * and returning their one's complement
 */
uint16_t checksum(uchar *buf, int len)
{
	uint32_t cksum = 0;
	uint16_t *p = (uint16_t *)buf;
	int i;

	for (i = 0; i < len; i++)
	{
		cksum += ntohs(*p);
		p++;
	}
	cksum = (cksum >> 16) + (cksum & 0xFFFF);
	cksum += (cksum >> 16);

	verbose(2, "[checksum]:: computed %x ..", ~cksum);
	return (uint16_t)(~cksum);
}

double subTimeVal(struct timeval *v2, struct timeval *v1)
{
	double val2, val1;

	val2 = v2->tv_sec * 1000.0 + v2->tv_usec/1000.0;
	val1 = v1->tv_sec * 1000.0 + v1->tv_usec/1000.0;

	return (val2 - val1);
}


void printTimeVal(struct timeval *v)
{
	printf("Time val = %d sec, %d usec \n", (int)v->tv_sec, (int)v->tv_usec);
}
