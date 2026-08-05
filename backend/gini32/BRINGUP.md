# GINI32 bring-up

Getting a real ESP32 board to carry physical devices into a drawn GINI topology —
and, in `routed` mode, to be reachable *from* it.

**Verified working (2026-08-01, ESP32-S3, ESP-IDF v5.4):** bidirectional ping between
an iPad on the board's hotspot and emulated machines, plus Internet access for the
iPad through the topology's Internet element.

---

## 1. Provision a board

The firmware is built **once** and is identical on every board. What makes a board
`gini-5` is a few hundred bytes of NVS, written separately — so adding a board takes
seconds and you can never flash "the wrong build".

```bash
cd backend/gini32
. ~/esp/esp-idf/export.sh

./gini32 build                                   # once, for all boards
./gini32 setup -p /dev/cu.usbserial-120 --id gini-5 \
               --ssid <your lab wifi> --pass <password>
```

**Write `gini-5` on a label and stick it on the board.** That string is the only
link between this physical object and the canvas.

Only three things live on a board — its **id**, and the **lab Wi-Fi credentials** it
needs to reach gBuilder. Those are bootstrap: they cannot be delivered over the link
they are required to establish. Everything else comes from the canvas.

**Which USB port?** On chips with native USB (S2/S3/C3/C6) use the one labelled
**UART** — the console is on UART0, so the native-USB port flashes but shows nothing.

| Chip | `--target` | Runs gBridge? |
|---|---|---|
| ESP32 / S2 / S3 / C3 / C6 | `esp32`…`esp32c6` | yes (S3 is the best all-rounder) |
| ESP32-C5 | `esp32c5` | yes — the only **dual-band** part |
| ESP32-H2, ESP32-P4 | — | **no Wi-Fi radio** |

## 2. Draw it

Drop a **GINI32 Board** on the canvas and wire it to a Router or Switch.

| Property | Set it to | Notes |
|---|---|---|
| `BoardID` | `gini-5` | **must match the label.** Blank = nothing will attach |
| `Mode` | `routed` (default) | `routed` = reachable both ways; `nat` = devices hidden |
| `ApSSID` | blank | blank ⇒ named after the element, e.g. `GINI32-GB1` |
| `ApPassword` | `gini12345` | ≥ 8 chars, or the hotspot is open |
| `PhysicalSubnet` | blank | blank ⇒ allocated automatically, never colliding |
| `Channel` | *(read-only)* | reported by the board — APSTA forces it to the uplink's |

Press **Run**. The Console prints the address boards should discover.

## 3. Watch it attach

The board finds the lab by mDNS — nothing is flashed with an IP:

```
I fabric: build: gbridge-7 (non-blocking linkoutput)
I gbridge: uplink up, address 192.168.1.77
I fabric: discovered GINI at 192.168.1.42:5555 (_gini._udp)
I fabric: routed mode: devices behind the radio are reachable from the emulated network
I fabric: fabric up: 10.0.4.10/255.255.255.0 gw 10.0.4.1 mtu 1400
```

The element turns **running** on the canvas once the board actually checks in — it has
no container, so that status means real hardware, not a process.

```
gini> status
fabric: UP routed 10.0.4.10/255.255.255.0 gw 10.0.4.1 via 192.168.1.42:5555 (discovered)
       rx=42 tx=39 drop=0 [datagrams in: ack=12 frame=30]
```

## 4. Bring a device in

Join the hotspot (**`GINI32-GB1`**) from a phone or tablet. It appears **on the canvas**
as a dashed node hanging off the board, showing its address — and disappears when it
leaves. Those nodes are observed, never saved: they belong to the physical world.

Then ping an emulated machine from the device, and the device from a machine.

---

## Open: intermittent behaviour

Bidirectional ping works, but the link is not yet rock-solid. Recorded here so a later
debugging session starts from evidence rather than from memory.

**What was seen (2026-08-02, one board, ESP32-S3):** ping working in both directions,
with intermittent hiccups. Not yet characterised — no measurement of loss rate,
duration, or whether it correlates with anything.

