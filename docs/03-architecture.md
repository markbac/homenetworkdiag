# Architecture

C4 diagrams (context, containers, components), deployment, data model and the main runtime flows. Mermaid diagrams render on GitHub. Decisions referenced as D-nn are in [decisions](10-decisions.md).

## 1. System context (C4 level 1)

```mermaid
C4Context
  title homenetworkdiag - system context
  Person(admin, "Household admin", "Runs diagnostics, reads findings, moves probes")
  Person(family, "Family", "Reports lag; may run a quick test from a probe's screen")
  System(hnd, "homenetworkdiag", "Windows tray app + ESP32 probes: finds why the network is slow and what to change")
  System_Ext(asus, "ASUS AiMesh", "BT8 router + BT8/BD4 nodes; stock firmware; SSH :1111 and web API")
  System_Ext(lan, "Home devices", "~55 phones, consoles, PCs, TVs, IoT on 10.0.10.0/23")
  System_Ext(inet, "Internet test targets", "1.1.1.1, DNS resolvers, speed test servers")
  System_Ext(ieee, "IEEE registry", "MAC vendor lists, downloaded and cached")
  Rel(admin, hnd, "Uses", "browser / tray")
  Rel(family, hnd, "Quick test", "probe touch screen")
  Rel(hnd, asus, "Reads, never writes", "SSH key, HTTPS")
  Rel(hnd, lan, "Discovers, pings, listens", "ARP, ICMP, mDNS, SSDP, NetBIOS, 802.11 headers")
  Rel(hnd, inet, "Latency, DNS, throughput", "ICMP, DNS, HTTPS")
  Rel(hnd, ieee, "Downloads vendor lists", "HTTPS")
```

## 2. Containers (C4 level 2)

```mermaid
C4Container
  title homenetworkdiag - containers
  Person(admin, "Household admin")
  System_Boundary(pc, "Windows PC") {
    Container(tray, "Tray launcher", "Python, pystray", "Starts/stops the engine, shows status, opens the dashboard")
    Container(engine, "Diagnostics engine", "Python 3.12, asyncio", "Collectors, scheduler, probe manager, rules, local API")
    Container(web, "Web control UI", "HTML/JS served by the engine", "Dashboard, controls, probe management, settings")
    ContainerDb(db, "Inventory + time series", "SQLite (WAL)", "Devices, sightings, mesh, probes, per-minute series, findings")
    ContainerDb(jsonl, "Raw logs", "JSON Lines per day", "Every collected record, probe minutes, events")
    ContainerDb(secrets, "Secrets", "Windows DPAPI / Credential Manager", "SSH key passphrase, ASUS web password, probe tokens")
  }
  System_Boundary(probes, "ESP32 probes (mobile)") {
    Container(c5, "C5 probe", "ESP32-C5, Arduino/IDF", "2.4 + 5 GHz scans, air survey, tests, monitors, buffering")
    Container(c6, "C6 probe", "ESP32-C6", "2.4 GHz + 802.15.4")
    Container(panel, "CrowPanel", "ESP32-S3", "Existing weather display; 2.4 GHz probe")
  }
  System_Ext(asus, "ASUS AiMesh")
  Rel(admin, web, "Uses", "HTTP localhost")
  Rel(admin, tray, "Starts")
  Rel(tray, engine, "Starts / stops")
  Rel(web, engine, "Calls", "JSON API + SSE")
  Rel(engine, db, "Reads/writes")
  Rel(engine, jsonl, "Appends")
  Rel(engine, secrets, "Reads")
  Rel(engine, asus, "Polls", "SSH :1111, HTTPS appGet.cgi")
  Rel(engine, c5, "Discovers, adopts, controls, syncs", "mDNS/UDP + HTTP")
  Rel(engine, c6, "Same")
  Rel(engine, panel, "Same")
```

## 3. Engine components (C4 level 3)

```mermaid
C4Component
  title Diagnostics engine - components
  Container_Boundary(engine, "Diagnostics engine") {
    Component(sched, "Scheduler", "asyncio", "Runs jobs on intervals; per-job status, timeouts, back-off")
    Component(disc, "LAN discovery", "reused + Windows APIs", "Paced ARP (SendARP), neighbour table, ping, mDNS, SSDP, NetBIOS")
    Component(enrich, "Enrichment + classification", "reused", "Names, vendor (local IEEE), type, randomised MAC, user overrides")
    Component(asusc, "ASUS collector", "reused router_ssh/node_ssh + web API", "Capability profile, clients, radios, utilisation, logs, settings")
    Component(probe, "Probe manager", "new", "mDNS/UDP discovery, adoption, placements, control, sync, health")
    Component(tests, "Tests and monitors", "reused methods", "Ping/jitter/loss, DNS matrix, bufferbloat, speed, device monitors")
    Component(wire, "Wire listener", "optional, Npcap", "Broadcast/multicast counts per source on the LAN")
    Component(store, "Store", "reused db layer", "SQLite schema + migrations, JSONL writer, retention")
    Component(rules, "Rule engine", "new, YAML rules", "Findings with evidence and recommendations")
    Component(api, "Local API + web server", "aiohttp", "REST, server-sent events, static UI, auth for LAN mode")
  }
  Rel(sched, disc, "runs")
  Rel(sched, asusc, "runs")
  Rel(sched, tests, "runs")
  Rel(sched, probe, "runs")
  Rel(disc, enrich, "new hosts")
  Rel(disc, store, "sightings")
  Rel(asusc, store, "clients, radios, events")
  Rel(probe, store, "scans, surveys, minutes, events")
  Rel(tests, store, "results")
  Rel(wire, store, "broadcast counts")
  Rel(store, rules, "series")
  Rel(rules, store, "findings")
  Rel(api, store, "queries")
  Rel(api, probe, "commands")
  Rel(api, tests, "commands")
```

