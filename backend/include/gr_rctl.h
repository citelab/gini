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

#endif /* __GR_RCTL_H__ */
