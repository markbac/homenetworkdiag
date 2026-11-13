# MeshLens

*(repository: `homenetworkdiag`)*

**MeshLens** is a home network diagnostics tool for Windows that answers one question well: **why is the Wi-Fi slow, glitchy or laggy, and what should I change?**

It is a **tray app started when needed** with a **web control interface** in the browser, and it **finds ESP32 probes automatically** wherever you place them. It combines three views of the network that are normally looked at separately:

1. **The wired/IP view** from a Windows PC: which devices are on the network, what they are, who is flooding it, how latency and DNS behave.
2. **The router's view** from the ASUS AiMesh system: every client, which node and band it is on, signal and link rate, roaming, DHCP, radio settings.
3. **The radio's view** from small ESP32 probes placed around the house: what is actually happening in the air on 2.4 GHz and 5 GHz (channel crowding, air time, broadcasts, re-sends, signal per room), which neither Windows nor the router can see.

> **Status: first working slice.** Tray app, web UI with login, and the read-only ASUS AiMesh collector (MediaTek BT8 + Qualcomm BD4) with Wi-Fi journeys and kick/roam detection. See [development](docs/13-development.md) to run it and for what is done.

## Documents

| Document | What it covers |
|---|---|
| [Goals and scope](docs/01-goals.md) | The problem, who it is for, what is in and out of scope |
| [Requirements](docs/02-requirements.md) | Numbered functional and non-functional requirements |
| [Architecture](docs/03-architecture.md) | C4 context / container / component diagrams, deployment, data model |
| [ESP32 probes](docs/04-esp32-probes.md) | Probe hardware, roles, capabilities, probe API |
| [ASUS AiMesh data](docs/05-asus-aimesh.md) | What can be read from the router and nodes, and how |
| [Reuse](docs/06-reuse.md) | What to take from `homelab-unified` and the `esp32` weather-display firmware |
| [Findings so far](docs/07-findings.md) | Real measurements from this network that shaped the requirements |
| [Open questions](docs/08-open-questions.md) | Decisions still needed |
| [Sequences and state machines](docs/09-sequences.md) | Start-up, probe discovery and moves, quick diagnosis, monitors, ASUS polling, lifecycles |
| [Decisions](docs/10-decisions.md) | Agreed decisions (Python, tray app, read-only router, probe discovery and placements...) |
| [Network inventory, services and security](docs/11-network-inventory-and-security.md) | Beyond Wi-Fi: services (mDNS, UPnP), open ports, router exposure and NAT, security hygiene, DHCP/DNS/ISP health, wired topology |
| [Topology, history and enrichment](docs/12-topology-history-enrichment.md) | Interactive map, device pages with Wi-Fi details and handovers, history explorer, manual enrichment, feature backlog |
| [Development](docs/13-development.md) | How to run and test, code layout, what the first slice implements |

## Related projects

- [markbac/homelab-unified](https://github.com/markbac/homelab-unified): Linux/Docker network scanner and homelab dashboard. Source of the discovery, enrichment, classification and ASUS SSH collectors.
- [markbac/esp32](https://github.com/markbac/esp32) (`weather-display`): CrowPanel firmware whose Network pages (signal, channel crowding, air survey, test, monitor) are the prototype for the ESP32 probes.
