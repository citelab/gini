#ifndef SLACK_ERR_H
#define SLACK_ERR_H

#include <stdio.h>
#include <stdlib.h>
#include <errno.h>
#include <string.h>

// Basic error handling macros to replace slack library
#define err_program_name "grouter"

#define err_internal(...) do { \
    fprintf(stderr, "%s: internal error: ", err_program_name); \
    fprintf(stderr, __VA_ARGS__); \
    fprintf(stderr, "\n"); \
    exit(1); \
} while (0)

#define err_quit(...) do { \
    fprintf(stderr, "%s: ", err_program_name); \
    fprintf(stderr, __VA_ARGS__); \
    fprintf(stderr, "\n"); \
    exit(1); \
} while (0)

#define err_warning(...) do { \
    fprintf(stderr, "%s: warning: ", err_program_name); \
    fprintf(stderr, __VA_ARGS__); \
    fprintf(stderr, "\n"); \
} while (0)

#endif // SLACK_ERR_H 