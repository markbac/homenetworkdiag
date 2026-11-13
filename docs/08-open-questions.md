# Open questions

None open. All questions are answered; see [decisions](10-decisions.md) D-01 to D-26.

| Topic | Answer |
|---|---|
| Name | **MeshLens** (repository `homenetworkdiag`) |
| Language | Python, reuse `homelab-unified` |
| How it runs | Tray app on an always-on mini PC, admin available |
| Relationship | Separate tool; `homelab-unified` later consumes its API |
| Probe firmware | ESP-IDF where required |
| Probes | Up to 6, moved around, Wi-Fi only, stand-alone with screens (signal, quick test, air survey, monitor status, placement), may channel-hop, USB-C power banks |
| Router | SSH port 1111, stock firmware, tool may hold the password, read-only forever |
| Network | Everything on `10.0.10.0/23` |
| Extra sources | Pi-hole and Home Assistant (optional plug-ins) |
| Fingbox / Domotz | Removed from the network |
| Watched devices | Chosen in the web UI |
| Dashboard | LAN access from phones/laptops with login |
| Alerts | None for now |
| Reports | Findings summary, evidence charts, device table, optional anonymisation |
| Retention | 1 month raw/per-minute, 12 months of hourly/daily summaries |
| UniFi switch | Local UniFi Network controller (on the mini PC) via a View Only account; switch SSH as fallback |

New questions found during implementation will be added here.
