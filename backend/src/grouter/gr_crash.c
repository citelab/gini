/*
 * gr_crash.c -- a crash report that outlives the lab.
 *
 * The router already printed a backtrace on a fatal signal (main's old gini_fatal_handler), to
 * fd 2, which Docker keeps as the container's log. That log is deleted with the container, and
 * gBuilder's Stop is `compose down`, which deletes the container. So the one moment anyone looked
 * -- after a lab ended, or after pressing Stop to recover from a dead router -- was the moment the
 * evidence had just gone. "The router sometimes crashes" arrived with no backtrace for exactly
 * that reason.
 *
 * Now a fatal signal writes the same report twice: to fd 2 as before, and to a file in
 * $GR_CRASH_DIR, which run_grouter.py points at /captures -- the host's ~/.gini/captures,
 * bind-mounted and kept across Stop. run_grouter.py keeps only the newest few reports, so the
 * folder cannot grow without bound. A report carries:
 *   - the signal, its code and the fault address, and WHICH thread died (each long-lived thread
 *     names itself through gr_crash_name_thread; a crash in the scheduler and one in the CLI
 *     are different bugs);
 *   - the backtrace. The binary is built -g -rdynamic, so exported functions are named; a static
 *     one shows as grouter(+0xOFFSET), which `addr2line -f -e grouter 0xOFFSET` in the build
 *     image turns into a file and line;
 *   - the last GR_CRASH_CMDS CLI lines, from the console, the control socket and the config file
 *     alike (parseACLICmd notes each one before tokenizing it). Students type at a router while
 *     it forwards, and "what was typed last" is the first question about any crash;
 *   - the newest GR_CRASH_WATCH packet fates, if `watch on` was set.
 *
 * Written from a signal handler, so: no malloc, no locks, no stdio. Strings go out with write().
 * Two compromises, both deliberate. snprintf formats the numbers -- glibc's does not allocate for
 * integer conversions, and a hand-rolled formatter for every field is more code to get wrong at
 * the worst moment. And backtrace() is called once at install time, because its first call loads
 * libgcc and may allocate; after that it is safe enough here, and it is what the old handler
 * relied on too. SA_RESETHAND makes a second fault inside the handler fatal at once, rather than
 * a loop.
 *
 * Not covered: a stack overflow. The handler runs on the faulting thread's stack, and an
 * alternate signal stack is per-thread (every pthread_create site would need one). A thread that
 * overflows its stack dies with the exit code alone.
 */
#define _GNU_SOURCE
#include <execinfo.h>
#include <fcntl.h>
#include <pthread.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include "grouter.h"
#include "gr_crash.h"

extern router_config rconfig;
extern volatile int gr_watch_enabled;

static char crash_dir[256];
static char router_name[64] = "grouter";
static struct timespec started;

typedef struct
{
    volatile unsigned long seq;          /* 0 = empty or being written; written LAST */
    unsigned long at_s;                  /* seconds since the router started */
    char text[GR_CRASH_CMD_LEN];
} cmd_ent_t;

static cmd_ent_t cmds[GR_CRASH_CMDS];
static volatile unsigned long cmd_next = 0;

static unsigned long uptime_s(void)
{
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return (unsigned long)(now.tv_sec - started.tv_sec);
}

void gr_crash_note_cmd(const char *line)
{
    unsigned long seq;
    cmd_ent_t *e;
    size_t n;

    if (line == NULL)
        return;
    while (*line == ' ' || *line == '\t')
        line++;
    if (*line == '\0' || *line == '\n')
        return;
    seq = __sync_add_and_fetch(&cmd_next, 1);
    e = &cmds[seq % GR_CRASH_CMDS];
    e->seq = 0;
    __sync_synchronize();
    n = strcspn(line, "\n");
    if (n >= GR_CRASH_CMD_LEN)
        n = GR_CRASH_CMD_LEN - 1;
    memcpy(e->text, line, n);
    e->text[n] = '\0';
    e->at_s = uptime_s();
    __sync_synchronize();
    e->seq = seq;
}

void gr_crash_name_thread(const char *name)
{
    pthread_setname_np(pthread_self(), name);
}

/* ---- the report, written with write() only ------------------------------------------------ */

static void put(int fd, const char *s)
{
    size_t n = strlen(s);
    while (n > 0)
    {
        ssize_t k = write(fd, s, n);
        if (k <= 0)
            return;
        s += k;
        n -= (size_t)k;
    }
}

static const char *signame(int sig)
{
    switch (sig)
    {
    case SIGSEGV: return "SIGSEGV (bad memory access)";
    case SIGBUS:  return "SIGBUS (bad memory access)";
    case SIGABRT: return "SIGABRT (abort: a failed assert, or the C library caught heap corruption)";
    case SIGFPE:  return "SIGFPE (arithmetic: e.g. divide by zero)";
    case SIGILL:  return "SIGILL (illegal instruction)";
    default:      return "fatal signal";
    }
}

