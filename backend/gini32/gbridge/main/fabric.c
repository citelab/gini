/* fabric.c -- a virtual Ethernet interface whose wire is a UDP socket.
 *
 * Outbound: lwIP hands us a pbuf -> flatten -> prepend the 24-byte G32 header ->
 *           sendto() the relay.
 * Inbound:  recvfrom() the relay -> validate header -> copy the Ethernet frame
 *           into a pbuf -> netif->input().
 *
 * Threading. netif->input is installed as tcpip_input, which posts the pbuf to
 * the lwIP TCP/IP thread rather than processing it inline, so it is safe to call
 * from our own RX task. linkoutput, by contrast, runs ON the tcpip thread, so it
 * must not block -- a UDP sendto on a non-blocking socket is fine.
 */
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <errno.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_netif.h"
#include "esp_wifi.h"          /* esp_wifi_get_mac / WIFI_IF_STA, for our identity */
#include "mdns.h"

#include "lwip/opt.h"
#include "lwip/netif.h"
#include "lwip/etharp.h"
#include "lwip/tcpip.h"
#include "lwip/sockets.h"
#include "lwip/ip4_addr.h"

#include "fabric.h"
#include "gbridge_config.h"

static const char *TAG = "fabric";

static struct netif s_netif;
static int s_sock = -1;
static struct sockaddr_in s_relay;
static char s_board_id[G32_ID_LEN + 1];
static bool s_up = false;          /* netif configured and running          */
static bool s_added = false;       /* netif_add already done                */
static fabric_netcfg_t s_cfg;
static uint32_t s_rx = 0, s_tx = 0, s_drop = 0;
static uint32_t s_seen[8];         /* datagrams received, by G32 message type */
static QueueHandle_t s_txq;        /* linkoutput -> sender task (see fabric_linkoutput) */
static int64_t s_last_ack_us = 0;

/* how we were told to find the relay ("auto" | "x.local" | a literal address) */
static char s_server_setting[64];
static uint16_t s_server_port;
static char s_relay_ip[16];        /* the address currently in use          */
static bool s_discovered = false;  /* did mDNS (rather than config) find it */
static int  s_hellos_unanswered = 0;  /* hellos sent since the relay last configured us */
/* Control-plane transmit health (HELLO / KEEPALIVE / acks). Separate from s_tx, which
 * counts data frames: while the fabric is DOWN there are no data frames, so s_tx tells
 * you nothing about whether this board is managing to speak at all. */
static uint32_t s_ctrl_tx = 0, s_ctrl_fail = 0;
static uint32_t s_trunc = 0;       /* control payloads clipped to G32_CTRL_MAX */
static uint32_t s_fwd_in = 0;      /* frames arriving for someone BEHIND our radio */
static uint32_t s_default_fixups = 0;  /* times we took the default route back */
static uint32_t s_oversize = 0;    /* frames too big for this hop (see clamp_tcp_mss) */
static uint32_t s_clamped = 0;     /* SYNs whose MSS we lowered to fit the fabric */
static int s_ctrl_errno = 0;
static uint32_t s_dgrams_in = 0;   /* valid datagrams from the relay, any type */

/* The laptop that owns this board. Empty = unclaimed, and an unclaimed board answers
 * anybody so it can be found and adopted. Once set, every other laptop is ignored. */
static char s_owner[G32_OWNER_LEN];
static char s_mac_str[18];         /* our station MAC, for the "who are you" reply */

/* ------------------------------------------------------------------ helpers */

static void g32_fill_header(uint8_t *buf, uint8_t type)
{
    memset(buf, 0, G32_HDR_LEN);
    buf[0] = G32_MAGIC0;
    buf[1] = G32_MAGIC1;
    buf[2] = G32_MAGIC2;
    buf[3] = G32_VERSION;
    buf[4] = type;
    /* buf[5..7] reserved, already zero */
    strncpy((char *) (buf + 8), s_board_id, G32_ID_LEN);
}

/* Control messages only (HELLO / HELLO_ACK / KEEPALIVE), never data frames, so this
 * buffer is deliberately small — it must not put an MTU-sized array on the caller's
 * stack, which is the RX task's. */
static void g32_send(uint8_t type, const uint8_t *payload, size_t len)
{
    /* Big enough for the largest control payload we build (the keepalive's telemetry
     * buffer is 400 bytes), with room to grow.
     *
     * This was 64, which is smaller than a keepalive: `mac=… owner=…` is already ~44
     * bytes before any telemetry, so EVERY keepalive was over the limit and was thrown
     * away here, silently, before it reached the socket. The board therefore said hello
     * once, was configured, then never spoke again — and 30s later declared the link
     * dead and re-ran discovery, forever. Liveness must never be hostage to the size of
     * the diagnostics we attach to it, so oversized payloads are now TRUNCATED rather
     * than dropped: a keepalive carrying a short client list still keeps the link up. */
    uint8_t buf[G32_HDR_LEN + G32_CTRL_MAX];
    if (s_sock < 0) {
        s_drop++;
        return;
    }
    if (len > G32_CTRL_MAX) {
        len = G32_CTRL_MAX;
        s_trunc++;
    }
    g32_fill_header(buf, type);
    if (len && payload) {
        memcpy(buf + G32_HDR_LEN, payload, len);
    }
    /* Check the send. "We are talking and nobody answers" and "our transmits are not
     * leaving the board" look identical from the console but are different faults with
     * different fixes, and without this the second one is invisible. */
    int rc = sendto(s_sock, buf, G32_HDR_LEN + len, 0,
                    (struct sockaddr *) &s_relay, sizeof(s_relay));
    if (rc < 0) {
        s_ctrl_fail++;
        s_ctrl_errno = errno;
    } else {
        s_ctrl_tx++;
    }
}

/* Extract `owner=<id>` from a payload of exactly `len` bytes.
 *
 * Length-bounded on purpose: the datagram buffer is reused, so anything that reads
 * to a terminator can walk into the previous message's tail. The value also stops at
 * the first character that cannot appear in a laptop id, which is what keeps a
 * netmask fragment or an address from ever being welded onto an owner again.
 * Returns true if an owner was found.
 */
