"""Wi-Fi journeys and stability findings from AP events (EVT-01 to EVT-05).

Pure functions over event dicts (``ts``, ``unit``, ``kind``, ``mac``, ``iface``, ``detail``)
so they can be tested without a database.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

# Thresholds (to become editable rule data, DIA-04).
KICK_LOOP = (3, 3600)        # >= 3 weak-signal kicks within an hour
ROAM_STORM = (4, 120)        # >= 4 roam attempts within 2 minutes
FLAPPING = (6, 3600)         # >= 6 disconnects within an hour


@dataclass
class DeviceStability:
    mac: str
    connects: int = 0
    disconnects: int = 0
    kicks: int = 0
    roam_attempts: int = 0
    roams: int = 0
    moved_away: int = 0
    units: set[str] = field(default_factory=set)
    weakest_rssi: int | None = None
    last_event: float = 0.0

    @property
    def score(self) -> int:
        """Higher = less stable (for ranking, EVT-05)."""
        return self.kicks * 5 + self.roam_attempts * 2 + self.disconnects


def stability(events: Iterable[dict[str, Any]]) -> list[DeviceStability]:
    """Per-device counts, least stable first."""
    by: dict[str, DeviceStability] = {}
    for e in events:
        mac = e.get("mac")
        if not mac:
            continue
        d = by.setdefault(mac, DeviceStability(mac))
        k = e["kind"]
        if k == "connect":
            d.connects += 1
        elif k == "disconnect":
            d.disconnects += 1
        elif k == "kick":
            d.kicks += 1
        elif k == "roam_candidate":
            d.roam_attempts += 1
            r = (e.get("detail") or {}).get("rssi")
            if r is not None:
                d.weakest_rssi = r if d.weakest_rssi is None else min(d.weakest_rssi, r)
        elif k == "roam":
            d.roams += 1
        elif k == "moved_away":
            d.moved_away += 1
        elif k == "leave":
            r = (e.get("detail") or {}).get("rssi")
            if r is not None:
                d.weakest_rssi = r if d.weakest_rssi is None else min(d.weakest_rssi, r)
        if e.get("unit"):
            d.units.add(e["unit"])
        d.last_event = max(d.last_event, e["ts"])
    return sorted(by.values(), key=lambda d: d.score, reverse=True)


def _bursts(times: list[float], count: int, window: float) -> list[tuple[float, float, int]]:
    """Windows where at least ``count`` timestamps fall within ``window`` seconds."""
    times = sorted(times)
    out = []
    i = 0
    for j in range(len(times)):
        while times[j] - times[i] > window:
            i += 1
        if j - i + 1 >= count and (not out or times[i] > out[-1][1]):
            k = j
            while k + 1 < len(times) and times[k + 1] - times[i] <= window:
                k += 1
            out.append((times[i], times[k], k - i + 1))
    return out


def findings(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Kick loops, roam storms and flapping devices with their evidence windows (EVT-03)."""
    per: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    rssi: dict[str, list[tuple[float, int]]] = defaultdict(list)
    for e in events:
        if e.get("mac"):
            per[e["mac"]][e["kind"]].append(e["ts"])
            r = (e.get("detail") or {}).get("rssi")
            if r is not None:
                rssi[e["mac"]].append((e["ts"], r))
    out = []
    for mac, kinds in per.items():
        for start, end, n in _bursts(kinds.get("kick", []), *KICK_LOOP):
            # Signal readings around the kicks (roam checks and leave lines carry RSSI; kick lines do not).
            near = [r for t, r in rssi[mac] if start - 600 <= t <= end]
            out.append({"rule": "kick_loop", "severity": "high", "mac": mac, "from": start, "to": end, "count": n,
                        "text": f"Kicked {n} times for weak signal within an hour"
                                + (f" (signal down to {min(near)} dBm)" if near else "")
                                + ". The router's roaming assistant disconnects it below its threshold; "
                                  "move it closer, use 5/6 GHz or a cable, or lower the threshold."})
        for start, end, n in _bursts(kinds.get("roam_candidate", []), *ROAM_STORM):
            out.append({"rule": "roam_storm", "severity": "high", "mac": mac, "from": start, "to": end, "count": n,
                        "text": f"{n} roam attempts within {int(end - start) or 1} s: the mesh keeps trying to move it, "
                                "which feels like lag."})
        for start, end, n in _bursts(kinds.get("disconnect", []), *FLAPPING):
            out.append({"rule": "flapping", "severity": "medium", "mac": mac, "from": start, "to": end, "count": n,
                        "text": f"Disconnected {n} times within an hour."})
    return sorted(out, key=lambda f: f["to"], reverse=True)


def journey(events: Iterable[dict[str, Any]], mac: str) -> list[dict[str, Any]]:
    """Chronological journey for one device: where it connected, moves, kicks, gaps (EVT-02)."""
    evs = sorted((e for e in events if e.get("mac") == mac), key=lambda e: e["ts"])
    out: list[dict[str, Any]] = []
    connected_since: float | None = None
    for e in evs:
        k = e["kind"]
        item = {"ts": e["ts"], "kind": k, "unit": e.get("unit"), "iface": e.get("iface"), "band": e.get("band"),
                "detail": e.get("detail") or {}}
        if k == "connect":
            if out and out[-1]["kind"] in ("disconnect", "kick") :
                item["offline_s"] = round(e["ts"] - out[-1]["ts"])
            connected_since = e["ts"]
        elif k in ("disconnect", "kick") and connected_since is not None:
            item["connected_s"] = round(e["ts"] - connected_since)
            connected_since = None
        out.append(item)
    return out
