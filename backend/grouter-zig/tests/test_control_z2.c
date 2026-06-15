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

    printf("Z2 control surface: %s\n", fail ? "FAIL" : "ALL PASS");
    return fail;
}