static bool owner_from_payload(const char *payload, int len, char *out, size_t out_sz)
{
    const char key[] = "owner=";
    const int klen = (int) (sizeof(key) - 1);
    out[0] = '\0';
    if (!payload || len < klen) {
        return false;
    }
    for (int i = 0; i + klen <= len; i++) {
        if (memcmp(payload + i, key, (size_t) klen) != 0) {
            continue;
        }
        int j = i + klen;
        size_t o = 0;
        while (j < len && o + 1 < out_sz) {
            char c = payload[j];
            /* an id is letters, digits and '-'; anything else ends it */
            if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
                  (c >= '0' && c <= '9') || c == '-' || c == '_')) {
                break;
            }
            out[o++] = c;
            j++;
        }
        out[o] = '\0';
        return o > 0;
    }
    return false;
}

/* Parse "ip=... mask=... gw=... mac=... mtu=..." from the HELLO_ACK. */
static bool parse_netcfg(const char *s, size_t len, fabric_netcfg_t *out)
{
    /* Sized to the largest control payload the relay can send, and STATIC.
     *
     * This was `char buf[192]` on the stack, and 192 was already too small: a 32-char
     * SSID with a 64-char password puts the HELLO_ACK at ~258 bytes, and the guard below
     * rejects anything that does not fit — so the board would discard its whole
     * configuration and log only "HELLO_ACK without a usable address". Nothing would
     * have shown WHY. Adding `dns=` made an already-tight buffer worse.
     *
     * Static rather than a bigger stack array because this runs on the RX task, whose
     * stack has overflowed before (two MTU-sized buffers). Only that task parses, so
     * there is no re-entrancy to protect against. */
    static char buf[G32_CTRL_MAX];
    if (len == 0 || len >= sizeof(buf)) {
        ESP_LOGW(TAG, "config payload is %u bytes, too long to parse (max %u) — "
                      "is the hotspot SSID or password very long?",
                 (unsigned) len, (unsigned) sizeof(buf) - 1);
        return false;
    }
    memcpy(buf, s, len);
    buf[len] = '\0';
    memset(out, 0, sizeof(*out));
    out->mtu = GB_FABRIC_MTU;

    for (char *tok = strtok(buf, " "); tok; tok = strtok(NULL, " ")) {
        char *eq = strchr(tok, '=');
        if (!eq) {
            continue;
        }
        *eq = '\0';
        const char *key = tok, *val = eq + 1;
        if (!strcmp(key, "ip")) {
            strncpy(out->ip, val, sizeof(out->ip) - 1);
        } else if (!strcmp(key, "mask")) {
            strncpy(out->mask, val, sizeof(out->mask) - 1);
        } else if (!strcmp(key, "gw")) {
            strncpy(out->gw, val, sizeof(out->gw) - 1);
        } else if (!strcmp(key, "mtu")) {
            out->mtu = (uint16_t) atoi(val);
        } else if (!strcmp(key, "mode")) {
            out->routed = !strcmp(val, "routed");
        } else if (!strcmp(key, "apnet")) {
            strncpy(out->ap_cidr, val, sizeof(out->ap_cidr) - 1);
        } else if (!strcmp(key, "apssid")) {
            strncpy(out->ap_ssid, val, sizeof(out->ap_ssid) - 1);
        } else if (!strcmp(key, "appass")) {
            strncpy(out->ap_pass, val, sizeof(out->ap_pass) - 1);
        } else if (!strcmp(key, "dns")) {
            /* An EMPTY value is meaningful: it means the canvas has no Internet element,
             * so stop offering a resolver. memset above already cleared it, so simply
             * copying the (possibly empty) value carries that instruction through. */
            strncpy(out->dns, val, sizeof(out->dns) - 1);
        } else if (!strcmp(key, "mac")) {
            unsigned m[6];
            if (sscanf(val, "%x:%x:%x:%x:%x:%x",
                       &m[0], &m[1], &m[2], &m[3], &m[4], &m[5]) == 6) {
                for (int i = 0; i < 6; i++) {
                    out->mac[i] = (uint8_t) m[i];
                }
                out->have_mac = true;
            }
        }
    }
    return out->ip[0] != '\0';
}

/* ------------------------------------------------------------- finding the relay */

/* A board used to be flashed with the laptop's address, which breaks whenever the
 * laptop moves network or gets a new lease. Instead gBuilder announces the running
 * lab on the local link and we look it up here. Order of preference:
 *
 *   1. `_gini._udp` service browse -- gives the address AND the port in one answer;
 *   2. an A lookup of `gini.local`  -- for a stripped-down responder;
 *   3. whatever literal address was configured -- so a pinned board still works.
 *
 * Called again whenever the link goes quiet, which is what makes an address change
 * on the laptop heal by itself.
 */
/* Do we have an address on the lab network yet? mDNS is multicast over that
 * interface, so asking before the uplink is up can only ever time out. */
static bool uplink_ready(void)
{
    esp_netif_t *sta = esp_netif_get_handle_from_ifkey("WIFI_STA_DEF");
    esp_netif_ip_info_t ip;
    return sta && esp_netif_get_ip_info(sta, &ip) == ESP_OK && ip.ip.addr != 0;
}

/* Announce WHERE the relay is, but only when that answer changes.
 *
 * Re-resolving every 30s is deliberate — it is how a board heals when a laptop
 * changes address — but logging an unchanged result each time turns the console
 * into a wall of identical lines, which hides the one line that differs. Say it
 * once, then stay quiet until the answer is actually new. */
static void note_relay(const char *how)
{
    static uint32_t s_logged_addr;
    static uint16_t s_logged_port;
    if (s_relay.sin_addr.s_addr == s_logged_addr &&
        s_relay.sin_port == s_logged_port) {
        return;
    }
    s_logged_addr = s_relay.sin_addr.s_addr;
    s_logged_port = s_relay.sin_port;
    ESP_LOGI(TAG, "GINI server is at %s:%u (via %s)",
             s_relay_ip, (unsigned) ntohs(s_relay.sin_port), how);
}

