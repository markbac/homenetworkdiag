# Requirements

Each requirement has an ID so later design, code and tests can refer to it. **Priority:** M = must (first release), S = should, C = could (later).

## 1. Device discovery and inventory

| ID | Requirement | Pri |
|---|---|---|
| DEV-01 | Discover every device on the configured LAN subnet(s) (e.g. `10.0.10.0/23`) using ARP, ping sweep, the Windows ARP/neighbour table, mDNS, SSDP and NetBIOS. | M |
| DEV-10 | Source plug-ins (D-22): **Pi-hole** (DNS queries, blocked queries and latency per device; flags chatty devices and DNS problems) and **Home Assistant** (friendly device names, presence, device areas). Both optional, configured in the web UI. | S |
| DEV-02 | Merge in the ASUS client list and DHCP leases (see ASUS-*) so devices that are asleep or silent still appear. | M |
| DEV-03 | Key devices by MAC address; track IP changes, first seen, last seen, online/offline history. | M |
| DEV-04 | Identify each device: hostname (DHCP, mDNS, NetBIOS, reverse DNS), vendor (IEEE registry, looked up locally), type (phone, console, TV, camera, plug, NAS...), using the classification rules from `homelab-unified`. | M |
| DEV-05 | Recognise randomised ("private") MAC addresses and say so, instead of showing a wrong or missing vendor. | M |
| DEV-06 | Let the user name, tag and group devices (e.g. "Ollie's Xbox", owner, room), stored locally and applied everywhere (dashboard, reports, probe results). | M |
| DEV-07 | Show for each device: wired or wireless, mesh node, band (2.4 / 5 / 6 GHz), signal, link rates, connected time, traffic (from ASUS-*), air time used (from PRB-*). | M |
| DEV-08 | Optional service scan (selected ports, banners, TLS certificates) on demand, not by default. | C |
| DEV-09 | Flag new / unknown devices and devices that change MAC vendor or name. | S |

## 2. ASUS AiMesh integration

| ID | Requirement | Pri |
|---|---|---|
| ASUS-00 | Detect each unit's model, firmware and Wi-Fi tool set (Broadcom `wl`, MediaTek `iwpriv`/`mwctl`, mac80211 `iw`) and keep a per-unit capability profile; re-check at every start. | M |
| ASUS-01 | Collect **everything the units expose, read-only** (full catalogue in [ASUS data](05-asus-aimesh.md)): from the main router client database, DHCP leases, ARP/neighbours, bridge FDB, conntrack, syslog; per band channel/width/DFS, noise, **channel utilisation**, counters, tx power, cached neighbour scan; per client band, node, signal per chain, tx/rx rate, MCS/NSS/width/PHY mode, retries/failures, idle and connected time, MLO links. | M |
| ASUS-02 | The same radio, client, backhaul, health and counter data from every AiMesh node (BT8 and BD4), found via the mesh topology. | M |
| ASUS-09 | Use the ASUS web API (`appGet.cgi` hooks, allow-listed) alongside SSH: chipset-independent client list with node/band/RSSI/rates, node list with backhaul, WAN, traffic counters. | M |
| ASUS-10 | Log in to SSH with a key (the PC's public key added to the router's Authorized Keys); never store the SSH password. | M |
| ASUS-03 | Build a mesh map: nodes, how they connect (wired / wireless / MLO backhaul), which clients are on which node and band, and node status (online/offline). | M |
| ASUS-04 | Read wireless settings that affect air time and say what they mean: per-band channel and width, multicast rate, IGMP snooping, guest networks, Smart Connect, legacy (802.11b) support, AiMesh backhaul preference, airtime fairness, roaming assistant thresholds. | S |
| ASUS-05 | Collect roam events and Wi-Fi disconnects from the router/node logs with reason codes, timestamped. | S |
| ASUS-06 | Never change router settings in the first release. Commands are an allow-list of read-only commands. | M |
| ASUS-07 | Store credentials in the Windows Credential Manager (or DPAPI-protected file), never in plain text config. | M |
| ASUS-08 | Track settings over time (diff each poll) so "what changed" can be lined up with when problems started. | S |

## 3. ESP32 radio probes