## 4. Deployment

```mermaid
flowchart TB
  subgraph house["House LAN 10.0.10.0/23"]
    subgraph pc["Windows 11 PC"]
      tray["Tray app<br/>homenetworkdiag.exe"]
      eng["Engine + web UI<br/>http://localhost:8787"]
      data["%LOCALAPPDATA%\\homenetworkdiag<br/>db.sqlite, logs\\*.jsonl"]
    end
    bt8["BT8 router 10.0.10.1<br/>SSH :1111, HTTPS"]
    n1["BT8 node 1F"]
    n2["BD4 node 2F"]
    n3["BD4 Outdoor"]
    n4["BD4 (offline?)"]
    c5["ESP32-C5 probe<br/>moves between rooms"]
    c6["ESP32-C6 probe"]
    cp["CrowPanel (kitchen)"]
    phone["Phone browser<br/>(LAN mode, optional)"]
  end
  tray --> eng --> data
  eng -- SSH/HTTPS --> bt8
  eng -- SSH --> n1 & n2 & n3 & n4
  eng -- HTTP --> c5 & c6 & cp
  c5 & c6 & cp -. mDNS / UDP beacon .-> eng
  phone -. HTTP + password .-> eng
```

## 5. Key design points

- **Tray app started when needed (D-02).** The engine runs only while the tray app runs. Probes therefore own long monitors: they keep measuring and buffering (RAM ring + SD card where fitted) while the PC is off, and the probe manager pulls everything since the last sync when the app starts. Records are idempotent (probe id + sequence number).
- **Web control interface (D-05).** Everything is done in the browser: dashboard, starting tests/surveys/monitors, adopting and placing probes, naming devices, settings. The tray menu offers: Open dashboard, Quick diagnosis, Pause collectors, Quit.
- **Probe auto-discovery (D-06).** See [probes](04-esp32-probes.md) and the sequence below.
- **Placements (D-07).** A probe's results are tagged with its current placement; history and comparisons are per placement (room), not per probe.
- **Read-only router (D-04), SSH key (D-09), capability detection (D-08).** See [ASUS data](05-asus-aimesh.md).
- **Do no harm (NFR-02).** Discovery is paced (for a /23: about 50 ARP requests/s, full sweep every 15 min), never continuous; the engine counts its own packets and shows them.
- **Time.** PC and probes use NTP; every record has UTC time plus local time for display.

## 6. Data model

```mermaid
erDiagram
  DEVICE ||--o{ SIGHTING : "seen as"
  DEVICE ||--o{ AIR_DEVICE : "uses air"
  DEVICE ||--o{ TEST_RESULT : "target of"
  DEVICE }o--o| MESH_UNIT : "is (for units)"
  MESH_UNIT ||--o{ RADIO : has
  RADIO ||--o{ RADIO_SAMPLE : "utilisation, noise"
  RADIO ||--o{ STATION_SAMPLE : "client stats"
  DEVICE ||--o{ STATION_SAMPLE : "as client"
  PROBE ||--o{ PLACEMENT : "placed at"
  PLACEMENT ||--o{ AIR_SAMPLE : records
  PLACEMENT ||--o{ SCAN : records
  PLACEMENT ||--o{ TEST_RESULT : records
  AIR_SAMPLE ||--o{ AIR_DEVICE : "per device"
  AIR_SAMPLE ||--o{ BROADCAST_SOURCE : "per source"
  EVENT }o--o| DEVICE : about
  FINDING }o--o{ EVENT : "evidence"
  FINDING }o--o| DEVICE : about

  DEVICE {
    string mac PK
    string user_name
    string dhcp_name
    string mdns_name
    string vendor
    bool randomised
    string type
    string owner
    string room
  }
  MESH_UNIT {
    string id PK
    string model
    string role
    string ip
    string capability_profile
  }
  RADIO {
    string id PK
    string band
    int channel
    int width
  }
  PROBE {
    string id PK
    string chip
    string bands
    string firmware
    string token_ref
  }
  PLACEMENT {
    int id PK
    string probe FK
    string room
    string spot
    datetime from
    datetime to
  }
  AIR_SAMPLE {
    int id PK
    datetime minute
    string band
    int channel
    float busy_pct
    json categories
  }
  FINDING {
    int id PK
    string rule
    string severity
    datetime window_from
    datetime window_to
    string status
    json evidence
  }
```

## 7. Runtime flows

See [sequences and state machines](09-sequences.md).
