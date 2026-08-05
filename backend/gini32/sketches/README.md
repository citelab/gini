# GINI32 Wi-Fi dissection sketches

Standalone ESP-IDF sketches that turn a GINI32 board into a Wi-Fi *instrument*, used by
the labs in the book chapter **"Dissecting Wi-Fi with GINI32."** They are independent of
the gBridge gateway firmware: flash one, watch the serial console, then reflash gBridge
when you want the board back on the fabric.

| Sketch | What it does | Book section |
|--------|--------------|--------------|
| `sniffer/` | Monitor mode: prints every 802.11 frame on a channel with its type and RSSI | Anatomy of an 802.11 frame; RF layer |
| `beacon/`  | Injects a harmless phantom beacon so a chosen SSID appears on nearby phones | Active radio: transmitting frames |
| `csi/`     | Streams a per-frame Channel State Information energy figure for Wi-Fi sensing | Wi-Fi as a sensor (CSI) |

## ⚠️ Legal and ethical use

These sketches **transmit and capture** on real radio. Capturing traffic on networks you
do not own, and transmitting management frames (a phantom beacon, and above all
deauthentication frames), affects every device in range and is **unlawful in most
jurisdictions** and against every campus network's rules.

Run them only on **your own devices**, on an **isolated, instructor-controlled** access
point, ideally in an **RF-shielded** space or well away from production Wi-Fi. The beacon
sketch is deliberately benign (nothing can associate to a beacon-only "network"); it is
provided so the management plane can be studied, not to interfere with anyone.

## Build & flash

Each directory is a complete ESP-IDF project — nothing to add. With ESP-IDF v5.x:

```bash
cd sniffer                      # or beacon, or csi
idf.py set-target esp32s3       # your chip: esp32 | esp32s2 | esp32s3 | esp32c3 | esp32c6
idf.py -p /dev/cu.usbserial-XXXX flash monitor
```

`set-target` is only needed once per sketch (it generates `sdkconfig`); to switch chips
later, delete `sdkconfig` and re-run it.

**Which port?** On chips with native USB (S2/S3/C3/C6) a dev board usually has two: use
the one labelled **UART**, because these sketches log to UART0. The native USB port
flashes fine but shows no output unless you also set `CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y`.

Pick the Wi-Fi channel (and, for `csi`, the access point credentials) at the top of the
sketch's `main/*.c`.

**Chip notes.** All ESP32 chips with Wi-Fi do monitor mode, injection, and CSI; the
radio-less **H2** and **P4** cannot run these at all. The `csi` sketch's
`wifi_csi_config_t` fields match ESP32/S2/S3/C3 — the C5/C6 CSI struct differs, so that
sketch needs a small edit on those chips.
