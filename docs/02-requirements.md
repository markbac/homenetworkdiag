# Requirements

Each requirement has an ID so later design, code and tests can refer to it. **Priority:** M = must (first release), S = should, C = could (later).

## 1. Device discovery and inventory

| ID | Requirement | Pri |
|---|---|---|
| DEV-01 | Discover every device on the configured LAN subnet(s) (e.g. `10.0.10.0/23`) using ARP, ping sweep, the Windows ARP/neighbour table, mDNS, SSDP and NetBIOS. | M |
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
| ASUS-01 | Read, over SSH and read-only, from the main router: client list (`clientlist`, `custom_clientlist`), DHCP leases, ARP table, per-radio status (channel, chanspec, noise, tx power), associated clients per radio with signal, rates and idle time (`wl assoclist`, `wl sta_info`), interface counters. | M |
| ASUS-02 | Read the same per-radio and per-client data from every AiMesh node (`cfg_device_list` to find them, then SSH to each). | M |
| ASUS-03 | Build a mesh map: nodes, how they connect (wired / wireless / MLO backhaul), which clients are on which node and band, and node status (online/offline). | M |
| ASUS-04 | Read wireless settings that affect air time and say what they mean: per-band channel and width, multicast rate, IGMP snooping, guest networks, Smart Connect, legacy (802.11b) support, AiMesh backhaul preference, airtime fairness, roaming assistant thresholds. | S |
| ASUS-05 | Collect roam events and Wi-Fi disconnects from the router/node logs with reason codes, timestamped. | S |
| ASUS-06 | Never change router settings in the first release. Commands are an allow-list of read-only commands. | M |
| ASUS-07 | Store credentials in the Windows Credential Manager (or DPAPI-protected file), never in plain text config. | M |
| ASUS-08 | Support the ASUS web API as a fallback where SSH is disabled. | C |

## 3. ESP32 radio probes

| ID | Requirement | Pri |
|---|---|---|
| PRB-01 | Support one or more ESP32 probes on the LAN, discovered by mDNS and/or configured by IP, each with a name and room. | M |
| PRB-02 | Probe capabilities reported by the probe (bands, display, battery, firmware version) so the tool adapts to C5 (2.4 + 5 GHz), C6 (2.4 GHz), S3 (2.4 GHz). | M |
| PRB-03 | **Scan:** all access points per band with SSID, BSSID, channel, width, security, signal, signal history; group BSSIDs into physical radios / mesh units. | M |
| PRB-04 | **Air survey** on a chosen channel (default: the one the probe is joined on; optionally hop through channels): air time by category (your data, broadcast/multicast by kind, mesh backhaul, beacons, management, neighbours), retries, devices active, per-device air time / bytes / signal / rate, top broadcast sources. Headers only, never payloads. | M |
| PRB-05 | **Quick test** from the probe's location: ping router and internet (avg, max, jitter, loss), DNS, download. | M |
| PRB-06 | **Monitor** for 15 min to 8 h from the probe: per-minute ping, loss, spikes, signal, roams, DNS, air figures and top devices; events (drop-out with reason, roam, spike, loss burst, busy channel, broadcast flood, device hog). | M |
| PRB-07 | Probes stream results to the Windows tool when it is running and buffer (and/or log to SD) when it is not. | S |
| PRB-08 | A probe can run stand-alone (battery, on its own screen) and sync results later. | S |
| PRB-09 | Walk-around mode: live signal and quick test at a button press, tagged with a room name, to build a coverage map. | S |
| PRB-10 | 802.15.4 (Zigbee/Thread) channel energy survey on ESP32-C6/C5, to spot overlap with 2.4 GHz Wi-Fi. | C |

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
| UI-01 | Local web dashboard (served by the Windows tool on localhost; optionally LAN): Overview (health + top findings), Devices, Mesh map, Radio (per band/probe), Tests, Monitor timeline, Findings, Probes, Settings. | M |
| UI-02 | Timeline view: lag spikes, loss, events, channel busy %, top devices and roams on one zoomable time axis. | M |
| UI-03 | Export a report (HTML/PDF) of a session: findings, charts, device table. | S |
| UI-04 | All data available as JSON over a local API, and logged as JSON Lines; device table exportable as CSV. | M |
| UI-05 | Plain-English help for every metric (what it is, good / bad ranges), as on the panel's Help page. | M |

## 7. Non-functional

| ID | Requirement | Pri |
|---|---|---|
| NFR-01 | **Windows 10/11**, no Docker or WSL. Installs per user; admin only where unavoidable (e.g. raw packet capture), and the tool works with reduced features without it. | M |
| NFR-02 | **Does not harm the network.** Scans are rate-limited (e.g. ARP sweep paced, not more than N packets/s), never continuous, and the tool reports its own traffic. It must never become the broadcast flood it is looking for. | M |
| NFR-03 | **Privacy.** Everything stays on the LAN. No device addresses sent to online lookup services (vendor registry is downloaded and searched locally). Probes read frame headers only. | M |
| NFR-04 | **Security.** Router and probe credentials stored with Windows DPAPI / Credential Manager; dashboard bound to localhost by default; probe API authenticated (shared token). | M |
| NFR-05 | **Storage.** SQLite for inventory and time series; JSON Lines for raw logs; retention configurable (default 90 days). | M |
| NFR-06 | **Resilience.** Any single source (router, a probe, a scan) can be missing or failing without breaking the rest; each shows its own status. | M |
| NFR-07 | **Observability.** Its own debug log with levels, rotated; a diagnostics bundle (logs + config without secrets) for troubleshooting. | S |
| NFR-08 | **Testable.** Parsers (ASUS command output, probe JSON) covered by tests with captured fixtures from this network. | M |
