/* fabric.h -- the GINI fabric link: a virtual Ethernet interface carried in UDP.
 *
 * This is the heart of gBridge. It registers a real lwIP network interface whose
 * "wire" is a UDP socket to the gbridge relay. Frames the IP stack wants to send
 * are wrapped in a G32 header and posted to the relay; datagrams from the relay
 * are unwrapped and fed back into the stack. To lwIP it is an ordinary Ethernet
 * interface, which is exactly why the emulated gRouter on the far side needs no
 * knowledge that this hop is real hardware.
 */
#ifndef GBRIDGE_FABRIC_H
#define GBRIDGE_FABRIC_H

#include <stdbool.h>
#include <stdint.h>

/* ---- the board-hop wire format (must match runtime/gbridge.py) ------------ */
#define G32_MAGIC0      'G'
#define G32_MAGIC1      '3'
#define G32_MAGIC2      '2'
#define G32_VERSION     1
#define G32_HDR_LEN     24
#define G32_ID_LEN      16
/* Largest CONTROL payload (hello / keepalive telemetry). Must exceed the biggest
 * string those build -- the keepalive's is 400 bytes -- or the message that keeps the
 * link alive gets discarded for being too informative. Data frames do not come through
 * here; they take fabric_linkoutput and are bounded by the MTU. */
#define G32_CTRL_MAX    512

#define G32_HELLO       0x01
#define G32_HELLO_ACK   0x02
#define G32_FRAME       0x03
#define G32_KEEPALIVE   0x04
/* Claiming. A board records the laptop that claimed it and thereafter ignores every
 * other laptop — which is what stops a room full of students from taking each other's
 * hardware. An UNCLAIMED board answers anyone, so it can be found and adopted. */
#define G32_CLAIM       0x05    /* laptop -> board: owner=<laptop id>     */
#define G32_CLAIM_ACK   0x06    /* board -> laptop: owner=<id> [busy=1]   */
#define G32_RELEASE     0x07    /* laptop -> board: you are free again    */
#define G32_BLINK       0x08    /* laptop -> board: flash the LED         */

#define G32_OWNER_LEN   40      /* laptop id, NUL-terminated */

/* Settings the relay hands us in the HELLO_ACK (i.e. what the canvas assigned). */
typedef struct {
    char ip[16];
    char mask[16];
    char gw[16];
    uint8_t mac[6];
    uint16_t mtu;
    bool have_mac;
    /* false = "nat": hide the AP-side devices behind our single fabric address.
     * true  = "routed": forward them untouched, so the emulated side can reach IN
     *         (the compiler has emitted a route for the physical subnet via us). */
    bool routed;
    /* The hotspot this board should raise, assigned by the canvas. Empty means
     * "keep what you have" (older relay, or nothing configured). */
    char ap_cidr[20];      /* e.g. 10.0.9.0/24 — we take .1 and DHCP the rest */
    char ap_ssid[33];
    char ap_pass[65];
    /* Resolver to hand real devices by DHCP. EMPTY means the canvas has no Internet
     * element, so offer none: a device given a resolver it cannot reach sits there
     * timing out, which looks like broken Wi-Fi rather than a network that deliberately
     * has no way out. Empty is an instruction, not a missing value. */
    char dns[16];
} fabric_netcfg_t;

/* --- implemented in gbridge_main.c, which owns the Wi-Fi interfaces ---------- */

/* Raise/adjust the soft AP to match what the canvas asked for. Safe to call
 * repeatedly: it returns immediately unless something actually changed, so a
 * keepalive every few seconds does not keep kicking connected devices off. */
void gb_ap_configure(const char *cidr, const char *ssid, const char *pass,
                     const char *dns);

/* Fill `out` with what this board can see, for the keepalive payload:
 *   ch=6 rssi=-71 up=lab-wifi sta=2 c=aa:bb:cc:dd:ee:ff/10.0.9.2 c=...
 * The FULL client list goes every time (not deltas) so a lost datagram cannot
 * strand a phantom device on the canvas. Returns bytes written. */
int gb_telemetry(char *out, size_t out_len);

/* Start the fabric link: opens the socket and spawns the RX task. Returns
 * immediately; the interface comes up only once the relay answers our HELLO, so
 * this is safe to call before the topology is running.
 *
 * `server` may be:
 *   "auto"            discover the relay with mDNS (`_gini._udp`, then gini.local)
 *   "name.local"      resolve that name with mDNS
 *   "192.168.1.42"    a literal address (also the fallback if discovery fails)
 *
 * The relay is re-discovered whenever the link goes quiet, so a laptop changing
 * address -- or a board carried to a different lab -- heals without reflashing. */
void fabric_start(const char *server, uint16_t server_port, const char *board_id,
                  const char *owner);

/* True once the relay has answered and the netif is configured and up. */
bool fabric_is_up(void);

/* Human-readable one-liner for the serial console's `status` command. */
void fabric_status(char *out, size_t out_len);

/* The laptop that owns this board, or "" while unclaimed. */
const char *fabric_owner(void);

/* Forget the current owner: the board becomes claimable again. Physical possession
 * is the authority here — you can always reach the board over USB — which is why
 * there is no timeout that would let a board re-open itself unattended. */
void fabric_unpair(void);

/* --- implemented in gbridge_main.c ------------------------------------------ */

/* Persist the owner (or "" to clear) so a claim survives a reboot. */
void gb_owner_save(const char *owner);

/* Flash the LED so a human can tell which board on the bench this is. */
void gb_blink(void);

#endif /* GBRIDGE_FABRIC_H */
