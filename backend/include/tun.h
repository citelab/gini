/*
 * This is the low level driver for the tun interface. 
 * It hooks up a UDP socket to the mesh interface.
 * 
 * Copyright (C) 2015 Ahmed Youssef (ahmed.youssef@mail.mcgill.ca)
 * Licensed under the GPL.
 */

#ifndef __TUN_H__
#define __TUN_H__

#include "vpl.h"
#include "message.h"

// Function declarations
void *toTunDev(void *arg);
void *fromTunDev(void *arg);
vpl_data_t *tun_connect(short int src_port, uchar* src_IP,
                         short int dst_port, uchar* dst_IP);

#endif	/* __TUN_H__ */