**The obvious suspect is physical.** The board reported `rssi: -88` on first light,
which is at the edge of usable. Move the board within a few metres of the AP and see
whether the symptom survives before looking at code. `status` on the board prints the
current RSSI.

**Three counters now distinguish the candidate causes**, in `curl -s localhost:39098`:

| Counter | Rising means |
|---|---|
| `worst_gap_s`, `late` | The board went quiet — radio, signal, or power. Board-side. |
| `addr_changes` | The source address we learned for the board **moved**. Docker's published port is a stateful translation; a re-map breaks the *return* path only. |
| `dropped` | Frames arrived for a board we had no address for. |

`addr_changes` is the one worth watching. It is invisible from the board — its own
transmits keep succeeding — so without this counter that failure is indistinguishable
from a dead topology. The board keepalives every 5 s, so any gap over ~10 s is real.

Whether to act on it is a separate question: see the Docker translation note under
Known limits — on macOS that hop cannot be removed, only measured.

## Troubleshooting

Read the board's own log first — it names the actual blocker.

| Symptom | Cause | Fix |
|---|---|---|
| `cannot join '<ssid>' (reason 201)` | SSID not in range | ESP32 is **2.4 GHz only** — check the band |
| `... (reason 15 / 202)` | Wrong password | `set pass …` at the prompt, then `save` + `reboot` |
| `... (reason 2 / 4)`, and `wifi: state: assoc -> init` looping | Joined far enough to associate, then pushed off. Wrong password, **or** an AP that requires PMF | We advertise PMF-capable since `gbridge-9`; on an older build, reflash |
| `waiting for the uplink before looking for the GINI server` | No Wi-Fi yet | fix the credentials first; everything else is blocked on this |
| `no GINI server found on this network yet` | Nothing is announcing, **or the Mac is on a different network** | Run a topology containing a board; check `ipconfig getifaddr en0` shares the board's subnet |
| Found the server, never comes up | `BoardID` ≠ the canvas `BoardID` | make them identical |
| `gbridge` log: *names unknown board* | same | ditto |
| Element stays **idle** with the board up | ids differ, or the lab was restarted | check `docker logs $(docker ps -qf name=gbridge)` |
| Devices join the hotspot but reach nothing | Board in `nat` while you expect inbound | set `Mode: routed` |
| Traceroute *from* a device shows `*` at hop 1 | lwIP will not emit ICMP Time Exceeded for an ICMP payload | expected; ping and traceroute *to* the device both work |
| Board seems fine, nothing flows | stale flash | check the `build:` line at boot |

**Diagnostics that actually localise a fault:**

```bash
docker logs --tail 40 $(docker ps -qf name=gbridge)   # board check-ins, per-board counters
curl -s localhost:39098 | python3 -m json.tool        # live board + device state
```

On the board, `status` distinguishes *nothing arrives* (`frame=0`) from *arrives but is
rejected* (`frame>0, rx=0`) from *received but never answered* (`rx>0, tx=0`). Those have
completely different causes — `tx` is usually the load-bearing number.

---

## How it fits together

```
 phone ──802.11──▶ ESP32 (gBridge) ──G32/UDP──▶ Mac:5555 ──▶ gbridge ──eth/UDP──▶ gRouter ──▶ M1
       \___ real radio ___/        \__ your LAN __/        \______ Docker `gini` network ______/
```

* The fabric is **Ethernet-in-UDP** already, so the board speaks it natively.
* Router containers publish no ports, so the **`gbridge` relay** is the one doorway.
* The gRouter only replies to a configured peer, so the relay **learns** each board's
  address — which is also why boards may roam, reboot, or arrive in any order.
* Nobody's laptop has a fixed address, so gBuilder **announces** the lab over mDNS.

Two rules follow, and they are what make this classroom-proof:

* **The canvas owns per-run identity** — addresses, hotspot name, subnet, mode.
* **The hardware owns its own identity** — the `BoardID` on the label. Never generated,
  so a board can play different roles across many topologies without reflashing.

## Claiming — a board belongs to one laptop

In a room of thirty laptops all announcing themselves, a board must not attach to
whoever answers first. So a board is in one of two states:

