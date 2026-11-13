# Sequences and state machines

## 1. Starting the tray app

```mermaid
sequenceDiagram
  actor U as User
  participant T as Tray app
  participant E as Engine
  participant DB as SQLite / JSONL
  participant P as Probes
  participant R as ASUS units
  U->>T: Start homenetworkdiag
  T->>E: start engine
  E->>DB: open, migrate schema
  E->>E: load settings, rules, IEEE cache
  par discovery of probes
    E->>P: mDNS browse _hndprobe._tcp + listen for UDP beacons
    P-->>E: announce (id, ip, chip, firmware)
    E->>P: GET /api/sync?since=<last seq> (token)
    P-->>E: buffered records while app was closed
  and router
    E->>R: SSH (key) capability capture
    R-->>E: tools present, interfaces, firmware
    E->>R: first full poll (clients, radios, settings)
  and LAN
    E->>E: neighbour table read, paced ARP sweep
  end
  E->>DB: store everything
  E->>E: evaluate rules
  T->>U: tray icon green, "Open dashboard"
```

## 2. Probe auto-discovery and adoption

```mermaid
sequenceDiagram
  actor U as User
  participant W as Web UI
  participant E as Engine (probe manager)
  participant P as New probe (C5)
  P->>P: boot, join Wi-Fi (provisioned once over USB or SoftAP)
  P-->>E: mDNS _hndprobe._tcp + UDP beacon (id, chip, bands, unadopted)
  E->>W: "New probe found: ESP32-C5 (2.4+5 GHz)"
  U->>W: Adopt
  W->>E: adopt(probe id)
  E->>P: POST /api/adopt (engine id)
  P->>P: show 6-digit pairing code on screen
  U->>W: enter code shown on probe
  W->>E: code
  E->>P: POST /api/adopt/confirm (code)
  P-->>E: shared token (stored by engine with DPAPI)
  E->>W: adopted: please set placement
  U->>W: placement = "Boys' room, desk"
  W->>E: set placement
  E->>P: POST /api/placement
  P->>P: show placement on screen
```

Probes that are already adopted are matched by id whatever IP they get, so DHCP changes and moves need no configuration.

## 3. Moving a probe (placement change)

```mermaid
sequenceDiagram
  actor U as User
  participant P as Probe
  participant E as Engine
  participant W as Web UI
  U->>P: unplug, carry to new room, power on
  P->>P: compare surroundings with last placement (joined unit, signal to units, neighbour set)
  alt surroundings changed a lot
    P->>P: screen: "Moved? Pick room" (rooms list synced from engine)
    U->>P: tap "Living room"
    P->>E: placement changed (room, time)
  else user changes it in the browser
    E->>W: "Probe C5 looks moved (joined Outdoor node, -78 dBm)"
    U->>W: set placement = "Living room"
    W->>E: set placement
    E->>P: POST /api/placement
  end
  E->>E: close old placement, open new one; later results tagged with it
```

## 4. Quick diagnosis ("it's laggy right now")

```mermaid
sequenceDiagram
  actor U as User
  participant W as Web UI
  participant E as Engine
  participant P as Probe in the problem room
  participant R as ASUS units
  U->>W: Quick diagnosis (room = Boys' room, device = Xbox)
  W->>E: start session (5 min)
  par probe
    E->>P: POST /api/test, then /api/air (joined channel, 60 s)
    P-->>E: latency, loss, DNS, air time by category, devices, broadcast sources
  and router
    E->>R: poll every 30 s: Xbox band/node/rate/RSSI/retries, radio utilisation all bands
  and PC
    E->>E: ping Xbox every 1 s, ping router/internet, DNS matrix
  and wire (if Npcap)
    E->>E: count broadcasts per source
  end
  E->>E: rules over the session window
  E->>W: findings ranked, with evidence charts
  W->>U: "Broadcast flood from Fingbox uses 27% of 2.4 GHz air. Xbox is on 2.4 GHz at -71 dBm via Outdoor node; it supports 5 GHz."
```

## 5. Evening monitor while the PC app may close

```mermaid
sequenceDiagram
  actor U as User
  participant W as Web UI
  participant E as Engine
  participant P as Probe
  U->>W: Monitor 4 h from 19:00, probe C6 (Boys' room)
  W->>E: create monitor
  E->>P: POST /api/monitor (start 19:00, 240 min, air on)
  Note over E: User closes the tray app at 19:30
  loop every minute
    P->>P: record minute + events, buffer (RAM ring + SD)
  end
  Note over U,E: Next morning: tray app started
  E->>P: GET /api/sync?since=seq
  P-->>E: 240 minutes, events
  E->>E: router data exists only for 19:00-19:30 (app closed): marked as gap
  E->>W: timeline with probe data, gaps shown, findings
```

## 6. ASUS poll cycle

```mermaid
sequenceDiagram
  participant S as Scheduler
  participant A as ASUS collector
  participant R as BT8 (router)
  participant N as Nodes (BT8 1F, BD4 x3)
  participant DB as Store
  S->>A: every 60 s
  A->>R: web API: get_clientlist(), get_cfg_clientlist()
  R-->>A: clients with node, band, rssi, rates; nodes with backhaul
  A->>R: SSH: per-band station stats, utilisation, /proc/net/dev, new syslog lines
  loop each online node
    A->>N: SSH: per-band station stats, utilisation, backhaul link, counters
  end
  A->>A: parse with the unit's capability profile (wl / iw / iwpriv)
  A->>DB: station samples, radio samples, events (roam, deauth reason, DFS)
  Note over A,R: every 15 min also: settings diff, DHCP leases, topology
```

## 7. Probe lifecycle

```mermaid
stateDiagram-v2
  [*] --> Unprovisioned: new firmware
  Unprovisioned --> Discovered: Wi-Fi set (USB / SoftAP), announces
  Discovered --> Adopted: pairing code confirmed
  Adopted --> Placed: placement set
  Placed --> Measuring: test / survey / monitor started
  Measuring --> Placed: finished
  Placed --> Moving: big change in surroundings or power cycle elsewhere
  Moving --> Placed: placement confirmed (probe screen or web)
  Placed --> Offline: no contact > 2 min
  Measuring --> Offline: engine closed or probe out of range (keeps measuring, buffers)
  Offline --> Placed: back in contact, buffer synced
  Adopted --> Discovered: forgotten in web UI
```

## 8. Finding lifecycle

```mermaid
stateDiagram-v2
  [*] --> Open: rule matched
  Open --> Acknowledged: user has seen it
  Open --> Resolved: condition gone for N windows
  Acknowledged --> Resolved: condition gone
  Acknowledged --> Snoozed: user snoozes (e.g. known device)
  Snoozed --> Open: snooze expires and still true
  Resolved --> Verified: before/after test shows improvement
  Resolved --> Open: condition returns
  Verified --> [*]
```
