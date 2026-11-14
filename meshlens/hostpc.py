"""The MeshLens PC as a network sensor (read-only, no extra software).

- Air survey (``netsh wlan show networks mode=bssid``): every access point the PC's Wi-Fi card hears, with
  channel and signal, and - where the AP advertises BSS Load (ASUS units do) - the AP's own measurement
  of how busy its channel is and how many devices it has. Works while the card is not connected.
- Wired adapter counters (``Get-NetAdapterStatistics``): broadcast and multicast packets per second,
  the signature of a broadcast flood, plus traffic, discards and errors.
- The PC's own Wi-Fi link (``netsh wlan show interfaces``) when it is connected.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import time
from typing import Any

log = logging.getLogger("meshlens.hostpc")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run(cmd: list[str], timeout: float = 20) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    for enc in ("utf-8", "cp1252", "cp437"):
        try:
            return r.stdout.decode(enc)
        except UnicodeDecodeError:
            continue
    return r.stdout.decode("utf-8", "replace")


# ---------------------------------------------------------------- air survey

def parse_networks(text: str) -> list[dict[str, Any]]:
    """``netsh wlan show networks mode=bssid`` (English output) -> one row per BSSID."""
    out: list[dict[str, Any]] = []
    ssid = auth = None
    cur: dict[str, Any] | None = None
    for line in text.splitlines():
        s = line.strip()
        m = re.match(r"SSID \d+ : ?(.*)$", s)
        if m:
            ssid, cur = m.group(1).strip() or None, None
            continue
        m = re.match(r"Authentication\s*:\s*(.+)", s)
        if m and cur is None:
            auth = m.group(1).strip()
            continue
        m = re.match(r"BSSID \d+\s*:\s*([0-9a-fA-F:]{17})", s)
        if m:
            cur = {"ssid": ssid, "auth": auth, "bssid": m.group(1).lower(), "signal_pct": None, "band": None,
                   "channel": None, "radio": None, "stations": None, "util_pct": None}
            out.append(cur)
            continue
        if cur is None:
            continue
        if m := re.match(r"Signal\s*:\s*(\d+)%", s):
            cur["signal_pct"] = int(m.group(1))
        elif m := re.match(r"Radio type\s*:\s*(\S+)", s):
            cur["radio"] = m.group(1)
        elif m := re.match(r"Band\s*:\s*([\d.]+) GHz", s):
            cur["band"] = m.group(1)
        elif m := re.match(r"Channel\s*:\s*(\d+)", s):
            cur["channel"] = int(m.group(1))
        elif m := re.match(r"Connected Stations:\s*(\d+)", s):
            cur["stations"] = int(m.group(1))
        elif m := re.match(r"Channel Utilization:\s*\d+\s*\((\d+)\s*%\)", s):
            cur["util_pct"] = int(m.group(1))
    for r in out:   # Windows percent -> approximate dBm (Windows maps -100..-50 dBm to 0..100 %)
        r["rssi"] = round(r["signal_pct"] / 2 - 100) if r["signal_pct"] is not None else None
    return out


def own_unit(bssid: str, units: list[dict[str, Any]]) -> str | None:
    """ASUS BSSIDs share bytes 1, 2 and 4 with the unit's MAC; byte 0, byte 3 and the last byte vary per
    radio/SSID (e.g. unit cc:28:aa:6c:2e:64 broadcasts ce:28:aa:1c:2e:64). The closest last byte wins."""
    key = (bssid[3:8], bssid[12:14])
    best, dist = None, 99
    for u in units:
        for m in [u["mac"], *(u.get("macs") or [])]:
            if (m[3:8], m[12:14]) == key:
                d = abs(int(m[15:17], 16) - int(bssid[15:17], 16))
                if d < dist:
                    best, dist = u["mac"], d
    return best if dist <= 15 else None


def scan() -> list[dict[str, Any]]:
    if sys.platform != "win32":
        return []
    return parse_networks(_run(["netsh", "wlan", "show", "networks", "mode=bssid"]))


def parse_interfaces(text: str) -> dict[str, Any] | None:
    """``netsh wlan show interfaces`` -> the PC's own Wi-Fi link (None when not connected)."""
    kv: dict[str, str] = {}
    for line in text.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            kv.setdefault(k.strip().lower(), v.strip())
    if kv.get("state", "").lower() != "connected":
        return {"connected": False, "adapter": kv.get("description")} if kv else None

    def num(k):
        m = re.search(r"[\d.]+", kv.get(k, ""))
        return float(m.group()) if m else None
    sig = num("signal")
    return {"connected": True, "adapter": kv.get("description"), "ssid": kv.get("ssid"), "bssid": kv.get("ap bssid") or kv.get("bssid"),
            "band": (kv.get("band") or "").replace(" GHz", "") or None, "channel": num("channel"), "radio": kv.get("radio type"),
            "signal_pct": sig, "rssi": round(sig / 2 - 100) if sig is not None else None,
            "rx_mbps": num("receive rate (mbps)"), "tx_mbps": num("transmit rate (mbps)")}


