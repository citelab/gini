/*
 * sender.c — STARTER SKELETON for the Multicast File Distribution capstone.
 *
 *     ./sender [-c chunksize] file1 [file2 ...]
 *
 * The carousel scaffolding is here and it compiles; the TODOs are the assignment.
 * Build on a station:  gcc -O2 -o sender /shared/multicast_fs/sender.c \
 *                          /shared/multicast_fs/multicast.c
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "multicast.h"

#define GROUP     "239.1.1.1"
#define PORT      5000
#define MAX_FILES 16

typedef struct {
    char     name[128];
    uint8_t  id;
    long     size;
    uint32_t total_chunks;
    uint32_t file_checksum;       /* mc_checksum over the whole file            */
    unsigned char *data;          /* the file, in memory                        */
} mc_file_t;

static mc_file_t files[MAX_FILES];
static int       nfiles = 0;
static int       chunk_size = 1024;

/* statistics — print them at the end of every pass */
static long stat_data_pkts = 0, stat_meta_pkts = 0, stat_bytes = 0, stat_passes = 0;

static int load_file(const char *path, uint8_t id)
{
    FILE *fp = fopen(path, "rb");
    if (!fp) { perror(path); return -1; }
    mc_file_t *f = &files[nfiles];
    fseek(fp, 0, SEEK_END); f->size = ftell(fp); fseek(fp, 0, SEEK_SET);
    f->data = (unsigned char *)malloc((size_t)f->size);
    if (fread(f->data, 1, (size_t)f->size, fp) != (size_t)f->size) { fclose(fp); return -1; }
    fclose(fp);
    snprintf(f->name, sizeof f->name, "%s", path);
    f->id = id;
    f->total_chunks  = (uint32_t)((f->size + chunk_size - 1) / chunk_size);
    f->file_checksum = mc_checksum(f->data, (size_t)f->size);
    nfiles++;
    printf("loaded %-20s  %ld bytes  %u chunks  cksum %08x\n",
           f->name, f->size, f->total_chunks, f->file_checksum);
    return 0;
}

/* Announce the session: one META packet carrying the file table, so a receiver that
 * joins at any moment learns what exists and how big it is. */
static void send_file_table(mcast_t *m)
{
    unsigned char pkt[MC_MAX_PKT];
    mc_hdr_t *h = (mc_hdr_t *)pkt;
    char     *p = (char *)(pkt + sizeof *h);
    int i, n = 0;

    /* a simple text payload: one line per file "id name size chunks checksum" */
    for (i = 0; i < nfiles; i++)
        n += snprintf(p + n, sizeof pkt - sizeof *h - n, "%u %s %ld %u %u\n",
                      files[i].id, files[i].name, files[i].size,
                      files[i].total_chunks, files[i].file_checksum);

    memset(h, 0, sizeof *h);
    h->type      = MC_META;
    h->chunk_len = (uint16_t)n;
    h->checksum  = mc_checksum(p, (size_t)n);
    multicast_send(m, pkt, (int)(sizeof *h + n));
    stat_meta_pkts++;
}

static void send_chunk(mcast_t *m, mc_file_t *f, uint32_t seq)
{
    unsigned char pkt[MC_MAX_PKT];
    mc_hdr_t *h = (mc_hdr_t *)pkt;
    long off = (long)seq * chunk_size;
    int  len = (int)((f->size - off < chunk_size) ? (f->size - off) : chunk_size);

    /* TODO 1: fill in the header — type, file_id, seq, total_chunks, chunk_len,
     *         and the checksum of THIS chunk's bytes (mc_checksum).            */
    (void)m; (void)h; (void)off; (void)len;

    /* TODO 2: copy the chunk's bytes after the header and multicast_send() it.
     *         Update stat_data_pkts and stat_bytes.                             */
}

int main(int argc, char **argv)
{
    int i, opt;
    while ((opt = getopt(argc, argv, "c:")) != -1)
        if (opt == 'c') chunk_size = atoi(optarg);
    if (chunk_size < 64 || chunk_size > MC_MAX_CHUNK) {
        fprintf(stderr, "chunk size must be 64..%d\n", (int)MC_MAX_CHUNK);
        return 1;
    }
    if (optind >= argc) {
        fprintf(stderr, "usage: %s [-c chunksize] file1 [file2 ...]\n", argv[0]);
        return 1;
    }
    for (i = optind; i < argc && nfiles < MAX_FILES; i++)
        if (load_file(argv[i], (uint8_t)nfiles) != 0) return 1;

    mcast_t *m = multicast_init(GROUP, PORT, PORT);
    if (!m) { fprintf(stderr, "multicast_init failed\n"); return 1; }

    printf("session up: %d file(s), chunk %d bytes, group %s:%d\n",
           nfiles, chunk_size, GROUP, PORT);

    for (;;) {                                    /* one pass of the carousel */
        send_file_table(m);
        for (i = 0; i < nfiles; i++) {
            uint32_t s;
            for (s = 0; s < files[i].total_chunks; s++) {
                send_chunk(m, &files[i], s);
                /* TODO 3: pace yourself — a short usleep between packets keeps the
                 *         router's queue from overflowing (see the Congestion
                 *         Control and Fair Queuing chapter for what happens if
                 *         you do not).                                          */
            }
            /* TODO 4 (design choice): interleave chunks of different files
             *         instead of sending file after file? Justify in your report. */
        }
        stat_passes++;
        printf("pass %ld done: %ld data pkts, %ld meta pkts, %ld bytes\n",
               stat_passes, stat_data_pkts, stat_meta_pkts, stat_bytes);
    }
}
