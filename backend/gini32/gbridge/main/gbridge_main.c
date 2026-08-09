/* gbridge_main.c -- GINI32 gateway firmware.
 *
 * Turns a bare ESP32 into a GINI32 node: a real radio that carries real devices
 * into an emulated GINI topology.
 *
 *   phone --802.11--> [ SoftAP | NAPT | fabric netif ] --UDP--> relay --> gRouter
 *                     \________ this firmware ________/
 *
 * The board runs in APSTA mode. Its station face joins the lab Wi-Fi and is the
 * path to the machine running gBuilder. Its soft-access-point face raises a small
 * network that phones, Raspberry Pis and other real devices join, with the board's
 * own DHCP server. Traffic from those devices is translated (NAPT) onto the
 * board's single fabric address and posted to the relay as Ethernet-in-UDP, where
 * it enters the emulated topology as ordinary traffic.
 *
 * Configuration lives in NVS and is editable over the serial console; see
 * gbridge_config.h for the compiled-in defaults and the command list.
 */
#include <string.h>
#include <stdio.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include "esp_system.h"
#include "esp_log.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_netif.h"
/* `dhcps_offer_t` (u8) and OFFER_DNS (0x02) — which options the soft AP's DHCP server
 * hands out. esp_netif.h does NOT pull these in: esp_netif_dhcps_option() takes a void*,
 * so the option types belong to the DHCP server, not the netif API. Verified against
 * IDF v5.4: both are in dhcpserver.h (NOT dhcpserver_options.h, which exists but only
 * carries the option-code defines). Reached via the lwip/include/apps include path. */
#include "dhcpserver/dhcpserver.h"
/* Pairing a connected station's MAC with the address our DHCP server gave it needs an
 * API whose home has moved between IDF releases, and which is absent from IDF v5.4's
 * public headers here. MACs always work (esp_wifi_ap_get_sta_list), so that is the
 * baseline; define GB_STA_IP_HEADER to the right header to get addresses back:
 *
 *     idf.py build -DGB_STA_IP_HEADER='"esp_netif_sta_list.h"'
 *
 * Note this is a SYMBOL question, not a file question — a header can exist and still
 * not declare esp_netif_get_sta_list, which is exactly what __has_include got wrong. */
#ifdef GB_STA_IP_HEADER
#  include GB_STA_IP_HEADER
#  define GB_HAVE_STA_IPS 1
#endif
#include "nvs_flash.h"
#include "nvs.h"
#include "driver/uart.h"
#include "driver/gpio.h"
#include "led_strip.h"          /* the WS2812 on most S3 devkits — see gb_blink() */
#include "lwip/ip4_addr.h"
#include "lwip/sockets.h"
#include "esp_timer.h"
#include <errno.h>
#include <unistd.h>          /* close() for the ping socket */

#include "gbridge_config.h"
#include "fabric.h"

static const char *TAG = "gbridge";
static const char *NVS_NS = "gbridge";

#define WIFI_CONNECTED_BIT BIT0

typedef struct {
    char sta_ssid[33];
    char sta_pass[65];
    char server_ip[16];
    uint16_t server_port;
    char board_id[17];
    char ap_ssid[33];
    char ap_pass[65];
    uint8_t ap_channel;
    /* The laptop that claimed this board. Empty = unclaimed, so any laptop
     * may adopt it. Persisted, so a claim survives a power cut. */
    char owner[40];
    /* Which pin the indicator LED is on; -1 = this board has none.
     *
     * A SETTING rather than a compile-time constant because the pin differs between
     * board models and is the one thing about a board you cannot determine from
     * software. Baking in a guess means a wrong guess costs a full build-and-flash
     * cycle to test the next candidate; `set led 13` costs a second. */
    int16_t led_gpio;
    /* 1 = the pin drives an ADDRESSABLE WS2812 (one pixel), 0 = a plain LED.
     * Not detectable either: both are "a pin with an LED on it" from software, and
     * driving the wrong one produces silence rather than an error. */
    uint8_t led_rgb;
} gb_settings_t;

static gb_settings_t s_set;
static int s_join_fails;              /* consecutive failures to join the uplink */
static EventGroupHandle_t s_wifi_events;
static esp_netif_t *s_ap_netif;
static esp_netif_t *s_sta_netif;

/* ------------------------------------------------------------------- config */

static void settings_defaults(gb_settings_t *s)
{
    memset(s, 0, sizeof(*s));
    strncpy(s->sta_ssid, GB_DEFAULT_STA_SSID, sizeof(s->sta_ssid) - 1);
    strncpy(s->sta_pass, GB_DEFAULT_STA_PASS, sizeof(s->sta_pass) - 1);
    strncpy(s->server_ip, GB_DEFAULT_SERVER_IP, sizeof(s->server_ip) - 1);
    s->server_port = GB_DEFAULT_SERVER_PORT;
    strncpy(s->board_id, GB_DEFAULT_BOARD_ID, sizeof(s->board_id) - 1);
    strncpy(s->ap_ssid, GB_DEFAULT_AP_SSID, sizeof(s->ap_ssid) - 1);
    strncpy(s->ap_pass, GB_DEFAULT_AP_PASS, sizeof(s->ap_pass) - 1);
    s->ap_channel = GB_DEFAULT_AP_CHANNEL;
    s->led_gpio = GB_DEFAULT_LED_GPIO;
    s->led_rgb = GB_DEFAULT_LED_RGB;
}

static void nvs_get_str_or(nvs_handle_t h, const char *key, char *dst, size_t len)
{
    size_t n = len;
    if (nvs_get_str(h, key, dst, &n) != ESP_OK) {
        /* leave the compiled-in default in place */
    }
}

