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

/*
 * Verbosity while a console command is running.
 *
 * gr_rctl_exec() drops the level to 0 around each command so packet-path logging
 * doesn't bleed into the captured output, then restores it. That broke `set verbose`
 * from the console in BOTH directions: a read reported the temporary 0 instead of the
 * real level, and a write was silently undone by the restore a moment later.
 *
 * So the effective level lives here while a command is in flight. >= 0 means "inside a
 * hushed command, and this is the level the router should be at"; -1 means no command
 * is running and prog_verbosity_level() is authoritative. cli.c reads and writes
 * verbosity through the two accessors below rather than touching libslack directly.
 */
static long rctl_effective_v = -1;

long gr_rctl_verbosity(void)
{
    return (rctl_effective_v >= 0) ? rctl_effective_v : prog_verbosity_level();
}

void gr_rctl_set_verbosity(long v)
{
    if (rctl_effective_v >= 0)
        rctl_effective_v = v;           /* applied by the restore in gr_rctl_exec() */
    else
        prog_set_verbosity_level(v);    /* interactive CLI: take effect immediately */
}

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
    rctl_effective_v = saved_v;         /* what a `set verbose` read/write sees */
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

    /* Restore the level the command asked for, which may differ from what we saved
     * if it was itself a `set verbose N`. Restoring saved_v unconditionally is what
     * made that command a no-op from the console. */
    prog_set_verbosity_level(rctl_effective_v);
    rctl_effective_v = -1;
    pthread_mutex_unlock(&rctl_lock);

    free(cmd);
    return n;
}

/* Send fully, without ever raising SIGPIPE. A client that vanished mid-reply (a
 * timed-out docker exec, a killed console) used to hit plain write() -> SIGPIPE ->
 * the DEFAULT ACTION TERMINATES THE ROUTER. MSG_NOSIGNAL turns that into an error
 * return we handle by dropping the client; SO_SNDTIMEO (set per client) bounds a
 * reader that stopped consuming. The data plane must never die because a CLI
 * client misbehaved. */
static int send_all(int fd, const char *buf, size_t len)
{
    while (len > 0) {
        ssize_t k = send(fd, buf, len, MSG_NOSIGNAL);
        if (k <= 0)
            return -1;                  /* client gone / send timeout — drop it */
        buf += k;
        len -= (size_t)k;
    }
    return 0;
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
                if (send_all(fd, out, strlen(out)) < 0)
                    return;
            }
            if (send_all(fd, RCTL_END, strlen(RCTL_END)) < 0)
                return;
        } else {
            line[pos++] = c;
        }
    }
}

/* One detached thread per client. The old serial accept loop served a single client
 * to EOF before accepting the next, so one open console (or one leaked one-shot
 * query holding its connection) starved every other client forever: console dead,
 * HUD queries empty — while the data plane forwarded happily. Command EXECUTION is
 * still serialized by rctl_lock inside gr_rctl_exec, so concurrency here is safe. */
static void *client_thread(void *arg)
{
    int fd = (int)(long)arg;
    struct timeval rcv = { 3600, 0 };   /* reap leaked/idle clients after an hour */
    struct timeval snd = { 10, 0 };     /* a stuck reader can't hold a thread forever */

    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &rcv, sizeof rcv);
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &snd, sizeof snd);
    serve_client(fd);
    close(fd);
    return NULL;
}

static void *accept_loop(void *arg)
{
    int srv = (int)(long)arg;
    pthread_t ct;
    pthread_setcanceltype(PTHREAD_CANCEL_ASYNCHRONOUS, NULL);
    for (;;) {
        int cl = accept(srv, NULL, NULL);
        if (cl < 0) continue;
        if (pthread_create(&ct, NULL, client_thread, (void *)(long)cl) == 0) {
            pthread_detach(ct);
        } else {                        /* thread spawn failed: serve inline (old way) */
            serve_client(cl);
            close(cl);
        }
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
