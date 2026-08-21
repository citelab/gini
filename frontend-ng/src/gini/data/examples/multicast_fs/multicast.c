/*
 * multicast.c — provided starter code (see multicast.h). Complete; nothing to change.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/socket.h>
#include <sys/select.h>
#include <netinet/in.h>
#include <arpa/inet.h>

#include "multicast.h"

struct mcast {
    int                sock;
    struct sockaddr_in send_addr;     /* group:send_port                  */
    struct in_addr     group;
    int                recv_port;
};

mcast_t *multicast_init(const char *group, int send_port, int recv_port)
{
    mcast_t *m = (mcast_t *)calloc(1, sizeof *m);
    if (!m) return NULL;

    m->sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (m->sock < 0) { free(m); return NULL; }

    int yes = 1;
    setsockopt(m->sock, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof yes);

    if (inet_aton(group, &m->group) == 0) { close(m->sock); free(m); return NULL; }
    m->recv_port = recv_port;

    m->send_addr.sin_family = AF_INET;
    m->send_addr.sin_addr   = m->group;
    m->send_addr.sin_port   = htons((uint16_t)send_port);

    /* enough hops to cross the routers of a GINI multi-LAN; the multicast tree
     * (who joined, where) is what actually bounds delivery */
    unsigned char ttl = 8;
    setsockopt(m->sock, IPPROTO_IP, IP_MULTICAST_TTL, &ttl, sizeof ttl);

    return m;
}

void multicast_setup_recv(mcast_t *m)
{
    struct sockaddr_in any;
    memset(&any, 0, sizeof any);
    any.sin_family      = AF_INET;
    any.sin_addr.s_addr = htonl(INADDR_ANY);
    any.sin_port        = htons((uint16_t)m->recv_port);
    if (bind(m->sock, (struct sockaddr *)&any, sizeof any) < 0)
        perror("multicast: bind");

    struct ip_mreq mreq;
    mreq.imr_multiaddr        = m->group;
    mreq.imr_interface.s_addr = htonl(INADDR_ANY);
    if (setsockopt(m->sock, IPPROTO_IP, IP_ADD_MEMBERSHIP, &mreq, sizeof mreq) < 0)
        perror("multicast: join");
}

int multicast_check_receive(mcast_t *m)
{
    fd_set rf;
    struct timeval tv = { 0, 0 };
    FD_ZERO(&rf);
    FD_SET(m->sock, &rf);
    return select(m->sock + 1, &rf, NULL, NULL, &tv);
}

int multicast_receive(mcast_t *m, void *buf, int len)
{
    return (int)recvfrom(m->sock, buf, (size_t)len, 0, NULL, NULL);
}

int multicast_send(mcast_t *m, const void *buf, int len)
{
    int n = (int)sendto(m->sock, buf, (size_t)len, 0,
                        (struct sockaddr *)&m->send_addr, sizeof m->send_addr);
    if (n < 0) perror("multicast: send");
    return n;
}

void multicast_close(mcast_t *m)
{
    if (!m) return;
    close(m->sock);
    free(m);
}

uint32_t mc_checksum(const void *buf, size_t len)
{
    const unsigned char *p = (const unsigned char *)buf;
    uint32_t h = 2166136261u;               /* FNV-1a */
    size_t i;
    for (i = 0; i < len; i++) {
        h ^= p[i];
        h *= 16777619u;
    }
    return h;
}
