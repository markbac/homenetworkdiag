"""SQLite store.

Connection handling is taken from ``homelab-unified`` (``db/connection.py``):
one connection per thread, WAL mode. All SQL lives in this module.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS devices (
  mac TEXT PRIMARY KEY,
  first_seen REAL, last_seen REAL,
  ip TEXT,
  name TEXT, name_src TEXT,
  vendor TEXT, vendor_src TEXT,
  type TEXT,
  randomised INTEGER DEFAULT 0,
  wired INTEGER,
  wifi_auth TEXT, unit TEXT, band TEXT,
  -- manual enrichment (ENR-01): always wins over detected values
  user_name TEXT, owner TEXT, room TEXT, user_type TEXT, tags TEXT, notes TEXT,
  watched INTEGER DEFAULT 0,
  -- discovery (DEV-01, SVC-*)
  names TEXT,                     -- JSON {source: name}: dhcp, mdns, netbios, dns, ssdp
  services TEXT,                  -- JSON list of mDNS/SSDP services
  model TEXT, dtype TEXT,         -- detected model, classified type
  os_hint TEXT,                   -- DHCP vendor class, e.g. MSFT 5.0 (Windows), android-dhcp-17
  last_alive REAL,                -- last answered a ping / ARP
  ports_scanned REAL
);

CREATE TABLE IF NOT EXISTS units (
  mac TEXT PRIMARY KEY,           -- unit's main MAC (as in cfg_device_list)
  model TEXT, productid TEXT, ip TEXT, role TEXT, alias TEXT,
  chip TEXT, firmware TEXT,
  macs TEXT,                      -- JSON list: every radio/BSSID/backhaul address of the unit
  reachable INTEGER, last_seen REAL, last_error TEXT,
  parent_hint TEXT,               -- preferred parent BSSID from the node's own settings
  backhaul TEXT,                  -- JSON [{parent, band, rssi, ts}]: this node's backhaul radios seen as clients
  info TEXT                       -- JSON snapshot: identity, uptime, load, interfaces, ports (Router tab)
);

CREATE TABLE IF NOT EXISTS station_samples (
  ts REAL, unit TEXT, mac TEXT, iface TEXT, band TEXT, ssid TEXT,
  rssi INTEGER, tx_rate REAL, rx_rate REAL, tx_bytes INTEGER, rx_bytes INTEGER,
  inactive_ms INTEGER, connected_s INTEGER
);
CREATE INDEX IF NOT EXISTS station_mac_ts ON station_samples(mac, ts);
CREATE INDEX IF NOT EXISTS station_ts ON station_samples(ts);

CREATE TABLE IF NOT EXISTS radio_samples (
  ts REAL, unit TEXT, iface TEXT, band TEXT,
  tx_fail_pct REAL, rx_crc_pct REAL, temperature REAL, noise REAL, data TEXT
);
CREATE INDEX IF NOT EXISTS radio_ts ON radio_samples(ts);

CREATE TABLE IF NOT EXISTS iface_counters (
  ts REAL, unit TEXT, iface TEXT, rx_bytes INTEGER, tx_bytes INTEGER
);
CREATE INDEX IF NOT EXISTS iface_ts ON iface_counters(ts);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL, unit TEXT, kind TEXT, mac TEXT, iface TEXT, detail TEXT,
  raw_hash TEXT,
  UNIQUE(unit, raw_hash)
);
CREATE INDEX IF NOT EXISTS events_mac_ts ON events(mac, ts);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);

CREATE TABLE IF NOT EXISTS unit_settings (
  ts REAL, unit TEXT, data TEXT
);

CREATE TABLE IF NOT EXISTS ports (
  mac TEXT, proto TEXT, port INTEGER, ip TEXT, service TEXT, banner TEXT, title TEXT, risk TEXT,
  first_seen REAL, last_seen REAL, open INTEGER,
  PRIMARY KEY (mac, proto, port)
);

CREATE TABLE IF NOT EXISTS inventory_events (
  ts REAL, mac TEXT, kind TEXT, detail TEXT
);
CREATE INDEX IF NOT EXISTS inv_ts ON inventory_events(ts);

CREATE TABLE IF NOT EXISTS plc_adapters (
  mac TEXT PRIMARY KEY, ip TEXT, name TEXT, product TEXT, firmware TEXT,
  attached_to_router INTEGER, bridged TEXT, last_seen REAL
);

CREATE TABLE IF NOT EXISTS plc_rates (
  ts REAL, src TEXT, dst TEXT, tx_mbps INTEGER, rx_mbps INTEGER
);
CREATE INDEX IF NOT EXISTS plc_ts ON plc_rates(ts);

CREATE TABLE IF NOT EXISTS ping_minutes (
  ts REAL, target TEXT, kind TEXT, label TEXT,
  sent INTEGER, lost INTEGER, avg_ms REAL, min_ms REAL, max_ms REAL, p95_ms REAL, jitter_ms REAL
);
CREATE INDEX IF NOT EXISTS ping_ts ON ping_minutes(ts);
CREATE INDEX IF NOT EXISTS ping_target_ts ON ping_minutes(target, ts);

CREATE TABLE IF NOT EXISTS speed_tests (
  ts REAL, server TEXT, down_mbps REAL, up_mbps REAL,
  idle_ms REAL, down_loaded_ms REAL, up_loaded_ms REAL, grade TEXT, trigger TEXT, error TEXT,
  location TEXT                   -- where the test ran: pc (this PC), router, probe:<id>; empty = pc
);

CREATE TABLE IF NOT EXISTS host_lan (
  ts REAL, adapter TEXT, rx_mbps REAL, tx_mbps REAL, ucast_pps REAL, mcast_pps REAL, bcast_pps REAL,
  discards INTEGER, errors INTEGER
);
CREATE INDEX IF NOT EXISTS host_lan_ts ON host_lan(ts);

CREATE TABLE IF NOT EXISTS air_scan (
  ts REAL, bssid TEXT, ssid TEXT, band TEXT, channel INTEGER, signal_pct INTEGER, rssi INTEGER,
  util_pct INTEGER, stations INTEGER, radio TEXT, own_unit TEXT
);
CREATE INDEX IF NOT EXISTS air_scan_ts ON air_scan(ts);

CREATE TABLE IF NOT EXISTS unit_samples (
  ts REAL, unit TEXT, cpu_pct REAL, max_core_pct REAL, softirq_pct REAL, load1 REAL,
  mem_used_pct REAL, mem_avail_mb INTEGER, mem_total_mb INTEGER, temp_c REAL,
  conntrack INTEGER, conntrack_max INTEGER, jffs_pct INTEGER, tmp_pct INTEGER, uptime_s INTEGER
);
CREATE INDEX IF NOT EXISTS unit_samples_ts ON unit_samples(ts);

CREATE TABLE IF NOT EXISTS daily_device_summary (
  day TEXT, mac TEXT, kicks INTEGER, roams INTEGER, connects INTEGER, disconnects INTEGER,
  min_rssi INTEGER, max_rssi INTEGER,
  avg_rssi REAL, avg_tx_rate REAL, samples INTEGER, score INTEGER,
  PRIMARY KEY (day, mac)
);
"""

