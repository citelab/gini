/* gini32/sketches/csi -- Channel State Information logger for the GINI32 board.
 *
 * Associates to a lab access point so frames keep arriving, enables CSI, and
 * prints one energy figure per frame. Stream the output to a host-side plotter:
 * with the room still the value is steady; when someone crosses the path between
 * the access point and the board, the multipath changes and the value wobbles.
 * That is the basis of Wi-Fi sensing -- presence/motion detection with no camera.
 *
 * Use only on your own network. See ../../README.md for the legal notice.
 */
#include <stdio.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs_flash.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_netif.h"

#define LAB_SSID     "gini-lab-ap"       /* your isolated lab access point */
#define LAB_PASS     "changeme12345"

static void csi_cb(void *ctx, wifi_csi_info_t *info)
{
    const int8_t *d = info->buf;         /* interleaved (imag, real) per subcarrier */
    double energy = 0;
    for (int i = 0; i + 1 < info->len; i += 2)
        energy += (double) d[i] * d[i] + (double) d[i + 1] * d[i + 1];
    printf("csi_energy %.0f\n", energy); /* one line per frame -> host plotter */
}

static void wifi_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START)
        esp_wifi_connect();
    else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED)
        esp_wifi_connect();              /* keep frames flowing */
}

void app_main(void)
{
    ESP_ERROR_CHECK(nvs_flash_init());
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event, NULL));

    wifi_config_t sta = { 0 };
    strncpy((char *) sta.sta.ssid, LAB_SSID, sizeof(sta.sta.ssid));
    strncpy((char *) sta.sta.password, LAB_PASS, sizeof(sta.sta.password));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &sta));
    ESP_ERROR_CHECK(esp_wifi_start());

    /* enable CSI collection */
    wifi_csi_config_t csi_cfg = {
        .lltf_en = true, .htltf_en = true, .stbc_htltf2_en = true,
        .ltf_merge_en = true, .channel_filter_en = true, .manu_scale = false,
    };
    ESP_ERROR_CHECK(esp_wifi_set_csi_config(&csi_cfg));
    ESP_ERROR_CHECK(esp_wifi_set_csi_rx_cb(&csi_cb, NULL));
    ESP_ERROR_CHECK(esp_wifi_set_csi(true));

    for (;;) vTaskDelay(pdMS_TO_TICKS(1000));
}