| ID | Requirement | Pri |
|---|---|---|
| PRB-01 | **Discover probes automatically** (mDNS `_hndprobe._tcp` plus a UDP broadcast beacon every 30 s), list new ones in the web UI, and adopt them with a pairing code shown on the probe's screen. No IP configuration; adopted probes are recognised by id after DHCP changes. | M |
| PRB-11 | **Placements:** each probe has a current placement (room + spot). The user sets it when moving the probe, on the probe's touch screen (room list synced from the tool) or in the web UI. All results are tagged with the placement; history and comparisons are per room. | M |
| PRB-12 | Suggest a placement change when a probe's surroundings change (joined a different mesh unit, signal to units changed by > 10 dB, different neighbour set) or after a power cycle. | S |
| PRB-13 | Wi-Fi provisioning of a new probe without code changes: USB serial from the tool, or the probe's own setup access point. | M |
| PRB-02 | Probe capabilities reported by the probe (bands, display, battery, firmware version) so the tool adapts to C5 (2.4 + 5 GHz), C6 (2.4 GHz), S3 (2.4 GHz). | M |
| PRB-03 | **Scan:** all access points per band with SSID, BSSID, channel, width, security, signal, signal history; group BSSIDs into physical radios / mesh units. | M |
| PRB-04 | **Air survey** on a chosen channel (default: the one the probe is joined on; optionally hop through channels): air time by category (your data, broadcast/multicast by kind, mesh backhaul, beacons, management, neighbours), retries, devices active, per-device air time / bytes / signal / rate, top broadcast sources. Headers only, never payloads. | M |
| PRB-05 | **Quick test** from the probe's location: ping router and internet (avg, max, jitter, loss), DNS, download. | M |
| PRB-06 | **Monitor** for 15 min to 8 h from the probe: per-minute ping, loss, spikes, signal, roams, DNS, air figures and top devices; events (drop-out with reason, roam, spike, loss burst, busy channel, broadcast flood, device hog). | M |
| PRB-14 | **Autonomous operation:** probes run their own schedules (scans every minute, air surveys every N minutes, monitors) whether or not the Windows app is running, keep everything locally (SD card if fitted, otherwise a flash ring buffer: about 1 KB per monitored minute, so ~2 MB holds 1.5 days), and the tool syncs all of it when it next connects. Oldest data is dropped only when storage is full, and the probe reports that. | M |
| PRB-07 | Probes stream results to the tool when it is running and **buffer when it is not** (the tray app is only started when needed); on start, the tool syncs everything since the last record it has (sequence numbers, no duplicates). Monitors scheduled on a probe run without the PC. | M |
| PRB-08 | **Stand-alone probes** (D-14): each shows its own results on screen (live signal, quick test, air survey summary, monitor status, placement) and works on a USB-C power bank without the PC. Up to 6 probes. | M |
| PRB-15 | **Channel hopping allowed** (D-13): a probe may leave the network briefly to survey other channels/bands; hops are short, scheduled, logged, and never during monitor ping sampling. | M |
| PRB-09 | Walk-around mode: live signal and quick test at a button press, tagged with a room name, to build a coverage map. | S |
| PRB-10 | 802.15.4 (Zigbee/Thread) channel energy survey on ESP32-C6/C5, to spot overlap with 2.4 GHz Wi-Fi. | C |

## 3a. Wi-Fi journeys and events (from the access points)

| ID | Requirement | Pri |
|---|---|---|
| EVT-01 | Read every unit's log incrementally (main router and every reachable node, MediaTek and Qualcomm formats) and turn lines into typed events: connect, disconnect, **kicked for weak signal** (roaming assistant, with signal values), **steered/roamed** to another unit (candidate unit, signals, result), deauth with reason, backhaul up/down, mesh (cfg) events, DFS, WAN up/down. | M |
| EVT-02 | Combine events with per-minute client snapshots (which unit, band and SSID each device is on) into a **Wi-Fi journey per device**: a timeline of where it was connected, each move, each kick, gaps while disconnected and how long they lasted. | M |
| EVT-03 | Detect patterns and raise findings: **kick loops** (kicked > N times/hour), **roam storms** (several roam attempts within a minute), **flapping** devices (connect/disconnect cycles, e.g. every 30 min), **ping-pong** between two units, devices stuck on a far unit, sticky clients that never roam, and devices that keep falling back to 2.4 GHz. | M |
| EVT-04 | Relate events to symptoms: show on the monitor timeline when a watched device (e.g. a gaming PC) was kicked or moved, next to its ping spikes. | M |
| EVT-05 | Per device, count kicks, roams, disconnects and time offline per day; rank "least stable devices". | M |
| EVT-06 | Explain the cause in plain language, e.g. "Kicked 21 times today because its signal fell below the roaming assistant threshold (-70 dBm) on First Floor; it has no closer unit, so it reconnects to the same one." | S |
| EVT-07 | Recognise USB Wi-Fi adapters and IoT chipsets from the vendor (e.g. ALFA, TP-Link, Espressif, Tuya) and include that in explanations. | S |

## 3b. Network busyness

| ID | Requirement | Pri |
|---|---|---|
| BUS-01 | Throughput over time per unit and interface: internet (WAN), each band and SSID, mesh backhaul links, wired ports (from interface counters, sampled every 30 to 60 s). | M |
| BUS-02 | Per device: data rates (from per-client counters on the APs), active connections (connection tracking), and air time used (probes); "who is using the network right now". | M |
| BUS-03 | Radio health per band and unit: transmit failure and receive CRC error rates, temperature, noise, rates in use (MediaTek `mwctl stat`, Qualcomm `apstats`/`iwconfig`), with history and thresholds. | M |
| BUS-04 | Optional: per-device application categories from the router's Traffic Analyzer if the user enables it. | C |

## 4. Tests and monitoring from Windows

