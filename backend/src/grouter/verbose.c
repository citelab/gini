#include <stdio.h>
#include <stdarg.h>
#include <stdlib.h>
#include "verbose.h"

// Global verbosity level - can be set through command line or configuration
int gini_verbosity_level = 2;  // Default verbosity level

void verbose(int level, const char *format, ...)
{
    if (level <= gini_verbosity_level)
    {
        va_list args;
        va_start(args, format);
        vfprintf(stderr, format, args);
        fprintf(stderr, "\n");
        va_end(args);
    }
}

void error(const char *format, ...)
{
    va_list args;
    va_start(args, format);
    fprintf(stderr, "ERROR: ");
    vfprintf(stderr, format, args);
    fprintf(stderr, "\n");
    va_end(args);
}

void fatal(const char *format, ...)
{
    va_list args;
    va_start(args, format);
    fprintf(stderr, "FATAL: ");
    vfprintf(stderr, format, args);
    fprintf(stderr, "\n");
    va_end(args);
    exit(1);
} 