# Development

## Running

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e .[dev]
.venv\Scripts\python -m meshlens            # console mode, web UI on http://localhost:8787
.venv\Scripts\python -m meshlens --tray     # tray icon (normal use)
.venv\Scripts\python -m meshlens --once     # one full router poll, print a summary
.venv\Scripts\python -m pytest              # tests
```

First start: open the web UI, create the dashboard password, then **Settings → ASUS router**: address, SSH port (1111) and username are pre-filled; enter the router password once (stored in the Windows Credential Manager).

## Installer and releases

```powershell
powershell -ExecutionPolicy Bypass -File packaginguild.ps1            # dist\MeshLens\MeshLens.exe (+ installer if Inno Setup 6 is installed)
```

- `packaging/meshlens.spec` (PyInstaller, one folder, no console), `packaging/installer.iss` (Inno Setup: Program Files,
  Start menu, optional start at sign-in, optional firewall rule for **private** networks only, uninstaller that keeps the data).
- **Release:** bump `meshlens/__init__.py`, merge to `main`, then push a tag `vX.Y.Z`. `.github/workflows/release.yml` runs the
  tests, builds the exe, smoke-tests that it serves the dashboard, builds `MeshLens-X.Y.Z-setup.exe` and a portable zip,
  and publishes them as a GitHub release. Pull requests that touch packaging build the installer as a workflow artifact
  (nothing published). `ci.yml` runs the tests on every pull request.

Data lives in `%LOCALAPPDATA%\MeshLens` (`config.yaml`, `meshlens.db`, `raw\*.jsonl`, `logs\meshlens.log`, `oui\` vendor lists). Set `MESHLENS_HOME` to use another folder (e.g. a scratch instance for testing).

## Layout

| Path | What |
|---|---|
| `meshlens/config.py` | Settings file and data directory |
| `meshlens/secrets.py` | Windows Credential Manager (keyring) |
| `meshlens/store/db.py` | SQLite schema, migrations, all SQL (connection pattern from `homelab-unified`) |
| `meshlens/store/jsonl.py` | Daily raw JSON Lines |
| `meshlens/collectors/asus/commands.py` | **The read-only command allow-list** sent to ASUS units |
| `meshlens/collectors/asus/parsers.py` | Pure parsers for every verified ASUS output format |
| `meshlens/collectors/asus/collector.py` | Polls router + nodes, normalises, stores |
| `meshlens/journeys.py` | Wi-Fi journeys, stability counts |
| `meshlens/analysis.py` | **Issue rules** (evidence, cause, fixes, help link), health scores, trends, week-on-week |
| `meshlens/collectors/devolo.py` | devolo Magic powerline: adapters, devices behind each, PHY rates (read-only device API) |
| `meshlens/discovery/` | Ping/ARP sweep (mesh proxy-ARP aware), DNS/NetBIOS/mDNS/UPnP names and services, TCP port scan, device types |
| `meshlens/net/icmp.py` | ICMP echo via the Windows IP Helper API (no admin), TTL-limited probes |
| `meshlens/monitor.py` | Latency / jitter / loss monitor, one row per target per minute |
| `meshlens/net/speedtest.py` | Speed test with bufferbloat grade (idle, loaded down, loaded up) |
| `meshlens/topology.py` | Network map: units, backhaul, powerline, devices |
| `meshlens/analysis_net.py` | Issue rules for powerline, latency, speed, bufferbloat, ports |
| `meshlens/integrations.py` | Read-only connection tests for Pi-hole, Home Assistant, UniFi |
| `meshlens/engine.py` | Background scheduler, retention |
| `meshlens/web/` | aiohttp server, login, JSON API, static UI, `static/help.html` (in-app help) |
| `meshlens/tray.py` | System tray icon |
| `meshlens/oui.py` | IEEE vendor lookup, local (replaces `homelab-unified`'s online lookup) |
| `tests/fixtures/asus/` | Hand-written fixtures in the exact verified formats; **no real addresses or names** |

## Status

| Area | Requirements | State |
|---|---|---|
| Tray app, web UI with login, JSON API | UI-01, UI-06, NFR-04 | Done: Overview, Analysis, Devices + drawer, Wi-Fi events, Settings, Help |
| ASUS collector (MediaTek BT8, Qualcomm BD4), read-only allow-list, chip detection | ASUS-00/01/02/03/06/09*, D-04, D-08 | Done over SSH (*web API not yet) |
| Client list, device database, leases, Wi-Fi security per device | DEV-02/03/04*, SEC-01 | Done (*classification not yet) |
| Per-client radio stats, radio health, interface counters | BUS-01/03 | Stored and used by analysis; per-unit charts not yet |
| Log events, journeys | EVT-01..05 | Done |
| Analysis: 20 issue rules with cause and fix, device / unit / network health, settings review, cross-device patterns | DIA-01..05 | Done (thresholds editable) |
| Trends: daily summaries, 30-day chart, week on week | D-25, RPT (part) | Done |
| Manual enrichment, watched devices | ENR-01, UI-07 | Done (basic fields) |
| Settings for everything: dashboard password, network/subnets, router, Pi-hole, Home Assistant, UniFi, switch/powerline logins, thresholds, retention, web | UI-06, D-21, D-27 | Done; secrets in Credential Manager; Pi-hole / HA / UniFi connection test |
| In-app help: getting started, every issue, every setting, troubleshooting, glossary | UI | Done |
| Storage, JSONL, retention | NFR-05, D-20, D-25 | Done |
| devolo powerline collector | SW-04 | Done (adapters, bridged devices, pairwise rates, slow-link issue) |
| LAN discovery: sweep, ARP (proxy-ARP aware), reverse DNS, NetBIOS, mDNS, SSDP/UPnP, classification | DEV-01, SVC-01/02/03/07/08 | Done |
| Port scan, web titles, banners, risk labels, port history | PRT-01/03/04/05 | Done (TCP; UDP checks not yet) |
| Topology map | TOP-01, MAP-01/02 | Done (tree view; per-port switch mapping needs the switch collectors) |
| Latency / jitter / loss monitors, speed tests, bufferbloat | TST-01/02/03/05, ISP-05 | Done |
| Tray status colour, start with Windows, installer, CI, release workflow | UI-06, NFR-01 | Done |
| Next | Pi-hole / HA / UniFi / TP-Link switch collectors, ESP32 probes, reports, timeline, IPv6, rogue AP/DHCP checks | Not started |

## Rules for contributors

- Collectors never write to network equipment. New commands go into `commands.py` and must be read-only.
- Parsers stay pure and get a fixture-based test. Fixtures use made-up addresses (`02:..`, `0a:..`) and names.
- No device data leaves the LAN; no secrets in config, logs, the database or API responses.
