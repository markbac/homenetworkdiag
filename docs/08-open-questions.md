# Open questions

Answers will be folded back into the requirements and architecture.

## Platform and packaging

1. **Language:** Python (reuse `homelab-unified` collectors, packaged with PyInstaller) or .NET 8 (native Windows, single binary, rewrite collectors)?
2. **How should it run:** a tray app you start when needed, a background Windows service that runs all the time, or both?
3. **Which PC:** always-on desktop, or a laptop you carry around (affects monitoring and probe sync)?
4. **Admin rights:** acceptable to install Npcap and run parts as admin (needed for raw capture and some scans), or must it work as a normal user?
5. **Relationship to `homelab-unified`:** separate tool that copies code, a shared Python package used by both, or should `homelab-unified` eventually be replaced by this?

## ESP32 probes

6. **Firmware base:** Arduino (like the CrowPanel firmware, easy reuse) or ESP-IDF directly (needed for some C5 5 GHz features)?
7. **How many probes and where:** e.g. C5 portable, C6 fixed in the boys' room, CrowPanel in the kitchen?
8. **Connection to the PC:** over Wi-Fi only, or also USB serial when plugged in (more robust, no extra Wi-Fi traffic)?
9. **Stand-alone use:** should probes show results on their own screens (walk-around without the PC), or act purely as sensors?
10. **Channel hopping:** may a probe briefly leave the network to survey other channels/bands, or must it stay connected?
11. **Battery:** will the C5 have a battery fitted? (Its listing says batteries not included.)

## ASUS

12. **SSH:** is SSH enabled on the BT8 (LAN only), and are you happy for the tool to hold the admin password (DPAPI-encrypted)?
13. **Firmware:** stock Asuswrt or Asuswrt-Merlin? (Changes available commands and logs.)
14. **Write actions later:** should the tool ever change settings (e.g. multicast rate, disable guest SSID), with confirmation, or stay read-only forever?

## Network and devices

15. **Subnets/VLANs:** is everything on `10.0.10.0/23`, or are IoT/guest on separate subnets the PC cannot reach?
16. **Fingbox / Domotz:** is `f0:23:b9:ed:95:4d` the Fingbox? Should the tool integrate with it (Fing API) or just treat it as a device?
17. **Other sources:** use Pi-hole (DNS per device), the Synology NAS, Home Assistant presence, or Uptime Kuma as extra sources?
18. **Key devices to watch:** which devices matter most (consoles, PCs, TVs) so they get per-device monitors by default?

## Output and use

19. **Dashboard access:** localhost only, or from phones on the LAN too (with a password)?
20. **Alerts:** notifications when something goes wrong (Windows toast, email, Home Assistant, push), or dashboard only?
21. **Reports:** do you want a shareable report (e.g. to send to an ISP or post on a forum)?
22. **Data retention:** how long to keep raw data and per-minute history?
23. **Name:** keep "homenetworkdiag"?