static bool resolve_relay(void)
{
    const char *cfg = s_server_setting;
    bool want_discovery = (cfg[0] == '\0') || !strcmp(cfg, "auto");
    char host[64];

    /* an explicit ".local" name: resolve just that */
    size_t len = strlen(cfg);
    bool dot_local = (len > 6) && !strcmp(cfg + len - 6, ".local");

    /* Report the REAL blocker. Saying "no GINI server found" when the board never
     * joined a network sends you hunting for a laptop/firewall problem that isn't
     * there — the only thing wrong is the Wi-Fi credentials. */
    if ((want_discovery || dot_local) && !uplink_ready()) {
        static bool told = false;
        if (!told) {
            ESP_LOGW(TAG, "waiting for the uplink before looking for the GINI server "
                          "— fix 'ssid'/'pass' first");
            told = true;
        }
        return false;
    }

    if (!want_discovery && !dot_local) {
        /* a literal address (or something we cannot resolve): use it as given */
        uint32_t a = ipaddr_addr(cfg);
        if (a == IPADDR_NONE) {
            ESP_LOGE(TAG, "server '%s' is neither an address nor a .local name", cfg);
            return false;
        }
        s_relay.sin_addr.s_addr = a;
        s_relay.sin_port = htons(s_server_port);
        strncpy(s_relay_ip, cfg, sizeof(s_relay_ip) - 1);
        s_discovered = false;
        return true;
    }

    if (want_discovery) {
        /* 1. browse for the service: one answer carries host + port */
        mdns_result_t *results = NULL;
        if (mdns_query_ptr("_gini", "_udp", GB_DISCOVER_TIMEOUT_MS, 4, &results) == ESP_OK
            && results) {
            for (mdns_result_t *r = results; r; r = r->next) {
                if (!r->addr) {
                    continue;
                }
                for (mdns_ip_addr_t *a = r->addr; a; a = a->next) {
                    if (a->addr.type != ESP_IPADDR_TYPE_V4) {
                        continue;
                    }
                    s_relay.sin_addr.s_addr = a->addr.u_addr.ip4.addr;
                    s_relay.sin_port = htons(r->port ? r->port : s_server_port);
                    esp_ip4addr_ntoa(&a->addr.u_addr.ip4, s_relay_ip, sizeof(s_relay_ip));
                    mdns_query_results_free(results);
                    s_discovered = true;
                    note_relay("_gini._udp");
                    return true;
                }
            }
            mdns_query_results_free(results);
        }
        strncpy(host, "gini", sizeof(host) - 1);       /* 2. fall back to gini.local */
    } else {
        /* strip the trailing ".local" -- the mDNS API wants the bare hostname */
        size_t n = len - 6;
        if (n >= sizeof(host)) {
            n = sizeof(host) - 1;
        }
        memcpy(host, cfg, n);
        host[n] = '\0';
    }

    esp_ip4_addr_t addr = { 0 };
    if (mdns_query_a(host, GB_DISCOVER_TIMEOUT_MS, &addr) == ESP_OK && addr.addr) {
        s_relay.sin_addr.s_addr = addr.addr;
        s_relay.sin_port = htons(s_server_port);
        esp_ip4addr_ntoa(&addr, s_relay_ip, sizeof(s_relay_ip));
        s_discovered = true;
        note_relay(host);
        return true;
    }

    /* 3. nothing answered. Keep any address we already had rather than going dark. */
    if (s_relay.sin_addr.s_addr != 0 && s_relay.sin_addr.s_addr != IPADDR_NONE) {
        return true;
    }
    ESP_LOGW(TAG, "no GINI server found on this network yet "
                  "(is a topology with a GINI32 board running?)");
    return false;
}

/* ------------------------------------------------------- path MTU / MSS clamp */

/* The largest TCP payload that still fits the fabric hop: MTU minus the smallest
 * possible IPv4 (20) and TCP (20) headers. */
#define GB_FABRIC_MSS   (GB_FABRIC_MTU - 40)

/* One's-complement checksum fixup for a single changed 16-bit word (RFC 1624).
 * Recomputing the whole TCP checksum would mean walking the payload; this is exact
 * and costs a few instructions. */
static uint16_t csum_replace16(uint16_t hc, uint16_t old16, uint16_t new16)
{
    uint32_t sum = (uint16_t) ~hc;
    sum += (uint16_t) ~old16;
    sum += new16;
    while (sum >> 16) {
        sum = (sum & 0xFFFF) + (sum >> 16);
    }
    return (uint16_t) ~sum;
}

/* Lower the MSS a device advertises so the far end never sends a segment too big for
 * this hop. Returns true if the frame was modified.
 *
 * WHY THIS EXISTS. The fabric hop is Ethernet-in-UDP, so it carries 1400 bytes, not the
 * 1500 an ordinary Wi-Fi client assumes. Nothing tells a phone that. It joins the
 * hotspot, advertises MSS 1460, and the web server duly sends 1500-byte packets which
 * this board cannot forward — they were counted as `drop` and nothing was ever sent
 * back, so from the phone the internet simply hangs. The give-away is that PING and DNS
 * work perfectly: they are small. Only real traffic is big enough to fall off the edge.
 *
 * Path MTU discovery is supposed to solve this and does not, in practice: it needs ICMP
 * "fragmentation needed" to come back, lwIP does not generate it for FORWARDED packets,
 * and modern TCP sets DF on everything. Clamping the MSS in the SYN is what every router
 * on a tunnelled link does instead, precisely because it needs no cooperation from
 * either end. It must be applied in BOTH directions: the phone's SYN tells the server
 * what to send, and the server's SYN-ACK tells the phone.
 */
