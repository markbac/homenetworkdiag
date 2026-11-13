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

## 4. Latency symptoms

- Monitor runs (2 to 4 minutes each) showed router ping spikes of 500 to 1,000 ms every minute, a few lost pings, and DNS lookups up to 3.5 s.
- The panel's signal varied between -65 dBm and -82 dBm depending on which unit it was attached to (it moved between units after restarts).

## 5. Other observations

- One BD4 node is shown grey in the ASUS app with 0 clients (offline?).
- 2.4 GHz clients are mostly IoT: Espressif, Meross, Tuya, Motorola, Amazon, Google, HP printer, TP-Link.
- Download speed from the ESP32 is about 1.2 Mbps (single-antenna, TLS-bound): only for comparing places.

## What this means for the tool

- Broadcast/multicast analysis per source and per kind is the most valuable radio measurement (PRB-04, TST-06, DIA-02).
- The tool's own scanning must be paced (NFR-02): an ARP sweep of a /23 every minute is exactly the problem above.
- The router's multicast rate and IGMP snooping settings should be read and explained (ASUS-04).