_local = threading.local()
_path: Path | None = None
_write_lock = threading.Lock()


def init(path: Path) -> None:
    """Set the database path and create/migrate the schema."""
    global _path
    _path = path
    c = conn()
    c.executescript(SCHEMA)
    _add_missing_columns(c)
    c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    c.commit()


def _add_missing_columns(c: sqlite3.Connection) -> None:
    """Lightweight migration: add columns that newer versions of SCHEMA define."""
    import re
    tables = []
    for chunk in SCHEMA.split("CREATE TABLE IF NOT EXISTS ")[1:]:
        m = re.match(r"(\w+) \((.*)\);", chunk.split("CREATE INDEX")[0].strip(), re.S)
        if m:
            tables.append((m.group(1), m.group(2)))
    for table, body in tables:
        have = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
        for line in body.splitlines():
            line = line.split("--")[0].strip().rstrip(",")
            for col in (x.strip() for x in line.split(",")):
                m = re.match(r"(\w+) (TEXT|INTEGER|REAL)\b(.*)", col)
                if not m or m.group(1) in have or m.group(1).upper() in ("PRIMARY", "UNIQUE"):
                    continue
                extra = m.group(3)
                if "PRIMARY" in extra.upper() or "UNIQUE" in extra.upper():
                    continue  # SQLite cannot add key columns; tables with them are created whole.
                c.execute(f"ALTER TABLE {table} ADD COLUMN {m.group(1)} {m.group(2)}{extra}")
                have.add(m.group(1))


