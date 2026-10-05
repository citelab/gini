/*
 * gr_watch.c — packet watch ring + `watch` and `ifstat` commands. See gr_watch.h for the why.
 *
 * Wire format (one line each, read by gini.domain.router_watch):
 *
 *   watch dump <since>
 *     WATCH <on|off> <next_seq> <ms_now>
 *     W <seq> <ms> <fate> <in_if> <out_if> <module_idx> <tag|-> <src> <dst> <proto> <ttl> <len> <sport> <dport>
 *
 *   <tag> is the dropping module's type for a D, the outcome for an ARP (answered/learned/
 *   ignored), and "-" otherwise. For ICMP, sport/dport carry the ICMP type and code; for ARP
 *   (proto 2054), sport is the opcode.
 *
 *   ifstat
 *     IFSTAT <id> <name> <ip> <rx_bytes> <rx_pkts> <tx_bytes> <tx_pkts>
 *
 * Addresses print from the packet's WIRE bytes in order (a.b.c.d), not through the router's
 * reversed-word helpers, so there is one convention to get right rather than two.
 *
 * The slot is claimed with ONE atomic add. The xv6 trap ring did `slot = i % N; ...; i++` and two
 * cores landing together shared a slot and skipped the next, which then printed as a phantom
 * all-zero trap. Here every packet owns its own slot. A reader can still meet a slot mid-write,
 * so `seq` is written LAST, after a barrier, and a dump skips any slot whose seq is not the one it
 * expects: a torn entry is left out rather than drawn.
 */
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <time.h>
#include <arpa/inet.h>

#include "grouter.h"
#include "gnet.h"
#include "ip.h"
#include "message.h"
#include "gr_pipeline.h"
#include "gr_watch.h"

volatile int gr_watch_enabled = 0;

extern interface_array_t netarray;                 /* gnet.c; no header declares it */

typedef struct
{
    volatile unsigned long long seq;    /* 0 = never written; written LAST */
    unsigned long long ms;
    char  fate;
    short in_if, out_if, module;
    char  mtype[16];
    uchar src[4], dst[4];
    uchar proto, ttl;
    unsigned short len, sport, dport;
    uchar arp;                                     /* 1 for an ARP entry: printed as proto 2054 */
} gr_watch_ent_t;

static gr_watch_ent_t ring[GR_WATCH_N];
static volatile unsigned long long next_seq = 0;   /* seqs start at 1 */

static unsigned long long now_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (unsigned long long)ts.tv_sec * 1000ULL + (unsigned long long)(ts.tv_nsec / 1000000L);
}

void gr_watch_note(gpacket_t *pkt, char fate, int out_if, int module)
{
    unsigned long long seq = __sync_add_and_fetch(&next_seq, 1ULL);
    gr_watch_ent_t *e = &ring[seq % GR_WATCH_N];
    ip_packet_t *ip = (ip_packet_t *)pkt->data.data;
    int hl = (ip->ip_hdr_len & 0x0f) * 4;

    e->seq = 0;                                    /* mark in-progress for a concurrent reader */
    __sync_synchronize();
    e->ms = now_ms();
    e->fate = fate;
    /* A packet the router SENDS did not arrive anywhere. Its reply reuses the incoming packet's
     * buffer, so src_interface still names where the request came in -- not this packet. */
    e->in_if = (short)(fate == GW_SENT ? -1 : pkt->frame.src_interface);
    e->out_if = (short)out_if;
    e->module = (short)module;
    e->mtype[0] = '\0';
    if (module >= 0)
    {
        gr_pipeline_t *p = gr_default_pipeline();
        if (module < p->count && p->modules[module] && p->modules[module]->type)
        {
            strncpy(e->mtype, p->modules[module]->type, sizeof e->mtype - 1);
            e->mtype[sizeof e->mtype - 1] = '\0';
        }
    }
    memcpy(e->src, ip->ip_src, 4);
    memcpy(e->dst, ip->ip_dst, 4);
    e->proto = ip->ip_prot;
    e->arp = 0;
    e->ttl = ip->ip_ttl;
    e->len = ntohs(ip->ip_pkt_len);
    e->sport = e->dport = 0;
    if ((ip->ip_prot == 6 || ip->ip_prot == 17) && hl >= 20)
    {
        uchar *l4 = (uchar *)ip + hl;              /* TCP and UDP both lead with the two ports */
        e->sport = (unsigned short)((l4[0] << 8) | l4[1]);
        e->dport = (unsigned short)((l4[2] << 8) | l4[3]);
    }
    else if (ip->ip_prot == 1 && hl >= 20)
    {
        uchar *l4 = (uchar *)ip + hl;              /* ICMP: type, then code */
        e->sport = l4[0];
        e->dport = l4[1];
    }
    __sync_synchronize();                          /* publish the fields BEFORE the seq */
    e->seq = seq;
}

