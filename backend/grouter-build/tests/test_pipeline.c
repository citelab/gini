/*
 * test_pipeline.c  —  Z2 runnable test of the module-graph runner (libc only).
 * Builds packets, runs them through an ACL + counter pipeline, checks verdicts.
 */
#include "gr_pipeline.h"
#include "gr_modules.h"
#include <stdio.h>
#include <string.h>

static void set_dst(gpacket_t *pkt, int a, int b, int c, int d)
{
    memset(pkt, 0, sizeof(*pkt));
    pkt->data.data[16] = (unsigned char)a;
    pkt->data.data[17] = (unsigned char)b;
    pkt->data.data[18] = (unsigned char)c;
    pkt->data.data[19] = (unsigned char)d;
}

int main(void)
{
    int fail = 0;
    gr_pipeline_t pl;
    gr_pipeline_init(&pl);
    gr_module_t *acl = gr_mod_acl("10.0.3.0/24");
    gr_module_t *cnt = gr_mod_counter();
    gr_pipeline_add(&pl, acl);     /* drop onto base: ACL ... */
    gr_pipeline_add(&pl, cnt);     /* ... then counter         */

    gpacket_t pkt;

    /* denied destination -> DROP; counter must NOT advance (terminal before it) */
    set_dst(&pkt, 10, 0, 3, 10);
    gr_verdict_t v = gr_pipeline_run(&pl, &pkt);
    printf("dst 10.0.3.10 (denied) -> %s\n", v.action == GR_DROP ? "DROP" : "??");
    fail |= (v.action != GR_DROP);
    if (gr_mod_counter_value(cnt) != 0) { printf("  ! counter advanced on dropped pkt\n"); fail = 1; }

    /* allowed destination -> CONTINUE through; counter increments */
    set_dst(&pkt, 10, 0, 9, 9);
    v = gr_pipeline_run(&pl, &pkt);
    printf("dst 10.0.9.9  (allowed) -> %s, counter=%ld\n",
           v.action == GR_CONTINUE ? "CONTINUE (-> base forwarding)" : "??",
           gr_mod_counter_value(cnt));
    fail |= (v.action != GR_CONTINUE);
    fail |= (gr_mod_counter_value(cnt) != 1);

    /* reorder check: counter FIRST sees every packet, even denied ones */
    gr_pipeline_t pl2; gr_pipeline_init(&pl2);
    gr_module_t *cnt2 = gr_mod_counter();
    gr_pipeline_add(&pl2, cnt2);
    gr_pipeline_add(&pl2, gr_mod_acl("10.0.3.0/24"));
    set_dst(&pkt, 10, 0, 3, 10);
    v = gr_pipeline_run(&pl2, &pkt);
    printf("reordered [counter, acl], denied pkt -> %s, counter=%ld\n",
           v.action == GR_DROP ? "DROP" : "??", gr_mod_counter_value(cnt2));
    fail |= (v.action != GR_DROP);
    fail |= (gr_mod_counter_value(cnt2) != 1);   /* counter ran before the drop */

    /* NAT module rewrites the source IP (offset 12..15) and CONTINUEs */
    gr_pipeline_t pl3; gr_pipeline_init(&pl3);
    gr_pipeline_add(&pl3, gr_mod_nat("203.0.113.7"));
    set_dst(&pkt, 10, 0, 9, 9);
    pkt.data.data[12] = 10; pkt.data.data[13] = 0; pkt.data.data[14] = 1; pkt.data.data[15] = 5;  /* orig src */
    v = gr_pipeline_run(&pl3, &pkt);
    printf("nat -> %s, new src %u.%u.%u.%u\n",
           v.action == GR_CONTINUE ? "CONTINUE" : "??",
           pkt.data.data[12], pkt.data.data[13], pkt.data.data[14], pkt.data.data[15]);
    fail |= (v.action != GR_CONTINUE);
    fail |= !(pkt.data.data[12] == 203 && pkt.data.data[13] == 0 &&
              pkt.data.data[14] == 113 && pkt.data.data[15] == 7);

    gr_pipeline_clear(&pl);
    gr_pipeline_clear(&pl2);
    gr_pipeline_clear(&pl3);

    printf("\nZ2 pipeline runner: %s\n", fail ? "FAIL" : "ALL PASS");
    return fail;
}
