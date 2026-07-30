/* Z2 control-surface test (libc only): drives gr_control() like the CLI / Router Lab. */
#include "gr_control.h"
#include <stdio.h>
#include <string.h>

static void run(const char *cmd)
{
    char out[512];
    gr_control(cmd, out, sizeof out);
    printf("> %s\n%s\n\n", cmd, out);
}

int main(void)
{
    int fail = 0;
    char out[512];

    run("clear");
    run("add acl 10.0.3.0/24");
    run("add counter");
    run("list");

    gr_control("list", out, sizeof out);
    if (!strstr(out, "[0:acl]") || !strstr(out, "[1:counter]")) fail = 1;

    run("trace 10.0.3.10");                 /* denied -> ACL DROP, counter not reached */
    gr_control("trace 10.0.3.10", out, sizeof out);
    if (!strstr(out, "acl") || !strstr(out, "DROP")) fail = 1;

    run("trace 10.0.9.9");                  /* allowed -> CONTINUE -> base forwarding   */
    gr_control("trace 10.0.9.9", out, sizeof out);
    if (!strstr(out, "base forwarding")) fail = 1;

    /* native module written in C (gr_mod_block), added through the registry */
    run("clear");
    run("add block 10.0.5.5");
    gr_control("list", out, sizeof out);
    if (!strstr(out, "[0:block]")) fail = 1;
    run("trace 10.0.5.5");                   /* matches -> DROP */
    gr_control("trace 10.0.5.5", out, sizeof out);
    if (!strstr(out, "block") || !strstr(out, "DROP")) fail = 1;
    run("trace 10.0.9.9");                   /* no match -> CONTINUE -> base forwarding */
    gr_control("trace 10.0.9.9", out, sizeof out);
    if (!strstr(out, "base forwarding")) fail = 1;

    /* registry still serves the built-ins (nat here) */
    run("clear");
    run("add nat 203.0.113.1");
    gr_control("list", out, sizeof out);
    if (!strstr(out, "[0:nat]")) fail = 1;

    /* arg-required guard: 'add block' with no IP must be rejected, not crash */
    gr_control("add block", out, sizeof out);
    if (!strstr(out, "usage")) fail = 1;

    /* rate policer (token bucket): 1 pps / burst 1 -> first passes, immediate 2nd policed */
    run("clear");
    run("add rate 1/1");
    gr_control("list", out, sizeof out);
    if (!strstr(out, "[0:rate]")) fail = 1;
    gr_control("trace 10.0.9.9", out, sizeof out);   /* bucket full -> forwards */
    if (!strstr(out, "base forwarding")) fail = 1;
    gr_control("trace 10.0.9.9", out, sizeof out);   /* immediate 2nd -> over rate -> DROP */
    if (!strstr(out, "rate") || !strstr(out, "DROP")) fail = 1;

    /* QoS classifier: marks a DSCP and always CONTINUEs (a marker never drops) */
    run("clear");
    run("add classify 10.0.3.0/24:ef");
    gr_control("list", out, sizeof out);
    if (!strstr(out, "[0:classify]")) fail = 1;
    gr_control("trace 10.0.3.10", out, sizeof out);  /* match -> mark -> CONTINUE -> base */
    if (!strstr(out, "base forwarding")) fail = 1;

    /* tap / capture: writes a pcap and always CONTINUEs */
    run("clear");
    run("add tap /tmp/gini_test_tap.pcap");
    gr_control("list", out, sizeof out);
    if (!strstr(out, "[0:tap]")) fail = 1;
    gr_control("trace 10.0.1.2", out, sizeof out);
    if (!strstr(out, "base forwarding")) fail = 1;

    printf("Z2 control surface: %s\n", fail ? "FAIL" : "ALL PASS");
    return fail;
}
