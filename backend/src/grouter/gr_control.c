/*
 * gr_control.c  —  Z2 pipeline control. Pure gpacket_t + libc; runnable/testable.
 * The same calls back the gRouter CLI and the Router Lab control protocol.
 */
#include "gr_control.h"
#include "gr_pipeline.h"
#include "gr_modules.h"
#include <stdio.h>
#include <string.h>

static const char *verdict_name(gr_action_t a)
{
    switch (a)
    {
        case GR_CONTINUE: return "CONTINUE";
        case GR_DROP:     return "DROP";
        case GR_FORWARD:  return "FORWARD";
        case GR_TO_HOST:  return "TO_HOST";
        case GR_CONSUMED: return "CONSUMED";
        default:          return "?";
    }
}

int gr_control(const char *line, char *out, size_t outlen)
{
    gr_pipeline_t *pl = gr_default_pipeline();
    char buf[256];
    strncpy(buf, line ? line : "", sizeof buf - 1);
    buf[sizeof buf - 1] = 0;

    char *tok = strtok(buf, " \t\n");
    if (!tok)
    {
        snprintf(out, outlen, "usage: add acl <cidr> | add counter | list | clear | trace <a.b.c.d>");
        return 0;
    }

    if (strcmp(tok, "add") == 0)
    {
        char *what = strtok(NULL, " \t\n");
        if (what && strcmp(what, "acl") == 0)
        {
            char *cidr = strtok(NULL, " \t\n");
            if (!cidr) { snprintf(out, outlen, "usage: add acl <cidr>"); return -1; }
            gr_pipeline_add(pl, gr_mod_acl(cidr));
            snprintf(out, outlen, "added acl %s  (pipeline: %d modules)", cidr, pl->count);
        }
        else if (what && strcmp(what, "counter") == 0)
        {
            gr_pipeline_add(pl, gr_mod_counter());
            snprintf(out, outlen, "added counter  (pipeline: %d modules)", pl->count);
        }
        else if (what && strcmp(what, "nat") == 0)
        {
            char *ip = strtok(NULL, " \t\n");
            if (!ip) { snprintf(out, outlen, "usage: add nat <snat-ip>"); return -1; }
            gr_pipeline_add(pl, gr_mod_nat(ip));
            snprintf(out, outlen, "added nat (snat %s)  (pipeline: %d modules)", ip, pl->count);
        }
#ifdef GR_LEGACY_MODULES
        else if (what && strcmp(what, "filter") == 0)
        {
            gr_pipeline_add(pl, gr_mod_filter());
            snprintf(out, outlen, "added filter (firewall)  (pipeline: %d modules)", pl->count);
        }
#endif
        else
        {
            snprintf(out, outlen, "unknown module: %s", what ? what : "(none)");
            return -1;
        }
    }
    else if (strcmp(tok, "list") == 0)
    {
        int n = snprintf(out, outlen, "base: parse"), i;
        for (i = 0; i < pl->count; i++)
            n += snprintf(out + n, outlen - n, " -> [%d:%s]", i, pl->modules[i]->type);
        snprintf(out + n, outlen - n, " -> route -> rewrite");
    }
    else if (strcmp(tok, "clear") == 0)
    {
        gr_pipeline_clear(pl);
        snprintf(out, outlen, "pipeline cleared (base only)");
    }
    else if (strcmp(tok, "trace") == 0)
    {
        char *ip = strtok(NULL, " \t\n");
        int a = 0, b = 0, c = 0, d = 0, i, n;
        if (ip) sscanf(ip, "%d.%d.%d.%d", &a, &b, &c, &d);
        gpacket_t pkt;
        memset(&pkt, 0, sizeof pkt);
        pkt.data.data[16] = (unsigned char)a; pkt.data.data[17] = (unsigned char)b;
        pkt.data.data[18] = (unsigned char)c; pkt.data.data[19] = (unsigned char)d;
        n = snprintf(out, outlen, "trace dst %d.%d.%d.%d:", a, b, c, d);
        gr_verdict_t v = { GR_CONTINUE, -1 };
        for (i = 0; i < pl->count; i++)
        {
            v = pl->modules[i]->process(pl->modules[i], &pkt);
            n += snprintf(out + n, outlen - n, "\n  %d. %-8s -> %s",
                          i, pl->modules[i]->type, verdict_name(v.action));
            if (v.action != GR_CONTINUE) break;
        }
        if (v.action == GR_CONTINUE)
            snprintf(out + n, outlen - n, "\n  -> base forwarding (route, rewrite, egress)");
    }
    else
    {
        snprintf(out, outlen, "unknown command: %s", tok);
        return -1;
    }
    return 0;
}