static void settings_load(gb_settings_t *s)
{
    settings_defaults(s);
    nvs_handle_t h;
    if (nvs_open(NVS_NS, NVS_READONLY, &h) != ESP_OK) {
        ESP_LOGI(TAG, "no saved settings; using flash-time defaults");
        return;
    }
    nvs_get_str_or(h, "ssid", s->sta_ssid, sizeof(s->sta_ssid));
    nvs_get_str_or(h, "pass", s->sta_pass, sizeof(s->sta_pass));
    nvs_get_str_or(h, "server", s->server_ip, sizeof(s->server_ip));
    nvs_get_str_or(h, "id", s->board_id, sizeof(s->board_id));
    nvs_get_str_or(h, "apssid", s->ap_ssid, sizeof(s->ap_ssid));
    nvs_get_str_or(h, "appass", s->ap_pass, sizeof(s->ap_pass));
    nvs_get_str_or(h, "owner", s->owner, sizeof(s->owner));
    uint16_t port = 0;
    if (nvs_get_u16(h, "port", &port) == ESP_OK && port) {
        s->server_port = port;
    }
    uint8_t ch = 0;
    if (nvs_get_u8(h, "apchan", &ch) == ESP_OK && ch) {
        s->ap_channel = ch;
    }
    /* No `&& led` guard here, unlike the two above: -1 ("this board has no LED") and
     * 0 (GPIO 0) are both meaningful saved values, so absence is the ONLY reason to
     * keep the default. */
    int16_t led = 0;
    if (nvs_get_i16(h, "led", &led) == ESP_OK) {
        s->led_gpio = led;
    }
    uint8_t rgb = 0;
    if (nvs_get_u8(h, "ledrgb", &rgb) == ESP_OK) {
        s->led_rgb = rgb ? 1 : 0;       /* 0 is a real saved value here too */
    }
    nvs_close(h);
}

static esp_err_t settings_save(const gb_settings_t *s)
{
    nvs_handle_t h;
    esp_err_t err = nvs_open(NVS_NS, NVS_READWRITE, &h);
    if (err != ESP_OK) {
        return err;
    }
    nvs_set_str(h, "ssid", s->sta_ssid);
    nvs_set_str(h, "pass", s->sta_pass);
    nvs_set_str(h, "server", s->server_ip);
    nvs_set_str(h, "id", s->board_id);
    nvs_set_str(h, "apssid", s->ap_ssid);
    nvs_set_str(h, "appass", s->ap_pass);
    nvs_set_u16(h, "port", s->server_port);
    nvs_set_u8(h, "apchan", s->ap_channel);
    nvs_set_i16(h, "led", s->led_gpio);
    nvs_set_u8(h, "ledrgb", s->led_rgb);
    /* NOTE: `owner` is deliberately NOT written here. A claim is changed only by
     * claiming or releasing (gb_owner_save), never as a side effect of `save` —
     * otherwise editing an unrelated setting could silently re-own the board. */
    err = nvs_commit(h);
    nvs_close(h);
    return err;
}

/* --------------------------------------------------------------- wifi setup */

static void on_wifi_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        wifi_event_sta_disconnected_t *e = (wifi_event_sta_disconnected_t *) data;
        bool had_link = (xEventGroupGetBits(s_wifi_events) & WIFI_CONNECTED_BIT) != 0;
        xEventGroupClearBits(s_wifi_events, WIFI_CONNECTED_BIT);

        /* Distinguish "we were online and dropped" from "we have never got on".
         * The second is nearly always a wrong SSID/password, and saying "lost"
         * sends people hunting for a signal problem that isn't there. */
        if (had_link) {
            s_join_fails = 0;
            ESP_LOGW(TAG, "uplink lost (reason %d); reconnecting", e->reason);
        /* Rate-limit by REASON, not by attempt count. A join that fails the same way
         * forever is one fact and should be said once; a join whose reason changes is
         * new information every time. Counting attempts instead hides exactly the
         * transitions that identify the fault. */
        } else {
            static int s_last_reason = -1;
            ++s_join_fails;
            if (s_join_fails == 1 || e->reason != s_last_reason ||
                (s_join_fails % 20) == 0) {
                const char *why =
                    (e->reason == WIFI_REASON_NO_AP_FOUND)
                        ? "no such network in range — check 'set ssid' (2.4 GHz only)"
                    : (e->reason == WIFI_REASON_AUTH_FAIL ||
                       e->reason == WIFI_REASON_4WAY_HANDSHAKE_TIMEOUT ||
                       e->reason == WIFI_REASON_HANDSHAKE_TIMEOUT)
                        ? "wrong password — check 'set pass'"
                    /* Reached the AP and got pushed back off. Genuinely ambiguous:
                     * a bad key looks like this too, but so does an AP that requires
                     * PMF, or one that is full, or MAC filtering. */
                    : (e->reason == WIFI_REASON_AUTH_EXPIRE ||
                       e->reason == WIFI_REASON_ASSOC_EXPIRE ||
                       e->reason == WIFI_REASON_ASSOC_FAIL ||
                       e->reason == WIFI_REASON_CONNECTION_FAIL)
                        ? "the AP answered then dropped us — usually a wrong password, "
                          "otherwise the AP is full or refusing this client"
                        : "see the reason code in the ESP-IDF docs";
                ESP_LOGW(TAG, "cannot join '%s' (attempt %d, reason %d): %s",
                         s_set.sta_ssid, s_join_fails, e->reason, why);
                s_last_reason = e->reason;
            }
        }
        /* Back off so a misconfigured board does not hot-loop the radio or drown
         * the console you need in order to fix it. */
        vTaskDelay(pdMS_TO_TICKS(s_join_fails > 5 ? 5000 : 1000));
        esp_wifi_connect();
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *e = (ip_event_got_ip_t *) data;
        s_join_fails = 0;
        ESP_LOGI(TAG, "uplink up, address " IPSTR, IP2STR(&e->ip_info.ip));
        xEventGroupSetBits(s_wifi_events, WIFI_CONNECTED_BIT);
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_AP_STACONNECTED) {
        wifi_event_ap_staconnected_t *e = (wifi_event_ap_staconnected_t *) data;
        ESP_LOGI(TAG, "device joined: %02x:%02x:%02x:%02x:%02x:%02x",
                 e->mac[0], e->mac[1], e->mac[2], e->mac[3], e->mac[4], e->mac[5]);
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_AP_STADISCONNECTED) {
        wifi_event_ap_stadisconnected_t *e = (wifi_event_ap_stadisconnected_t *) data;
        ESP_LOGI(TAG, "device left: %02x:%02x:%02x:%02x:%02x:%02x",
                 e->mac[0], e->mac[1], e->mac[2], e->mac[3], e->mac[4], e->mac[5]);
    }
}