void gr_watch_arp(char fate, int in_if, int out_if, int op, const unsigned char *sender,
                  const unsigned char *target, const char *outcome)
{
    unsigned long long seq = __sync_add_and_fetch(&next_seq, 1ULL);
    gr_watch_ent_t *e = &ring[seq % GR_WATCH_N];

    e->seq = 0;
    __sync_synchronize();
    e->ms = now_ms();
    e->fate = fate;
    e->in_if = (short)in_if;
    e->out_if = (short)out_if;
    e->module = -1;
    e->mtype[0] = '\0';
    if (outcome && outcome[0])
    {
        strncpy(e->mtype, outcome, sizeof e->mtype - 1);
        e->mtype[sizeof e->mtype - 1] = '\0';
    }
    memcpy(e->src, sender, 4);
    memcpy(e->dst, target, 4);
    e->proto = 0;                                  /* uchar: ARP is flagged by `arp`, below */
    e->ttl = 0;
    e->len = 28;                                   /* an Ethernet/IPv4 ARP body */
    e->sport = (unsigned short)op;
    e->dport = 0;
    e->arp = 1;
    __sync_synchronize();
    e->seq = seq;
}

static void watch_dump(unsigned long long since)
{
    unsigned long long top = next_seq, s;
    unsigned long long lo = (top > GR_WATCH_N) ? top - GR_WATCH_N + 1 : 1;
    if (since + 1 > lo)
        lo = since + 1;
    printf("WATCH %s %llu %llu\n", gr_watch_enabled ? "on" : "off", top, now_ms());
    for (s = lo; s <= top; s++)
    {
        gr_watch_ent_t *e = &ring[s % GR_WATCH_N];
        gr_watch_ent_t c;
        memcpy(&c, (const void *)e, sizeof c);
        __sync_synchronize();
        if (c.seq != s || e->seq != s)             /* torn or already overwritten: leave it out */
            continue;
        printf("W %llu %llu %c %d %d %d %s %d.%d.%d.%d %d.%d.%d.%d %u %u %u %u %u\n",
               c.seq, c.ms, c.fate, c.in_if, c.out_if, c.module, c.mtype[0] ? c.mtype : "-",
               c.src[0], c.src[1], c.src[2], c.src[3], c.dst[0], c.dst[1], c.dst[2], c.dst[3],
               c.arp ? GW_PROTO_ARP : c.proto, c.ttl, c.len, c.sport, c.dport);
    }
}

/*
 * watch on | off | show | dump [since]
 */
void watchCmd(void)
{
    char *tok = strtok(NULL, " \n");
    if (tok == NULL || !strcmp(tok, "show"))
    {
        printf("packet watch is %s (%llu packets recorded since start)\n",
               gr_watch_enabled ? "ON" : "off", next_seq);
        return;
    }
    if (!strcmp(tok, "on"))       { gr_watch_enabled = 1; printf("packet watch on\n"); return; }
    if (!strcmp(tok, "off"))      { gr_watch_enabled = 0; printf("packet watch off\n"); return; }
    if (!strcmp(tok, "dump"))
    {
        char *a = strtok(NULL, " \n");
        watch_dump(a ? strtoull(a, NULL, 10) : 0ULL);
        return;
    }
    printf("usage: watch on | off | show | dump [since_seq]\n");
}

/*
 * ifstat — cumulative per-interface counters; the Lab turns two readings into bits per second.
 */
void ifstatCmd(void)
{
    int i;
    for (i = 0; i < MAX_INTERFACES; i++)
    {
        interface_t *f = netarray.elem[i];
        if (f == NULL)
            continue;
        printf("IFSTAT %d %s %d.%d.%d.%d %llu %llu %llu %llu\n", f->interface_id, f->device_name,
               f->ip_addr[3], f->ip_addr[2], f->ip_addr[1], f->ip_addr[0],
               f->rx_bytes, f->rx_pkts, f->tx_bytes, f->tx_pkts);
    }
}
