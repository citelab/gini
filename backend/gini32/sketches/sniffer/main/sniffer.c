/* gini32/sketches/sniffer -- 802.11 monitor-mode sniffer for the GINI32 board.
 *
 * Puts the ESP32 radio in promiscuous mode and prints one line per captured
 * frame: its RSSI, the Frame Control byte (type/subtype), and the first three
 * octets of the receiver and transmitter addresses. Match the output against
 * the MAC-header figure in the "Dissecting Wi-Fi with GINI32" chapter.
 *
 * Capture only on networks you own. See ../../README.md for the legal notice.
 */
#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs_flash.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_netif.h"

#define SNIFF_CHANNEL 6          /* the channel your lab access point uses */

static const char *frame_family(uint8_t fc)
{
    switch ((fc & 0x0C) >> 2) {          /* Frame Control type bits */
    case 0:  return "mgmt";
    case 1:  return "ctrl";
    case 2:  return "data";
    default: return "ext ";
    }
}

static void sniff_cb(void *buf, wifi_promiscuous_pkt_type_t type)
{
    const wifi_promiscuous_pkt_t *p = (wifi_promiscuous_pkt_t *) buf;
    const uint8_t *f = p->payload;       /* the raw 802.11 frame */

    /* f[0] = Frame Control; f[4..9] = Address 1 (RA); f[10..15] = Address 2 (TA) */
    printf("rssi=%4d  %s  fc=0x%02x  RA=%02x:%02x:%02x  TA=%02x:%02x:%02x\n",
           p->rx_ctrl.rssi, frame_family(f[0]), f[0],
           f[4], f[5], f[6], f[10], f[11], f[12]);
}

void app_main(void)
{
    ESP_ERROR_CHECK(nvs_flash_init());
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_NULL));   /* no AP, no STA: just listen */
    ESP_ERROR_CHECK(esp_wifi_start());

    ESP_ERROR_CHECK(esp_wifi_set_promiscuous(true));
    esp_wifi_set_promiscuous_rx_cb(&sniff_cb);
    ESP_ERROR_CHECK(esp_wifi_set_channel(SNIFF_CHANNEL, WIFI_SECOND_CHAN_NONE));

    printf("sniffing channel %d -- Ctrl-] to quit monitor\n", SNIFF_CHANNEL);
    for (;;) vTaskDelay(pdMS_TO_TICKS(1000));
}
