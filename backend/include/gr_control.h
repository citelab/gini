/*
 * gr_control.h  —  Z2 pipeline control surface.
 *
 * Parses one control command and acts on the router's default pipeline, writing a
 * human-readable result into `out`. Backs both the gRouter CLI (`gpipe …`) and the
 * Router Lab control protocol, so the visual editor edits the SAME pipeline the
 * forwarding path runs.
 *
 *   add acl <cidr> | add counter | list | clear | trace <a.b.c.d>
 */
#ifndef __GR_CONTROL_H__
#define __GR_CONTROL_H__

#include <stddef.h>

int gr_control(const char *line, char *out, size_t outlen);

#endif /* __GR_CONTROL_H__ */
