#ifndef __VERBOSE_H__
#define __VERBOSE_H__

#include <stdarg.h>

// Declare the global verbosity level
extern int verbosity_level;

// Declare the verbose function
void gini_verbose(int level, const char *format, ...);
void gini_error(const char *format, ...);
void gini_fatal(const char *format, ...);

#endif // __VERBOSE_H__ 