static void wifi_start_apsta(void)
{
    s_wifi_events = xEventGroupCreate();
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    s_sta_netif = esp_netif_create_default_wifi_sta();
    s_ap_netif = esp_netif_create_default_wifi_ap();

    /* Put the soft AP on the private subnet devices will be NATed out of. */
    esp_netif_ip_info_t ap_ip;
    memset(&ap_ip, 0, sizeof(ap_ip));
    ap_ip.ip.addr = ipaddr_addr(GB_AP_IP);
    ap_ip.gw.addr = ipaddr_addr(GB_AP_IP);
    ap_ip.netmask.addr = ipaddr_addr(GB_AP_NETMASK);
    ESP_ERROR_CHECK(esp_netif_dhcps_stop(s_ap_netif));
    ESP_ERROR_CHECK(esp_netif_set_ip_info(s_ap_netif, &ap_ip));
    ESP_ERROR_CHECK(esp_netif_dhcps_start(s_ap_netif));

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID,
                                               &on_wifi_event, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP,
                                               &on_wifi_event, NULL));

    wifi_config_t sta = { 0 };
    strncpy((char *) sta.sta.ssid, s_set.sta_ssid, sizeof(sta.sta.ssid) - 1);
    strncpy((char *) sta.sta.password, s_set.sta_pass, sizeof(sta.sta.password) - 1);
    /* Protected Management Frames: "capable, not required" is the setting that joins
     * the widest range of APs. Leaving the zeroed default (capable = false) makes us
     * look PMF-incapable, so any AP that REQUIRES PMF — WPA3, and the WPA2/WPA3
     * transitional mode that modern phone and macOS hotspots raise by default — lets
     * us authenticate and associate and then drops us. That failure looks nothing
     * like a rejection; it looks like a flaky signal, which is why it wastes hours.
     * `required = false` keeps plain WPA2 lab APs working. */
    sta.sta.pmf_cfg.capable  = true;
    sta.sta.pmf_cfg.required = false;

    /* A board that has never been configured by a canvas must NOT raise the bare
     * factory name: every board would raise the SAME one, and a room of thirty boards
     * booting means thirty access points called "GINI32". A phone then attaches to
     * whichever it heard loudest, which is a genuinely nasty thing to debug in a class.
     * Qualify it with the board's own id, which is unique by construction. */
    if (!strcmp(s_set.ap_ssid, GB_DEFAULT_AP_SSID) && s_set.board_id[0]) {
        char boot_ssid[33];
        snprintf(boot_ssid, sizeof(boot_ssid), "%s-%s", GB_DEFAULT_AP_SSID, s_set.board_id);
        strncpy(s_set.ap_ssid, boot_ssid, sizeof(s_set.ap_ssid) - 1);
        s_set.ap_ssid[sizeof(s_set.ap_ssid) - 1] = '\0';
    }

    wifi_config_t ap = { 0 };
    strncpy((char *) ap.ap.ssid, s_set.ap_ssid, sizeof(ap.ap.ssid) - 1);
    ap.ap.ssid_len = strlen(s_set.ap_ssid);
    strncpy((char *) ap.ap.password, s_set.ap_pass, sizeof(ap.ap.password) - 1);
    ap.ap.channel = s_set.ap_channel;
    ap.ap.max_connection = GB_AP_MAX_STA;
    ap.ap.authmode = (strlen(s_set.ap_pass) >= 8) ? WIFI_AUTH_WPA2_PSK : WIFI_AUTH_OPEN;

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_APSTA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &sta));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_AP, &ap));
    ESP_ERROR_CHECK(esp_wifi_start());

    /* NOTE: address translation is deliberately NOT set up here. Whether the board
     * NATs its devices or forwards them untouched depends on the element's Mode on
     * the canvas, which we only learn when the relay answers — so fabric.c applies
     * it once the mode is known. See fabric_configure(). */

    /* APSTA shares one radio, so the AP follows the uplink's channel. Announce the
     * real channel rather than the configured one to avoid confusing students. */
    ESP_LOGI(TAG, "radio up, two faces:");
    ESP_LOGI(TAG, "  hotspot  '%s'  <- real devices join THIS (%s)",
             s_set.ap_ssid, GB_AP_IP);
    ESP_LOGI(TAG, "  uplink   '%s'  <- board joins YOUR wi-fi to reach gBuilder",
             s_set.sta_ssid);
}