| ID | Requirement | Pri |
|---|---|---|
| TST-01 | Latency, jitter and loss to the router, each mesh node, the internet (several targets), and DNS resolvers. | M |
| TST-02 | Bufferbloat / loaded latency: latency while a download and upload run (reuse the 3-phase method from `homelab-unified`). | S |
| TST-03 | Throughput from the PC (internet speed test) and, optionally, LAN throughput to a probe or another PC. | S |
| TST-04 | Long-running monitor (minutes to days) combining Windows tests, router polling (e.g. every 60 s) and all probes, on one shared timeline. | M |
| TST-05 | Per-device reachability monitor for chosen devices (e.g. the games console): ping every second, with loss and spikes, so "his Xbox lags" is measured at the Xbox. | M |
| TST-06 | Detect broadcast/multicast floods on the wire from Windows (count ARP, mDNS, SSDP per source per second) to confirm what probes see in the air. | M |

## 5. Diagnosis and recommendations

| ID | Requirement | Pri |
|---|---|---|
| DIA-01 | A rule engine turns measurements into findings with severity, evidence and a recommendation, e.g. "Broadcasts from Fingbox (f0:23:b9:ed:95:4d) use 27% of 2.4 GHz air time. Reduce its scan frequency or check the mesh multicast rate." | M |
| DIA-02 | Initial rules: broadcast flood source; channel busy > 50%; device on 2.4 GHz that supports 5/6 GHz; weak signal / low rate client; client stuck on a distant node; node offline; high retries; roam storms; DNS slow/failing; internet loss vs Wi-Fi loss (router ping fine, internet bad); bufferbloat; overlapping neighbour networks; too many SSIDs x nodes; legacy-rate devices. | M |
| DIA-03 | Each finding links to the raw data behind it and to the time window it applies to. | M |
| DIA-04 | Rules are data (YAML/JSON), so thresholds can be tuned without code changes. | S |
| DIA-05 | "Before / after" comparison: run the same test after a change and show what improved. | S |

## 6. Dashboard, reports and data

| ID | Requirement | Pri |
|---|---|---|
| UI-01 | **Web control interface** served by the tray app, **reachable from phones and laptops on the LAN with a login** (D-18): Overview (health + top findings), Devices, Mesh map, Radio (per band/probe), Tests, Monitor timeline, Findings, Probes (adopt, place, start/stop, firmware), Settings. Every action is available here, not only viewing. | M |
| UI-06 | **Tray app**, started when needed: icon shows status (green / amber findings / red problem / grey collectors paused); menu: Open dashboard, Quick diagnosis, Pause collectors, Quit. Optionally start with Windows. | M |
| UI-02 | Timeline view: lag spikes, loss, events, channel busy %, top devices and roams on one zoomable time axis. | M |
| UI-03 | **Generate reports** (HTML and PDF) of a session or a period: findings, evidence charts, device table; option to anonymise names/MACs for sharing with an ISP or a forum. | M |
| UI-07 | Choose **watched devices** in the web UI (e.g. gaming PCs, consoles); they get per-device ping monitors, journeys and a section in reports by default. | M |
| UI-08 | **Versioned read API** for other consumers, so `homelab-unified` can use this tool for data collection (D-10). | S |
| UI-09 | No alerts in the first release (D-19); findings are shown in the dashboard. | - |
| UI-04 | All data available as JSON over a local API, and logged as JSON Lines; device table exportable as CSV. | M |
| UI-05 | Plain-English help for every metric (what it is, good / bad ranges), as on the panel's Help page. | M |

## 7. Non-functional

| ID | Requirement | Pri |
|---|---|---|
| NFR-01 | **Windows 10/11**, no Docker or WSL; **Python 3.12** packaged with PyInstaller, reusing `homelab-unified` code after review. Installs per user; admin only where unavoidable (e.g. raw packet capture), and the tool works with reduced features without it. | M |
| NFR-02 | **Does not harm the network.** Scans are rate-limited (e.g. ARP sweep paced, not more than N packets/s), never continuous, and the tool reports its own traffic. It must never become the broadcast flood it is looking for. | M |
| NFR-03 | **Privacy.** Everything stays on the LAN. No device addresses sent to online lookup services (vendor registry is downloaded and searched locally). Probes read frame headers only. | M |
| NFR-04 | **Security.** Router password and probe tokens stored with Windows DPAPI / Credential Manager; dashboard on the LAN behind a login (hashed password, rate-limited, session cookies), optional HTTPS with a self-signed certificate; probe API authenticated (shared token). | M |
| NFR-05 | **Storage.** SQLite for inventory and time series; JSON Lines for raw logs; **retention 1 month** for raw and per-minute data (configurable), findings/device history/reports kept. | M |
| NFR-06 | **Resilience.** Any single source (router, a probe, a scan) can be missing or failing without breaking the rest; each shows its own status. | M |
| NFR-07 | **Observability.** Its own debug log with levels, rotated; a diagnostics bundle (logs + config without secrets) for troubleshooting. | S |
| NFR-08 | **Testable.** Parsers (ASUS command output, probe JSON) covered by tests with captured fixtures from this network. | M |
