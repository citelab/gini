/*
 * tapio.h (header file the low level Tap driver)
 * AUTHOR: Muthucumaru Maheswaran
 *
 * VERSION:
 */

#ifndef __TAPIO_H__
#define __TAPIO_H__

#include "vpl.h"

#ifdef __APPLE__
#define SYSPROTO_CONTROL 2
#define AF_SYS_CONTROL   2
#endif

/*
 * function prototypes
 */

vpl_data_t *tap_connect(char *sock_name);
int tap_recvfrom(vpl_data_t *vpl, void *buf, int len);
int tap_sendto(vpl_data_t *vpl, void *buf, int len);

// Function declarations for tap read/write
size_t tapRead(void *arg, void *buf, size_t len);
size_t tapWrite(void *arg, void *buf, size_t len);

#endif