/* --------------------------------------------------------------- claiming */

void gb_owner_save(const char *owner)
{
    nvs_handle_t h;
    if (nvs_open(NVS_NS, NVS_READWRITE, &h) != ESP_OK) {
        ESP_LOGE(TAG, "could not persist the claim — it will be lost on reboot");
        return;
    }
    nvs_set_str(h, "owner", owner ? owner : "");
    nvs_commit(h);
    nvs_close(h);
}

/* Flash the on-board LED so a human can pick this board out of a row of identical
 * ones. Claiming by name is useless if you cannot tell which object you are naming. */
void gb_blink(void)
{
    /* This used to be `#ifdef GB_LED_GPIO`, and GB_LED_GPIO was never defined anywhere
     * in the tree — so every board ever built took the log-only branch below and Blink
     * appeared to do nothing at all. A fallback that is silently the ONLY path is worse
     * than no fallback: the feature looked implemented and wasn't. The pin is a runtime
     * setting now, so a board with no LED is a deliberate `set led -1`, not an accident
     * of the build. */
    const int pin = s_set.led_gpio;
    if (pin < 0 || !GPIO_IS_VALID_OUTPUT_GPIO(pin)) {
        for (int i = 0; i < 3; i++) {
            ESP_LOGW(TAG, ">>> THIS IS THE BOARD YOU ARE LOOKING FOR (%s) <<< "
                          "(no LED pin set — `set led <gpio>` to use one)", s_set.board_id);
            vTaskDelay(pdMS_TO_TICKS(300));
        }
        return;
    }
    if (s_set.led_rgb) {
        /* An addressable WS2812 — what most ESP32-S3 devkits carry INSTEAD of a plain
         * LED (DevKitC-1: GPIO38 on v1.1, GPIO48 on the original; the only other LED on
         * that board is the power indicator, hardwired to the rail). It decodes pulse
         * WIDTHS in the hundreds of nanoseconds, so toggling the pin every 120 ms reads
         * as a long reset and the LED simply stays dark — which is exactly what a wrong
         * `set led <pin>` looks like, hence the explicit `rgb` flag rather than a guess.
         *
         * The strip is created and torn down per blink so the RMT channel is held only
         * while flashing; blink is rare and not on any hot path. */
        led_strip_handle_t strip = NULL;
        led_strip_config_t scfg = {
            .strip_gpio_num = pin,
            .max_leds = 1,
        };
        led_strip_rmt_config_t rcfg = {
            .clk_src = RMT_CLK_SRC_DEFAULT,
            .resolution_hz = 10 * 1000 * 1000,   /* 10 MHz: 0.1 us per tick */
        };
        esp_err_t err = led_strip_new_rmt_device(&scfg, &rcfg, &strip);
        if (err != ESP_OK || !strip) {
            ESP_LOGW(TAG, "cannot drive an RGB LED on gpio %d: %s", pin,
                     esp_err_to_name(err));
            return;
        }
        for (int i = 0; i < 6; i++) {
            /* Deliberately dim. These are bright enough at full scale to be unpleasant
             * to look at on a bench, and we only need to be identifiable, not blinding. */
            led_strip_set_pixel(strip, 0, 0, 40, 60);
            led_strip_refresh(strip);
            vTaskDelay(pdMS_TO_TICKS(150));
            led_strip_clear(strip);
            vTaskDelay(pdMS_TO_TICKS(150));
        }
        led_strip_del(strip);
        return;
    }

    gpio_reset_pin((gpio_num_t) pin);
    gpio_set_direction((gpio_num_t) pin, GPIO_MODE_OUTPUT);
    for (int i = 0; i < 12; i++) {
        gpio_set_level((gpio_num_t) pin, i & 1);
        vTaskDelay(pdMS_TO_TICKS(120));
    }
    gpio_set_level((gpio_num_t) pin, 0);
}

/* ------------------------------------------------ canvas-driven hotspot ---- */

/* What we last applied, so a keepalive every few seconds does not keep
 * reconfiguring the radio and kicking connected devices off. */
static char s_ap_applied_cidr[20];
static char s_ap_applied_ssid[33];
static char s_ap_applied_pass[65];
static char s_ap_applied_dns[16];
static bool s_dns_ever_applied = false;   /* "" is a real value, so track "never set" */

/* Tell the DHCP server which resolver to hand out, or to stop offering one.
 *
 * Without this a device gets an address and a gateway but NO name server, which is a
 * peculiarly confusing failure: `ping 8.8.8.8` works, so the network is obviously fine,
 * yet nothing loads. ESP-IDF's softAP does not offer DNS unless asked to.
 *
 * The address comes from the canvas' Internet element, so removing that element stops
 * the offer — a device is never promised name resolution the topology cannot deliver.
 */
