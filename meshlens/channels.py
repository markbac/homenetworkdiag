"""Channel planner and radar (DFS) analysis.

Pure functions over plain rows (air survey, units, events, station samples) so they can be tested without a
database. ``plan`` scores every 5 GHz 80 MHz block and the 2.4 GHz channels 1/6/11 by how crowded and how
radar-prone they are and says which to use; ``radar_incidents`` turns radar log lines into incidents with what
each one did to the devices (dropped, pushed onto 2.4 GHz, still stuck there).
"""
from __future__ import annotations

import statistics
import time
from collections import defaultdict
from typing import Any, Iterable

# 80 MHz blocks on 5 GHz (Europe/UK channel plan). DFS = shared with radar: a unit must leave on detection.
BLOCKS_5: list[dict[str, Any]] = [
    {"block": "36-48", "first": 36, "last": 48, "dfs": False, "note": "no radar checks"},
    {"block": "52-64", "first": 52, "last": 64, "dfs": True, "note": "radar (DFS)"},
    {"block": "100-112", "first": 100, "last": 112, "dfs": True, "note": "radar (DFS)"},
    {"block": "116-128", "first": 116, "last": 128, "dfs": True, "note": "radar (DFS); 120-128 also weather radar"},
    {"block": "132-144", "first": 132, "last": 144, "dfs": True, "note": "radar (DFS)"},
    {"block": "149-161", "first": 149, "last": 165, "dfs": False,
     "note": "not offered everywhere (UK: indoor, low power); use only if the router lists it"},
]
CHANNELS_24 = [1, 6, 11]
STRONG_DBM = -70          # a neighbour heard this loud really shares the air with you
RADAR_WINDOW_S = 300      # radar lines this close together are one incident
AFTER_S = 180             # how long after radar to look for drops


def block_of(ch: int | None) -> str | None:
    if not ch:
        return None
    for b in BLOCKS_5:
        if b["first"] <= ch <= b["last"]:
            return b["block"]
    return None


