# Findings so far (13 November 2026)

Measured with the CrowPanel 7" (ESP32-S3, 2.4 GHz only) running the `esp32/weather-display` network tools, placed indoors near the main BT8. They motivated most of the requirements.

## 1. One shared 2.4 GHz channel

- All four online AiMesh units' 2.4 GHz radios are on **channel 11** (normal for AiMesh).
- 17 network broadcasts heard (3 to 5 SSIDs per unit: MH-Pvt, MH-Pvt_IoT, MH-Guest and hidden AiMesh networks) from **4 units**.
- Neighbours barely overlap (0 to 1 strong networks). Changing channel would not help.

## 2. The air is busy, but not with data

20-second air surveys on channel 11 (estimates; lower bounds):

| Survey | Busy | Your data | Broadcast/multicast | Beacons | Other |
|---|---|---|---|---|---|
| 1 | 67% | n/a | n/a | 8% | n/a |
| 2 | 35% | 2% | (counted as management) | 8% | 24% mgmt |
| 3 | 51% | 2% | 38% | 9% | 1% |
| 4 | **66%** | **3%** | **54%** (ARP/broadcast 31%, mDNS 20%, IPv6 2%) | 8% | 1% |

Unicast data from 21 to 25 active 2.4 GHz devices used only 2 to 3% of the air. **Broadcast and multicast repeated by the mesh to every wireless client used 38 to 54%.**

## 3. One device causes most of it

Top broadcast sources in survey 4:

| MAC | Vendor (IEEE) | Air time | Frames in 20 s |
|---|---|---|---|
| `f0:23:b9:ed:95:4d` | Domotz Ltd | **27%** | 16,267 (~800/s) |
| `2c:cf:67:72:0e:29` | Raspberry Pi | 4.2% | 846 |
| `d8:5e:d3:e0:49:b4` | Gigabyte (PC) | 2% | 1,296 |
| `c4:e7:ae:18:c9:fd` | Meross (smart plug) | 1.9% | 313 |

The `f0:23:b9:e` block is registered to Domotz Ltd. The suspicion is that it is the **Fingbox**, which monitors presence by ARP-probing every address; on a /23 network (512 addresses) each sweep is twice the size of a typical home network's. Not yet confirmed: the test is to unplug it for 5 minutes and repeat the survey.

## 4. Router configuration and logs (SSH capture)

- **IGMP snooping is off** on every band of every unit (`wlX_igs=0`, `emf_enable=0`): multicast is flooded to all wireless clients. This matches the 20% of air time measured as mDNS.
- **Roaming assistant at -70 dBm** disconnects clients whose signal drops below it. The logs show it repeatedly kicking the CrowPanel (10.0.11.141) and making a private-MAC phone (`3a:99:7f:fd:a8:ed`, about -72 dBm on 5 GHz) attempt to roam every 5 to 10 seconds: a roam storm, which feels like lag.
- An IoT device (`10:52:1c:0a:aa:a6`) on MH-Pvt_IoT connects and disconnects every 30 minutes.
- **Mixed chipsets:** BT8 units are MediaTek, BD4 units Qualcomm; the collector needs both tool sets (see [ASUS data](05-asus-aimesh.md)).
- **Two nodes unmanaged:** Office (BD4) and Garden (BD4 Outdoor) are not in the router's managed node list and do not accept SSH; Garden's backhaul links are marked not alive.
- Correction to section 1: the `28:aa` addresses are the **BT8** units and `c5:99` the **BD4** units.

## 5. Who gets kicked and who flaps (router logs)

Main router (~31 h) and First Floor (~3.3 days) logs, names from the router's device database, vendors from the IEEE registry:

| Device | Vendor / adapter | Disconnects | Weak-signal kicks | Notes |
|---|---|---|---|---|
| Tuya smart device ("lwip0") `e4:ae:e4:58:18:c2` | Tuya | **368** (First Floor) | | Reconnects about every 13 minutes |
| Phone, private MAC `3a:99:7f:fd:a8:ed` | (randomised) | 196 + 37 | 21+ | Roam storm: attempts every 5 to 10 s |
| Espressif device `24:d7:eb:e2:91:48` | Espressif | 160 | | |
| Android phone (android-dhcp-17) | | 138 | 6 | |
| **Windows PC with ALFA USB Wi-Fi adapter** `00:c0:ca:b0:19:d2` | ALFA | 136 + 57 (two units) | | Moving between First and Second Floor |
| Android phone (android-dhcp-16) | | 77 + 42 | 7 | |
| **Windows PC with TP-Link USB Wi-Fi adapter** `0c:ef:15:da:2c:8b` | TP-Link | 74 (Second Floor) | | |
| Espressif `10:52:1c:0a:aa:a6` | Espressif | 60 | | Every 30 minutes on MH-Pvt_IoT |
| Laptop UKMANNB10745 | Windows | 18 | 15 | On 6 GHz at -82 dBm |
| Hisense Roku TV | | 15 | | |
| CrowPanel (esp32s3-FAD174) | Espressif | 44 (Second Floor) | 4 | Kicked below -70 dBm |

If the two Windows PCs with USB adapters are the gaming PCs, their repeated disconnects and moves between units would explain lag and drop-outs directly.

## 6. Radio health

The main router's 2.4 GHz radio reports 15.7% transmit failures and 22.6% receive CRC errors, against 6.1% / 4.5% on 5 GHz and 2.4% / 0.6% on 6 GHz. The 2.4 GHz SSIDs carried almost no data during the capture (< 0.1 Mbit/s) while the probe measured the channel 50 to 66% busy: the air is used by broadcasts and errors, not traffic.

## 7. Latency symptoms

- Monitor runs (2 to 4 minutes each) showed router ping spikes of 500 to 1,000 ms every minute, a few lost pings, and DNS lookups up to 3.5 s.
- The panel's signal varied between -65 dBm and -82 dBm depending on which unit it was attached to (it moved between units after restarts).

## 8. Other observations

- One BD4 node is shown grey in the ASUS app with 0 clients; SSH shows Office and Garden are not managed nodes (section 4).
- 2.4 GHz clients are mostly IoT: Espressif, Meross, Tuya, Motorola, Amazon, Google, HP printer, TP-Link.
- Download speed from the ESP32 is about 1.2 Mbps (single-antenna, TLS-bound): only for comparing places.

## What this means for the tool

- Broadcast/multicast analysis per source and per kind is the most valuable radio measurement (PRB-04, TST-06, DIA-02).
- The tool's own scanning must be paced (NFR-02): an ARP sweep of a /23 every minute is exactly the problem above.
- The router's multicast rate and IGMP snooping settings should be read and explained (ASUS-04).