static void ap_apply_dns(const char *dns)
{
    esp_netif_dhcps_stop(s_ap_netif);            /* required before either call below */

    dhcps_offer_t offer = dns[0] ? OFFER_DNS : 0;
    if (dns[0]) {
        esp_netif_dns_info_t info;
        memset(&info, 0, sizeof(info));
        info.ip.type = ESP_IPADDR_TYPE_V4;
        info.ip.u_addr.ip4.addr = ipaddr_addr(dns);
        if (esp_netif_set_dns_info(s_ap_netif, ESP_NETIF_DNS_MAIN, &info) != ESP_OK) {
            ESP_LOGW(TAG, "could not set hotspot DNS to %s", dns);
        }
    }
    esp_netif_dhcps_option(s_ap_netif, ESP_NETIF_OP_SET,
                           ESP_NETIF_DOMAIN_NAME_SERVER, &offer, sizeof(offer));
    esp_netif_dhcps_start(s_ap_netif);

    if (dns[0]) {
        ESP_LOGI(TAG, "hotspot DNS %s — devices can resolve names through the topology",
                 dns);
    } else {
        ESP_LOGI(TAG, "hotspot offers no DNS (no Internet element on the canvas) — "
                      "devices can reach the drawn network but cannot resolve names");
    }
    /* Existing devices keep the lease they already have, so they pick this up on their
     * next renewal or when they rejoin. Nothing is kicked off deliberately: dropping
     * every device to change one DHCP option is a worse trade than a delayed update. */
}

void gb_ap_configure(const char *cidr, const char *ssid, const char *pass,
                     const char *dns)
{
    cidr = cidr ? cidr : "";
    ssid = ssid ? ssid : "";
    pass = pass ? pass : "";
    dns = dns ? dns : "";

    bool net_changed = cidr[0] && strcmp(cidr, s_ap_applied_cidr) != 0;
    bool wifi_changed = (ssid[0] && strcmp(ssid, s_ap_applied_ssid) != 0)
                        || (strcmp(pass, s_ap_applied_pass) != 0);
    /* No `dns[0] &&` guard, unlike ssid: going from a resolver to none is a real change
     * that must be applied, and it is what deleting the Internet element looks like. */
    bool dns_changed = !s_dns_ever_applied || strcmp(dns, s_ap_applied_dns) != 0;
    if (!net_changed && !wifi_changed && !dns_changed) {
        return;                                  /* nothing to do — stay quiet */
    }

    if (dns_changed) {
        ap_apply_dns(dns);
        strncpy(s_ap_applied_dns, dns, sizeof(s_ap_applied_dns) - 1);
        s_ap_applied_dns[sizeof(s_ap_applied_dns) - 1] = '\0';
        s_dns_ever_applied = true;
    }

    if (net_changed) {
        /* "10.0.9.0/24" -> we are 10.0.9.1, DHCP hands out the rest. */
        unsigned a, b, c, d, bits = 24;
        if (sscanf(cidr, "%u.%u.%u.%u/%u", &a, &b, &c, &d, &bits) >= 4) {
            char ip[16], mask[16];
            snprintf(ip, sizeof(ip), "%u.%u.%u.1", a, b, c);
            uint32_t m = bits >= 32 ? 0xFFFFFFFFu : ~((1u << (32 - bits)) - 1);
            /* uint32_t is `unsigned long` on xtensa, so the shifts must be narrowed
             * explicitly — %u would otherwise be a format/type mismatch. */
            snprintf(mask, sizeof(mask), "%u.%u.%u.%u",
                     (unsigned) ((m >> 24) & 0xff), (unsigned) ((m >> 16) & 0xff),
                     (unsigned) ((m >> 8) & 0xff), (unsigned) (m & 0xff));

            esp_netif_ip_info_t info;
            memset(&info, 0, sizeof(info));
            info.ip.addr = ipaddr_addr(ip);
            info.gw.addr = ipaddr_addr(ip);
            info.netmask.addr = ipaddr_addr(mask);

            /* DHCP must be stopped to change the interface's address. */
            esp_netif_dhcps_stop(s_ap_netif);
            if (esp_netif_set_ip_info(s_ap_netif, &info) == ESP_OK) {
                ESP_LOGI(TAG, "hotspot subnet %s (we are %s)", cidr, ip);
                strncpy(s_ap_applied_cidr, cidr, sizeof(s_ap_applied_cidr) - 1);
            } else {
                ESP_LOGE(TAG, "could not set hotspot subnet %s", cidr);
            }
            esp_netif_dhcps_start(s_ap_netif);
        } else {
            ESP_LOGW(TAG, "hotspot subnet %s is not a CIDR I understand", cidr);
        }
    }

    if (wifi_changed) {
        wifi_config_t ap = { 0 };
        const char *use_ssid = ssid[0] ? ssid : s_set.ap_ssid;
        strncpy((char *) ap.ap.ssid, use_ssid, sizeof(ap.ap.ssid) - 1);
        ap.ap.ssid_len = strlen(use_ssid);
        strncpy((char *) ap.ap.password, pass, sizeof(ap.ap.password) - 1);
        ap.ap.channel = s_set.ap_channel;        /* ignored in APSTA; uplink wins */
        ap.ap.max_connection = GB_AP_MAX_STA;
        ap.ap.authmode = (strlen(pass) >= 8) ? WIFI_AUTH_WPA2_PSK : WIFI_AUTH_OPEN;
        if (esp_wifi_set_config(WIFI_IF_AP, &ap) == ESP_OK) {
            ESP_LOGI(TAG, "hotspot is now '%s' (%s)", use_ssid,
                     strlen(pass) >= 8 ? "WPA2" : "open");
            strncpy(s_ap_applied_ssid, use_ssid, sizeof(s_ap_applied_ssid) - 1);
            strncpy(s_ap_applied_pass, pass, sizeof(s_ap_applied_pass) - 1);
            /* Remember the name across reboots. It used to live only in the RAM copy
             * above, so a board reverted to the factory SSID on every power cycle and
             * every phone on it had to be re-pointed by hand — even though the canvas
             * had named it. Written only inside `wifi_changed`, so this is a handful of
             * NVS writes over a board's life, not one per keepalive. */
            strncpy(s_set.ap_ssid, use_ssid, sizeof(s_set.ap_ssid) - 1);
            strncpy(s_set.ap_pass, pass, sizeof(s_set.ap_pass) - 1);
            nvs_handle_t h;
            if (nvs_open(NVS_NS, NVS_READWRITE, &h) == ESP_OK) {
                nvs_set_str(h, "apssid", s_set.ap_ssid);
                nvs_set_str(h, "appass", s_set.ap_pass);
                nvs_commit(h);
                nvs_close(h);
            }
        } else {
            ESP_LOGE(TAG, "could not set hotspot SSID '%s'", use_ssid);
        }
    }
}

