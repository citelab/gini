/* gini32/sketches/beacon -- phantom-beacon injector for the GINI32 board.
 *
 * Transmits a hand-built 802.11 beacon frame a few times a second, so the SSID
 * "GINI-32" appears in the Wi-Fi list of nearby devices. It is deliberately
 * benign: nothing can associate to a beacon-only "network." The point is to see
 * how small a management frame is, and why unauthenticated management frames are
 * a design weakness (a forged beacon is as easy as a forged deauth).
 *
 * ONLY on your own devices, on an isolated/instructor channel, ideally RF-shielded.
 * See ../../README.md for the legal notice. Do NOT adapt this to send deauth frames.
 */
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs_flash.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_netif.h"

#define BEACON_CHANNEL 6

/* A minimal beacon: 24-byte MAC header + 12-byte fixed params + tagged fields. */
static uint8_t beacon[] = {
    /* --- MAC header --- */
    0x80, 0x00,                             /* Frame Control: type=mgmt, subtype=beacon */
    0x00, 0x00,                             /* Duration */
    0xff, 0xff, 0xff, 0xff, 0xff, 0xff,     /* A1: broadcast receiver */
    0x24, 0x6f, 0x28, 0x00, 0x00, 0x01,     /* A2: our BSSID (transmitter) */
    0x24, 0x6f, 0x28, 0x00, 0x00, 0x01,     /* A3: same BSSID */
    0x00, 0x00,                             /* Sequence control (driver rewrites) */
    /* --- fixed beacon parameters --- */
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, /* timestamp */
    0x64, 0x00,                             /* beacon interval (~100 TU) */
    0x01, 0x04,                             /* capability info */
    /* --- tagged parameters --- */
    0x00, 0x07, 'G', 'I', 'N', 'I', '-', '3', '2',  /* SSID element: "GINI-32" */
    0x01, 0x04, 0x82, 0x84, 0x8b, 0x96,     /* supported rates: 1,2,5.5,11 Mbps */
    0x03, 0x01, BEACON_CHANNEL              /* DS parameter set: channel */
};

void app_main(void)
{
    ESP_ERROR_CHECK(nvs_flash_init());
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_AP));
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_ERROR_CHECK(esp_wifi_set_channel(BEACON_CHANNEL, WIFI_SECOND_CHAN_NONE));

    for (;;) {
        /* last arg (en_sys_seq) = true: let the driver fill the sequence number */
        esp_wifi_80211_tx(WIFI_IF_AP, beacon, sizeof(beacon), true);
        vTaskDelay(pdMS_TO_TICKS(100));     /* ~10 beacons per second */
    }
}
