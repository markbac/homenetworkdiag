# Decisions

A running log of agreed decisions (lightweight ADRs). Newest first.

| # | Date | Decision | Why / consequences |
|---|---|---|---|
| D-26 | 2026-11-13 | **UniFi data comes from the local UniFi Network controller API** (9.1.120, running on the same mini PC, ports 8080/8443), using a **View Only** local admin created for MeshLens (credentials stored with DPAPI). Fallback: the switch's own SSH (`mca-dump`, device SSH authentication is enabled in the controller as `streakyb`). | Controller already aggregates port stats, PoE, LLDP and client-to-port mapping; read-only role enforces D-04-style safety. |
| D-25 | 2026-11-13 | **Keep hourly and daily summaries for 12 months**, in addition to 1 month of raw and per-minute data (D-20). | Long-term trends (ISP, kicks, busyness) survive at a few MB. |
| D-24 | 2026-11-13 | **Reports** contain: summary of findings, evidence charts, device table; option to anonymise names and MACs for sharing. | Proposal accepted. |
| D-23 | 2026-11-13 | **Probe screens** (stand-alone): live signal, quick test, air survey summary, monitor status, placement. | Proposal accepted. |
| D-22 | 2026-11-13 | **Pi-hole and Home Assistant are included** as optional source plug-ins: Pi-hole for per-device DNS queries and latency ("who is chatty", DNS problems), Home Assistant for friendly device names and presence. Not included: Synology, Uptime Kuma. | Confirmed; both already have collectors in `homelab-unified`. |
| D-21 | 2026-11-13 | **Name: MeshLens.** The repository stays `homenetworkdiag`. | Confirmed. |
| D-20 | 2026-11-13 | **Data retention: 1 month** for raw records and per-minute history; findings, device history and reports kept longer. | ~55 devices x per-minute samples fits comfortably in SQLite on the mini PC. |
| D-19 | 2026-11-13 | **Reports** can be generated (HTML/PDF, shareable with an ISP or a forum); **no alerts** for now. | Alerts can be added later on top of findings. |
| D-18 | 2026-11-13 | **Dashboard reachable from phones and laptops on the LAN**, with a login; not just localhost. | The web UI binds to the LAN interface with authentication and rate-limited login. |
| D-17 | 2026-11-13 | **Devices to watch are chosen in the web UI** (no hard-coded list); watched devices get per-device ping monitors and journey tracking by default. | |
| D-16 | 2026-11-13 | **The tool may hold the router admin password** (Windows DPAPI-encrypted) for SSH and the ASUS web API; SSH key login stays preferred where it works. | Nodes and the web API need the password; key login was not accepted by the stock firmware in testing. |
| D-15 | 2026-11-13 | **Up to 6 probes**, moved around the house as needed; **Wi-Fi only** (no USB link to the PC); powered from USB-C power banks when portable. | Probe manager sized for 6; placement handling is central. |
| D-14 | 2026-11-13 | **Probes are stand-alone**: they show their own results on screen (signal, survey, test, monitor status) and work without the PC. | Probe UI is part of the firmware scope. |
| D-13 | 2026-11-13 | **Probes may leave the network briefly** to survey other channels/bands (channel hopping). | Hops are scheduled, short, reported, and never during a monitor's ping sampling. |
| D-12 | 2026-11-13 | **Probe firmware on ESP-IDF where needed** (C5 5 GHz promiscuous / scanning features); Arduino-as-component acceptable for reuse of the CrowPanel code. | |
| D-11 | 2026-11-13 | **Runs on an always-on mini PC**, as a tray app started when needed (normally left running, starts at login), admin rights available (Npcap allowed). | Long monitors are practical from the PC side too; raw capture (wire broadcast counting) is in scope. |
| D-10 | 2026-11-13 | **Separate tool; long term `homelab-unified` uses it for data collection** through its API instead of running its own collectors. | The engine exposes a stable, versioned read API (JSON) intended for other consumers. |
| D-09 | 2026-11-13 | **Router login by SSH key where possible** (superseded in part by D-16). The PC's public key is added in the router UI (Administration > System > Authorized Keys); the ASUS web API (password) is optional and stored only via Windows DPAPI. | No router password stored by the tool for SSH; nodes inherit keys through AiMesh (to verify). |
| D-08 | 2026-11-13 | **Detect the router's Wi-Fi tool set per unit** (Broadcom `wl` vs MediaTek `iwpriv`/`mwctl`/`iw`) instead of assuming one. | The BT8 is reported to be MediaTek; BD4 unknown. A capability capture runs first and at every start. |
| D-07 | 2026-11-13 | **Probes are mobile.** Each probe has a current *placement* (room / spot) that the user sets when moving it, from the probe's screen or the web UI; all results are tagged with the placement. The tool suggests a re-placement when a probe's radio surroundings change. | Results stay comparable per room; history follows the room, not the probe. |
| D-06 | 2026-11-13 | **Probes are discovered automatically** (mDNS + UDP beacon) and adopted with a pairing code shown on the probe's screen. | No IP configuration; works after a probe moves or gets a new DHCP address. |
| D-05 | 2026-11-13 | **The tray app serves a web control interface** on localhost (option: LAN with password) for everything: dashboard, starting tests/surveys/monitors, probe management, settings. | One UI for PC and phone; the tray icon is just a launcher/status. |
| D-04 | 2026-11-13 | **Read-only towards the router**, permanently for now. Only allow-listed read commands / `appGet.cgi` hooks. | Safety; recommendations are carried out by the user. |
| D-03 | 2026-11-13 | **Network:** one subnet, `10.0.10.0/23`; stock ASUS firmware; SSH enabled on port 1111. | Single discovery scope; Merlin-only features not assumed. |
| D-02 | 2026-11-13 | **Tray application, started when needed** (not a Windows service); on the always-on mini PC it is normally left running (D-11). | Probes must keep working while the PC app is closed: they run monitors and buffer results, and the app syncs them when it starts. Long monitors are owned by probes or require the app to stay open. |
| D-01 | 2026-11-13 | **Python**, reusing as much of `homelab-unified` as possible; reused code is reviewed and updated (Windows APIs, tests, privacy fixes). | Fastest path; known-good ASUS collectors; packaged with PyInstaller. |
