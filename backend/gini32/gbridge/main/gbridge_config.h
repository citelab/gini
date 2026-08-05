/* gBridge flash-time configuration.
 *
 * These are defaults compiled into the firmware. Every one of them can be
 * overridden at runtime over the serial console and is then persisted in NVS,
 * so a board only has to be reflashed when the firmware itself changes:
 *
 *     gini> set ssid    my-lab-wifi
 *     gini> set pass    hunter2
 *     gini> set server  192.168.1.42
 *     gini> set id      gini32-1
 *     gini> save
 *     gini> reboot
 *
 * Note what is deliberately NOT here: the board's fabric IP address, netmask,
 * gateway and MAC. Those belong to the drawn topology, and the relay hands them
 * to the board in its HELLO_ACK. The canvas is the single source of truth.
 */
#ifndef GBRIDGE_CONFIG_H
#define GBRIDGE_CONFIG_H

/* ===========================================================================
 * THE BOARD HAS TWO WI-FI FACES AT ONCE. Do not confuse them.
 *
 *   phone ──┐
 *   Pi ─────┼──▶ [ SoftAP "GINI32" │ board │ Station ] ──▶ your Wi-Fi ──▶ Mac
 *   sensor ─┘         ^^^^^^^^^^^^             ^^^^^^^
 *                 the network the board       the network the board
 *                 CREATES (GB_DEFAULT_AP_*)   JOINS  (GB_DEFAULT_STA_*)
 *
 * The board must JOIN your ordinary Wi-Fi because that is the only path to the
 * machine running gBuilder. A board that only raises its hotspot is an island.
 * ======================================================================== */

/* ---- STATION face: YOUR EXISTING Wi-Fi, which the board joins as a client --
 * >>> THIS IS THE ONE YOU MUST CHANGE before flashing a new board. <<<
 * The ESP32 radio is 2.4 GHz only, so a 5 GHz-only SSID will never be found
 * (you would see "reason 201 / NO_AP_FOUND" forever). */
#define GB_DEFAULT_STA_SSID     "MaxHotspot25"          /* <-- your Wi-Fi name     */
#define GB_DEFAULT_STA_PASS     "pass4max_"     /* <-- your Wi-Fi password */

/* ---- where the GINI server (the gbridge relay) is listening --------------- */
/* "auto" (the default) means DISCOVER the server with mDNS: gBuilder announces
 * itself on the local link as `_gini._udp` / `gini.local` while a topology with
 * boards is running, so nothing has to be told an address. This is what lets the
 * same board work on any laptop, and survive a laptop's address changing.
 *
 * Set it to a literal address ("192.168.1.42") to pin a board to one machine, or
 * to a name ending in ".local" to resolve just that name. A literal address is
 * also the fallback if discovery finds nothing. */
#define GB_DEFAULT_SERVER_IP    "auto"
#define GB_DEFAULT_SERVER_PORT  5555

/* How long to wait for a discovery answer, and how long a live link may go quiet
 * before we suspect the server moved and start looking again. */
#define GB_DISCOVER_TIMEOUT_MS  3000
#define GB_LINK_STALE_MS        30000

/* ---- this board's identity, matched against the canvas element ------------ */
/* Must equal the GINI32 element's "BoardID" property in gBuilder. */
#define GB_DEFAULT_BOARD_ID     "gini32-1"

/* ---- SOFT-AP face: the hotspot the board CREATES for real devices ---------
 * This is the network your phone/Pi/sensor joins to get inside the emulated
 * topology. The defaults are fine — you normally do NOT need to touch these. */
#define GB_DEFAULT_AP_SSID      "GINI32"          /* what the phone will see  */
#define GB_DEFAULT_AP_PASS      "gini12345"       /* >= 8 chars, or AP is open */
#define GB_DEFAULT_AP_CHANNEL   6
#define GB_AP_MAX_STA           4

/* The private subnet handed to devices behind the board (NAT mode). Devices get
 * addresses here from the board's own DHCP server and are translated onto the
 * board's single fabric address. */
#define GB_AP_IP                "10.0.9.1"
#define GB_AP_NETMASK           "255.255.255.0"

/* ---- fabric hop ---------------------------------------------------------- */
/* 1400 to leave room for the UDP/IP encapsulation on the physical LAN; the
 * gRouter's tun interfaces use the same figure. */
#define GB_FABRIC_MTU           1400
#define GB_HELLO_INTERVAL_MS    2000    /* re-announce until the relay answers   */
#define GB_KEEPALIVE_MS         5000    /* so the relay keeps our address fresh  */

#endif /* GBRIDGE_CONFIG_H */
