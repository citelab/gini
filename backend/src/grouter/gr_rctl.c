/*
 * gr_rctl.c — remote control socket: the real gRouter CLI over a Unix socket.
 *
 * The router runs as a daemon (stdin = /dev/null), so its interactive CLI isn't
 * reachable. This exposes the SAME CLI dispatch (parseACLICmd) over a socket: a
 * console client sends a command line, we run it while capturing its printed output
 * to a temp file, and send that output back. Reuses every existing command
 * (ifconfig / route / arp / queue / qdisc / class / filter / openflow / gpipe …).
 *
 * Output capture: stdout (fd 1) is redirected to a tmpfile around the command, under
 * a mutex, with verbosity briefly silenced so packet-path logging doesn't bleed in.
 */
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/socket.h>
#include <sys/un.h>

#include <slack/prog.h>

#include "cli.h"        /* parseACLICmd */
#include "gr_rctl.h"

#define RCTL_MAX   65536
#define RCTL_END   "\n__END__\n"

static pthread_mutex_t rctl_lock = PTHREAD_MUTEX_INITIALIZER;
static char rctl_path[256];

int gr_rctl_exec(const char *line, char *out, size_t outlen)
{
    int saved_fd, n = 0;
    long saved_v;
    FILE *tmp;
    char *cmd;

    if (out && outlen) out[0] = '\0';
    if (!line || !*line) return 0;

    cmd = strdup(line);                 /* parseACLICmd tokenizes in place */
    if (!cmd) return 0;

    pthread_mutex_lock(&rctl_lock);

    saved_v = prog_verbosity_level();
    prog_set_verbosity_level(0);        /* hush packet-path logging during capture */

    fflush(stdout);
    saved_fd = dup(1);
    tmp = tmpfile();
    if (tmp) {
        dup2(fileno(tmp), 1);
        parseACLICmd(cmd);              /* the real CLI handler runs here */
        fflush(stdout);
        dup2(saved_fd, 1);
        if (out && outlen) {
            rewind(tmp);
            n = (int)fread(out, 1, outlen - 1, tmp);
            if (n < 0) n = 0;
            out[n] = '\0';
        }
        fclose(tmp);
    } else {
        parseACLICmd(cmd);              /* no capture available; still run it */
    }
    if (saved_fd >= 0) close(saved_fd);

    prog_set_verbosity_level(saved_v);
    pthread_mutex_unlock(&rctl_lock);

    free(cmd);
    return n;
}

static void serve_client(int fd)
{
    char line[4096];
    char out[RCTL_MAX];
    int pos = 0, k;
    char c;

    while ((k = read(fd, &c, 1)) == 1) {
        if (c == '\n' || pos >= (int)sizeof(line) - 1) {
            line[pos] = '\0';
            pos = 0;
            if (line[0]) {
                gr_rctl_exec(line, out, sizeof out);
                write(fd, out, strlen(out));
            }
            write(fd, RCTL_END, strlen(RCTL_END));
        } else {
            line[pos++] = c;
        }
    }
}

static void *accept_loop(void *arg)
{
    int srv = (int)(long)arg;
    pthread_setcanceltype(PTHREAD_CANCEL_ASYNCHRONOUS, NULL);
    for (;;) {
        int cl = accept(srv, NULL, NULL);
        if (cl < 0) continue;
        serve_client(cl);
        close(cl);
    }
    return NULL;
}

int gr_rctl_start(const char *sock_path)
{
    int srv;
    struct sockaddr_un addr;
    pthread_t tid;

    if (!sock_path || !*sock_path) return -1;
    strncpy(rctl_path, sock_path, sizeof rctl_path - 1);

    srv = socket(AF_UNIX, SOCK_STREAM, 0);
    if (srv < 0) return -1;

    unlink(sock_path);                  /* clear any stale socket */
    memset(&addr, 0, sizeof addr);
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, sock_path, sizeof addr.sun_path - 1);
    if (bind(srv, (struct sockaddr *)&addr, sizeof addr) < 0) {
        close(srv);
        return -1;
    }
    if (listen(srv, 4) < 0) {
        close(srv);
        return -1;
    }
    if (pthread_create(&tid, NULL, accept_loop, (void *)(long)srv) != 0) {
        close(srv);
        return -1;
    }
    pthread_detach(tid);
    return 0;
}
