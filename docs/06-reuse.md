# Reuse

## From `homelab-unified` (Python)

| Area | Module(s) | Reuse | Notes for Windows |
|---|---|---|---|
| ASUS router SSH | `discovery/router_ssh.py` | Take, split into collect + parse | Pure SSH (paramiko); works on Windows. Separate the parsers so they can be tested with fixtures |
| AiMesh nodes SSH | `discovery/node_ssh.py` | Take | As above |
| ARP / neighbours | `discovery/arp.py` | Rewrite the read side | Use `GetIpNetTable2` / `SendARP` instead of `/proc/net/arp`; pace the sweep (NFR-02) |
| DHCP leases | `discovery/dhcp.py` | Replace | Leases come from the router (ASUS-01) |
| Rogue DHCP check | `discovery/dhcp_probe.py` | Take | Needs a UDP socket on port 68 (may need admin on Windows) |
| DNS latency matrix | `discovery/dns_latency.py` | Take | Raw UDP, portable |
| Ping / bufferbloat | `discovery/ping_test.py`, `discovery/speedtest.py` | Take the method | Replace calls to the `ping` binary with `IcmpSendEcho` or parse Windows `ping` output |
| SSDP / UPnP | `discovery/ssdp.py` | Take | Portable |
| mDNS, reverse DNS | `enrichment/dns.py` | Take | Use `zeroconf` on Windows |
| NetBIOS | `enrichment/netbios.py` | Take the raw UDP path | `nmblookup` is not on Windows |
| SNMP | `discovery/snmp_probe.py`, `enrichment/snmp.py` | Take (optional) | |
| HTTP / TLS banner, SSH banner | `enrichment/http.py`, `enrichment/ssh.py` | Take (on demand only) | |
| nmap | `discovery/nmap.py` | Optional | Only if nmap + Npcap are installed |
| OUI vendor | `enrichment/oui.py` | **Replace** | Currently sends prefixes to macvendors.com; use the local IEEE registry (MA-L/M/S CSVs, cached), as in `esp32/weather-display/scripts/air_report.py` (NFR-03) |
| Classification | `classification/*` (classify, profiles, overrides, icons, risk) | Take | Data-driven; extend with console/TV/IoT profiles |
| Database | `db/connection.py`, `db/schema.py`, `db/queries.py` | Take the pattern | SQLite WAL, all SQL in one module, migrations |
| Pi-hole, Synology | `discovery/pihole.py`, `discovery/synology.py` | Optional plug-ins | DNS query stats are useful for "which device is chatty" |
| Dashboard style | `dashboard/templates` | Take the look | |

Not reused: Docker/compose, NPM, Octopus, Skoda, Met Office, rivers, Tempest (dashboard-only features).

## From `esp32/weather-display` (C++, ESP-IDF/Arduino)

| Module | Reuse for |
|---|---|
| `src/core/wifi_scan.*` | Probe scan + history + mesh family / unit grouping (`radioKey`) |
| `src/core/air_survey.*` | Probe air survey: categories, per-device, broadcast sources |
| `src/core/net_test.*` | Probe quick test and monitor with events |
| `src/core/log.*` | JSONL logging, crash report on boot, `/logs` |
| `src/core/https_get.*`, `connectivity.*` | HTTP client, Wi-Fi re-join watchdog |
| `scripts/air_report.py` | IEEE vendor lookup, report layout |
| `scripts/gzip_web_page.py` | Gzipped on-device web pages |

The CrowPanel itself can be adopted as a probe with no hardware change: it already serves `/api/wifi.json`, `/api/air.json` and `POST /api/nettest`.
