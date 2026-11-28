# MeshLens

*(repository: `homenetworkdiag`)*

**MeshLens** is a home network diagnostics tool for Windows that answers one question well: **why is the Wi-Fi slow, glitchy or laggy, and what should I change?**

It is a **tray app started when needed** with a **web control interface** in the browser, and it **finds ESP32 probes automatically** wherever you place them. It combines three views of the network that are normally looked at separately:

1. **The wired/IP view** from a Windows PC: which devices are on the network, what they are, who is flooding it, how latency and DNS behave.
2. **The router's view** from the ASUS AiMesh system: every client, which node and band it is on, signal and link rate, roaming, DHCP, radio settings.
3. **The radio's view** from small ESP32 probes placed around the house: what is actually happening in the air on 2.4 GHz and 5 GHz (channel crowding, air time, broadcasts, re-sends, signal per room), which neither Windows nor the router can see.

> **Status: 0.9 — in daily use.** Read-only ASUS AiMesh collector (MediaTek BT8, Qualcomm BD4, dual-band MediaTek nodes such as the RT-AX57 Go), devolo powerline, LAN discovery and ports, latency and speed monitors, and ESP32 probes (first version). See [what's new](CHANGELOG.md) and [development](docs/13-development.md).

### What it finds

- **Wi-Fi problems per device**: kick loops, roam storms, sticky clients, disconnects, weak signal, devices stuck on 2.4 GHz, devices wandering onto an outdoor unit, devices failing to join a unit (WPA3 key / PMF problems).
- **Mesh problems**: many devices dropping at once, radar (DFS) forcing 5 GHz off its channel (with what each event did to your devices), channel hopping, busy channels, units restarting, different firmware or PMF settings between units.
- **Wiring problems**: network loops behind powerline adapters, slow powerline links, wired port flaps and errors, a device on cable and Wi-Fi with one address.
- **Line and path problems**: internet loss and spikes, bufferbloat, speed below your plan, this PC's path being the bottleneck (and when that path changed).
- **A channel plan**: which 5 GHz block and 2.4 GHz channel to use, from this PC's and the probes' air surveys and the radar history.

### Using it

- Every issue says what was seen, why it probably happens, what to try, **step-by-step how to change it on the ASUS** and **how to check it worked**.
- **Mark a change** when you change something; MeshLens marks every graph and compares before with after.
- **Hide** a known, harmless issue for one device (it is still checked, just not counted); **Show hidden** brings it back.
- **Setup checklist** (Settings) shows what is left to set up; **key devices** (DNS, NAS, Home Assistant, switches) are suggested for watching.

## Running on a Linux server (Docker)

MeshLens can also run headless on a Linux Docker host on the same LAN (no tray). From this repository on Windows:

```powershell
.\deploy\deploy.ps1 -Ref main             # deploy main to the default host, 10.0.11.195
.\deploy\deploy.ps1                       # deploy the working tree, uncommitted changes included
.\deploy\deploy.ps1 -Ref main -HostName <address>
```

The script copies the source over SSH, builds the image there and starts it with `docker compose`
([deploy/docker-compose.yml](deploy/docker-compose.yml)). The web UI is then at `http://<host>:8787/`.

- **Host network**: needed for the LAN sweep, the ARP table, mDNS/SSDP and ESP32 probe discovery.
- **Data** (config, database, logs, web login) is kept in `~/meshlens/deploy/data` on the host.
- **Secrets**: there is no credential manager on a server, so passwords and tokens go to `data/secrets.json`
  (mode 0600) instead. Keep that folder private.
- **Not available on Linux**: the host-PC Wi-Fi air survey and adapter counters (they use Windows `netsh`),
  the tray icon and self-update (redeploy instead). Everything else works the same.

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
| [Changelog](CHANGELOG.md) | What changed in each release |
| [Development](docs/13-development.md) | How to run and test, code layout, what the first slice implements |

## Related projects

- [markbac/homelab-unified](https://github.com/markbac/homelab-unified): Linux/Docker network scanner and homelab dashboard. Source of the discovery, enrichment, classification and ASUS SSH collectors.
- [markbac/esp32](https://github.com/markbac/esp32) (`weather-display`): CrowPanel firmware whose Network pages (signal, channel crowding, air survey, test, monitor) are the prototype for the ESP32 probes.