static void thread_name(char *buf, size_t len)
{
    int fd = open("/proc/thread-self/comm", O_RDONLY);
    ssize_t k = -1;
    if (fd >= 0)
    {
        k = read(fd, buf, len - 1);
        close(fd);
    }
    if (k <= 0)
        k = 0;
    buf[k] = '\0';
    buf[strcspn(buf, "\n")] = '\0';
}

static void write_report(int fd, int sig, siginfo_t *si, void **frames, int nframes,
                         const char *tname, unsigned long up)
{
    char line[512];
    unsigned long top, s, lo;

    put(fd, "\n==== gRouter crash report ====\n");
    if (si && si->si_code <= 0)          /* SI_USER, SI_TKILL...: kill(), not a fault */
        snprintf(line, sizeof line, "router:    %s\nsignal:    %d %s, sent by process %ld "
                 "(kill), not raised by a fault\n", router_name, sig, signame(sig),
                 (long)si->si_pid);
    else
        snprintf(line, sizeof line, "router:    %s\nsignal:    %d %s, code %d, fault address %p\n",
                 router_name, sig, signame(sig), si ? si->si_code : 0, si ? si->si_addr : NULL);
    put(fd, line);
    snprintf(line, sizeof line, "thread:    %ld (%s)\nuptime:    %lu s\nwall:      %ld (unix time)\n",
             (long)gettid(), tname[0] ? tname : "?", up, (long)time(NULL));
    put(fd, line);
    snprintf(line, sizeof line, "scheduler: %s, sched-cycle %d us; packet watch %s\n",
             rconfig.schedpolicy == GR_SCHED_DRR ? "drr" : "rr", rconfig.schedcycle,
             gr_watch_enabled ? "on" : "off");
    put(fd, line);

    put(fd, "\n-- backtrace, innermost first. For a grouter(+0xN) frame, in the build image:"
            " addr2line -f -e /usr/local/bin/grouter 0xN --\n");
    backtrace_symbols_fd(frames, nframes, fd);

    put(fd, "\n-- last CLI commands, oldest first [seconds since start] --\n");
    top = cmd_next;
    lo = top > GR_CRASH_CMDS ? top - GR_CRASH_CMDS + 1 : 1;
    if (top == 0)
        put(fd, "(none)\n");
    for (s = lo; s <= top && top > 0; s++)
    {
        cmd_ent_t *e = &cmds[s % GR_CRASH_CMDS];
        if (e->seq != s)
            continue;                    /* being written, or already overwritten */
        snprintf(line, sizeof line, "[%6lu] %s\n", e->at_s, e->text);
        put(fd, line);
    }

    put(fd, "\n-- last packet fates, newest first --\n");
    if (gr_watch_enabled)
        gr_watch_crash_dump(fd, GR_CRASH_WATCH);
    else
        put(fd, "(packet watch was off; `watch on` before reproducing records these)\n");
    put(fd, "==== end of crash report ====\n");
}

static void crash_handler(int sig, siginfo_t *si, void *uctx)
{
    void *frames[48];
    int nframes = backtrace(frames, 48);
    char tname[32], path[400], msg[480];
    unsigned long up = uptime_s();
    int fd = -1;

    (void)uctx;
    thread_name(tname, sizeof tname);
    write_report(2, sig, si, frames, nframes, tname, up);
    if (crash_dir[0])
    {
        snprintf(path, sizeof path, "%s/%s-crash-%ld.txt", crash_dir, router_name, (long)time(NULL));
        fd = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
        if (fd >= 0)
        {
            write_report(fd, sig, si, frames, nframes, tname, up);
            fsync(fd);
            close(fd);
            snprintf(msg, sizeof msg, "[gRouter] crash report saved: %s\n", path);
            put(2, msg);
        }
        else
        {
            snprintf(msg, sizeof msg, "[gRouter] could not write a crash report under %s\n",
                     crash_dir);
            put(2, msg);
        }
    }
    signal(sig, SIG_DFL);
    raise(sig);                          /* die by the same signal: the exit code still says why */
}

void gr_crash_install(const char *name)
{
    static const int fatal[] = { SIGSEGV, SIGBUS, SIGABRT, SIGFPE, SIGILL };
    struct sigaction sa;
    const char *dir = getenv("GR_CRASH_DIR");
    void *warm[2];
    size_t i;

    clock_gettime(CLOCK_MONOTONIC, &started);
    if (name && *name)
        snprintf(router_name, sizeof router_name, "%s", name);
    if (dir && *dir)
        snprintf(crash_dir, sizeof crash_dir, "%s", dir);
    backtrace(warm, 2);                  /* load libgcc now, not inside the handler */

    memset(&sa, 0, sizeof sa);
    sa.sa_sigaction = crash_handler;
    sa.sa_flags = SA_SIGINFO | SA_RESETHAND;
    sigemptyset(&sa.sa_mask);
    for (i = 0; i < sizeof fatal / sizeof fatal[0]; i++)
        sigaction(fatal[i], &sa, NULL);
}
