/*
 * gr_mod_tap.c — Tap / capture: mirror matched packets to a .pcap file or a live FIFO.
 *
 * Writes a copy of each matched packet to a libpcap capture, then CONTINUEs — the original
 * packet is never touched. The network's version of a wiretap: a monitoring point you drop
 * into the pipeline. The stream uses link type 101 (LINKTYPE_RAW, raw IPv4), so it opens
 * directly in Wireshark / `tshark -r`.
 *
 * Two targets, chosen automatically from what `path` points at:
 *   - a regular FILE (the default): forensics. Overwritten on start; grows as packets match.
 *   - a FIFO / named pipe (if `path` already exists as one, e.g. the student ran `mkfifo`):
 *     LIVE streaming into a reader such as `suricata -r <fifo>` for real-time detection. The
 *     writer is non-blocking and drops when no reader is attached, so the gRouter never stalls;
 *     the pcap global header is (re)written each time a reader connects, so Suricata can be
 *     (re)started at will.
 *
 * Config:  gpipe add tap <path>[@<cidr>]     e.g.  add tap /captures/cap.pcap
 *                                                   add tap /captures/live.fifo@10.0.3.0/24
 *   path = capture file to create, OR an existing FIFO to stream into.
 *   cidr = optional destination filter (default: capture everything).
 *
 * Conforms to the gr_module_t ABI (gr_module.h); reads packet bytes only, writes out of band.
 */
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <sys/time.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <signal.h>
#include <unistd.h>

#include "gr_module.h"
#include "gr_modules.h"

typedef struct {
    FILE *f;
    uint32_t net, mask;
    long n;
    int is_fifo;          /* 1 => path is a FIFO: stream live, non-blocking, drop if no reader */
    char path[256];
} tap_state;

static void put32(FILE *f, uint32_t v) { fwrite(&v, 4, 1, f); }   /* native order: the pcap */
static void put16(FILE *f, uint16_t v) { fwrite(&v, 2, 1, f); }   /* magic tells the reader  */

static void tap_write_header(FILE *f)
{
    put32(f, 0xa1b2c3d4u); put16(f, 2); put16(f, 4);              /* magic + version */
    put32(f, 0); put32(f, 0); put32(f, 65535u); put32(f, 101u);  /* RAW IPv4 (LINKTYPE 101) */
    fflush(f);
}

/* For a FIFO target: try to (re)open it non-blocking. Succeeds only once a reader is attached;
 * on success we write a fresh pcap header so a just-started Suricata sees a valid stream. */
static void tap_open_fifo(tap_state *s)
{
    if (s->f) return;
    int fd = open(s->path, O_WRONLY | O_NONBLOCK);
    if (fd < 0) return;                       /* no reader yet -> stay closed, drop packets */
    s->f = fdopen(fd, "wb");
    if (s->f) tap_write_header(s->f);
}

static gr_verdict_t tap_process(gr_module_t *self, gpacket_t *pkt)
{
    tap_state *s = (tap_state *)self->state;
    gr_verdict_t v = { GR_CONTINUE, -1 };
    const unsigned char *d = (const unsigned char *)pkt->data.data;
    int iplen;
    struct timeval tv;

    if (s->is_fifo && !s->f) tap_open_fifo(s);   /* reconnect a live reader if one appeared */
    if (!s->f) return v;
    if ((gr_pkt_ipdst(pkt) & s->mask) != s->net) return v;   /* no match -> pass, uncaptured */

    iplen = ((int)d[2] << 8) | d[3];                          /* IP total length */
    if (iplen < 20) iplen = 20;
    if (iplen > DEFAULT_MTU) iplen = DEFAULT_MTU;

    gettimeofday(&tv, 0);
    put32(s->f, (uint32_t)tv.tv_sec);
    put32(s->f, (uint32_t)tv.tv_usec);
    put32(s->f, (uint32_t)iplen);     /* incl_len */
    put32(s->f, (uint32_t)iplen);     /* orig_len */
    fwrite(d, 1, (size_t)iplen, s->f);
    if (fflush(s->f) != 0 && s->is_fifo) {   /* reader went away (EPIPE): close, retry later */
        fclose(s->f);
        s->f = 0;
        return v;
    }
    s->n++;
    return v;   /* always CONTINUE — the original packet is untouched */
}

static void tap_destroy(gr_module_t *self)
{
    tap_state *s = (tap_state *)self->state;
    if (s->f) fclose(s->f);
    free(s);
    free(self);
}

/* gr_mod_tap(spec): the constructor the registry calls (`gpipe add tap <path>[@<cidr>]`). */
gr_module_t *gr_mod_tap(const char *spec)
{
    char path[200]; uint32_t net = 0, mask = 0;
    path[0] = 0;

    if (spec && spec[0])
    {
        char buf[256]; strncpy(buf, spec, sizeof buf - 1); buf[sizeof buf - 1] = 0;
        char *at = strchr(buf, '@');
        if (at)
        {
            int a=0,b=0,c=0,e=0,p=32;
            *at = 0;
            sscanf(at + 1, "%d.%d.%d.%d/%d", &a,&b,&c,&e,&p);
            mask = (p >= 32) ? 0xffffffffu : (p <= 0 ? 0u : ~((1u << (32-p)) - 1u));
            net = (((uint32_t)a<<24)|((uint32_t)b<<16)|((uint32_t)c<<8)|(uint32_t)e) & mask;
        }
        strncpy(path, buf, sizeof path - 1); path[sizeof path - 1] = 0;
    }
    if (!path[0]) strcpy(path, "/tmp/gini_tap.pcap");

    tap_state *s = (tap_state *)malloc(sizeof(tap_state));
    if (!s) return 0;
    s->net = net; s->mask = mask; s->n = 0; s->f = 0; s->is_fifo = 0;
    strncpy(s->path, path, sizeof s->path - 1); s->path[sizeof s->path - 1] = 0;

    /* Is the target an existing FIFO? Then stream live rather than truncating a file. */
    struct stat st;
    if (stat(path, &st) == 0 && S_ISFIFO(st.st_mode))
    {
        s->is_fifo = 1;
        signal(SIGPIPE, SIG_IGN);        /* a vanished reader must not kill the gRouter */
        tap_open_fifo(s);                /* attaches now if a reader is already waiting */
    }
    else
    {
        s->f = fopen(path, "wb");        /* forensics: a plain capture file */
        if (s->f) tap_write_header(s->f);
    }

    gr_module_t *m = (gr_module_t *)malloc(sizeof(gr_module_t));
    if (!m) { if (s->f) fclose(s->f); free(s); return 0; }
    m->type = "tap"; m->state = s; m->init = 0;
    m->process = tap_process; m->destroy = tap_destroy;
    return m;
}

/* Packets captured so far (for stats/inspection). */
long gr_mod_tap_count(gr_module_t *m)
{
    return ((tap_state *)m->state)->n;
}