static bool clamp_tcp_mss(uint8_t *frame, uint16_t len, uint16_t max_mss)
{
    if (len < 54) {                                  /* eth + min ip + min tcp */
        return false;
    }
    if (frame[12] != 0x08 || frame[13] != 0x00) {    /* not IPv4 */
        return false;
    }
    uint8_t *ip = frame + 14;
    if ((ip[0] >> 4) != 4) {
        return false;
    }
    const uint16_t ihl = (uint16_t) ((ip[0] & 0x0F) * 4);
    if (ihl < 20 || (uint16_t) (14 + ihl + 20) > len) {
        return false;
    }
    if (ip[9] != 6) {                                /* not TCP */
        return false;
    }
    /* A fragment other than the first has no TCP header to read. */
    if ((((uint16_t) ip[6] << 8 | ip[7]) & 0x1FFF) != 0) {
        return false;
    }
    uint8_t *tcp = ip + ihl;
    if (!(tcp[13] & 0x02)) {                         /* only SYN carries an MSS option */
        return false;
    }
    const uint16_t doff = (uint16_t) ((tcp[12] >> 4) * 4);
    if (doff < 20 || (uint16_t) (14 + ihl + doff) > len) {
        return false;
    }

    bool changed = false;
    uint16_t i = 20;
    while (i + 1 < doff) {
        const uint8_t kind = tcp[i];
        if (kind == 0) {                             /* end of options */
            break;
        }
        if (kind == 1) {                             /* NOP padding */
            i++;
            continue;
        }
        const uint8_t olen = tcp[i + 1];
        if (olen < 2 || i + olen > doff) {
            break;                                   /* malformed: leave well alone */
        }
        if (kind == 2 && olen == 4) {
            const uint16_t mss = (uint16_t) ((tcp[i + 2] << 8) | tcp[i + 3]);
            if (mss > max_mss) {
                const uint16_t ck = (uint16_t) ((tcp[16] << 8) | tcp[17]);
                const uint16_t nck = csum_replace16(ck, mss, max_mss);
                tcp[i + 2] = (uint8_t) (max_mss >> 8);
                tcp[i + 3] = (uint8_t) (max_mss & 0xFF);
                tcp[16] = (uint8_t) (nck >> 8);
                tcp[17] = (uint8_t) (nck & 0xFF);
                changed = true;
            }
            break;                                   /* only one MSS option is legal */
        }
        i += olen;
    }
    return changed;
}

/* ------------------------------------------------------------ netif callbacks */

/* One queued outbound frame, already wrapped in its G32 header. */
typedef struct {
    uint16_t len;                                   /* bytes valid in buf */
    uint8_t buf[G32_HDR_LEN + GB_FABRIC_MTU + 32];
} tx_item_t;

/* Handing frames to a sender task is NOT an optimisation — it is required.
 *
 * lwIP calls linkoutput ON the TCP/IP thread. The socket API (sendto) posts a message
 * to that same thread and waits for it, so calling sendto here deadlocks the entire
 * stack on the very first transmitted frame: no ARP reply, no keepalives, board falls
 * silent. That is exactly what happened. linkoutput must therefore only ever enqueue
 * and return; a normal task does the blocking send. */
static err_t fabric_linkoutput(struct netif *netif, struct pbuf *p)
{
    (void) netif;

    if (p->tot_len > GB_FABRIC_MTU + 32) {
        /* Counted separately and named, because this drop used to be indistinguishable
         * from a busy queue — and it is the one that makes the internet "not work" from
         * a device on the radio while ping and DNS look perfect. See clamp_tcp_mss(). */
        s_oversize++;
        if (s_oversize <= 3) {
            ESP_LOGW(TAG, "dropped a %u-byte frame: this hop carries %u "
                          "(a device is sending more than the fabric can take)",
                     (unsigned) p->tot_len, (unsigned) GB_FABRIC_MTU);
        }
        return ERR_MEM;
    }
    tx_item_t *it = malloc(sizeof(*it));            /* freed by the sender task */
    if (!it) {
        s_drop++;
        return ERR_MEM;
    }
    g32_fill_header(it->buf, G32_FRAME);
    /* pbuf_copy_partial flattens a possibly-chained pbuf for us */
    u16_t n = pbuf_copy_partial(p, it->buf + G32_HDR_LEN, p->tot_len, 0);
    if (n != p->tot_len) {
        free(it);
        s_drop++;
        return ERR_BUF;
    }
    /* Outbound: a device on our radio is telling the far end how much it may send. */
    if (clamp_tcp_mss(it->buf + G32_HDR_LEN, n, GB_FABRIC_MSS)) {
        s_clamped++;
    }
    it->len = (uint16_t) (G32_HDR_LEN + n);

    /* Never block the stack: if the sender is behind, drop this frame like a busy NIC. */
    if (!s_txq || xQueueSend(s_txq, &it, 0) != pdTRUE) {
        free(it);
        s_drop++;
        return ERR_WOULDBLOCK;
    }
    return ERR_OK;
}

/* Drains the queue and does the actual (blocking) socket send, off the lwIP thread. */
static void fabric_tx_task(void *arg)
{
    tx_item_t *it;
    (void) arg;

    for (;;) {
        if (xQueueReceive(s_txq, &it, portMAX_DELAY) != pdTRUE) {
            continue;
        }
        if (s_tx == 0) {
            uint16_t et = ((uint16_t) it->buf[G32_HDR_LEN + 12] << 8)
                          | it->buf[G32_HDR_LEN + 13];
            ESP_LOGI(TAG, "first frame out to the fabric (%u bytes, ethertype 0x%04x)",
                     (unsigned) (it->len - G32_HDR_LEN), et);
        }
        if (sendto(s_sock, it->buf, it->len, 0,
                   (struct sockaddr *) &s_relay, sizeof(s_relay)) < 0) {
            s_drop++;
        } else {
            s_tx++;
        }
        free(it);
    }
}

/* Build marker: bump on every firmware change so a stale flash is obvious in the log
 * rather than being mistaken for a failed fix. */
#define GB_BUILD "gbridge-19 (mss)"

static err_t fabric_netif_init(struct netif *netif)
{
    netif->name[0] = 'g';
    netif->name[1] = 'n';
    netif->output = etharp_output;          /* IP -> ARP -> linkoutput          */
    netif->linkoutput = fabric_linkoutput;  /* the "wire"                       */
    netif->mtu = s_cfg.mtu ? s_cfg.mtu : GB_FABRIC_MTU;
    netif->hwaddr_len = ETH_HWADDR_LEN;
    memcpy(netif->hwaddr, s_cfg.mac, ETH_HWADDR_LEN);
    netif->flags = NETIF_FLAG_BROADCAST | NETIF_FLAG_ETHARP | NETIF_FLAG_LINK_UP;
    ESP_LOGI(TAG, "netif init: name=%c%c mtu=%u flags=0x%02x mac=%02x:%02x:%02x:%02x:%02x:%02x",
             netif->name[0], netif->name[1], (unsigned) netif->mtu,
             (unsigned) netif->flags,
             netif->hwaddr[0], netif->hwaddr[1], netif->hwaddr[2],
             netif->hwaddr[3], netif->hwaddr[4], netif->hwaddr[5]);
    return ERR_OK;
}

/* Addresses staged for the bring-up callback below (written before scheduling it,
 * read only by the lwIP thread). */
static ip4_addr_t s_pending_ip, s_pending_mask, s_pending_gw;

