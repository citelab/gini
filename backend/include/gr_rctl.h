/*
 * gr_rctl.h — remote control socket for the gRouter.
 *
 * Exposes the real CLI over a Unix-domain socket so an external console (and the
 * GUI's Router Lab) can run commands against a *running* router and read the output.
 * One command per request; the server runs it through the normal CLI dispatch and
 * returns whatever the command printed.
 */
#ifndef __GR_RCTL_H__
#define __GR_RCTL_H__

#include <stddef.h>

/* Start the control socket at sock_path and spawn its accept thread. Returns 0 ok. */
int gr_rctl_start(const char *sock_path);

/* Run one CLI line, capturing its stdout into out (NUL-terminated). Returns length. */
int gr_rctl_exec(const char *line, char *out, size_t outlen);

/* Verbosity accessors that are correct BOTH from the interactive CLI and from a
 * console command. gr_rctl_exec() hushes logging to 0 while it captures output, so
 * reading or writing prog_verbosity_level() directly from a CLI handler sees (and is
 * overwritten by) that temporary value. `set verbose` must go through these. */
long gr_rctl_verbosity(void);
void gr_rctl_set_verbosity(long v);

#endif /* __GR_RCTL_H__ */