/* --------------------------------------------------------------- telemetry */

int gb_telemetry(char *out, size_t out_len)
{
    int n = 0;

    uint8_t primary = 0;
    wifi_second_chan_t second;
    esp_wifi_get_channel(&primary, &second);

    wifi_ap_record_t up;
    int rssi = 0;
    const char *ssid = "";
    if (esp_wifi_sta_get_ap_info(&up) == ESP_OK) {
        rssi = up.rssi;
        ssid = (const char *) up.ssid;
    }
    n += snprintf(out + n, out_len - n, "ch=%u rssi=%d up=%s",
                  (unsigned) primary, rssi, ssid);

    /* Every device currently associated to our hotspot. This is what makes a real
     * phone show up on the canvas. The MAC always comes from esp_wifi; the address
     * our DHCP server handed out needs the sta-list API, which is optional above. */
    wifi_sta_list_t sta;
    if (esp_wifi_ap_get_sta_list(&sta) != ESP_OK) {
        return n + snprintf(out + n, out_len - n, " sta=0");
    }
    n += snprintf(out + n, out_len - n, " sta=%d", sta.num);

#ifdef GB_HAVE_STA_IPS
    esp_netif_sta_list_t netif_sta;
    if (esp_netif_get_sta_list(&sta, &netif_sta) == ESP_OK) {
        for (int i = 0; i < netif_sta.num && n < (int) out_len - 40; i++) {
            const uint8_t *m = netif_sta.sta[i].mac;
            n += snprintf(out + n, out_len - n,
                          " c=%02x:%02x:%02x:%02x:%02x:%02x/" IPSTR,
                          m[0], m[1], m[2], m[3], m[4], m[5],
                          IP2STR(&netif_sta.sta[i].ip));
        }
        return n;
    }
#endif
    /* No address available: still report the device, with an unknown address. The
     * canvas can show it joined even if it cannot show where. */
    for (int i = 0; i < sta.num && n < (int) out_len - 30; i++) {
        const uint8_t *m = sta.sta[i].mac;
        n += snprintf(out + n, out_len - n, " c=%02x:%02x:%02x:%02x:%02x:%02x/?",
                      m[0], m[1], m[2], m[3], m[4], m[5]);
    }
    return n;
}

/* ------------------------------------------------------------------- ping */

/* Ping FROM the board.
 *
 * This exists because of a question that kept coming up and could not be answered:
 * when an emulated machine cannot reach a phone on the hotspot, is the board failing
 * to forward, or is the phone simply not replying? Every other test involves both at
 * once. From here the board talks to the device directly on its own AP subnet, with
 * no forwarding and no fabric in the path, so the two are finally separable.
 *
 * `ping 10.0.9.2`  -- a device on our hotspot: pure radio, no forwarding.
 * `ping 10.0.1.1`  -- the gRouter: our fabric link, no radio.
 * Phones are the usual culprit: iOS keeps an association on a network it has decided
 * has no internet, while quietly ignoring traffic on it.
 */
static uint16_t icmp_cksum(const void *data, size_t len)
{
    const uint8_t *b = (const uint8_t *) data;
    uint32_t sum = 0;
    for (size_t i = 0; i + 1 < len; i += 2) {
        sum += (uint32_t) ((b[i] << 8) | b[i + 1]);
    }
    if (len & 1) {
        sum += (uint32_t) (b[len - 1] << 8);
    }
    while (sum >> 16) {
        sum = (sum & 0xffff) + (sum >> 16);
    }
    return (uint16_t) ~sum;
}

