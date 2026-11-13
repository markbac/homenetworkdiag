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
  watched INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS units (
  mac TEXT PRIMARY KEY,           -- unit's main MAC (as in cfg_device_list)
  model TEXT, productid TEXT, ip TEXT, role TEXT, alias TEXT,
  chip TEXT, firmware TEXT,
  macs TEXT,                      -- JSON list: every radio/BSSID/backhaul address of the unit
  reachable INTEGER, last_seen REAL, last_error TEXT
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

CREATE TABLE IF NOT EXISTS daily_device_summary (
  day TEXT, mac TEXT, kicks INTEGER, roams INTEGER, connects INTEGER, disconnects INTEGER,
  min_rssi INTEGER, max_rssi INTEGER,
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
    for table, body in re.findall(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", SCHEMA, re.S):
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
        for t in ("station_samples", "radio_samples", "iface_counters", "events", "unit_settings"):
            c.execute(f"DELETE FROM {t} WHERE ts < ?", (cut,))
        day_cut = time.strftime("%Y-%m-%d", time.localtime(now - summary_days * 86400))
        c.execute("DELETE FROM daily_device_summary WHERE day < ?", (day_cut,))
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
    for e in out:
        e["detail"] = json.loads(e["detail"] or "{}")
    return out


def latest_radio() -> list[dict[str, Any]]:
    out = _rows(conn().execute(
        """SELECT r.* FROM radio_samples r
           JOIN (SELECT unit, iface, MAX(ts) AS ts FROM radio_samples GROUP BY unit, iface) m
             ON m.unit = r.unit AND m.iface = r.iface AND m.ts = r.ts"""))
    for r in out:
        r["data"] = json.loads(r["data"] or "{}")
    return out
