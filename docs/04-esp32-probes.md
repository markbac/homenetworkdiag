# ESP32 radio probes

Windows cannot see what happens in the air: a PC's Wi-Fi adapter only reports its own connection, and promiscuous capture on Windows is unreliable. Small ESP32 boards can listen to a channel (promiscuous mode), scan, and run tests from where the problem is. The Windows tool controls them and stores what they find.

## Hardware available

| Board | Chip | Wi-Fi bands | Display | Battery | Proposed role |
|---|---|---|---|---|---|
| Waveshare ESP32-C5 2.8" touch | ESP32-C5 | **2.4 + 5 GHz**, Wi-Fi 6 | 2.8" 240x320 touch | Supported (check if included) | **Portable probe**: the only board that sees 5 GHz; walk-around tests and room surveys |
| Waveshare ESP32-C6 1.47" touch | ESP32-C6 | 2.4 GHz only, Wi-Fi 6 (+ 802.15.4 Zigbee/Thread) | 1.47" touch | No | **Fixed 2.4 GHz probe** (e.g. in the boys' room) running continuous air monitoring; Zigbee/Thread channel energy |
| Elecrow CrowPanel 7" | ESP32-S3 | 2.4 GHz only, Wi-Fi 4 | 7" 800x480 touch | No (USB power bank) | Existing weather station; already a working 2.4 GHz probe (Network pages) |

Notes:

- ESP32 radios are **single-antenna, single-stream**, so throughput figures are for comparing places, not for judging broadband. Latency, loss, jitter, air time and signal are the useful measures.
- A probe listens to **one channel at a time** while connected. Surveying other channels means hopping (briefly leaving the network) or using an unconnected probe.
- **6 GHz** (used by the BT8s) is not covered by any ESP32. The router's own data (ASUS-*) is the only view of 6 GHz clients.
- To verify before relying on it: ESP-IDF promiscuous mode on the C5's **5 GHz** band, and how much the C5's 5 GHz scanning disturbs its connection.

## Capabilities per probe

| Capability | S3 (CrowPanel) | C6 | C5 |
|---|---|---|---|
| Scan networks, signal history | 2.4 | 2.4 | 2.4 + 5 |
| Air survey (air time by category and device, broadcast sources) | 2.4 | 2.4 | 2.4 + 5 |
| Quick test (ping, jitter, loss, DNS, download) | yes | yes | yes |
| Monitor (per-minute, events) | yes | yes | yes |
| Zigbee/Thread energy scan | no | yes | yes |
| Own screen | 7" | 1.47" (status only) | 2.8" |

## What the CrowPanel firmware already does (prototype)

The `esp32/weather-display` firmware is the reference implementation; the probe firmware should start from its `src/core` modules:

- `wifi_scan`: async scan every minute, per-BSSID 3-hour history, mesh "family" detection, `GET /api/wifi.json`.
- `air_survey`: promiscuous listening on the joined channel; air time split into your data / broadcast (by kind: ARP, mDNS, SSDP, IPv6, other) / mesh backhaul / beacons / management / neighbours; per-device air time, bytes, frames, signal, top rate, via-unit; top broadcast sources; `GET /api/air.json`.
- `net_test`: quick test and monitor (15 min to 8 h) with per-minute records and events (drop-out with reason, roam, spike, loss, DNS, weak signal, busy channel, device hog); `POST /api/nettest`.
- `log`: JSON Lines network log and debug log on SD, `/logs` web page, boot crash report.
- Lessons learned: yield in CPU-bound loops (task watchdog), pause scans during tests (they cause ~500 ms spikes), count the whole mesh as "yours", group BSSIDs into units.

## Probe API (target)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/info` | Chip, bands, firmware, battery, uptime, Wi-Fi status, capabilities |
| GET | `/api/wifi.json?band=` | Scan results with history |
| POST | `/api/air` | Start an air survey: `band`, `channel` (or `joined`), `seconds` |
| GET | `/api/air.json` | Last survey: summary, categories, units, devices, broadcast sources |
| POST | `/api/test` | Quick test (targets configurable) |
| POST | `/api/monitor` | Start/stop a monitor: `minutes`, `targets`, `air` on/off |
| GET | `/api/monitor.json` | Current/last monitor: minutes, events, totals |
| GET | `/api/stream` | Live records as they happen (decision: SSE / WebSocket / push to PC) |

All responses carry `probe`, `time` (ISO 8601, NTP) and `band`; records are the same JSON shapes as the JSONL logs.

## Privacy

Probes read only 802.11 frame headers (type, addresses, length, rate, signal, retry flag). Payloads are encrypted by WPA and are never read or stored. Device addresses stay on the LAN.