static void cmd_ping(const char *target)
{
    while (*target == ' ') {
        target++;
    }
    struct sockaddr_in dst;
    memset(&dst, 0, sizeof(dst));
    dst.sin_family = AF_INET;
    dst.sin_addr.s_addr = ipaddr_addr(target);
    if (dst.sin_addr.s_addr == IPADDR_NONE || !*target) {
        printf("usage: ping <ip>   (e.g. ping 10.0.9.2)\n");
        return;
    }

    /* IPPROTO_ICMP is the socket-API constant; IP_PROTO_ICMP is lwIP's internal
     * protocol number and is not visible through lwip/sockets.h. */
    int s = socket(AF_INET, SOCK_RAW, IPPROTO_ICMP);
    if (s < 0) {
        printf("ping: cannot open a raw socket (errno %d)\n", errno);
        return;
    }
    struct timeval tv = { .tv_sec = 2, .tv_usec = 0 };
    setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

    int replies = 0;
    for (int seq = 1; seq <= 3; seq++) {
        uint8_t pkt[32];
        memset(pkt, 0, sizeof(pkt));
        pkt[0] = 8;                                  /* type 8 = echo request     */
        pkt[4] = 0x67; pkt[5] = 0x62;                /* id "gb"                   */
        pkt[6] = (uint8_t) (seq >> 8); pkt[7] = (uint8_t) seq;
        memcpy(pkt + 8, "gini32", 6);
        uint16_t ck = icmp_cksum(pkt, sizeof(pkt));
        pkt[2] = (uint8_t) (ck >> 8); pkt[3] = (uint8_t) ck;

        int64_t t0 = esp_timer_get_time();
        if (sendto(s, pkt, sizeof(pkt), 0, (struct sockaddr *) &dst, sizeof(dst)) < 0) {
            printf("  seq=%d  send failed (errno %d) -- no route to %s?\n",
                   seq, errno, target);
            continue;
        }
        /* Read until we see OUR echo reply: a raw ICMP socket also sees other
         * traffic, and reporting someone else's packet as a reply would be worse
         * than reporting nothing. */
        for (;;) {
            uint8_t buf[128];
            struct sockaddr_in from;
            socklen_t flen = sizeof(from);
            int n = recvfrom(s, buf, sizeof(buf), 0, (struct sockaddr *) &from, &flen);
            if (n <= 0) {
                printf("  seq=%d  no reply (timeout)\n", seq);
                break;
            }
            int ihl = (buf[0] & 0x0f) * 4;           /* skip the IP header       */
            if (n < ihl + 8 || buf[ihl] != 0) {
                continue;                            /* not an echo REPLY        */
            }
            if (buf[ihl + 4] != 0x67 || buf[ihl + 5] != 0x62) {
                continue;                            /* not ours                 */
            }
            int rseq = (buf[ihl + 6] << 8) | buf[ihl + 7];
            if (rseq != seq) {
                continue;
            }
            /* Integer milliseconds on purpose. "%f" drags in newlib's floating-point
             * formatter, which needs far more stack than a console task has — it
             * overflowed and panicked the board on the first successful reply. */
            printf("  seq=%d  reply from %s  %d ms\n", seq, target,
                   (int) ((esp_timer_get_time() - t0) / 1000));
            replies++;
            break;
        }
        vTaskDelay(pdMS_TO_TICKS(300));
    }
    close(s);

    printf("ping %s: %d/3 replied", target, replies);
    if (replies == 0) {
        printf(" -- nothing came back. If this is a device on our hotspot, the board "
               "can reach it over the radio only if the device answers: phones often "
               "hold the association but ignore a network they think has no internet.");
    }
    printf("\n");
}

/* ---------------------------------------------------------- serial console */

static void print_settings(void)
{
    printf("  ssid    %s\n", s_set.sta_ssid);
    printf("  pass    %s\n", s_set.sta_pass[0] ? "(set)" : "(empty)");
    printf("  server  %s:%u\n", s_set.server_ip, (unsigned) s_set.server_port);
    printf("  id      %s\n", s_set.board_id);
    printf("  apssid  %s\n", s_set.ap_ssid);
    printf("  appass  %s\n", s_set.ap_pass[0] ? "(set)" : "(open)");
    printf("  apchan  %u\n", (unsigned) s_set.ap_channel);
    if (s_set.led_gpio < 0) {
        printf("  led     (none — `set led <gpio>` to make Blink light something)\n");
    } else {
        printf("  led     gpio %d (%s)\n", (int) s_set.led_gpio,
               s_set.led_rgb ? "addressable RGB" : "plain");
    }
}