def conn() -> sqlite3.Connection:
    if _path is None:
        raise RuntimeError("store.db.init() has not been called")
    c = getattr(_local, "conn", None)
    if c is None or getattr(_local, "path", None) != _path:
        c = sqlite3.connect(str(_path), check_same_thread=False, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        _local.conn, _local.path = c, _path
    return c


def close() -> None:
    c = getattr(_local, "conn", None)
    if c is not None:
        c.close()
        _local.conn = None


def _rows(cur: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------- writes

def upsert_device(mac: str, now: float, **fields: Any) -> None:
    """Create or update a device; only non-None detected fields overwrite."""
    mac = mac.lower()
    cols = {k: v for k, v in fields.items() if v is not None}
    with _write_lock:
        c = conn()
        c.execute("INSERT OR IGNORE INTO devices(mac, first_seen, last_seen) VALUES (?, ?, ?)", (mac, now, now))
        sets = ", ".join(f"{k} = ?" for k in cols)
        c.execute(f"UPDATE devices SET last_seen = ?{', ' + sets if sets else ''} WHERE mac = ?",
                  (now, *cols.values(), mac))
        c.commit()


def update_enrichment(mac: str, **fields: Any) -> bool:
    """Manual enrichment (ENR-01). Unknown keys are ignored."""
    allowed = {"user_name", "owner", "room", "user_type", "tags", "notes", "watched"}
    cols = {k: v for k, v in fields.items() if k in allowed}
    if not cols:
        return False
    with _write_lock:
        c = conn()
        cur = c.execute(f"UPDATE devices SET {', '.join(f'{k} = ?' for k in cols)} WHERE mac = ?",
                        (*cols.values(), mac.lower()))
        c.commit()
        return cur.rowcount > 0


def upsert_unit(mac: str, **fields: Any) -> None:
    mac = mac.lower()
    cols = {k: v for k, v in fields.items() if v is not None}
    with _write_lock:
        c = conn()
        c.execute("INSERT OR IGNORE INTO units(mac) VALUES (?)", (mac,))
        if cols:
            c.execute(f"UPDATE units SET {', '.join(f'{k} = ?' for k in cols)} WHERE mac = ?", (*cols.values(), mac))
        c.commit()


def insert_many(table: str, rows: Iterable[dict[str, Any]]) -> int:
    rows = list(rows)
    if not rows:
        return 0
    keys = list(rows[0].keys())
    with _write_lock:
        c = conn()
        c.executemany(f"INSERT INTO {table}({', '.join(keys)}) VALUES ({', '.join('?' for _ in keys)})",
                      [tuple(r.get(k) for k in keys) for r in rows])
        c.commit()
    return len(rows)


def insert_events(events: Iterable[dict[str, Any]]) -> int:
    """Insert events, skipping ones already stored (same unit + raw line hash)."""
    n = 0
    with _write_lock:
        c = conn()
        for e in events:
            cur = c.execute(
                "INSERT OR IGNORE INTO events(ts, unit, kind, mac, iface, detail, raw_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (e["ts"], e["unit"], e["kind"], e.get("mac"), e.get("iface"), json.dumps(e.get("detail") or {}),
                 e["raw_hash"]))
            n += cur.rowcount
        c.commit()
    return n


def prune(raw_days: int, summary_days: int, now: float | None = None) -> None:
    """Retention (D-20, D-25)."""
    now = now or time.time()
    cut = now - raw_days * 86400
    with _write_lock:
        c = conn()
        for t in ("station_samples", "radio_samples", "iface_counters", "events", "unit_settings", "inventory_events",
                  "plc_rates", "ping_minutes", "host_lan", "air_scan", "unit_samples"):
            c.execute(f"DELETE FROM {t} WHERE ts < ?", (cut,))
        day_cut = time.strftime("%Y-%m-%d", time.localtime(now - summary_days * 86400))
        c.execute("DELETE FROM daily_device_summary WHERE day < ?", (day_cut,))
        c.execute("DELETE FROM speed_tests WHERE ts < ?", (now - summary_days * 86400,))
        c.commit()


# ---------------------------------------------------------------- reads

def devices() -> list[dict[str, Any]]:
    return _rows(conn().execute("SELECT * FROM devices ORDER BY COALESCE(user_name, name, mac)"))


def device(mac: str) -> dict[str, Any] | None:
    r = conn().execute("SELECT * FROM devices WHERE mac = ?", (mac.lower(),)).fetchone()
    return dict(r) if r else None


def units() -> list[dict[str, Any]]:
    out = _rows(conn().execute("SELECT * FROM units ORDER BY role DESC, alias"))
    for u in out:
        u["macs"] = json.loads(u["macs"] or "[]")
        u["backhaul"] = json.loads(u.get("backhaul") or "[]")
        u["info"] = json.loads(u.get("info") or "{}")
    return out


def latest_stations() -> list[dict[str, Any]]:
    """Most recent sample per device (where each device is connected now)."""
    return _rows(conn().execute(
        """SELECT s.* FROM station_samples s
           JOIN (SELECT mac, MAX(ts) AS ts FROM station_samples GROUP BY mac) m ON m.mac = s.mac AND m.ts = s.ts"""))


def station_history(mac: str, since: float) -> list[dict[str, Any]]:
    return _rows(conn().execute("SELECT * FROM station_samples WHERE mac = ? AND ts >= ? ORDER BY ts", (mac.lower(), since)))


def events(since: float, mac: str | None = None, kinds: list[str] | None = None, limit: int = 1000) -> list[dict[str, Any]]:
    q = "SELECT * FROM events WHERE ts >= ?"
    args: list[Any] = [since]
    if mac:
        q += " AND mac = ?"
        args.append(mac.lower())
    if kinds:
        q += f" AND kind IN ({', '.join('?' for _ in kinds)})"
        args += kinds
    q += " ORDER BY ts DESC LIMIT ?"
    args.append(limit)
    out = _rows(conn().execute(q, args))
    from ..collectors.asus.parsers import band_of_iface
    for e in out:
        e["detail"] = json.loads(e["detail"] or "{}")
        e["band"] = band_of_iface(e["iface"]) if e.get("iface") else None
    return out


def latest_radio() -> list[dict[str, Any]]:
    out = _rows(conn().execute(
        """SELECT r.* FROM radio_samples r
           JOIN (SELECT unit, iface, MAX(ts) AS ts FROM radio_samples GROUP BY unit, iface) m
             ON m.unit = r.unit AND m.iface = r.iface AND m.ts = r.ts"""))
    for r in out:
        r["data"] = json.loads(r["data"] or "{}")
    return out


def station_samples_since(since: float) -> list[dict[str, Any]]:
    """Compact per-sample rows for analysis."""
    return _rows(conn().execute(
        "SELECT ts, unit, mac, band, rssi, tx_rate FROM station_samples WHERE ts >= ? ORDER BY ts", (since,)))


def latest_settings() -> dict[str, dict[str, str]]:
    """Most recent settings snapshot per unit."""
    rows = conn().execute(
        """SELECT s.unit, s.data FROM unit_settings s
           JOIN (SELECT unit, MAX(ts) AS ts FROM unit_settings GROUP BY unit) m ON m.unit = s.unit AND m.ts = s.ts""")
    return {r["unit"]: json.loads(r["data"] or "{}") for r in rows}


def rollup_day(day: str, start: float, end: float, score_fn) -> int:
    """Fill daily_device_summary for one local day (D-25: kept for a year)."""
    c = conn()
    ev = {r["mac"]: dict(r) for r in c.execute(
        """SELECT mac, SUM(kind = 'kick') AS kicks, SUM(kind = 'roam_candidate') AS roams,
                  SUM(kind = 'connect') AS connects, SUM(kind = 'disconnect') AS disconnects
           FROM events WHERE ts >= ? AND ts < ? AND mac IS NOT NULL GROUP BY mac""", (start, end))}
    st = {r["mac"]: dict(r) for r in c.execute(
        """SELECT mac, MIN(rssi) AS min_rssi, MAX(rssi) AS max_rssi, AVG(rssi) AS avg_rssi,
                  AVG(tx_rate) AS avg_tx_rate, COUNT(*) AS samples
           FROM station_samples WHERE ts >= ? AND ts < ? GROUP BY mac""", (start, end))}
    rows = []
    for mac in set(ev) | set(st):
        e, s = ev.get(mac, {}), st.get(mac, {})
        r = {"day": day, "mac": mac, "kicks": e.get("kicks") or 0, "roams": e.get("roams") or 0,
             "connects": e.get("connects") or 0, "disconnects": e.get("disconnects") or 0,
             "min_rssi": s.get("min_rssi"), "max_rssi": s.get("max_rssi"),
             "avg_rssi": round(s["avg_rssi"], 1) if s.get("avg_rssi") is not None else None,
             "avg_tx_rate": round(s["avg_tx_rate"], 1) if s.get("avg_tx_rate") is not None else None,
             "samples": s.get("samples") or 0}
        r["score"] = score_fn(r["kicks"], r["roams"], r["disconnects"], r["avg_rssi"])
        rows.append(r)
    with _write_lock:
        c.execute("DELETE FROM daily_device_summary WHERE day = ?", (day,))
        if rows:
            keys = list(rows[0])
            c.executemany(f"INSERT INTO daily_device_summary({', '.join(keys)}) VALUES ({', '.join('?' for _ in keys)})",
                          [tuple(r[k] for k in keys) for r in rows])
        c.commit()
    return len(rows)


def daily_summary(since_day: str, mac: str | None = None) -> list[dict[str, Any]]:
    q, args = "SELECT * FROM daily_device_summary WHERE day >= ?", [since_day]
    if mac:
        q += " AND mac = ?"
        args.append(mac.lower())
    return _rows(conn().execute(q + " ORDER BY day", args))


# ---------------------------------------------------------------- discovery

def merge_names(mac: str, names: dict[str, str]) -> None:
    """Record names per source (SVC-03) without touching the router/DHCP name or the user's name."""
    d = device(mac)
    if not d:
        return
    cur = json.loads(d.get("names") or "{}")
    cur.update({k: v for k, v in names.items() if v})
    fields: dict[str, Any] = {"names": json.dumps(cur, sort_keys=True)}
    if not d.get("name"):
        for src in ("mdns", "netbios", "dhcp", "ssdp", "dns"):
            if cur.get(src):
                fields["name"], fields["name_src"] = cur[src], src
                break
    set_device_fields(mac, **fields)


def set_device_fields(mac: str, **fields: Any) -> None:
    """Update detected fields of an existing device (None values are written)."""
    if not fields:
        return
    with _write_lock:
        c = conn()
        c.execute(f"UPDATE devices SET {', '.join(f'{k} = ?' for k in fields)} WHERE mac = ?", (*fields.values(), mac.lower()))
        c.commit()


def device_by_ip(ip: str) -> dict[str, Any] | None:
    r = conn().execute("SELECT * FROM devices WHERE ip = ? ORDER BY last_seen DESC LIMIT 1", (ip,)).fetchone()
    return dict(r) if r else None


def record_ports(mac: str, ip: str, found: list[dict[str, Any]], scanned: list[int], proto: str,
                 now: float) -> list[dict[str, Any]]:
    """Store a scan result; returns open/close changes (PRT-03)."""
    mac = mac.lower()
    changes = []
    c = conn()
    before = {r["port"]: dict(r) for r in c.execute("SELECT * FROM ports WHERE mac = ? AND proto = ?", (mac, proto))}
    first_scan = not before
    with _write_lock:
        for f in found:
            old = before.get(f["port"])
            if not old or not old["open"]:
                changes.append({"kind": "port_opened", "port": f["port"], "proto": proto, "service": f.get("service"),
                                "first_scan": first_scan})
            c.execute("""INSERT INTO ports(mac, proto, port, ip, service, banner, title, risk, first_seen, last_seen, open)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                         ON CONFLICT(mac, proto, port) DO UPDATE SET ip = excluded.ip, service = excluded.service,
                           banner = COALESCE(excluded.banner, banner), title = COALESCE(excluded.title, title),
                           risk = excluded.risk, last_seen = excluded.last_seen, open = 1""",
                      (mac, proto, f["port"], ip, f.get("service"), f.get("banner"), f.get("title"), f.get("risk"), now, now))
        open_now = {f["port"] for f in found}
        for port, old in before.items():
            if old["open"] and port in scanned and port not in open_now:
                c.execute("UPDATE ports SET open = 0 WHERE mac = ? AND proto = ? AND port = ?", (mac, proto, port))
                changes.append({"kind": "port_closed", "port": port, "proto": proto, "service": old.get("service")})
        c.execute("UPDATE devices SET ports_scanned = ? WHERE mac = ?", (now, mac))
        for ch in changes:
            c.execute("INSERT INTO inventory_events(ts, mac, kind, detail) VALUES (?, ?, ?, ?)",
                      (now, mac, ch["kind"], json.dumps(ch)))
        c.commit()
    return changes


def open_ports(mac: str | None = None) -> list[dict[str, Any]]:
    q, args = "SELECT * FROM ports WHERE open = 1", []
    if mac:
        q += " AND mac = ?"
        args.append(mac.lower())
    return _rows(conn().execute(q + " ORDER BY mac, port", args))


def inventory_events(since: float, mac: str | None = None) -> list[dict[str, Any]]:
    q, args = "SELECT * FROM inventory_events WHERE ts >= ?", [since]
    if mac:
        q += " AND mac = ?"
        args.append(mac.lower())
    out = _rows(conn().execute(q + " ORDER BY ts DESC", args))
    for e in out:
        e["detail"] = json.loads(e["detail"] or "{}")
    return out


def add_inventory_event(ts: float, mac: str, kind: str, detail: dict[str, Any]) -> None:
    with _write_lock:
        c = conn()
        c.execute("INSERT INTO inventory_events(ts, mac, kind, detail) VALUES (?, ?, ?, ?)",
                  (ts, mac.lower(), kind, json.dumps(detail)))
        c.commit()


# ---------------------------------------------------------------- powerline

def store_plc(adapters: list[dict[str, Any]], rates: list[dict[str, Any]], now: float) -> None:
    with _write_lock:
        c = conn()
        for a in adapters:
            c.execute("""INSERT INTO plc_adapters(mac, ip, name, product, firmware, attached_to_router, bridged, last_seen)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                         ON CONFLICT(mac) DO UPDATE SET ip = excluded.ip, name = excluded.name, product = excluded.product,
                           firmware = excluded.firmware, attached_to_router = excluded.attached_to_router,
                           bridged = excluded.bridged, last_seen = excluded.last_seen""",
                      (a["mac"], a.get("ip"), a.get("name"), a.get("product"), a.get("firmware"),
                       1 if a.get("attached_to_router") else 0, json.dumps(a.get("bridged") or []), now))
        c.executemany("INSERT INTO plc_rates(ts, src, dst, tx_mbps, rx_mbps) VALUES (?, ?, ?, ?, ?)",
                      [(now, r["src"], r["dst"], r["tx_mbps"], r["rx_mbps"]) for r in rates])
        c.commit()


def plc_adapters() -> list[dict[str, Any]]:
    out = _rows(conn().execute("SELECT * FROM plc_adapters ORDER BY attached_to_router DESC, name"))
    for a in out:
        a["bridged"] = json.loads(a["bridged"] or "[]")
    return out


def plc_latest_rates() -> list[dict[str, Any]]:
    return _rows(conn().execute(
        "SELECT r.* FROM plc_rates r JOIN (SELECT MAX(ts) AS ts FROM plc_rates) m ON r.ts = m.ts ORDER BY src, dst"))


def plc_rate_history(since: float) -> list[dict[str, Any]]:
    return _rows(conn().execute("SELECT * FROM plc_rates WHERE ts >= ? ORDER BY ts", (since,)))


# ---------------------------------------------------------------- monitors

def ping_minutes(since: float, target: str | None = None) -> list[dict[str, Any]]:
    q, args = "SELECT * FROM ping_minutes WHERE ts >= ?", [since]
    if target:
        q += " AND target = ?"
        args.append(target)
    return _rows(conn().execute(q + " ORDER BY ts", args))


def speed_tests(since: float) -> list[dict[str, Any]]:
    return _rows(conn().execute("SELECT * FROM speed_tests WHERE ts >= ? ORDER BY ts", (since,)))


def iface_counters_since(since: float) -> list[dict[str, Any]]:
    return _rows(conn().execute("SELECT * FROM iface_counters WHERE ts >= ? ORDER BY ts", (since,)))


def radio_samples_since(since: float) -> list[dict[str, Any]]:
    return _rows(conn().execute(
        "SELECT ts, unit, iface, band, tx_fail_pct, rx_crc_pct, temperature, noise FROM radio_samples WHERE ts >= ? ORDER BY ts",
        (since,)))


def host_lan_since(since: float) -> list[dict[str, Any]]:
    return _rows(conn().execute("SELECT * FROM host_lan WHERE ts >= ? ORDER BY ts", (since,)))


def air_scan_since(since: float) -> list[dict[str, Any]]:
    return _rows(conn().execute("SELECT * FROM air_scan WHERE ts >= ? ORDER BY ts", (since,)))


def air_scan_latest() -> list[dict[str, Any]]:
    return _rows(conn().execute(
        "SELECT * FROM air_scan WHERE ts = (SELECT MAX(ts) FROM air_scan) ORDER BY band, channel, signal_pct DESC"))


def add_speed_tests(rows: list[dict[str, Any]]) -> int:
    """Insert results not already stored (same location and time to the second)."""
    n = 0
    with _write_lock:
        c = conn()
        for r in rows:
            if c.execute("SELECT 1 FROM speed_tests WHERE COALESCE(location, 'pc') = ? AND ABS(ts - ?) < 1",
                         (r.get("location") or "pc", r["ts"])).fetchone():
                continue
            keys = [k for k in ("ts", "server", "down_mbps", "up_mbps", "idle_ms", "down_loaded_ms", "up_loaded_ms",
                                "grade", "trigger", "error", "location") if k in r]
            c.execute(f"INSERT INTO speed_tests({', '.join(keys)}) VALUES ({', '.join('?' for _ in keys)})",
                      [r[k] for k in keys])
            n += 1
        c.commit()
    return n


def unit_samples_since(since: float, unit: str | None = None) -> list[dict[str, Any]]:
    q, args = "SELECT * FROM unit_samples WHERE ts >= ?", [since]
    if unit:
        q += " AND unit = ?"
        args.append(unit)
    return _rows(conn().execute(q + " ORDER BY ts", args))
