/*
 * gr_control_plane.c  —  B2 control-plane runtime.
 *
 * One serialized thread owns every control-module callback (start, on_packet, timers), so a
 * module needs no locks for its own state. See gr_control_plane.h for the model. This file:
 *   - the services vtable the modules call (send / route / timers / iface inventory / log)
 *   - the control thread: a timed-wait loop over a timer list + an inbound packet queue
 *   - a small registry mapping a name to a control-module constructor
 *   - gr_cp_deliver(), the cheap filter-and-copy the forwarding worker calls
 */
#include "gr_control_plane.h"
#include "gr_modules.h"     /* gr_pkt_proto, gr_pkt_ipdst */
#include "gr_state.h"       /* gr_route_lookup/add, gr_route_del_match */
#include "routetable.h"     /* ROUTE_ORIGIN_DYNAMIC */
#include "ip.h"             /* IPOutgoingPacket, IPSend2Output, ip_packet_t, MTU_tbl */
#include "mtu.h"            /* findInterfaceIP, findAllInterfaceIPs */
#include "gnet.h"           /* findInterface: the interface's netmask */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <time.h>
#include <pthread.h>
#include <arpa/inet.h>   /* htons */
#include "gr_crash.h"

#define GR_CP_MAX_MODULES 8
#define GR_CP_MAX_TIMERS  32
#define IP_PROTO_UDP      17

/* ---- internal state (all guarded by cp_lock) ----------------------------- */
typedef struct cp_timer {
    int   used;
    int   id;
    long long deadline_ms;
    int   period_ms;
    void (*cb)(gr_cp_module_t *self, void *arg);
    void *arg;
    gr_cp_module_t *owner;
} cp_timer_t;

typedef struct cp_pkt {
    gpacket_t pkt;
    struct cp_pkt *next;
} cp_pkt_t;

typedef struct cp_evt {                  /* a link event for on_link, queued like a packet */
    int iface, up, metric;
    struct cp_evt *next;
} cp_evt_t;

static gr_cp_module_t *g_modules[GR_CP_MAX_MODULES];
static int             g_nmod = 0;
/* Detached but not yet stopped. A module leaves g_modules on the CLI thread (cp del, or a
 * cp add that replaces it) but its stop() -- which closes the Lua state -- must not run while
 * the control thread may be inside one of its callbacks. So removal only parks it here, and
 * the control thread buries it at the top of its next loop, where nothing is in flight. */
static gr_cp_module_t *g_dead[GR_CP_MAX_MODULES];
static int             g_ndead = 0;
static cp_timer_t      g_timers[GR_CP_MAX_TIMERS];
static int             g_next_timer_id = 1;
static cp_pkt_t       *g_qhead = NULL, *g_qtail = NULL;
static cp_evt_t       *g_ehead = NULL, *g_etail = NULL;
static pthread_mutex_t cp_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t  cp_cond;
static pthread_once_t  cp_cond_once = PTHREAD_ONCE_INIT;
static int             g_running = 0;

/* MONOTONIC, not REALTIME. Every tick deadline and the loop's timed wait are absolute times on
 * this clock, and the realtime clock STEPS: a laptop that slept has its Docker VM's clock
 * yanked forward or back on resume. Forward, every timer fired at once; backward by N seconds,
 * no tick fired for N seconds -- three missed ticks and every neighbour aged this router's
 * routes out, with nothing in any log to say why the network "just stopped working". The
 * monotonic clock cannot step. (macOS lacks pthread_condattr_setclock; the router is only
 * built for Linux, but keep it compiling there.) */
#if defined(__linux__)
#define CP_CLOCK CLOCK_MONOTONIC
#else
#define CP_CLOCK CLOCK_REALTIME
#endif

static void cp_cond_init(void)
{
    pthread_condattr_t attr;
    pthread_condattr_init(&attr);
#if defined(__linux__)
    pthread_condattr_setclock(&attr, CP_CLOCK);
#endif
    pthread_cond_init(&cp_cond, &attr);
    pthread_condattr_destroy(&attr);
}