/* Runs ON the lwIP TCP/IP thread — the only context allowed to touch the raw netif
 * API when core locking is disabled. */
static void fabric_netif_bringup(void *arg)
{
    (void) arg;
    if (!s_added) {
        /* tcpip_input (not ethernet_input): frames are injected from our RX task */
        netif_add(&s_netif, &s_pending_ip, &s_pending_mask, &s_pending_gw,
                  NULL, fabric_netif_init, tcpip_input);
        s_added = true;
    } else {
        netif_set_addr(&s_netif, &s_pending_ip, &s_pending_mask, &s_pending_gw);
    }
    netif_set_up(&s_netif);
    netif_set_link_up(&s_netif);
    /* The fabric is this board's route to everything that is not its own AP. */
    netif_set_default(&s_netif);
}

/* Re-assert the fabric as the default route. Runs on the lwIP thread.
 *
 * Setting it once at bring-up is not enough, and this was the bug that made the board
 * look like a router that would not route. ESP-IDF's esp_netif layer re-picks the
 * default netif on every interface event — AP start, station join, STA got-IP — by
 * choosing the highest `route_prio` among the netifs IT knows about. Our fabric netif
 * is a raw netif_add(); esp_netif has never heard of it, so it cannot win that contest
 * and cannot even enter it. The moment the hotspot came up or a phone associated, the
 * default route silently moved to the AP interface.
 *
 * Everything then followed from that one pointer:
 *   - board -> 10.0.3.10 timed out: off-link, so it took the default route, which now
 *     pointed at the radio, and went out over the air to nobody.
 *   - phone -> M1 was dropped: ip4_forward routes the packet, gets the AP netif back,
 *     sees route == input interface, and refuses to forward a packet out the interface
 *     it arrived on. That check is correct; the route handed to it was not.
 *   - board -> 10.0.9.2 worked throughout, because on-link traffic never consults the
 *     default route at all. Which is exactly why the radio always looked healthy.
 */
static void fabric_default_assert(void *arg)
{
    (void) arg;
    if (!s_added || netif_default == &s_netif) {
        return;
    }
    if (netif_default) {
        ESP_LOGW(TAG, "default route had been taken by %c%c -- moving it back to the "
                      "fabric (%s); off-link traffic was going out the radio",
                 netif_default->name[0], netif_default->name[1], s_cfg.ip);
    }
    netif_set_default(&s_netif);
    s_default_fixups++;
}

/* Take the link down, also on the lwIP thread. */
static void fabric_netif_down(void *arg)
{
    (void) arg;
    if (s_added) {
        netif_set_link_down(&s_netif);
    }
}

/* Bring the interface up with what the relay told us. Runs once, on first ACK. */
static void fabric_configure(const fabric_netcfg_t *cfg)
{
    ip4_addr_t ip, mask, gw;

    s_cfg = *cfg;
    if (!s_cfg.have_mac) {
        /* No MAC from the canvas: derive a stable locally-administered one from
         * the board id. The gRouter learns our MAC from ARP either way. */
        uint32_t h = 2166136261u;
        for (const char *c = s_board_id; *c; c++) {
            h = (h ^ (uint8_t) *c) * 16777619u;
        }
        s_cfg.mac[0] = 0x02;
        s_cfg.mac[1] = 0x00;
        s_cfg.mac[2] = (uint8_t) (h >> 24);
        s_cfg.mac[3] = (uint8_t) (h >> 16);
        s_cfg.mac[4] = (uint8_t) (h >> 8);
        s_cfg.mac[5] = (uint8_t) h;
    }

    ip4addr_aton(s_cfg.ip, &ip);
    ip4addr_aton(s_cfg.mask[0] ? s_cfg.mask : "255.255.255.0", &mask);
    ip4addr_aton(s_cfg.gw[0] ? s_cfg.gw : "0.0.0.0", &gw);

    /* Bring the interface up ON the lwIP thread.
     *
     * netif_add/netif_set_* are lwIP RAW API: they may only be touched by the tcpip
     * thread. This build has CONFIG_LWIP_TCPIP_CORE_LOCKING off, so there is not even
     * a lock to take — calling them from our RX task raced the stack and left a netif
     * that accepted input but never answered ARP (frames in, nothing out). Handing the
     * work to tcpip_callback() runs it in the right context. */
    s_pending_ip = ip;
    s_pending_mask = mask;
    s_pending_gw = gw;
    if (tcpip_callback(fabric_netif_bringup, NULL) != ERR_OK) {
        ESP_LOGE(TAG, "could not schedule interface bring-up on the lwIP thread");
        return;
    }

    /* Address translation is a per-MODE decision, and the mode comes from the canvas,
     * so it can only be applied here — once the relay has told us which we are.
     *
     *   nat     translate AP-side devices onto our one fabric address. The emulated
     *           side sees only us; it cannot reach in. Simple, and self-contained.
     *   routed  forward untouched. The devices keep their real addresses and the
     *           routers have a route to the physical subnet via us, so traffic flows
     *           BOTH ways — this is the mode that truly extends the drawn network
     *           into the physical world.
     *
     * Enabling NAPT in routed mode would rewrite the very source addresses the return
     * routes depend on, so the two are mutually exclusive. */
    esp_netif_t *ap = esp_netif_get_handle_from_ifkey("WIFI_AP_DEF");
    if (ap) {
        esp_err_t nerr = s_cfg.routed ? esp_netif_napt_disable(ap)
                                      : esp_netif_napt_enable(ap);
        if (nerr == ESP_OK) {
            ESP_LOGI(TAG, "%s mode: devices behind the radio are %s",
                     s_cfg.routed ? "routed" : "nat",
                     s_cfg.routed ? "reachable from the emulated network"
                                  : "hidden behind this board's address");
        } else {
            ESP_LOGE(TAG, "could not set %s mode (%s) — check CONFIG_LWIP_IP_FORWARD "
                          "and CONFIG_LWIP_IPV4_NAPT are =y",
                     s_cfg.routed ? "routed" : "nat", esp_err_to_name(nerr));
        }
    }

    /* The hotspot is a canvas decision too. Applied after the fabric is configured
     * so a board that cannot reach gBuilder never tears down a working AP. */
    if (s_cfg.ap_cidr[0] || s_cfg.ap_ssid[0]) {
        gb_ap_configure(s_cfg.ap_cidr, s_cfg.ap_ssid, s_cfg.ap_pass, s_cfg.dns);
    }

    s_up = true;
    s_hellos_unanswered = 0;    /* configured: the "am I being ignored?" warning resets */
    ESP_LOGI(TAG, "fabric up: %s/%s gw %s mac %02x:%02x:%02x:%02x:%02x:%02x mtu %u",
             s_cfg.ip, s_cfg.mask, s_cfg.gw,
             s_cfg.mac[0], s_cfg.mac[1], s_cfg.mac[2],
             s_cfg.mac[3], s_cfg.mac[4], s_cfg.mac[5], (unsigned) s_cfg.mtu);
}

