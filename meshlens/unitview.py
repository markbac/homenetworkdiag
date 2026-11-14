"""Everything known about one mesh unit, for the Router tab (read from the database; nothing is sent to the router)."""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

from .collectors.asus import settings_info
from .names import display_name
from .store import db


def detail(mac: str) -> dict[str, Any] | None:
    now = time.time()
    mac = mac.lower()
    units = db.units()
    u = next((x for x in units if x["mac"] == mac), None)
    if not u:
        return None
    names = {}
    for x in units:
        for m in [x["mac"], *(x.get("macs") or [])]:
            names[m] = x.get("alias") or x.get("model") or x["mac"]
    router = next((x for x in units if x.get("role") == "router"), None)
    info = u.get("info") or {}

    # Clients now, per interface.
    devs = {d["mac"]: d for d in db.devices()}
    clients = [s for s in db.latest_stations() if s["unit"] == mac and now - s["ts"] < 600]
    per_iface: dict[str, int] = defaultdict(int)
    for s in clients:
        per_iface[s.get("iface") or "?"] += 1

    # Traffic per interface from the last two counter samples.
    rates: dict[str, dict[str, float]] = {}
    rows = [c for c in db.iface_counters_since(now - 900) if c["unit"] == mac]
    by: dict[str, list] = defaultdict(list)
    for c in rows:
        by[c["iface"]].append(c)
    for iface, rs in by.items():
        if len(rs) >= 2:
            a, b = rs[-2], rs[-1]
            dt = b["ts"] - a["ts"]
            if dt > 0 and b["rx_bytes"] >= a["rx_bytes"] and b["tx_bytes"] >= a["tx_bytes"]:
                rates[iface] = {"rx_mbps": round((b["rx_bytes"] - a["rx_bytes"]) * 8 / dt / 1e6, 2),
                                "tx_mbps": round((b["tx_bytes"] - a["tx_bytes"]) * 8 / dt / 1e6, 2)}

    radios = {r["iface"]: r for r in db.latest_radio() if r["unit"] == mac}
    iwc = {i["iface"]: i for i in info.get("iwconfig") or []}
    ifaces = []
    for i in info.get("ifaces") or []:
        name = i["iface"]
        r = radios.get(name) or {}
        ifaces.append({**i, "clients": per_iface.get(name, 0), **rates.get(name, {}),
                       "noise": (iwc.get(name) or {}).get("noise"), "tx_fail_pct": r.get("tx_fail_pct"),
                       "rx_crc_pct": r.get("rx_crc_pct"), "temperature": r.get("temperature"),
                       "role": "backhaul" if name.startswith("apcli") or (i.get("ssid") or "").isupper() and len(i.get("ssid") or "") == 32
                       else "network"})
    ifaces.sort(key=lambda x: (x.get("band") or "z", x["iface"]))

    settings = db.latest_settings().get(mac) or {}
    return {
        "unit": {k: u.get(k) for k in ("mac", "alias", "model", "productid", "ip", "role", "chip", "firmware", "reachable",
                                       "last_seen", "last_error")},
        "macs": u.get("macs") or [],
        "uptime_s": info.get("uptime_s"), "load": info.get("load"), "snapshot_ts": info.get("ts"),
        "identity": info.get("identity") or {},
        "backhaul": [{**b, "parent_name": names.get(b["parent"], b["parent"])} for b in u.get("backhaul") or []],
        "ifaces": ifaces,
        "radios": [{"iface": k, "band": v.get("band"), **(v.get("data") or {})} for k, v in sorted(radios.items())],
        "ports": settings_info.ports(router.get("info") if router else None, mac),
        "settings": settings_info.describe(settings) if settings else None,
        "clients": sorted([{"mac": s["mac"], "name": display_name(devs.get(s["mac"]), s["mac"]), "iface": s.get("iface"),
                            "band": s.get("band"), "rssi": s.get("rssi"), "tx_rate": s.get("tx_rate")} for s in clients],
                          key=lambda c: (c["band"] or "", -(c["rssi"] or -999))),
        "counters": [{"iface": k, **v} for k, v in sorted(rates.items())],
    }
