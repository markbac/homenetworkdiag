# Decisions

A running log of agreed decisions (lightweight ADRs). Newest first.

| # | Date | Decision | Why / consequences |
|---|---|---|---|
| D-09 | 2026-11-13 | **Router login by SSH key**, not password. The PC's public key is added in the router UI (Administration > System > Authorized Keys); the ASUS web API (password) is optional and stored only via Windows DPAPI. | No router password stored by the tool for SSH; nodes inherit keys through AiMesh (to verify). |
| D-08 | 2026-11-13 | **Detect the router's Wi-Fi tool set per unit** (Broadcom `wl` vs MediaTek `iwpriv`/`mwctl`/`iw`) instead of assuming one. | The BT8 is reported to be MediaTek; BD4 unknown. A capability capture runs first and at every start. |
| D-07 | 2026-11-13 | **Probes are mobile.** Each probe has a current *placement* (room / spot) that the user sets when moving it, from the probe's screen or the web UI; all results are tagged with the placement. The tool suggests a re-placement when a probe's radio surroundings change. | Results stay comparable per room; history follows the room, not the probe. |
| D-06 | 2026-11-13 | **Probes are discovered automatically** (mDNS + UDP beacon) and adopted with a pairing code shown on the probe's screen. | No IP configuration; works after a probe moves or gets a new DHCP address. |
| D-05 | 2026-11-13 | **The tray app serves a web control interface** on localhost (option: LAN with password) for everything: dashboard, starting tests/surveys/monitors, probe management, settings. | One UI for PC and phone; the tray icon is just a launcher/status. |
| D-04 | 2026-11-13 | **Read-only towards the router**, permanently for now. Only allow-listed read commands / `appGet.cgi` hooks. | Safety; recommendations are carried out by the user. |
| D-03 | 2026-11-13 | **Network:** one subnet, `10.0.10.0/23`; stock ASUS firmware; SSH enabled on port 1111. | Single discovery scope; Merlin-only features not assumed. |
| D-02 | 2026-11-13 | **Tray application, started when needed** (not a Windows service). | Probes must keep working while the PC app is closed: they run monitors and buffer results, and the app syncs them when it starts. Long monitors are owned by probes or require the app to stay open. |
| D-01 | 2026-11-13 | **Python**, reusing as much of `homelab-unified` as possible; reused code is reviewed and updated (Windows APIs, tests, privacy fixes). | Fastest path; known-good ASUS collectors; packaged with PyInstaller. |
