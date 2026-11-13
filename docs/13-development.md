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
| `meshlens/journeys.py` | Wi-Fi journeys, stability counts, kick loops / roam storms / flapping |
| `meshlens/engine.py` | Background scheduler, retention |
| `meshlens/web/` | aiohttp server, login, JSON API, static UI |
| `meshlens/tray.py` | System tray icon |
| `meshlens/oui.py` | IEEE vendor lookup, local (replaces `homelab-unified`'s online lookup) |
| `tests/fixtures/asus/` | Hand-written fixtures in the exact verified formats; **no real addresses or names** |

## Status of the first slice

| Area | Requirements | State |
|---|---|---|
| Tray app, web UI with login, JSON API | UI-01, UI-06, NFR-04 | Done (basic pages: Overview, Devices + device drawer, Wi-Fi events, Settings) |
| ASUS collector (MediaTek BT8, Qualcomm BD4), read-only allow-list, chip detection | ASUS-00/01/02/03/06/09*, D-04, D-08 | Done over SSH (*web API not yet) |
| Client list, device database, leases, Wi-Fi security per device | DEV-02/03/04*, SEC-01 | Done (*classification not yet) |
| Per-client radio stats, radio health, interface counters | BUS-01/03 | Stored; charts beyond the device sparkline not yet |
| Log events, journeys, kick loops / roam storms / flapping | EVT-01..05 | Done |
| Manual enrichment, watched devices | ENR-01, UI-07 | Done (basic fields) |
| Storage, JSONL, retention (31 days raw, 366 days summaries) | NFR-05, D-20, D-25 | Done (daily summaries table present, not yet filled) |
| Next | LAN discovery, probes, tests/monitors, topology map, reports, Pi-hole/HA/UniFi/devolo plug-ins | Not started |

## Rules for contributors

- Collectors never write to network equipment. New commands go into `commands.py` and must be read-only.
- Parsers stay pure and get a fixture-based test. Fixtures use made-up addresses (`02:..`, `0a:..`) and names.
- No device data leaves the LAN; no secrets in config, logs, the database or API responses.
