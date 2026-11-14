"""Export stored data for a time range as CSV, JSON or a ZIP of CSVs.

Every row gets a readable local time, and device/unit names next to their MAC addresses.
Rows are streamed, so a month of samples doesn't need to fit in memory.
"""
from __future__ import annotations

import csv
import io
import json
import time
import zipfile
from datetime import date, datetime
from typing import Any, Iterator

from .names import display_name
from .store import db

# key: (label, table, time column)
DATASETS: dict[str, tuple[str, str | None, str | None]] = {
    "events": ("Wi-Fi events (connects, kicks, roams, disconnects)", "events", "ts"),
    "signal": ("Wi-Fi signal and link rate per device (every poll)", "station_samples", "ts"),
    "latency": ("Latency, jitter and loss per minute", "ping_minutes", "ts"),
    "speed": ("Speed tests (this PC and the router)", "speed_tests", "ts"),
    "radios": ("Radio health (failures, errors, temperature)", "radio_samples", "ts"),
    "traffic": ("Interface byte counters (router and nodes)", "iface_counters", "ts"),
    "air": ("Air survey from this PC (channel busyness, networks heard)", "air_scan", "ts"),
    "lan": ("Broadcast / multicast at this PC", "host_lan", "ts"),
    "powerline": ("Powerline link rates", "plc_rates", "ts"),
    "inventory": ("Inventory changes (new devices, ports opened/closed)", "inventory_events", "ts"),
    "daily": ("Daily device summaries (kept 12 months)", "daily_device_summary", "day"),
    "devices": ("Device list with your notes (current)", None, None),
}
MAC_COLS = ("mac", "src", "dst", "bssid")
UNIT_COLS = ("unit", "own_unit")


def _names() -> tuple[dict[str, str], dict[str, str]]:
    devs = {d["mac"]: display_name(d, d["mac"]) for d in db.devices()}
    units: dict[str, str] = {}
    for u in db.units():
        for m in [u["mac"], *(u.get("macs") or [])]:
            units[m] = u.get("alias") or u.get("model") or u["mac"]
    for a in db.plc_adapters():
        devs.setdefault(a["mac"], a.get("name") or a["mac"])
    return devs, units


def rows(key: str, t0: float, t1: float) -> Iterator[dict[str, Any]]:
    label, table, col = DATASETS[key]
    devs, units = _names()
    if table is None:
        for d in db.devices():
            yield {k: v for k, v in d.items()}
        return
    if col == "day":
        lo, hi = date.fromtimestamp(t0).isoformat(), date.fromtimestamp(t1).isoformat()
        cur = db.conn().execute(f"SELECT * FROM {table} WHERE day >= ? AND day <= ? ORDER BY day", (lo, hi))
    else:
        cur = db.conn().execute(f"SELECT * FROM {table} WHERE {col} >= ? AND {col} < ? ORDER BY {col}", (t0, t1))
    names = [c[0] for c in cur.description]
    while True:
        batch = cur.fetchmany(2000)
        if not batch:
            break
        for r in batch:
            row = dict(zip(names, r))
            out: dict[str, Any] = {}
            if col == "ts" and row.get("ts") is not None:
                out["time"] = datetime.fromtimestamp(row["ts"]).strftime("%Y-%m-%d %H:%M:%S")
            for k, v in row.items():
                out[k] = v
                if k in MAC_COLS and v:
                    out[f"{k}_name"] = devs.get(v) or units.get(v) or ""
                elif k in UNIT_COLS and v:
                    out[f"{k}_name"] = units.get(v, "")
            if "detail" in out and isinstance(out["detail"], str):
                try:
                    d = json.loads(out["detail"])
                    for dk in ("rssi", "target", "target_rssi", "new_unit", "status", "port", "service"):
                        if dk in d:
                            out[f"detail_{dk}"] = d[dk]
                    if "target" in d:
                        out["detail_target_name"] = units.get(d["target"], "")
                except ValueError:
                    pass
            yield out


def _csv(it: Iterator[dict[str, Any]]) -> Iterator[str]:
    """Columns are the union of the first 2000 rows' keys (optional detail columns appear only on some rows)."""
    import itertools
    buf = io.StringIO()
    head = list(itertools.islice(it, 2000))
    cols: list[str] = []
    for r in head:
        cols += [k for k in r if k not in cols]
    writer = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore") if head else None
    if writer:
        writer.writeheader()
    for r in itertools.chain(head, it):
        writer.writerow(r)
        if buf.tell() > 65536:
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate()
    if writer is None:
        yield "no data in this period\n"
    elif buf.tell():
        yield buf.getvalue()


def csv_chunks(key: str, t0: float, t1: float) -> Iterator[bytes]:
    yield "﻿".encode()      # BOM so Excel reads UTF-8 names correctly
    for chunk in _csv(rows(key, t0, t1)):
        yield chunk.encode("utf-8")


def json_chunks(key: str, t0: float, t1: float) -> Iterator[bytes]:
    yield b'{"dataset": ' + json.dumps(key).encode() + b', "from": ' + json.dumps(t0).encode() + \
        b', "to": ' + json.dumps(t1).encode() + b', "rows": ['
    first = True
    for r in rows(key, t0, t1):
        yield (b"" if first else b",\n") + json.dumps(r, default=str).encode()
        first = False
    yield b"]}\n"


def zip_file(keys: list[str], t0: float, t1: float) -> bytes:
    """ZIP of one CSV per dataset, plus a README with the period."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.txt", f"MeshLens export\nFrom: {datetime.fromtimestamp(t0)}\nTo:   {datetime.fromtimestamp(t1)}\n"
                                 f"Created: {datetime.now()}\n\n" + "\n".join(f"{k}.csv  {DATASETS[k][0]}" for k in keys) + "\n")
        for k in keys:
            with z.open(f"{k}.csv", "w") as f:
                for chunk in csv_chunks(k, t0, t1):
                    f.write(chunk)
    return buf.getvalue()


def parse_time(v: str | None, default: float) -> float:
    """Accepts unix seconds, 'YYYY-MM-DD' or 'YYYY-MM-DDTHH:MM' (local time)."""
    if not v:
        return default
    try:
        return float(v)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return time.mktime(datetime.strptime(v, fmt).timetuple())
        except ValueError:
            continue
    raise ValueError(f"not a date/time: {v}")
