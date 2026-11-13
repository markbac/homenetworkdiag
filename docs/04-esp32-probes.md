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

## Discovery, adoption and placement

Probes are found automatically and move freely (decisions D-06, D-07):

1. **Provisioning (once per probe):** the probe needs the Wi-Fi name and password. Either the tool sends them over USB serial, or the probe starts its own setup access point and the user enters them on a phone. The password is stored only on the probe.
2. **Announcing:** on the LAN the probe advertises `_hndprobe._tcp` over mDNS and sends a small UDP broadcast beacon every 30 s (id, chip, bands, firmware, adopted or not). Both are needed: mDNS is sometimes filtered between Wi-Fi and wired segments; the beacon is a fallback. The beacon is tiny (one packet per 30 s), so it does not add to broadcast load.
3. **Adoption:** the web UI lists new probes; adopting shows a 6-digit code on the probe's screen, which the user types in the web UI. The probe and tool then share a token for all API calls.
4. **Placement:** the user picks a room and spot (from a list kept by the tool, editable). The probe shows its placement on screen. Every record carries the placement id.
5. **Moving:** when the probe powers up somewhere new, or sees different surroundings (different mesh unit, unit signals changed by > 10 dB, different neighbour networks), it asks "Moved? Pick room" on its screen and the web UI shows a prompt. Results from the new place are never mixed with the old room's history.
6. **Tool closed:** probes keep running scheduled monitors and buffer records (RAM ring; SD card where fitted). When the tray app starts it syncs from each probe's last sequence number.

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
| GET | `/api/info` | Chip, bands, firmware, battery, uptime, Wi-Fi status, capabilities, adoption state, placement |
| POST | `/api/adopt`, `/api/adopt/confirm` | Pairing: show code, confirm, return token |
| POST | `/api/placement` | Set room / spot (also settable on the probe's screen) |
| GET | `/api/sync?since=<seq>` | Everything buffered since a sequence number |
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
