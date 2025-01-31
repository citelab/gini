#ifndef __UTILS_H__
#define __UTILS_H__

#include <stdint.h>

// Function declarations
void redefineSignalHandler(int sigid, void (*handler)(int));
uint16_t checksum(uchar *buf, int len);

// Remove ntohll/htonll declarations since they're provided by the system
// uint64_t ntohll(uint64_t arg);
// uint64_t htonll(uint64_t arg);

#endif 