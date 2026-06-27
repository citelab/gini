/*
 * utils.c — the few helpers that stay in C.
 *
 * The pure helpers (netMaskLen, compareIPUsingMask, IP2Dot, Dot2IP, Colon2MAC,
 * MAC2Colon, gAtoi, gHtonl, gNtohl, checksum, ntohll, htonll) were ported to Zig in
 * utils.zig (Z3). These three remain in C because they need libc signal / timeval /
 * stdio that isn't worth marshalling through Zig.
 */
#include "grouter.h"
#include <stdio.h>
#include <signal.h>
#include <time.h>
#include <slack/err.h>


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
