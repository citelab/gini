/*
 * receiver.c — STARTER SKELETON for the Multicast File Distribution capstone.
 *
 *     ./receiver
 *
 * Joins the group, learns the session from META packets, collects chunks, verifies,
 * and places completed files in received_files/. The scaffolding compiles; the TODOs
 * are the assignment.
 * Build on a station:  gcc -O2 -o receiver /shared/multicast_fs/receiver.c \
 *                          /shared/multicast_fs/multicast.c
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include "multicast.h"

#define GROUP     "239.1.1.1"
#define PORT      5000
#define MAX_FILES 16

typedef struct {
    char      name[128];
    long      size;
    uint32_t  total_chunks;
    uint32_t  file_checksum;
    int       known;              /* learned from a META packet                  */
    int       complete;           /* verified and written out                    */
    unsigned char *data;          /* reassembly buffer (or write to disk — your  */
                                  /* choice; argue it in the report)             */
    unsigned char *have;          /* bitmap: have[seq] = 1 once chunk seq landed */
    uint32_t  have_count;
} rx_file_t;

static rx_file_t files[MAX_FILES];

/* statistics — the assignment asks you to track and report these */
static long stat_pkts = 0, stat_bad = 0, stat_dup = 0, stat_bytes = 0;

static void on_meta(const char *table, int len)
{
    /* The file table is text: one line per file "id name size chunks checksum".
     * TODO 1: parse it; for each new id, fill files[id] (name/size/total_chunks/
     *         file_checksum), allocate data and have, and set known = 1.        */
    (void)table; (void)len;
}

static void on_data(const mc_hdr_t *h, const unsigned char *payload)
{
    rx_file_t *f;
    if (h->file_id >= MAX_FILES) return;
    f = &files[h->file_id];
    if (!f->known || f->complete) return;

    /* TODO 2: discard a corrupted chunk: recompute mc_checksum over the payload
     *         and compare with h->checksum (count it in stat_bad). The carousel
     *         will bring the chunk again.                                       */

    /* TODO 3: if the bitmap says this seq is new, store the payload at offset
     *         seq * chunk-size, set the bit, bump have_count (else stat_dup).   */

    /* TODO 4: when have_count == total_chunks, verify the WHOLE file with
     *         mc_checksum against f->file_checksum; on success write it to
     *         received_files/<name>, set complete = 1, and print a report line.
     *         On mismatch: decide (and defend) a recovery strategy.             */
    (void)payload;
}

static int all_complete(void)
{
    int i, any = 0;
    for (i = 0; i < MAX_FILES; i++) {
        if (files[i].known) any = 1;
        if (files[i].known && !files[i].complete) return 0;
    }
    return any;
}

int main(void)
{
    unsigned char pkt[MC_MAX_PKT];

    mkdir("received_files", 0755);
    mcast_t *m = multicast_init(GROUP, PORT, PORT);
    if (!m) { fprintf(stderr, "multicast_init failed\n"); return 1; }
    multicast_setup_recv(m);                  /* the IGMP join happens here */
    printf("joined %s:%d — waiting for the session\n", GROUP, PORT);

    while (!all_complete()) {
        int n = multicast_receive(m, pkt, sizeof pkt);
        if (n < (int)sizeof(mc_hdr_t)) continue;
        stat_pkts++; stat_bytes += n;

        mc_hdr_t *h = (mc_hdr_t *)pkt;
        const unsigned char *payload = pkt + sizeof *h;
        if ((int)(sizeof *h + h->chunk_len) > n) { stat_bad++; continue; }

        if (h->type == MC_META)      on_meta((const char *)payload, h->chunk_len);
        else if (h->type == MC_DATA) on_data(h, payload);
    }

    printf("all files complete: %ld pkts (%ld bytes), %ld corrupted, %ld duplicates\n",
           stat_pkts, stat_bytes, stat_bad, stat_dup);
    multicast_close(m);
    return 0;
}
