/*
 * gr_control.c  —  Z2 pipeline control. Pure gpacket_t + libc; runnable/testable.
 * The same calls back the gRouter CLI and the Router Lab control protocol.
 */
#include "gr_control.h"
#include "gr_pipeline.h"
#include "gr_modules.h"
#include "gr_control_plane.h"   /* B2: control-plane modules (cp add/list/stop) */
#include "gr_mcast.h"           /* B3: multicast membership (mcast join/leave/show) */
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

#ifdef GR_LUA
/* slurp a whole file into a malloc'd, NUL-terminated buffer (caller frees). */
static char *read_file(const char *path)
{
    FILE *f = fopen(path, "rb");
    long n;
    char *buf;
    size_t rd;
    if (!f) return 0;
    fseek(f, 0, SEEK_END); n = ftell(f); fseek(f, 0, SEEK_SET);
    if (n < 0) { fclose(f); return 0; }
    buf = (char *)malloc((size_t)n + 1);
    if (!buf) { fclose(f); return 0; }
    rd = fread(buf, 1, (size_t)n, f);
    buf[rd] = '\0';
    fclose(f);
    return buf;
}
#endif

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
        snprintf(out, outlen, "usage: add acl <cidr> | add counter | list | clear | trace <a.b.c.d>"
                 " | cp add <name> [args] | cp list | cp stop"
                 " | mcast join|leave <group> <iface> | mcast show");
        return 0;
    }

    if (strcmp(tok, "add") == 0)
    {
        char *what = strtok(NULL, " \t\n");
        char *arg  = strtok(NULL, " \t\n");
        gr_module_t *m;
        int need;

        if (!what)
        {
            snprintf(out, outlen, "usage: add <module> [arg]   (modules: %s | lua <path>)",
                     gr_module_names());
            return -1;
        }
#ifdef GR_LUA
        if (strcmp(what, "lua") == 0)                 /* the scripting tier: load a script */
        {
            char *script;
            if (!arg) { snprintf(out, outlen, "usage: add lua <script-path>"); return -1; }
            script = read_file(arg);
            if (!script) { snprintf(out, outlen, "add lua: cannot read '%s'", arg); return -1; }
            m = gr_mod_lua(script);
            free(script);
            if (!m) { snprintf(out, outlen, "add lua: failed to load '%s'", arg); return -1; }
            gr_pipeline_add(pl, m);
            snprintf(out, outlen, "added lua %s  (pipeline: %d modules)", arg, pl->count);
            return 0;
        }
#endif
        need = gr_module_needs_arg(what);             /* built-in or native, via the registry */
        if (need < 0)
        {
            snprintf(out, outlen, "unknown module: %s   (have: %s | lua <path>)",
                     what, gr_module_names());
            return -1;
        }
        if (need && !arg)
        {
            snprintf(out, outlen, "usage: add %s <arg>", what);
            return -1;
        }
        m = gr_module_create(what, arg);
        if (!m) { snprintf(out, outlen, "add %s: failed", what); return -1; }
        gr_pipeline_add(pl, m);
        snprintf(out, outlen, "added %s%s%s  (pipeline: %d modules)",
                 what, arg ? " " : "", arg ? arg : "", pl->count);
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
    else if (strcmp(tok, "cp") == 0)        /* B2: control-plane modules */
    {
        char *sub = strtok(NULL, " \t\n");
        if (sub && strcmp(sub, "add") == 0)
        {
            char *name = strtok(NULL, " \t\n");
            char *args = strtok(NULL, "\n");          /* rest of line = module args */
            if (!name)
            {
                snprintf(out, outlen, "usage: cp add <name> [args]   (modules: %s)",
                         gr_cp_names());
                return -1;
            }
            return gr_cp_add(name, args, out, outlen);
        }
        else if (sub && strcmp(sub, "list") == 0)
            return gr_cp_list(out, outlen);
        else if (sub && strcmp(sub, "status") == 0)   /* live snapshots (Multicast HUD polls this) */
            return gr_cp_status(out, outlen);
        else if (sub && strcmp(sub, "stop") == 0)
        {
            gr_cp_stop_all();
            snprintf(out, outlen, "control plane stopped (all modules removed)");
        }
        else
            snprintf(out, outlen, "usage: cp add <name> [args] | cp list | cp status | cp stop"
                     "   (modules: %s)", gr_cp_names());
    }
    else if (strcmp(tok, "mcast") == 0)     /* B3: multicast membership */
    {
        char *sub = strtok(NULL, " \t\n");
        if (sub && (strcmp(sub, "join") == 0 || strcmp(sub, "leave") == 0))
        {
            char *grp = strtok(NULL, " \t\n");
            char *ifs = strtok(NULL, " \t\n");
            uchar g[4]; uint32_t hg;
            int a = 0, b = 0, c = 0, d = 0, iface;
            if (!grp || !ifs)
            { snprintf(out, outlen, "usage: mcast %s <group> <iface>", sub); return -1; }
            sscanf(grp, "%d.%d.%d.%d", &a, &b, &c, &d);
            g[0]=(uchar)a; g[1]=(uchar)b; g[2]=(uchar)c; g[3]=(uchar)d;
            (void)hg;
            iface = atoi(ifs);
            if (strcmp(sub, "join") == 0) gr_mcast_join(g, iface);
            else gr_mcast_leave(g, iface);
            snprintf(out, outlen, "mcast %s %d.%d.%d.%d if%d", sub, a, b, c, d, iface);
        }
        else if (sub && strcmp(sub, "show") == 0)
            gr_mcast_show(out, (int)outlen);
        else
            snprintf(out, outlen, "usage: mcast join <group> <iface> | leave <group> <iface> | show");
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