/* ------------------------------------------------------------------ RX task */

static void fabric_rx_task(void *arg)
{
    /* static, not on the stack: this task also runs mDNS queries, which need real
     * stack headroom of their own. An MTU-sized local here overflowed a 4 KB stack
     * the moment discovery ran. Only this task touches it. */
    static uint8_t buf[G32_HDR_LEN + GB_FABRIC_MTU + 64];
    int64_t last_hello = 0, last_keepalive = 0, last_search = 0;
    (void) arg;

    resolve_relay();                       /* first look, before we say anything */

    for (;;) {
        int64_t now = esp_timer_get_time();

        /* Has the link gone quiet? Either nothing ever answered, or a live link has
         * heard nothing for a while -- which is exactly what a laptop changing address
         * looks like from here. Search again (rate-limited) so the board heals itself
         * instead of needing to be re-flashed or re-told. */
        bool stale = (s_last_ack_us == 0)
                     || (now - s_last_ack_us) > (int64_t) GB_LINK_STALE_MS * 1000;
        if (stale && (now - last_search) > (int64_t) GB_LINK_STALE_MS * 1000) {
            if (s_up) {
                ESP_LOGW(TAG, "no word from %s for %us -- looking for the server again",
                         s_relay_ip, (unsigned) (GB_LINK_STALE_MS / 1000));
                s_up = false;              /* stop forwarding into a hole; re-HELLO */
                tcpip_callback(fabric_netif_down, NULL);   /* raw API -> lwIP thread */
            }
            resolve_relay();
            last_search = now;
        }

        /* Announce ourselves until the relay answers, then just keep the entry warm.
         * A relay restart (or a topology re-run) is picked up automatically because
         * we fall back to HELLO whenever the link is not up. */
        if (!s_up && (now - last_hello) > (int64_t) GB_HELLO_INTERVAL_MS * 1000) {
            /* Announce who we are AND who owns us. A laptop that is not our owner
             * learns to leave us alone from this single field; an unclaimed board
             * advertises itself as available. */
            char hello[128];
            int n = snprintf(hello, sizeof(hello), "fw=%s mac=%s owner=%s",
                             GB_BUILD, s_mac_str, s_owner);
            g32_send(G32_HELLO, (const uint8_t *) hello, (size_t) (n > 0 ? n : 0));
            last_hello = now;

            /* Knowing where the relay is and still not being configured is a DIFFERENT
             * fault from not finding it, and it has exactly one common cause: no GINI32
             * element on the canvas carries this board's id. Without saying so, the
             * console shows a healthy-looking discovery loop and the id mismatch — which
             * is invisible from this end — goes unsuspected. */
            if (++s_hellos_unanswered == 6) {
                ESP_LOGW(TAG, "%s has not configured us after %d hellos.",
                         s_relay_ip, s_hellos_unanswered);
                ESP_LOGW(TAG, "  -> is there a GINI32 element with BoardID '%s' on the "
                              "canvas, and is the topology running?", s_board_id);
                ESP_LOGW(TAG, "  -> the id must match EXACTLY; the canvas does not "
                              "rename a board to fit.");
            }
        } else if (s_up && (now - last_keepalive) > (int64_t) GB_KEEPALIVE_MS * 1000) {
            /* The keepalive carries what this board can see — channel, uplink signal,
             * and every device on its hotspot — so gBuilder can show real hardware
             * state without a second channel or any polling of its own. */
            char tel[400];
            int n = snprintf(tel, sizeof(tel), "mac=%s owner=%s ", s_mac_str, s_owner);
            n += gb_telemetry(tel + n, sizeof(tel) - n);
            g32_send(G32_KEEPALIVE, (const uint8_t *) tel, (size_t) (n > 0 ? n : 0));
            last_keepalive = now;
            /* Piggy-backed on the keepalive rather than hooked to Wi-Fi events: there
             * are several events that can move the default route (AP start, each
             * station join, STA got-IP, and any reconnect), and a periodic check costs
             * one pointer comparison while covering all of them, including ones added
             * by a future IDF. */
            tcpip_callback(fabric_default_assert, NULL);
        }

        struct sockaddr_in from;
        socklen_t flen = sizeof(from);
        /* Leave room for the terminator below — see the NUL-termination note. */
        int n = recvfrom(s_sock, buf, sizeof(buf) - 1, 0,
                         (struct sockaddr *) &from, &flen);
        if (n < G32_HDR_LEN) {
            continue;                       /* timeout, or a runt */
        }
        if (buf[0] != G32_MAGIC0 || buf[1] != G32_MAGIC1 ||
            buf[2] != G32_MAGIC2 || buf[3] != G32_VERSION) {
            continue;                       /* not ours */
        }

        /* TERMINATE THE PAYLOAD before anything treats it as a string.
         *
         * `buf` is reused for every datagram, and recvfrom neither terminates nor
         * clears the tail — so the bytes after a short datagram are whatever a longer
         * earlier one left there. The text parsers below (strstr/sscanf for `owner=`)
         * have no length to stop at, so they read straight off the end of the message
         * and into that debris.
         *
         * This was not theoretical: a 21-byte `owner=gb-a5c4915cf6b9` landing on top of
         * an earlier `ip=… mask=255.255.255.0 …` left `.255.255.0` at byte 21, and the
         * board stored its owner as `gb-a5c4915cf6b9.255.255.0`. It then no longer
         * matched the laptop that set it, so the relay ignored the board for good —
         * silently, because an unrecognised owner is indistinguishable from silence.
         */
        buf[n] = '\0';

        uint8_t type = buf[4];
        uint8_t *payload = buf + G32_HDR_LEN;
        int paylen = n - G32_HDR_LEN;

        /* ANY well-formed datagram from the relay proves the link is alive. Counting
         * only HELLO_ACKs would declare a busy link dead — a board carrying steady
         * traffic would tear down and re-discover every GB_LINK_STALE_MS. */
        s_last_ack_us = now;

        /* Per-type tally. When the relay insists it is sending frames and rx stays 0,
         * this is the only way to tell "nothing arrives" from "frames arrive but are
         * rejected" — the two have completely different causes. */
        if (type < 8) {
            s_seen[type]++;
        }
        s_dgrams_in++;      /* any valid datagram — proof the return path works at all */
        /* Log the first few frames in full detail. An ARP that names an address we do
         * NOT hold is the classic "router points at a stale next hop" signature, and it
         * is invisible from every other vantage point — the relay sees a valid frame,
         * the board silently declines to answer, and both look healthy. */
        if (type == G32_FRAME && s_seen[G32_FRAME] <= 3 && paylen >= 14) {
            uint16_t et = ((uint16_t) payload[12] << 8) | payload[13];
            if (et == 0x0806 && paylen >= 42) {
                const uint8_t *t = payload + 38;      /* ARP target protocol address */
                const uint8_t *s = payload + 28;      /* ARP sender protocol address */
                char target[16];
                snprintf(target, sizeof(target), "%u.%u.%u.%u", t[0], t[1], t[2], t[3]);
                bool for_us = !strcmp(target, s_cfg.ip);
                ESP_LOGI(TAG, "frame: ARP who-has %s tell %u.%u.%u.%u -- we are %s -> %s",
                         target, s[0], s[1], s[2], s[3], s_cfg.ip,
                         for_us ? "FOR US, replying"
                                : "NOT for us, ignoring (stale next hop upstream?)");
            } else {
                ESP_LOGI(TAG, "frame: %d bytes ethertype 0x%04x", paylen, et);
            }
        }

        /* ---- claiming ------------------------------------------------------ */
        if (type == G32_CLAIM) {
            char who[G32_OWNER_LEN] = "";
            /* Parse within THIS datagram only. The buffer is terminated above, but an
             * owner is the one field that, if it absorbs a stray byte, locks the board
             * out of its rightful laptop permanently — so it is parsed against the
             * declared length rather than trusting a terminator to be in the right
             * place. Anything that is not a plain identifier ends the value. */
            owner_from_payload((const char *) payload, paylen, who, sizeof(who));
            if (s_owner[0] && strcmp(s_owner, who) != 0) {
                /* Already someone else's. Say so rather than going quiet, so the
                 * asking laptop can tell "refused" from "not listening". */
                char reply[80];
                int n = snprintf(reply, sizeof(reply), "owner=%s busy=1", s_owner);
                g32_send(G32_CLAIM_ACK, (const uint8_t *) reply, (size_t) n);
                ESP_LOGW(TAG, "refused a claim from '%s' — already owned by '%s'",
                         who, s_owner);
            } else if (who[0]) {
                strncpy(s_owner, who, sizeof(s_owner) - 1);
                gb_owner_save(s_owner);
                char reply[80];
                int n = snprintf(reply, sizeof(reply), "owner=%s", s_owner);
                g32_send(G32_CLAIM_ACK, (const uint8_t *) reply, (size_t) n);
                ESP_LOGI(TAG, "claimed by '%s' — this board now ignores other laptops",
                         s_owner);
            }
            continue;
        }
        if (type == G32_RELEASE) {
            if (s_owner[0]) {
                ESP_LOGI(TAG, "released by '%s' — available again", s_owner);
                fabric_unpair();
            }
            continue;
        }
        if (type == G32_BLINK) {
            ESP_LOGI(TAG, "blink requested — look for the flashing board");
            gb_blink();
            continue;
        }

        if (type == G32_HELLO_ACK) {
            /* Only our owner may configure us. An unclaimed board accepts config from
             * whoever answers, which is what lets it be adopted in the first place. */
            if (s_owner[0]) {
                char who[G32_OWNER_LEN] = "";
                /* Length-bounded, as in the CLAIM path. A normal HELLO_ACK carries no
                 * `owner=` at all, so an unbounded search would happily find one left
                 * behind by an earlier CLAIM in the same buffer — and we would then
                 * reject our OWN laptop's configuration and sit there unconfigured. */
                owner_from_payload((const char *) payload, paylen, who, sizeof(who));
                if (who[0] && strcmp(who, s_owner) != 0) {
                    continue;                 /* a different laptop — not ours */
                }
            }
            fabric_netcfg_t cfg;
            if (parse_netcfg((const char *) payload, (size_t) paylen, &cfg)) {
                if (!s_up || strcmp(cfg.ip, s_cfg.ip) != 0) {
                    fabric_configure(&cfg);       /* first ACK, or the canvas changed */
                }
            } else {
                ESP_LOGW(TAG, "HELLO_ACK without a usable address; is the element wired?");
            }
            continue;
        }

        if (type == G32_FRAME && paylen > 0 && s_up) {
            /* Count frames that are addressed THROUGH us rather than TO us.
             *
             * These are the ones lwIP has to forward out of the AP interface. Without
             * this number, "the board is not forwarding" and "nothing that needs
             * forwarding ever arrives" look identical from the console — and they have
             * completely different causes. If fwd_in climbs while devices behind the
             * radio stay unreachable, the fault is inside lwIP's forwarding, not in
             * the fabric, the routes, or the devices. */
            if (paylen >= 34 && payload[12] == 0x08 && payload[13] == 0x00) {
                const uint8_t *d = payload + 30;         /* IPv4 destination address */
                char dst[16];
                snprintf(dst, sizeof(dst), "%u.%u.%u.%u", d[0], d[1], d[2], d[3]);
                if (strcmp(dst, s_cfg.ip) != 0) {
                    s_fwd_in++;
                    if (s_fwd_in <= 3) {
                        ESP_LOGI(TAG, "frame to %s is not for us -> lwIP must forward it "
                                      "out the hotspot (fwd_in=%u)",
                                 dst, (unsigned) s_fwd_in);
                    }
                }
            }
            /* Inbound: the far end's SYN-ACK tells the device on our radio how much IT
             * may send. Clamping only one direction fixes only half the connections. */
            if (clamp_tcp_mss(payload, (uint16_t) paylen, GB_FABRIC_MSS)) {
                s_clamped++;
            }
            struct pbuf *p = pbuf_alloc(PBUF_RAW, (u16_t) paylen, PBUF_POOL);
            if (!p) {
                s_drop++;
                continue;
            }
            if (pbuf_take(p, payload, (u16_t) paylen) != ERR_OK ||
                s_netif.input(p, &s_netif) != ERR_OK) {
                pbuf_free(p);
                s_drop++;
                continue;
            }
            s_rx++;
        }
    }
}