static long long now_ms(void)
{
    struct timespec ts;
    clock_gettime(CP_CLOCK, &ts);
    return (long long)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

/* ---- services the modules call ------------------------------------------- */

static int svc_send_ipv4(const uchar *dst, int proto, const void *payload, int len)
{
    gpacket_t *pkt = (gpacket_t *)calloc(1, sizeof(gpacket_t));
    if (!pkt) return -1;
    /* the L4 payload sits right after the IP header (IPOutgoingPacket builds the header) */
    if (payload && len > 0)
        memcpy((uchar *)pkt->data.data + sizeof(ip_packet_t), payload, len);
    if (IPOutgoingPacket(pkt, (uchar *)dst, len, 1, proto) != EXIT_SUCCESS)
    {
        free(pkt);                 /* not queued (e.g. no route) -> we still own it */
        return -1;
    }
    return 0;                      /* queued; ownership passed to the output queue */
}

static int svc_send_raw(int iface, const uchar *dst_mac, int prot,
                        const void *payload, int len)
{
    gpacket_t *pkt = (gpacket_t *)calloc(1, sizeof(gpacket_t));
    if (!pkt) return -1;
    pkt->frame.dst_interface = iface;
    /* GNET sends header.dst as-is (no ARP, no cache) when arp_bcast is set; that is what we
     * want for a frame to an already-known MAC, including the broadcast MAC. */
    pkt->frame.arp_bcast = 1;
    if (dst_mac) memcpy(pkt->data.header.dst, dst_mac, 6);
    pkt->data.header.prot = htons((ushort)prot);
    /* A Lua script's string can be any length; the frame holds sizeof(pkt->data.data). */
    if (len > (int)sizeof(pkt->data.data) || iface < 0 || findInterface(iface) == NULL)
    {
        free(pkt);
        return -1;
    }
    if (payload && len > 0)
        memcpy(pkt->data.data, payload, len);
    if (IPSend2Output(pkt) != EXIT_SUCCESS) { free(pkt); return -1; }
    return 0;
}

/* one's-complement 16-bit checksum over a byte buffer (IP/UDP) */
static unsigned short cksum16(const unsigned char *b, int len)
{
    unsigned long sum = 0; int i;
    for (i = 0; i + 1 < len; i += 2) sum += ((unsigned long)b[i] << 8) | b[i + 1];
    if (i < len) sum += (unsigned long)b[i] << 8;
    while (sum >> 16) sum = (sum & 0xffff) + (sum >> 16);
    return (unsigned short)(~sum);
}

#define IP_PROTO_UDP_C 17
static int svc_send_udp(int iface, const uchar *dst_mac,
                        const uchar *src_ip, const uchar *dst_ip,
                        int sport, int dport, const void *data, int len)
{
    unsigned char buf[1500];
    int iphl = 20, udphl = 8, total = iphl + udphl + len;
    unsigned short c;
    if (total > (int)sizeof(buf)) return -1;
    memset(buf, 0, iphl + udphl);
    /* IP header */
    buf[0] = 0x45; buf[1] = 0;
    buf[2] = (total >> 8) & 0xff; buf[3] = total & 0xff;
    buf[4] = 0; buf[5] = 0; buf[6] = 0; buf[7] = 0;     /* id / flags / frag */
    buf[8] = 64; buf[9] = IP_PROTO_UDP_C;               /* ttl / proto */
    buf[10] = 0; buf[11] = 0;                           /* checksum (filled below) */
    memcpy(buf + 12, src_ip, 4);
    memcpy(buf + 16, dst_ip, 4);
    c = cksum16(buf, iphl);
    buf[10] = (c >> 8) & 0xff; buf[11] = c & 0xff;
    /* UDP header (checksum 0 = not computed, legal for IPv4) */
    buf[20] = (sport >> 8) & 0xff; buf[21] = sport & 0xff;
    buf[22] = (dport >> 8) & 0xff; buf[23] = dport & 0xff;
    buf[24] = ((udphl + len) >> 8) & 0xff; buf[25] = (udphl + len) & 0xff;
    buf[26] = 0; buf[27] = 0;
    if (data && len > 0) memcpy(buf + 28, data, len);
    return svc_send_raw(iface, dst_mac, 0x0800 /* ETH_P_IP */, buf, total);
}

static void svc_route_add(const uchar *net, const uchar *mask, const uchar *nhop, int iface)
{
    /* control-plane routes are tagged DYNAMIC: `route show` attributes them, and a
     * flood of them can never evict the router's own connected routes */
    gr_route_add_tagged((uchar *)net, (uchar *)mask, (uchar *)nhop, iface,
                        ROUTE_ORIGIN_DYNAMIC);
}
static void svc_route_del(const uchar *net, const uchar *mask)
{
    gr_route_del_match((uchar *)net, (uchar *)mask);
}
static int svc_route_lookup(const uchar *dst, uchar *nhop, int *iface)
{
    return gr_route_lookup((uchar *)dst, nhop, iface);
}

static int svc_timer_add(gr_cp_module_t *self, int period_ms,
                         void (*cb)(gr_cp_module_t *self, void *arg), void *arg)
{
    int id = -1, i;
    pthread_once(&cp_cond_once, cp_cond_init);
    pthread_mutex_lock(&cp_lock);
    for (i = 0; i < GR_CP_MAX_TIMERS; i++)
        if (!g_timers[i].used)
        {
            g_timers[i].used = 1;
            g_timers[i].id = (id = g_next_timer_id++);
            g_timers[i].period_ms = period_ms;
            g_timers[i].deadline_ms = now_ms() + period_ms;
            g_timers[i].cb = cb;
            g_timers[i].arg = arg;
            g_timers[i].owner = self;
            break;
        }
    pthread_cond_signal(&cp_cond);
    pthread_mutex_unlock(&cp_lock);
    return id;
}

static void svc_timer_del(int timer_id)
{
    int i;
    pthread_mutex_lock(&cp_lock);
    for (i = 0; i < GR_CP_MAX_TIMERS; i++)
        if (g_timers[i].used && g_timers[i].id == timer_id)
            g_timers[i].used = 0;
    pthread_mutex_unlock(&cp_lock);
}

static int svc_iface_count(void)
{
    uchar buf[MAX_MTU][4];
    return findAllInterfaceIPs(MTU_tbl, buf);
}
static int svc_iface_addr(int iface, uchar *ip)
{
    return (findInterfaceIP(MTU_tbl, iface, ip) == EXIT_SUCCESS) ? 0 : -1;
}

static int svc_iface_mask(int iface, uchar *mask)
{
    interface_t *f = findInterface(iface);
    if (f == NULL) return -1;
    COPY_IP(mask, f->netmask);
    return 0;
}

static int svc_iface_up(int iface)
{
    interface_t *f = findInterface(iface);
    return (f != NULL && f->state == INTERFACE_UP) ? 1 : 0;
}

static int svc_iface_metric(int iface)
{
    interface_t *f = findInterface(iface);
    return (f != NULL && f->metric >= 1) ? f->metric : 1;
}

static void svc_log(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    printf("[control-plane] ");
    vprintf(fmt, ap);
    printf("\n");
    fflush(stdout);
    va_end(ap);
}

static const gr_cp_services_t SERVICES = {
    .send_ipv4 = svc_send_ipv4,  .send_raw    = svc_send_raw,  .send_udp = svc_send_udp,
    .route_add = svc_route_add,  .route_del   = svc_route_del,  .route_lookup = svc_route_lookup,
    .timer_add = svc_timer_add,  .timer_del   = svc_timer_del,
    .iface_count = svc_iface_count, .iface_addr = svc_iface_addr,
    .log = svc_log,
    .iface_mask = svc_iface_mask,
    .iface_metric = svc_iface_metric,
    .iface_up = svc_iface_up,
};

/* ---- filter matching ----------------------------------------------------- */
static uint32_t b4(const uchar *a)
{
    return ((uint32_t)a[0] << 24) | ((uint32_t)a[1] << 16) |
           ((uint32_t)a[2] << 8)  |  (uint32_t)a[3];
}

static int filter_match(const gr_cp_filter_t *f, gpacket_t *pkt)
{
    int proto = gr_pkt_proto(pkt);
    if (f->proto && proto != f->proto) return 0;
    if (b4(f->dst_addr))
    {
        uint32_t m = b4(f->dst_mask);
        if ((gr_pkt_ipdst(pkt) & m) != (b4(f->dst_addr) & m)) return 0;
    }
    if (f->udp_dport && proto == IP_PROTO_UDP)
    {
        const unsigned char *d = (const unsigned char *)pkt->data.data;
        int ihl = (d[0] & 0x0f) * 4; if (ihl < 20) ihl = 20;
        int dport = ((int)d[ihl + 2] << 8) | d[ihl + 3];
        if (dport != f->udp_dport) return 0;
    }
    return 1;
}

/* ---- inbound from the forwarding worker (ip.c) --------------------------- */
void gr_cp_deliver(gpacket_t *pkt)
{
    int i, want = 0;
    if (g_nmod == 0) return;                 /* fast path: nothing loaded */
    pthread_once(&cp_cond_once, cp_cond_init);
    pthread_mutex_lock(&cp_lock);
    for (i = 0; i < g_nmod; i++)
        if (filter_match(&g_modules[i]->filter, pkt)) { want = 1; break; }
    if (want)
    {
        cp_pkt_t *n = (cp_pkt_t *)malloc(sizeof(cp_pkt_t));
        if (n)
        {
            memcpy(&n->pkt, pkt, sizeof(gpacket_t));   /* copy: the worker frees the original */
            n->next = NULL;
            if (g_qtail) g_qtail->next = n; else g_qhead = n;
            g_qtail = n;
            pthread_cond_signal(&cp_cond);
        }
    }
    pthread_mutex_unlock(&cp_lock);
}

void gr_cp_link_event(int iface, int up, int metric)
{
    cp_evt_t *e;
    if (g_nmod == 0) return;                 /* nothing loaded: nobody to tell */
    if ((e = (cp_evt_t *)malloc(sizeof *e)) == NULL) return;
    e->iface = iface; e->up = up; e->metric = metric; e->next = NULL;
    pthread_once(&cp_cond_once, cp_cond_init);
    pthread_mutex_lock(&cp_lock);
    if (g_etail) g_etail->next = e; else g_ehead = e;
    g_etail = e;
    pthread_cond_signal(&cp_cond);
    pthread_mutex_unlock(&cp_lock);
}

/* Both dispatchers walk a SNAPSHOT of the module list taken under the lock, not g_modules
 * itself: `cp del` and a replacing `cp add` shift that array on the CLI thread. */
static void dispatch_link(gr_cp_module_t **mods, int nmod, cp_evt_t *e)
{
    int i;
    for (i = 0; i < nmod; i++)
        if (mods[i]->on_link)
            mods[i]->on_link(mods[i], e->iface, e->up, e->metric);
}

static void dispatch(gr_cp_module_t **mods, int nmod, gpacket_t *pkt)
{
    int i;
    for (i = 0; i < nmod; i++)
        if (mods[i]->on_packet && filter_match(&mods[i]->filter, pkt))
            mods[i]->on_packet(mods[i], pkt);
}

/* Take a module out of g_modules and park it in g_dead. Called with cp_lock HELD. Its timers
 * go now, so no callback of its can be scheduled again; the stop() itself waits for bury(). */
static void detach_locked(int i)
{
    gr_cp_module_t *m = g_modules[i];
    int t;
    for (t = 0; t < GR_CP_MAX_TIMERS; t++)
        if (g_timers[t].used && g_timers[t].owner == m)
            g_timers[t].used = 0;
    for (t = i; t + 1 < g_nmod; t++) g_modules[t] = g_modules[t + 1];
    g_modules[--g_nmod] = NULL;
    if (g_ndead < GR_CP_MAX_MODULES) g_dead[g_ndead++] = m;
}

/* Run stop() and free every parked module. Called WITHOUT the lock, on a thread that is not
 * inside any module callback: the control loop between turns, or stop_all at shutdown. */
static void bury(void)
{
    gr_cp_module_t *dead[GR_CP_MAX_MODULES];
    int n, i;
    pthread_mutex_lock(&cp_lock);
    n = g_ndead;
    for (i = 0; i < n; i++) dead[i] = g_dead[i];
    g_ndead = 0;
    pthread_mutex_unlock(&cp_lock);
    for (i = 0; i < n; i++)
    {
        if (dead[i]->stop) dead[i]->stop(dead[i]);
        free(dead[i]);
    }
}

/* ---- the control thread -------------------------------------------------- */
static void *gr_cp_loop(void *unused)
{
	gr_crash_name_thread("gr-ctlplane");
    (void)unused;
    pthread_setcanceltype(PTHREAD_CANCEL_ASYNCHRONOUS, NULL);
    pthread_once(&cp_cond_once, cp_cond_init);
    pthread_mutex_lock(&cp_lock);
    g_running = 1;
    while (g_running)
    {
        long long now = now_ms();
        long long next = now + 1000;   /* cap the idle wait at 1s */
        gr_cp_module_t *mods[GR_CP_MAX_MODULES];
        int nmod = g_nmod, m;
        for (m = 0; m < nmod; m++) mods[m] = g_modules[m];
        if (g_ndead)                   /* something was removed since the last turn */
        {
            pthread_mutex_unlock(&cp_lock);
            bury();
            pthread_mutex_lock(&cp_lock);
            continue;                  /* re-snapshot: the list changed under us */
        }

        /* collect due timer callbacks under the lock, then fire them unlocked */
        struct { void (*cb)(gr_cp_module_t *, void *); gr_cp_module_t *owner; void *arg; }
            due[GR_CP_MAX_TIMERS];
        int ndue = 0, i;
        for (i = 0; i < GR_CP_MAX_TIMERS; i++)
            if (g_timers[i].used)
            {
                if (g_timers[i].deadline_ms <= now)
                {
                    due[ndue].cb = g_timers[i].cb;
                    due[ndue].owner = g_timers[i].owner;
                    due[ndue].arg = g_timers[i].arg;
                    ndue++;
                    g_timers[i].deadline_ms = now + g_timers[i].period_ms;
                }
                if (g_timers[i].deadline_ms < next) next = g_timers[i].deadline_ms;
            }

        /* detach the inbound packet queue, and the link events */
        cp_pkt_t *pkts = g_qhead;
        g_qhead = g_qtail = NULL;
        cp_evt_t *evts = g_ehead;
        g_ehead = g_etail = NULL;

        pthread_mutex_unlock(&cp_lock);

        while (evts)                   /* link changes first: they explain what follows */
        {
            cp_evt_t *e = evts; evts = evts->next;
            dispatch_link(mods, nmod, e);
            free(e);
        }
        for (i = 0; i < ndue; i++)
            if (due[i].cb) due[i].cb(due[i].owner, due[i].arg);
        while (pkts)
        {
            cp_pkt_t *n = pkts; pkts = pkts->next;
            dispatch(mods, nmod, &n->pkt);
            free(n);
        }

        pthread_mutex_lock(&cp_lock);
        if (g_qhead == NULL && g_ehead == NULL)   /* nothing new arrived while we worked */
        {
            struct timespec ts;
            ts.tv_sec  = next / 1000;
            ts.tv_nsec = (next % 1000) * 1000000;
            pthread_cond_timedwait(&cp_cond, &cp_lock, &ts);
        }
    }
    pthread_mutex_unlock(&cp_lock);
    return NULL;
}

pthread_t gr_cp_thread_init(void)
{
    pthread_t tid;
    if (pthread_create(&tid, NULL, gr_cp_loop, NULL) != 0)
        return 0;
    return tid;
}

/* ---- control-module registry -------------------------------------------- */
/* Lua is the gRouter's ONE control-plane surface. The services above (send, timers, the
 * route table, interface list) are the C substrate; protocols are written in Lua on top
 * of them and loaded with `cp add lua <script>`. Reference modules ship with GINI and are
 * seeded into ~/.gini/scripts, which every router sees at /scripts.
 *
 * There were once C protocol modules here (rip, dhcp, igmp, a hello demo) built directly
 * against the same services. They were removed deliberately: two parallel implementations
 * of the same protocols meant two things to document, two to teach from, and two to keep
 * working, for no gain the Lua tier did not already provide. */
extern gr_cp_module_t *gr_cp_lua_create(void);     /* control-plane Lua: protocols in Lua */

typedef struct { const char *name; gr_cp_module_t *(*ctor)(void); } gr_cp_reg_t;

static const gr_cp_reg_t CP_REGISTRY[] = {
    { "lua",   gr_cp_lua_create   },   /* gpipe cp add lua <script> — implement a protocol */
};

const char *gr_cp_names(void)
{
    static char buf[128];
    unsigned i; int n = 0;
    buf[0] = '\0';
    for (i = 0; i < sizeof(CP_REGISTRY) / sizeof(CP_REGISTRY[0]); i++)
        n += snprintf(buf + n, sizeof(buf) - n, "%s%s", i ? " " : "", CP_REGISTRY[i].name);
    return buf;
}

/* The first word of a module's args: for Lua the script path, which is what identifies "the
 * same protocol" across two `cp add` lines (the tick after it may differ). */
static void first_word(const char *args, char *out, size_t outlen)
{
    size_t n = 0;
    out[0] = '\0';
    if (!args) return;
    while (*args == ' ' || *args == '\t') args++;
    while (args[n] && args[n] != ' ' && args[n] != '\t' && args[n] != '\n' && n + 1 < outlen)
    {
        out[n] = args[n];
        n++;
    }
    out[n] = '\0';
}

int gr_cp_add(const char *name, const char *args, char *out, size_t outlen)
{
    unsigned i;
    int k, replaced = -1;
    gr_cp_module_t *m = NULL;
    char script[256];

    first_word(args, script, sizeof script);
    pthread_once(&cp_cond_once, cp_cond_init);
    pthread_mutex_lock(&cp_lock);
    for (k = 0; k < g_nmod; k++)             /* the same script already running? */
        if (strcmp(g_modules[k]->name, name) == 0 && script[0]
            && strcmp(g_modules[k]->script, script) == 0)
            replaced = k;
    if (replaced < 0 && g_nmod >= GR_CP_MAX_MODULES)
    {
        pthread_mutex_unlock(&cp_lock);
        snprintf(out, outlen, "control plane full (%d modules)", g_nmod);
        return -1;
    }
    pthread_mutex_unlock(&cp_lock);
    for (i = 0; i < sizeof(CP_REGISTRY) / sizeof(CP_REGISTRY[0]); i++)
        if (strcmp(name, CP_REGISTRY[i].name) == 0) { m = CP_REGISTRY[i].ctor(); break; }
    if (!m)
    {
        snprintf(out, outlen, "unknown control module: %s   (have: %s)", name, gr_cp_names());
        return -1;
    }
    strncpy(m->script, script, sizeof m->script - 1);
    if (m->start && m->start(m, &SERVICES, args) != 0)   /* may register timers (safe) */
    {
        snprintf(out, outlen, "cp add %s: start failed", name);
        if (m->stop) m->stop(m);
        free(m);
        return -1;
    }
    /* The new copy is running before the old one goes, so the protocol never stops: its
     * routes stay in the table until the replacement has learned its own. Re-find the old
     * one -- the list may have moved while start() ran unlocked. */
    pthread_mutex_lock(&cp_lock);
    replaced = -1;
    for (k = 0; k < g_nmod; k++)
        if (strcmp(g_modules[k]->name, name) == 0 && script[0]
            && strcmp(g_modules[k]->script, script) == 0)
        {
            replaced = k;
            break;
        }
    if (replaced >= 0)
        detach_locked(replaced);
    if (g_nmod < GR_CP_MAX_MODULES)
        g_modules[g_nmod++] = m;
    pthread_cond_signal(&cp_cond);
    pthread_mutex_unlock(&cp_lock);
    if (replaced >= 0)
        snprintf(out, outlen, "control module '%s' restarted: replaced the copy of %s that was "
                 "already running (two copies would fight over one route table)", name, script);
    else
        snprintf(out, outlen, "control module '%s' started", name);
    return 0;
}

int gr_cp_del(int index, char *out, size_t outlen)
{
    char what[300];
    pthread_mutex_lock(&cp_lock);
    if (index < 0 || index >= g_nmod)
    {
        int n = g_nmod;
        pthread_mutex_unlock(&cp_lock);
        if (n == 0)
            snprintf(out, outlen, "no control modules loaded");
        else
            snprintf(out, outlen, "cp del: no module [%d]  (cp list shows 0..%d)", index, n - 1);
        return -1;
    }
    snprintf(what, sizeof what, "%s %s", g_modules[index]->name, g_modules[index]->script);
    detach_locked(index);
    pthread_cond_signal(&cp_cond);           /* wake the control thread to bury it */
    pthread_mutex_unlock(&cp_lock);
    snprintf(out, outlen, "control module [%d] %s removed", index, what);
    return 0;
}

/*
 * `gpipe cp stop`, from the CLI. It used to call gr_cp_stop_all, which runs every module's stop()
 * -- lua_close -- and frees it on the CLI thread, while the control thread could be inside that
 * module's tick or packet callback with a snapshot of the list. That is the use-after-free `cp del`
 * was already built to avoid, so this does what `cp del` does for each module: park them, and let
 * the control thread bury them between turns.
 */
void gr_cp_detach_all(void)
{
    int running;
    pthread_once(&cp_cond_once, cp_cond_init);
    pthread_mutex_lock(&cp_lock);
    while (g_nmod > 0)
        detach_locked(0);
    running = g_running;
    pthread_cond_signal(&cp_cond);
    pthread_mutex_unlock(&cp_lock);
    if (!running)
        bury();                              /* no control thread to do it */
}

void gr_cp_stop_all(void)
{
    gr_cp_module_t *snap[GR_CP_MAX_MODULES];
    int n, i;
    /* detach modules + clear timers under the lock, then run stop() WITHOUT the lock:
     * a module's stop() may call timer_del(), which takes cp_lock (non-recursive). */
    pthread_mutex_lock(&cp_lock);
    for (i = 0; i < GR_CP_MAX_TIMERS; i++) g_timers[i].used = 0;
    n = g_nmod;
    for (i = 0; i < n; i++) { snap[i] = g_modules[i]; g_modules[i] = NULL; }
    g_nmod = 0;
    pthread_mutex_unlock(&cp_lock);
    for (i = 0; i < n; i++)
    {
        if (snap[i]->stop) snap[i]->stop(snap[i]);
        free(snap[i]);
    }
    bury();                                  /* and whatever cp del had parked */
}

int gr_cp_status(char *out, size_t outlen)
{
    int i, n = 0, any = 0;
    pthread_mutex_lock(&cp_lock);
    for (i = 0; i < g_nmod && n < (int)outlen - 1; i++)
        if (g_modules[i]->status)
        {
            any = 1;
            n += g_modules[i]->status(g_modules[i], out + n, outlen - n);
        }
    if (g_nmod == 0)
        snprintf(out, outlen, "no control modules loaded");
    else if (!any || n == 0)
        snprintf(out, outlen, "no status published");
    pthread_mutex_unlock(&cp_lock);
    return 0;
}

int gr_cp_list(char *out, size_t outlen)
{
    int i, n = 0;
    pthread_mutex_lock(&cp_lock);
    if (g_nmod == 0) n = snprintf(out, outlen, "no control modules loaded");
    else
    {
        /* One line per module, with what it was started with: `lua lua` said nothing about
         * WHICH scripts were running, or that the same one was running twice. */
        n = snprintf(out, outlen, "control modules (%d):", g_nmod);
        for (i = 0; i < g_nmod && n < (int)outlen; i++)
            n += snprintf(out + n, outlen - n, "\n  [%d] %s %s", i, g_modules[i]->name,
                          g_modules[i]->script);
    }
    pthread_mutex_unlock(&cp_lock);
    return 0;
}
