/*
 * This is the low level driver for the wlan interface. 
 * It creates a wlan interface on the station and hooks up a 
 * raw socket to the interface.
 * 
 * Copyright (C) 2015 Ahmed Youssef (ahmed.youssef@mail.mcgill.ca)
 * Licensed under the GPL.
 */

#ifndef __RAW_H__
#define __RAW_H__

#include "vpl.h"
#include "message.h"

// Function declarations
vpl_data_t* raw_connect(unsigned char* mac_addr, char *bridge);
void* fromRawDev(void *arg);
void* toRawDev(void *arg);
int create_raw_interface(unsigned char *nw_addr);

#endif	/* __RAW_H__ */