/* -------------------------------------------------------------------- public */

void fabric_start(const char *server, uint16_t server_port, const char *board_id,
                  const char *owner)
{
    strncpy(s_board_id, board_id ? board_id : "", sizeof(s_board_id) - 1);
    strncpy(s_owner, owner ? owner : "", sizeof(s_owner) - 1);

    /* Our MAC is the one identifier nobody can mistype, so it goes in every HELLO —
     * it is what lets gBuilder tell two boards apart before either has been named. */
    uint8_t mac[6] = {0};
    esp_wifi_get_mac(WIFI_IF_STA, mac);
    snprintf(s_mac_str, sizeof(s_mac_str), "%02x:%02x:%02x:%02x:%02x:%02x",
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);

    ESP_LOGI(TAG, "identity: name '%s' mac %s owner %s", s_board_id, s_mac_str,
             s_owner[0] ? s_owner : "(unclaimed — any laptop may adopt this board)");
    strncpy(s_server_setting, server ? server : "auto", sizeof(s_server_setting) - 1);
    s_server_port = server_port;

    memset(&s_relay, 0, sizeof(s_relay));
    s_relay.sin_family = AF_INET;
    s_relay.sin_port = htons(server_port);

    /* mDNS is how we find the lab; start it before the first lookup. Harmless (and
     * useful) even when a literal address is configured, since it also makes this
     * board answerable by name. */
    if (mdns_init() == ESP_OK) {
        mdns_hostname_set(s_board_id[0] ? s_board_id : "gini32");
        mdns_instance_name_set("GINI32 board");
    } else {
        ESP_LOGW(TAG, "mdns_init failed -- discovery unavailable, "
                      "set a literal address with `set server <ip>`");
    }

    s_sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (s_sock < 0) {
        ESP_LOGE(TAG, "socket() failed");
        return;
    }
    /* Block for at most 200 ms so the task can also drive HELLO/keepalive timing. */
    struct timeval tv = { .tv_sec = 0, .tv_usec = 200 * 1000 };
    setsockopt(s_sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

    ESP_LOGI(TAG, "build: %s", GB_BUILD);
    ESP_LOGI(TAG, "board '%s', server '%s' -- %s",
             s_board_id, s_server_setting,
             (!s_server_setting[0] || !strcmp(s_server_setting, "auto"))
                 ? "discovering with mDNS" : "using the configured address");
    /* Outbound path: linkoutput enqueues (never blocks), this task does the send. */
    s_txq = xQueueCreate(16, sizeof(tx_item_t *));
    if (!s_txq) {
        ESP_LOGE(TAG, "no memory for the transmit queue");
        return;
    }
    xTaskCreate(fabric_tx_task, "fabric_tx", 3072, NULL, 7, NULL);

    /* 6 KB: the buffers now live in .bss, but mDNS queries still want headroom. */
    xTaskCreate(fabric_rx_task, "fabric_rx", 6144, NULL, 6, NULL);
}

bool fabric_is_up(void)
{
    return s_up;
}

const char *fabric_owner(void)
{
    return s_owner;
}

void fabric_unpair(void)
{
    s_owner[0] = '\0';
    gb_owner_save("");
    /* Drop the link too: whatever configured us is no longer entitled to. */
    s_up = false;
    tcpip_callback(fabric_netif_down, NULL);
}

void fabric_status(char *out, size_t out_len)
{
    if (!s_up) {
        if (s_relay_ip[0]) {
            /* The counters matter MORE when down than up, and used to be printed only
             * when up. sent>0 with in=0 means we are talking into a void — the relay
             * is not receiving us, which is a network fault, not a BoardID one.
             * fail>0 means our own transmits are erroring before they leave. */
            snprintf(out, out_len,
                     "fabric: DOWN  server %s:%u (%s), no HELLO_ACK yet\n"
                     "       sent=%u fail=%u (errno %d)  in=%u  clipped=%u  board '%s'\n"
                     "       sent>0 and in=0 -> nothing is coming back: check the relay "
                     "is listening and no firewall sits between\n"
                     "       in>0 -> we ARE being answered but not configured: check "
                     "BoardID '%s' is on the canvas",
                     s_relay_ip, (unsigned) ntohs(s_relay.sin_port),
                     s_discovered ? "discovered" : "configured",
                     (unsigned) s_ctrl_tx, (unsigned) s_ctrl_fail, s_ctrl_errno,
                     (unsigned) s_dgrams_in, (unsigned) s_trunc,
                     s_board_id, s_board_id);
        } else {
            snprintf(out, out_len,
                     "fabric: DOWN  no server found yet (looking for _gini._udp on this "
                     "network; set one by hand with `set server <ip>`)");
        }
        return;
    }
    snprintf(out, out_len,
             "fabric: UP %s  %s/%s gw %s  via %s:%u (%s)  rx=%u tx=%u drop=%u  "
             "[datagrams in: ack=%u frame=%u]  fwd_in=%u  default=%c%c fixups=%u  "
             "mss<=%u clamped=%u oversize=%u",
             s_cfg.routed ? "routed" : "nat",
             s_cfg.ip, s_cfg.mask, s_cfg.gw,
             s_relay_ip, (unsigned) ntohs(s_relay.sin_port),
             s_discovered ? "discovered" : "configured",
             (unsigned) s_rx, (unsigned) s_tx, (unsigned) s_drop,
             (unsigned) s_seen[G32_HELLO_ACK], (unsigned) s_seen[G32_FRAME],
             (unsigned) s_fwd_in,
             netif_default ? netif_default->name[0] : '?',
             netif_default ? netif_default->name[1] : '?',
             (unsigned) s_default_fixups,
             (unsigned) GB_FABRIC_MSS, (unsigned) s_clamped, (unsigned) s_oversize);
}
