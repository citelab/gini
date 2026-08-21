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

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <time.h>
#include <pthread.h>
#include <arpa/inet.h>   /* htons */

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

static gr_cp_module_t *g_modules[GR_CP_MAX_MODULES];
static int             g_nmod = 0;
static cp_timer_t      g_timers[GR_CP_MAX_TIMERS];
static int             g_next_timer_id = 1;
static cp_pkt_t       *g_qhead = NULL, *g_qtail = NULL;
static pthread_mutex_t cp_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t  cp_cond = PTHREAD_COND_INITIALIZER;
static int             g_running = 0;

static long long now_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
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

static void dispatch(gpacket_t *pkt)
{
    int i;
    for (i = 0; i < g_nmod; i++)
        if (g_modules[i]->on_packet && filter_match(&g_modules[i]->filter, pkt))
            g_modules[i]->on_packet(g_modules[i], pkt);
}

/* ---- the control thread -------------------------------------------------- */
static void *gr_cp_loop(void *unused)
{
    (void)unused;
    pthread_setcanceltype(PTHREAD_CANCEL_ASYNCHRONOUS, NULL);
    pthread_mutex_lock(&cp_lock);
    g_running = 1;
    while (g_running)
    {
        long long now = now_ms();
        long long next = now + 1000;   /* cap the idle wait at 1s */

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

        /* detach the inbound packet queue */
        cp_pkt_t *pkts = g_qhead;
        g_qhead = g_qtail = NULL;

        pthread_mutex_unlock(&cp_lock);

        for (i = 0; i < ndue; i++)
            if (due[i].cb) due[i].cb(due[i].owner, due[i].arg);
        while (pkts)
        {
            cp_pkt_t *n = pkts; pkts = pkts->next;
            dispatch(&n->pkt);
            free(n);
        }

        pthread_mutex_lock(&cp_lock);
        if (g_qhead == NULL)           /* nothing new arrived while we worked */
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
extern gr_cp_module_t *gr_cp_hello_create(void);   /* the B2.3 demo module */
extern gr_cp_module_t *gr_cp_dhcp_create(void);    /* B2.4 DHCP server */
extern gr_cp_module_t *gr_cp_rip_create(void);     /* B2.4 distance-vector routing */
extern gr_cp_module_t *gr_cp_igmp_create(void);    /* B3 IGMP snooping */
#ifdef GR_LUA
extern gr_cp_module_t *gr_cp_lua_create(void);     /* control-plane Lua: routing in Lua */
#endif

typedef struct { const char *name; gr_cp_module_t *(*ctor)(void); } gr_cp_reg_t;

static const gr_cp_reg_t CP_REGISTRY[] = {
    { "hello", gr_cp_hello_create },
    { "dhcp",  gr_cp_dhcp_create  },
    { "rip",   gr_cp_rip_create   },
    { "igmp",  gr_cp_igmp_create  },
#ifdef GR_LUA
    { "lua",   gr_cp_lua_create   },   /* gpipe cp add lua <script> — implement a protocol */
#endif
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

int gr_cp_add(const char *name, const char *args, char *out, size_t outlen)
{
    unsigned i;
    gr_cp_module_t *m = NULL;

    if (g_nmod >= GR_CP_MAX_MODULES)
    {
        snprintf(out, outlen, "control plane full (%d modules)", g_nmod);
        return -1;
    }
    for (i = 0; i < sizeof(CP_REGISTRY) / sizeof(CP_REGISTRY[0]); i++)
        if (strcmp(name, CP_REGISTRY[i].name) == 0) { m = CP_REGISTRY[i].ctor(); break; }
    if (!m)
    {
        snprintf(out, outlen, "unknown control module: %s   (have: %s)", name, gr_cp_names());
        return -1;
    }
    if (m->start && m->start(m, &SERVICES, args) != 0)   /* may register timers (safe) */
    {
        snprintf(out, outlen, "cp add %s: start failed", name);
        if (m->stop) m->stop(m);
        free(m);
        return -1;
    }
    pthread_mutex_lock(&cp_lock);
    g_modules[g_nmod++] = m;
    pthread_cond_signal(&cp_cond);
    pthread_mutex_unlock(&cp_lock);
    snprintf(out, outlen, "control module '%s' started", name);
    return 0;
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
        n = snprintf(out, outlen, "control modules (%d):", g_nmod);
        for (i = 0; i < g_nmod && n < (int)outlen; i++)
            n += snprintf(out + n, outlen - n, " %s", g_modules[i]->name);
    }
    pthread_mutex_unlock(&cp_lock);
    return 0;
}
