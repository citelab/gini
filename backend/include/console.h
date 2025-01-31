#ifndef __CONSOLE_H__
#define __CONSOLE_H__

#include <pthread.h>

// Function declarations
void consoleInit(char *rpath, char *rname);
void consoleRestart(char *rpath, char *rname);
void consoleCleanup();
void consoleGetState();
void consoleHandler(void *ptr);

#endif 