static void handle_command(char *line)
{
    while (*line == ' ') {
        line++;
    }
    if (!*line) {
        return;
    }

    if (!strcmp(line, "help") || !strcmp(line, "?")) {
        printf("commands:\n"
               "  status              link state, counters, and who owns this board\n"
               "  show                current settings\n"
               "  set <key> <value>   ssid|pass|server|port|id|apssid|appass|apchan|led\n"
               "  save                persist settings to NVS\n"
               "  unpair              release this board so another laptop can claim it\n"
               "  blink               flash the LED, to find this board on the bench\n"
               "                      (needs `set led <gpio> [rgb|plain]` once — the pin\n"
               "                      and the LED type differ per board model and cannot\n"
               "                      be detected; S3 devkits are usually `38 rgb`)\n"
               "  ping <ip>           ping FROM the board — the only way to tell a\n"
               "                      forwarding fault from a device that is not\n"
               "                      answering (try a phone on the hotspot, then\n"
               "                      the gRouter)\n"
               "  reboot              restart the board\n");
        return;
    }
    if (!strcmp(line, "status")) {
        char buf[512];      /* the DOWN text carries counters + what they mean */
        fabric_status(buf, sizeof(buf));
        printf("%s\n", buf);
        /* The radio, in the same breath as the link. Signal strength is the first
         * thing to check when anything is flaky — and on a WROOM-1U with no antenna
         * fitted it is the whole story — so it should not require reading a keepalive
         * off the relay to see it. */
        char radio[400];
        gb_telemetry(radio, sizeof(radio));
        printf("radio: %s\n", radio);
        /* What devices are handed for name resolution. Worth its own line: "ping 8.8.8.8
         * works but nothing loads" is the signature of a missing DNS option, and this
         * turns that from a guess into a reading. */
        printf("dhcp:  %s\n", s_ap_applied_dns[0]
               ? s_ap_applied_dns
               : "no DNS offered (no Internet element on the canvas)");
        const char *o = fabric_owner();
        printf("claim: %s\n", o && o[0] ? o
               : "unclaimed — any gBuilder on this network may adopt this board");
        return;
    }
    if (!strcmp(line, "unpair")) {
        /* Physical possession is the authority: whoever can reach this console can
         * free the board. That is why there is no timed auto-release — a board must
         * never re-open itself while its owner is away from the bench. */
        const char *o = fabric_owner();
        if (!o || !o[0]) {
            printf("already unclaimed\n");
        } else {
            printf("released from '%s' — this board can now be claimed again\n", o);
            fabric_unpair();
        }
        return;
    }
    if (!strcmp(line, "blink")) {
        gb_blink();
        return;
    }
    if (!strncmp(line, "ping ", 5)) {
        cmd_ping(line + 5);
        return;
    }
    if (!strcmp(line, "show")) {
        print_settings();
        return;
    }
    if (!strcmp(line, "save")) {
        printf(settings_save(&s_set) == ESP_OK
               ? "saved (reboot to apply Wi-Fi changes)\n" : "save FAILED\n");
        return;
    }
    if (!strcmp(line, "reboot")) {
        printf("rebooting\n");
        vTaskDelay(pdMS_TO_TICKS(200));
        esp_restart();
    }
    if (!strncmp(line, "set ", 4)) {
        char *key = line + 4;
        while (*key == ' ') {
            key++;
        }
        char *val = strchr(key, ' ');
        if (!val) {
            printf("usage: set <key> <value>\n");
            return;
        }
        *val++ = '\0';
        while (*val == ' ') {
            val++;
        }
        if (!strcmp(key, "ssid")) {
            strncpy(s_set.sta_ssid, val, sizeof(s_set.sta_ssid) - 1);
        } else if (!strcmp(key, "pass")) {
            strncpy(s_set.sta_pass, val, sizeof(s_set.sta_pass) - 1);
        } else if (!strcmp(key, "server")) {
            strncpy(s_set.server_ip, val, sizeof(s_set.server_ip) - 1);
        } else if (!strcmp(key, "port")) {
            s_set.server_port = (uint16_t) atoi(val);
        } else if (!strcmp(key, "id")) {
            strncpy(s_set.board_id, val, sizeof(s_set.board_id) - 1);
        } else if (!strcmp(key, "apssid")) {
            strncpy(s_set.ap_ssid, val, sizeof(s_set.ap_ssid) - 1);
        } else if (!strcmp(key, "appass")) {
            strncpy(s_set.ap_pass, val, sizeof(s_set.ap_pass) - 1);
        } else if (!strcmp(key, "apchan")) {
            s_set.ap_channel = (uint8_t) atoi(val);
        } else if (!strcmp(key, "led")) {
            /* `set led <gpio> [rgb|plain]` — the type defaults to plain, so the common
             * case stays short and the unusual one is stated explicitly. */
            int pin = atoi(val);
            /* Reject an unusable pin HERE, where a human is watching, rather than
             * silently at blink time when they are across the room looking at the LED. */
            if (pin >= 0 && !GPIO_IS_VALID_OUTPUT_GPIO(pin)) {
                printf("gpio %d cannot drive an output on this chip\n", pin);
                return;
            }
            s_set.led_gpio = (int16_t) pin;
            s_set.led_rgb = (strstr(val, "rgb") != NULL) ? 1 : 0;
            printf("unknown key: %s\n", key);
            return;
        }
        printf("ok (use 'save' to persist)\n");
        return;
    }
    printf("unknown command: %s (try 'help')\n", line);
}

static void console_task(void *arg)
{
    char line[160];
    size_t len = 0;
    (void) arg;

    /* Unbuffered both ways: we echo per keystroke, so a buffered stdin would
     * swallow characters until a newline and make the prompt look dead. */
    setvbuf(stdin, NULL, _IONBF, 0);
    setvbuf(stdout, NULL, _IONBF, 0);

    printf("\ngBridge console -- type 'help'\ngini> ");
    fflush(stdout);
    for (;;) {
        int c = getchar();
        if (c == EOF) {
            vTaskDelay(pdMS_TO_TICKS(50));
            continue;
        }
        if (c == '\r' || c == '\n') {
            printf("\n");
            line[len] = '\0';
            handle_command(line);
            len = 0;
            printf("gini> ");
            fflush(stdout);
            continue;
        }
        if ((c == '\b' || c == 0x7f) && len) {
            len--;
            printf("\b \b");
            fflush(stdout);
            continue;
        }
        if (len < sizeof(line) - 1 && c >= 32 && c < 127) {
            line[len++] = (char) c;
            putchar(c);
            fflush(stdout);
        }
    }
}

/* -------------------------------------------------------------------- main */

void app_main(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);

    settings_load(&s_set);
    ESP_LOGI(TAG, "GINI32 gBridge starting; board id '%s'", s_set.board_id);

    /* The console comes up FIRST and never waits on the network. The single most
     * likely state for a fresh board is "wrong Wi-Fi credentials", and that is
     * exactly when you need the prompt — making you wait for a network that will
     * never arrive is precisely backwards. */
    /* 6 KB, not 4: `ping` runs on this task and holds a receive buffer plus a socket,
     * and printf's formatter is not frugal. 4 KB overflowed and panicked the board. */
    xTaskCreate(console_task, "console", 6144, NULL, 3, NULL);

    wifi_start_apsta();

    /* Wait for the uplink, but only to keep the startup log readable — the fabric
     * task tolerates being started with no uplink and simply keeps looking. */
    xEventGroupWaitBits(s_wifi_events, WIFI_CONNECTED_BIT, pdFALSE, pdTRUE,
                        pdMS_TO_TICKS(15000));
    if (!(xEventGroupGetBits(s_wifi_events) & WIFI_CONNECTED_BIT)) {
        ESP_LOGW(TAG, "no uplink yet — set 'ssid'/'pass' at the prompt, then 'save' "
                      "and 'reboot'. Retrying in the background.");
    }

    fabric_start(s_set.server_ip, s_set.server_port, s_set.board_id,
                 s_set.owner);
}
