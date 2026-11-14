"""Data for the Graphs tab. Pure functions over stored rows, plus ``collect()`` which reads the database.

Every series is a list of ``[t, value]`` points (t = unix seconds) so the browser can draw it directly.
"""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

from .collectors.asus.parsers import band_of_iface


def bucket_size(hours: float) -> int:
    """Seconds per point: about 150-300 points per chart."""
    for b in (60, 300, 600, 1800, 3600, 3 * 3600):
        if hours * 3600 / b <= 300:
            return b
    return 6 * 3600


def events_per_bucket(events: list[dict[str, Any]], since: float, bucket: int) -> dict[str, list[list[float]]]:
    kinds = {"kick": "Kicks", "roam_candidate": "Roam attempts", "disconnect": "Disconnects", "assoc_failed": "Failed joins"}
    counts: dict[str, dict[int, int]] = {k: defaultdict(int) for k in kinds}
    for e in events:
        if e["kind"] in counts and e["ts"] >= since:
            counts[e["kind"]][int(e["ts"] // bucket) * bucket] += 1
    start, end = int(since // bucket) * bucket, int(time.time() // bucket) * bucket
    steps = range(start, end + bucket, bucket)
    return {kinds[k]: [[t, c.get(t, 0)] for t in steps] for k, c in counts.items()}


def clients_over_time(samples: list[dict[str, Any]], unit_names: dict[str, str], bucket: int) -> dict[str, Any]:
    """Distinct Wi-Fi devices per unit and per band in each bucket."""
    per_unit: dict[str, dict[int, set]] = defaultdict(lambda: defaultdict(set))
    per_band: dict[str, dict[int, set]] = defaultdict(lambda: defaultdict(set))
    for s in samples:
        t = int(s["ts"] // bucket) * bucket
        per_unit[unit_names.get(s["unit"], s["unit"])][t].add(s["mac"])
        if s.get("band"):
            per_band[f"{s['band']} GHz"][t].add(s["mac"])
    times = sorted({t for d in per_unit.values() for t in d})

    def series(d):
        return {k: [[t, len(v.get(t, ()))] for t in times] for k, v in sorted(d.items())}
    return {"by_unit": series(per_unit), "by_band": series(per_band)}


def rssi_histogram(latest: list[dict[str, Any]], now: float) -> list[dict[str, Any]]:
    edges = list(range(-90, -25, 5))
    counts = defaultdict(int)
    for s in latest:
        if s.get("rssi") is None or now - s["ts"] > 900:
            continue
        r = max(-90, min(-30, s["rssi"]))
        counts[edges[min(len(edges) - 1, (r + 90) // 5)]] += 1
    return [{"from": e, "to": e + 5, "count": counts.get(e, 0)} for e in edges[:-1]]


def throughput(counters: list[dict[str, Any]], unit_names: dict[str, str], router_mac: str | None) -> dict[str, Any]:
    """Mbit/s from byte counters: the router's internet link, and Wi-Fi per unit and band."""
    rows = defaultdict(list)
    for c in counters:
        rows[(c["unit"], c["iface"])].append(c)
    wan_if = None
    if router_mac:
        ifs = {i for (u, i) in rows if u == router_mac}
        wan_if = "ppp0" if "ppp0" in ifs else ("eth0" if "eth0" in ifs else None)
    wan = {"Download": [], "Upload": []}
    wifi: dict[str, dict[float, float]] = defaultdict(lambda: defaultdict(float))
    for (unit, iface), rs in rows.items():
        rs.sort(key=lambda r: r["ts"])
        band = band_of_iface(iface)
        is_wifi = band and iface[:2] in ("ra", "at") and "." not in iface and not iface.startswith("apcli")
        for a, b in zip(rs, rs[1:]):
            dt = b["ts"] - a["ts"]
            if dt <= 0 or dt > 900:
                continue
            drx, dtx = b["rx_bytes"] - a["rx_bytes"], b["tx_bytes"] - a["tx_bytes"]
            if drx < 0 or dtx < 0:          # counter reset (reboot)
                continue
            rx, tx = drx * 8 / dt / 1e6, dtx * 8 / dt / 1e6
            if unit == router_mac and iface == wan_if:
                wan["Download"].append([b["ts"], round(rx, 2)])
                wan["Upload"].append([b["ts"], round(tx, 2)])
            elif is_wifi:
                wifi[f"{unit_names.get(unit, unit)} {band} GHz"][round(b["ts"] / 60) * 60] += rx + tx
    return {"wan": wan, "wan_iface": wan_if,
            "wifi": {k: [[t, round(v, 2)] for t, v in sorted(d.items())] for k, d in sorted(wifi.items())}}


def radio_errors(rows: list[dict[str, Any]], unit_names: dict[str, str]) -> dict[str, list[list[float]]]:
    out: dict[str, list[list[float]]] = defaultdict(list)
    for r in rows:
        if r.get("tx_fail_pct") is not None:
            out[f"{unit_names.get(r['unit'], r['unit'])} {r.get('band') or '?'} GHz"].append([r["ts"], r["tx_fail_pct"]])
    return dict(sorted(out.items()))


def latency_series(pings: list[dict[str, Any]]) -> dict[str, Any]:
    by_target: dict[str, list] = defaultdict(list)
    label: dict[str, str] = {}
    loss: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    for p in pings:
        if p.get("kind") == "isp":
            continue
        label[p["target"]] = p.get("label") or p["target"]     # latest name wins (units get renamed)
        if p.get("avg_ms") is not None:
            by_target[p["target"]].append([p["ts"], p["avg_ms"]])
        if p.get("kind") in ("internet", "router"):
            loss[int(p["ts"])][0] += p["lost"] or 0
            loss[int(p["ts"])][1] += p["sent"] or 0
    avg = {label[t]: pts for t, pts in by_target.items()}
    return {"avg": avg, "loss_pct": [[t, round(100 * l / s, 1)] for t, (l, s) in sorted(loss.items()) if s]}


def latency_heatmap(pings: list[dict[str, Any]]) -> dict[str, Any]:
    """Internet latency by weekday and hour: [7][24] of {p95, loss_pct, minutes}."""
    cells: dict[tuple[int, int], list] = defaultdict(lambda: [[], 0, 0])
    for p in pings:
        if p.get("kind") != "internet":
            continue
        lt = time.localtime(p["ts"])
        c = cells[(lt.tm_wday, lt.tm_hour)]
        if p.get("p95_ms") is not None:
            c[0].append(p["p95_ms"])
        c[1] += p["lost"] or 0
        c[2] += p["sent"] or 0
    grid = []
    for d in range(7):
        row = []
        for h in range(24):
            v, lost, sent = cells.get((d, h), [[], 0, 0])
            v = sorted(v)
            row.append({"p95": round(v[len(v) // 2], 1) if v else None,
                        "loss_pct": round(100 * lost / sent, 2) if sent else None, "minutes": len(v)})
        grid.append(row)
    return {"days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], "grid": grid}


def powerline_series(rates: list[dict[str, Any]], adapters: list[dict[str, Any]]) -> dict[str, list[list[float]]]:
    names = {a["mac"]: a.get("name") or a["mac"] for a in adapters}
    pair: dict[tuple, dict[float, list[int]]] = defaultdict(dict)
    for r in rates:
        key = tuple(sorted((r["src"], r["dst"])))
        pair[key].setdefault(r["ts"], []).append(r["tx_mbps"])
    return {f"{names.get(a, a)} ↔ {names.get(b, b)}": [[t, min(v)] for t, v in sorted(d.items())]
            for (a, b), d in sorted(pair.items())}


def lan_noise(rows: list[dict[str, Any]]) -> dict[str, list[list[float]]]:
    """Broadcast and multicast packets/s reaching the MeshLens PC (per wired adapter)."""
    out: dict[str, list[list[float]]] = defaultdict(list)
    for r in rows:
        tag = r["adapter"]
        out[f"Broadcast ({tag})"].append([r["ts"], r["bcast_pps"]])
        out[f"Multicast ({tag})"].append([r["ts"], r["mcast_pps"]])
    return dict(out)


def air_util(rows: list[dict[str, Any]], unit_names: dict[str, str]) -> dict[str, list[list[float]]]:
    """Channel utilisation each of your access points reports (BSS Load), one line per unit radio."""
    pts: dict[str, dict[float, int]] = defaultdict(dict)
    for r in rows:
        if r.get("own_unit") and r.get("util_pct") is not None:
            key = f"{unit_names.get(r['own_unit'], r['own_unit'])} {r['band']} GHz ch {r['channel']}"
            pts[key][r["ts"]] = max(pts[key].get(r["ts"], 0), r["util_pct"])
    return {k: sorted([t, v] for t, v in d.items()) for k, d in sorted(pts.items())}


def unit_series(rows: list[dict[str, Any]], unit_names: dict[str, str]) -> dict[str, dict[str, list]]:
    """CPU %, busiest core %, memory % and temperature per unit."""
    out: dict[str, dict[str, list]] = {"cpu": {}, "core": {}, "mem": {}, "temp": {}}
    for r in rows:
        n = unit_names.get(r["unit"], r["unit"])
        for key, col in (("cpu", "cpu_pct"), ("core", "max_core_pct"), ("mem", "mem_used_pct"), ("temp", "temp_c")):
            if r.get(col) is not None:
                out[key].setdefault(n, []).append([r["ts"], r[col]])
    return out


def collect(hours: float) -> dict[str, Any]:
    from .store import db
    now = time.time()
    since = now - hours * 3600
    b = bucket_size(hours)
    units = db.units()
    names = {}
    for u in units:
        for m in [u["mac"], *(u.get("macs") or [])]:
            names[m] = u.get("alias") or u.get("model") or u["mac"]
    router = next((u["mac"] for u in units if u.get("role") == "router"), None)
    pings = db.ping_minutes(since)
    return {
        "hours": hours, "bucket": b, "generated": now,
        "events": events_per_bucket(db.events(since, limit=500000), since, max(b, 300)),
        "clients": clients_over_time(db.station_samples_since(since), names, b),
        "rssi": rssi_histogram(db.latest_stations(), now),
        "traffic": throughput(db.iface_counters_since(since), names, router),
        "radio_errors": radio_errors(db.radio_samples_since(since), names),
        "latency": latency_series(pings),
        "heatmap": latency_heatmap(db.ping_minutes(now - 7 * 86400)),
        "powerline": powerline_series(db.plc_rate_history(since), db.plc_adapters()),
        "lan_noise": lan_noise(db.host_lan_since(since)),
        "units": unit_series(db.unit_samples_since(since), names),
        "air": air_util(db.air_scan_since(since), names),
    }


def device(mac: str, hours: float) -> dict[str, Any]:
    """Per-device graphs: signal, link rates, traffic, where it was connected, events, ping, daily health."""
    import datetime as dt

    from .store import db
    now = time.time()
    since = now - hours * 3600
    mac = mac.lower()
    names = {}
    for u in db.units():
        for m in [u["mac"], *(u.get("macs") or [])]:
            names[m] = u.get("alias") or u.get("model") or u["mac"]
    rows = db.station_history(mac, since)
    signal = [[r["ts"], r["rssi"]] for r in rows if r.get("rssi") is not None]
    tx = [[r["ts"], r["tx_rate"]] for r in rows if r.get("tx_rate") is not None]
    rx = [[r["ts"], r["rx_rate"]] for r in rows if r.get("rx_rate") is not None]
    down, up = [], []
    for a, b in zip(rows, rows[1:]):
        d = b["ts"] - a["ts"]
        if 0 < d <= 900 and a.get("tx_bytes") is not None and b.get("tx_bytes") is not None and a["unit"] == b["unit"]:
            dtx, drx = b["tx_bytes"] - a["tx_bytes"], (b.get("rx_bytes") or 0) - (a.get("rx_bytes") or 0)
            if dtx >= 0 and drx >= 0:      # AP's tx = data to the device
                down.append([b["ts"], round(dtx * 8 / d / 1e6, 3)])
                up.append([b["ts"], round(drx * 8 / d / 1e6, 3)])
    # Where it was connected: runs of (unit, band).
    spans: list[dict[str, Any]] = []
    for r in rows:
        key = (names.get(r["unit"], r["unit"]), r.get("band"))
        if spans and (spans[-1]["unit"], spans[-1]["band"]) == key and r["ts"] - spans[-1]["to"] <= 300:
            spans[-1]["to"] = r["ts"]
        else:
            spans.append({"unit": key[0], "band": key[1], "from": r["ts"], "to": r["ts"]})
    d = db.device(mac) or {}
    ping = []
    if d.get("ip"):
        ping = [[p["ts"], p["avg_ms"], p["lost"], p["sent"]] for p in db.ping_minutes(since, d["ip"])]
    daily = [{"day": r["day"], "score": r["score"], "kicks": r["kicks"], "disconnects": r["disconnects"], "avg_rssi": r["avg_rssi"]}
             for r in db.daily_summary((dt.date.today() - dt.timedelta(days=29)).isoformat(), mac)]
    return {"hours": hours, "signal": signal, "tx_rate": tx, "rx_rate": rx, "down_mbps": down, "up_mbps": up,
            "spans": spans, "events": events_per_bucket(db.events(since, mac=mac, limit=100000), since, max(300, bucket_size(hours))),
            "ping": ping, "daily": daily}