def _own_channels(units: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The main router's current channel per band (nodes follow it), plus every unit's for reference."""
    out: dict[str, dict[str, Any]] = {}
    for u in sorted(units, key=lambda x: 0 if x.get("role") == "router" else 1):
        for i in (u.get("info") or {}).get("ifaces") or []:
            band, ch = i.get("band"), i.get("channel")
            if not band or not ch or i["iface"].startswith("apcli") or i.get("type") == "managed":
                continue
            out.setdefault(band, {"channel": ch, "width": i.get("width"), "unit": u["mac"]})
    return out


def _latest_per_bssid(air: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    per: dict[str, dict[str, Any]] = {}
    utils: dict[str, list[int]] = defaultdict(list)
    for r in sorted(air, key=lambda r: r["ts"]):
        per[r["bssid"]] = dict(r)
        if r.get("util_pct") is not None:
            utils[r["bssid"]].append(r["util_pct"])
    for b, v in utils.items():
        per[b]["util_typical"] = round(statistics.median(v))
        per[b]["util_peak"] = max(v)
    return per


def _own_of(bssid: str, units: list[dict[str, Any]]) -> str | None:
    from .hostpc import own_unit
    return own_unit(bssid, units)


def merge_probe_aps(aps: dict[str, dict[str, Any]], probe_aps: list[dict[str, Any]], units: list[dict[str, Any]],
                    probe_names: dict[str, str]) -> None:
    """Add what the probes hear: one entry per access point, its strongest signal anywhere, and who heard it."""
    for a in aps.values():
        a.setdefault("heard_by", ["this PC"])
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for r in sorted(probe_aps, key=lambda r: r["ts"]):
        latest[(r["probe"], r["bssid"])] = r
    for (pid, bssid), r in latest.items():
        who = probe_names.get(pid, pid)
        cur = aps.get(bssid)
        if cur is None:
            aps[bssid] = cur = {"ts": r["ts"], "bssid": bssid, "ssid": r.get("ssid"), "band": r.get("band"),
                                "channel": r.get("channel"), "rssi": r.get("rssi"), "own_unit": _own_of(bssid, units),
                                "heard_by": []}
        elif r.get("rssi") is not None and (cur.get("rssi") is None or r["rssi"] > cur["rssi"]):
            cur["rssi"] = r["rssi"]
        if who not in cur["heard_by"]:
            cur["heard_by"].append(who)


def plan(air: list[dict[str, Any]], units: list[dict[str, Any]], unit_events: list[dict[str, Any]],
         now: float | None = None, probe_aps: list[dict[str, Any]] | None = None,
         probes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Score each 5 GHz block and 2.4 GHz channel; lower score = better. Returns blocks, current and advice.

    ``probe_aps``: access points heard by ESP32 probes around the house; merged with this PC's survey using the
    strongest signal anywhere, so a neighbour loud in one room counts."""
    now = now or time.time()
    aps = _latest_per_bssid(air)
    pnames = {p["id"]: (p.get("room") or p["id"]) for p in probes or []}
    if probe_aps:
        merge_probe_aps(aps, probe_aps, units, {k: f"probe {v}" for k, v in pnames.items()})
    sensors = ["this PC"] + [f"probe {v}" for k, v in pnames.items() if any(r["probe"] == k for r in probe_aps or [])]
    own = _own_channels(units)
    radar = [e for e in unit_events if e["kind"] == "radar"]

    # ---------------------------------------------------------------- 5 GHz
    five = []
    radar_nearby = bool(radar)
    for b in BLOCKS_5:
        inb = [a for a in aps.values() if a.get("band") == "5" and b["first"] <= (a.get("channel") or 0) <= b["last"]]
        neigh = [a for a in inb if not a.get("own_unit")]
        strong = [a for a in neigh if (a.get("rssi") or -100) >= STRONG_DBM]
        # Neighbours' own busy reports count against a block; yours don't (your traffic moves with you).
        nb = [a["util_typical"] for a in neigh if a.get("util_typical") is not None]
        ob = [a["util_typical"] for a in inb if a.get("own_unit") and a.get("util_typical") is not None]
        hits = [e for e in radar if b["first"] - 2 <= (e.get("detail") or {}).get("channel", 0) <= b["last"] + 2]
        score = len(strong) * 12 + (len(neigh) - len(strong)) * 4 + (max(nb) if nb else 0) * 0.6 + len(hits) * 40
        if b["dfs"]:
            # Any DFS block can be knocked off by radar; more so once radar has been heard here at all.
            score += 30 + (25 if radar_nearby else 0) + (10 if b["block"] == "116-128" else 0)
        score += 25 if b["block"] == "149-161" else 0
        five.append({**{k: b[k] for k in ("block", "first", "last", "dfs", "note")},
                     "neighbours": len(neigh), "strong_neighbours": len(strong),
                     "neighbour_names": sorted({a.get("ssid") or "(hidden)" for a in strong})[:6],
                     "busy_pct": max(nb) if nb else None, "own_busy_pct": max(ob) if ob else None,
                     "radar_hits": len(hits), "last_radar": max((e["ts"] for e in hits), default=None),
                     "score": round(score)})
    cur5 = own.get("5")
    cur_block = block_of(cur5["channel"]) if cur5 else None
    for f in five:
        f["current"] = f["block"] == cur_block
    usable = [f for f in five if f["block"] != "149-161"] or five
    best5 = min(usable, key=lambda f: f["score"])
    rec5 = _advice_5(best5, next((f for f in five if f["current"]), None), cur5)

    # ---------------------------------------------------------------- 2.4 GHz
    two4 = []
    for ch in CHANNELS_24:
        over = [a for a in aps.values() if a.get("band") == "2.4" and abs((a.get("channel") or 0) - ch) <= 4]
        neigh = [a for a in over if not a.get("own_unit")]
        strong = [a for a in neigh if (a.get("rssi") or -100) >= STRONG_DBM]
        nb = [a["util_typical"] for a in neigh if a.get("util_typical") is not None]
        ob = [a["util_typical"] for a in over if a.get("own_unit") and a.get("util_typical") is not None]
        score = len(strong) * 12 + (len(neigh) - len(strong)) * 4 + (max(nb) if nb else 0) * 0.3
        two4.append({"channel": ch, "neighbours": len(neigh), "strong_neighbours": len(strong),
                     "neighbour_names": sorted({a.get("ssid") or "(hidden)" for a in strong})[:6],
                     "busy_pct": max(nb) if nb else None, "own_busy_pct": max(ob) if ob else None, "score": round(score)})
    cur24 = own.get("2.4")
    for c in two4:
        c["current"] = bool(cur24) and c["channel"] == cur24["channel"]
    best24 = min(two4, key=lambda c: c["score"])
    rec24 = _advice_24(best24, two4, cur24)

    return {"generated": now, "five": five, "two4": two4, "current": own, "sensors": sensors,
            "recommend": {"5": rec5, "2.4": rec24},
            "access_points_heard": len(aps), "neighbours_heard": sum(1 for a in aps.values() if not a.get("own_unit")),
            "last_scan": max((a["ts"] for a in aps.values()), default=None)}


def _advice_5(best: dict[str, Any], cur: dict[str, Any] | None, cur5: dict[str, Any] | None) -> dict[str, Any]:
    if not cur5:
        return {"block": best["block"], "change": False,
                "text": f"Use channels {best['block']} on 5 GHz (MeshLens hasn't read the router's current 5 GHz channel yet)."}
    if cur and cur["block"] == best["block"]:
        why = "it has no radar checks" if not best["dfs"] else "it is the least crowded"
        auto = " Set it as a fixed channel (or untick 'include DFS channels' on Auto) so Auto can't move to a radar channel." if not best["dfs"] else ""
        return {"block": best["block"], "change": False,
                "text": f"Stay on {best['block']} (now channel {cur5['channel']}): {why}.{auto}"}
    reasons = []
    if cur and cur["radar_hits"]:
        reasons.append(f"radar hit {cur['block']} {cur['radar_hits']} time{'s' if cur['radar_hits'] > 1 else ''}")
    if cur and cur["strong_neighbours"] > best["strong_neighbours"]:
        reasons.append(f"{cur['strong_neighbours']} strong neighbours on {cur['block']} vs {best['strong_neighbours']}")
    if cur and cur["dfs"] and not best["dfs"]:
        reasons.append(f"{best['block']} has no radar checks")
    change = not cur or cur["score"] - best["score"] >= 15
    text = (f"Move 5 GHz to channels {best['block']} (e.g. control channel {best['first']}, 80 MHz)"
            + (f": {'; '.join(reasons)}." if reasons else ".")
            if change else f"{best['block']} scores slightly better than {cur['block'] if cur else '?'}, but not enough to be worth a change.")
    return {"block": best["block"], "change": change, "text": text}


def _advice_24(best: dict[str, Any], two4: list[dict[str, Any]], cur24: dict[str, Any] | None) -> dict[str, Any]:
    if not cur24:
        return {"channel": best["channel"], "change": False, "text": f"Use channel {best['channel']} at 20 MHz."}
    ch = cur24["channel"]
    if ch not in CHANNELS_24:
        overl = [c["channel"] for c in two4 if abs(c["channel"] - ch) <= 4]
        return {"channel": best["channel"], "change": True,
                "text": f"2.4 GHz is on channel {ch}, which overlaps channels {' and '.join(map(str, overl))}: it takes "
                        f"interference from networks on both. Use channel {best['channel']} (one of 1, 6, 11) at 20 MHz."}
    cur = next(c for c in two4 if c["channel"] == ch)
    if cur["channel"] == best["channel"] or cur["score"] - best["score"] < 12:
        return {"channel": ch, "change": False, "text": f"Stay on channel {ch}."}
    return {"channel": best["channel"], "change": True,
            "text": f"Channel {best['channel']} is quieter than {ch} ({best['strong_neighbours']} vs {cur['strong_neighbours']} "
                    "strong neighbours). Use it at 20 MHz."}


# -------------------------------------------------------------------- radar incidents

def radar_incidents(unit_events: list[dict[str, Any]], drops: list[dict[str, Any]],
                    band_moves: dict[float, list[dict[str, Any]]] | None = None,
                    names: dict[str, str] | None = None) -> dict[str, Any]:
    """Group radar detections into incidents and say what each did.

    ``drops``: device disconnect/leave/kick events around the incidents. ``band_moves``: per incident start,
    devices that were on 5/6 GHz before and on 2.4 GHz after (``{"mac", "name", "still_on_24"}``), worked out
    by the caller from station samples.
    """
    names = names or {}
    radar = sorted((e for e in unit_events if e["kind"] == "radar"), key=lambda e: e["ts"])
    moves = sorted((e for e in unit_events if e["kind"] in ("channel_change", "channel_replan")), key=lambda e: e["ts"])
    groups: list[list[dict[str, Any]]] = []
    for e in radar:
        if groups and e["ts"] - groups[-1][-1]["ts"] <= RADAR_WINDOW_S:
            groups[-1].append(e)
        else:
            groups.append([e])
    out = []
    for g in groups:
        t0, t1 = g[0]["ts"], g[-1]["ts"]
        chans = sorted({(e.get("detail") or {}).get("channel") for e in g} - {None})
        after = [m for m in moves if t0 - 30 <= m["ts"] <= t1 + AFTER_S]
        to = [m["detail"].get("to_channel") or m["detail"].get("channel") for m in after
              if (m["detail"].get("band") in (None, "5"))]
        unavailable = sorted({int(c) for m in after for c in str(m["detail"].get("unavailable") or "").split(",") if c.strip().isdigit()})
        dropped = sorted({d["mac"] for d in drops if d.get("mac") and t0 - 5 <= d["ts"] <= t1 + AFTER_S})
        mv = (band_moves or {}).get(t0, [])
        out.append({"ts": t0, "until": t1, "detected_by": sorted({names.get(e["unit"], e["unit"]) for e in g}),
                    "detected_by_macs": sorted({e["unit"] for e in g}),
                    "channels": chans, "blocks": sorted({block_of(c) for c in chans} - {None}),
                    "moved_to": [c for c in to if c][-1:] or [], "unavailable": unavailable,
                    "devices_dropped": len(dropped), "dropped_macs": dropped,
                    "pushed_to_24": mv, "still_on_24": [m for m in mv if m.get("still_on_24")]})
    detectors: dict[str, int] = defaultdict(int)
    for e in radar:
        detectors[e["unit"]] += 1
    only_one = len(detectors) == 1 and len(radar) >= 2
    return {"incidents": out, "count": len(radar),
            "detectors": {names.get(k, k): v for k, v in detectors.items()},
            "single_detector": names.get(next(iter(detectors)), next(iter(detectors))) if only_one else None}


def band_moves_after(t: float, samples: list[dict[str, Any]], latest: dict[str, dict[str, Any]],
                     skip: set[str], names: dict[str, str], window: float = 600, until: float | None = None) -> list[dict[str, Any]]:
    """Devices on 5/6 GHz just before ``t`` that show up on 2.4 GHz within ``window`` seconds after it (or after
    ``until``, the end of the incident). A device can take a minute or two to drop, so any 2.4 GHz reading counts."""
    end = (until or t) + window
    before: dict[str, str] = {}
    went_24: set[str] = set()
    for s in sorted(samples, key=lambda s: s["ts"]):
        if s["mac"] in skip or not s.get("band"):
            continue
        if t - window <= s["ts"] < t:
            before[s["mac"]] = s["band"]
        elif t <= s["ts"] <= end and s["band"] == "2.4":
            went_24.add(s["mac"])
    out = []
    for mac, b in before.items():
        if b in ("5", "6") and mac in went_24:
            out.append({"mac": mac, "name": names.get(mac, mac),
                        "still_on_24": (latest.get(mac) or {}).get("band") == "2.4"})
    return out