def wifi_link() -> dict[str, Any] | None:
    if sys.platform != "win32":
        return None
    return parse_interfaces(_run(["netsh", "wlan", "show", "interfaces"]))


# ---------------------------------------------------------------- wired adapter counters

PS_STATS = ("Get-NetAdapterStatistics | Select-Object Name, ReceivedBytes, SentBytes, ReceivedUnicastPackets, "
            "ReceivedMulticastPackets, ReceivedBroadcastPackets, ReceivedDiscardedPackets, ReceivedPacketErrors "
            "| ConvertTo-Json -Compress")


def adapter_counters() -> list[dict[str, Any]]:
    if sys.platform != "win32":
        return []
    out = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", PS_STATS], timeout=30)
    try:
        data = json.loads(out)
    except ValueError:
        return []
    return data if isinstance(data, list) else [data]


def rates(prev: dict[str, Any], cur: dict[str, Any], dt: float) -> dict[str, Any] | None:
    """Per-second rates between two counter readings of one adapter (None after a reset)."""
    keys = ("ReceivedBytes", "SentBytes", "ReceivedUnicastPackets", "ReceivedMulticastPackets", "ReceivedBroadcastPackets",
            "ReceivedDiscardedPackets", "ReceivedPacketErrors")
    d = {k: (cur.get(k) or 0) - (prev.get(k) or 0) for k in keys}
    if dt <= 0 or any(v < 0 for v in d.values()):
        return None
    return {"rx_mbps": round(d["ReceivedBytes"] * 8 / dt / 1e6, 3), "tx_mbps": round(d["SentBytes"] * 8 / dt / 1e6, 3),
            "ucast_pps": round(d["ReceivedUnicastPackets"] / dt, 2), "mcast_pps": round(d["ReceivedMulticastPackets"] / dt, 2),
            "bcast_pps": round(d["ReceivedBroadcastPackets"] / dt, 2),
            "discards": d["ReceivedDiscardedPackets"], "errors": d["ReceivedPacketErrors"]}


class HostMonitor:
    """Runs from the engine loop: adapter counters every call (about a minute), air survey every 2 minutes."""

    def __init__(self) -> None:
        self.prev: dict[str, tuple[float, dict[str, Any]]] = {}
        self.last_scan = 0.0
        self.status: dict[str, Any] = {"link": None, "adapters": [], "last_scan": None, "bss_seen": 0, "error": None}

    def poll(self, units: list[dict[str, Any]], now: float | None = None) -> None:
        from .store import db
        now = now or time.time()
        rows = []
        live = []
        for a in adapter_counters():
            name = a.get("Name")
            if not name or not (a.get("ReceivedBytes") or a.get("SentBytes")):
                continue      # unused adapters (virtual switches, disconnected Wi-Fi)
            if name in self.prev:
                t0, p = self.prev[name]
                r = rates(p, a, now - t0)
                if r:
                    rows.append({"ts": now, "adapter": name, **r})
                    live.append({"adapter": name, **r})
            self.prev[name] = (now, a)
        if rows:
            db.insert_many("host_lan", rows)
        self.status["adapters"] = live or self.status["adapters"]
        self.status["link"] = wifi_link()
        if now - self.last_scan >= 120:
            self.last_scan = now
            bss = scan()
            for b in bss:
                b["own_unit"] = own_unit(b["bssid"], units)
            if bss:
                db.insert_many("air_scan", [{"ts": now, **{k: b[k] for k in ("bssid", "ssid", "band", "channel", "signal_pct",
                                                                             "rssi", "util_pct", "stations", "radio", "own_unit")}}
                                            for b in bss])
            self.status.update(last_scan=now, bss_seen=len(bss))
