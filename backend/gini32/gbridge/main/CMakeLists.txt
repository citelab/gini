idf_component_register(
    SRCS "gbridge_main.c" "fabric.c"
    INCLUDE_DIRS "."
    REQUIRES esp_wifi esp_netif nvs_flash lwip driver esp_timer esp_event mdns led_strip
)
