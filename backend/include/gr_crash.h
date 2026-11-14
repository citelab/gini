/*
 * gr_crash.h -- what the router was doing when it died, kept where Stop cannot delete it.
 *
 * See gr_crash.c for why. Three calls: install once in main(), note every CLI line, and let each
 * long-lived thread name itself so a report can say which one crashed.
 */
#ifndef __GR_CRASH_H__
#define __GR_CRASH_H__

#define GR_CRASH_CMDS      64      /* CLI lines remembered */
#define GR_CRASH_CMD_LEN   160     /* bytes kept of each */
#define GR_CRASH_WATCH     256     /* packet-watch entries copied into a report, newest first */

void gr_crash_install(const char *router_name);
void gr_crash_note_cmd(const char *line);
void gr_crash_name_thread(const char *name);   /* <= 15 chars: the kernel's comm limit */

/* gr_watch.c: the newest `max` watch entries, written to fd with no locks and no malloc. */
void gr_watch_crash_dump(int fd, int max);

#endif