| State | Behaviour |
|---|---|
| **Unclaimed** | Answers any laptop; appears in every gBuilder's board list |
| **Claimed** | Talks only to its owner; **invisible to every other gBuilder** |

The claim is stored on the board, so it survives reboots and power cuts.

**You rarely have to do anything.** Drawing a GINI32 element whose `BoardID` matches a
board and pressing Run *is* the intent to use it, so the board is claimed on use. The
Inspector's board list is for the other cases — adopting a board before wiring it up, or
seeing what is on the network.

* **Blink** flashes a board's LED, so you can tell which physical object you are naming.
* **Release** (or `unpair` on the board) hands it back to the pool.

There is deliberately **no timeout**: a board must never re-open itself while its owner
is away from the bench. Physical possession is the authority — anyone who can plug in
USB can free a board:

```bash
./gini32 unpair -p /dev/cu.usbserial-120     # then type: unpair
```

`status` on the board says who owns it. If a board is silent and you suspect it is
claimed by a machine that is no longer around, that is the command that tells you.

## Multiple boards

Provision each with a distinct `--id` and label it. Nothing needs to boot in order: a
board announces its id, and the relay matches it against the canvas. Distinct hotspot
SSIDs and physical subnets are allocated automatically; duplicate ids and overlapping
subnets are flagged on the canvas.

One physical caveat: in APSTA the radio is shared, so **every board's hotspot sits on
the uplink's channel**. Several boards in a room all contend on that one channel —
which is the airtime lesson from Chapter 13, now measurable.

### Verifying two boards (not yet done on hardware)

The allocation logic is covered by tests; what is unverified is two real radios at
once. Each step below fails differently, so do them in order and stop at the first
surprise rather than running the whole list.

```bash
./gini32 setup -p /dev/cu.usbserial-XXX --id gini-6 --ssid <lab wifi> --pass <pw>
```

1. **Both attach.** Draw two GINI32 elements, `BoardID` `gini-5` and `gini-6`, each
   wired to a router. Both should reach `fabric up:`. *If only one does*, check the
   relay's table — a duplicate id makes one board silently vanish:
   `curl -s localhost:39098 | python3 -m json.tool`
2. **Distinct hotspots and subnets.** The Inspector should show different `ApSSID`
   and `PhysicalSubnet` for each. Two boards on one subnet gives the routers two
   routes to the same network via different next hops, and neither works.
3. **A device on each.** Join a phone to one hotspot and a tablet to the other. Both
   should appear as dashed nodes under their own board.
4. **Cross-board ping.** Phone → tablet, through the drawn topology. This is the one
   that exercises routing between two physical subnets, and it is the real test.
5. **Claiming holds.** `status` on each board should name this laptop. Neither board
   should appear in the other's element dropdown once claimed.
6. **Channel contention.** Both hotspots will report the same `Channel` — expected,
   and the measurable version of the airtime lesson.

## Known limits

* Campus/guest Wi-Fi that blocks multicast or isolates clients breaks discovery — pin
  it with `--server <ip>` at provision time. If client isolation is on, nothing works.
* The board↔relay hop traverses Docker's published-port translation, and on macOS it
  cannot be removed. The relay has to sit on the `gini` bridge to reach the gRouter tun
  endpoints, and Docker Desktop gives the host no route to that network — so a
  host-side relay would need a published UDP port per board-facing router link, with
  each gRouter's tun peer repointed at `host.docker.internal`. That relocates the
  translation from the board side to the router side rather than removing it. One
  published port is the floor here. (On Linux the relay could use `network_mode: host`
  and the hop would genuinely disappear.)
* Joined devices are reported by MAC, and appear on the canvas with their address shown
  as `?`. The API that pairs a station's MAC with the address our DHCP server gave it
  (`esp_netif_get_sta_list`) is not in IDF v5.4's public headers here. Once you know
  which header declares it, build with the address lookup switched on:

  ```bash
  grep -rl esp_netif_get_sta_list "$IDF_PATH/components/"
  idf.py build -DGB_STA_IP_HEADER='"<that header>"'
  ```
