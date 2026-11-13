"""Parsers for ASUS Asuswrt / AiMesh command output and state files.

All functions are pure (text in, data out) so they can be tested with
fixtures. Formats were verified on stock firmware 3.0.0.6.102 on a ZenWiFi BT8
(MediaTek) and BD4 (Qualcomm) on 13 November 2026 (docs/05-asus-aimesh.md).
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime
from typing import Any

MAC_RE = r"[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}"


def norm_mac(m: str) -> str:
    return m.strip().lower()


def is_randomised(mac: str) -> bool:
    """Locally administered (private / randomised) MAC address."""
    return bool(int(mac[:2], 16) & 0x02)


# ---------------------------------------------------------------- sections

def split_sections(text: str, marker: str = "@@@ ") -> dict[str, str]:
    """Split batch output into {key: body} on lines starting with ``marker``."""
    out: dict[str, list[str]] = {}
    key = None
    # Files without a trailing newline (e.g. /tmp/clientlist.json) leave the next marker mid-line: split it off.
    text = re.sub(r"(?<!\n)" + re.escape(marker) + r"(?=[\w:.\-]+\n)", "\n" + marker, text)
    for line in text.splitlines():
        if line.startswith(marker):
            key = line[len(marker):].strip()
            out[key] = []
        elif key is not None:
            out[key].append(line)
    return {k: "\n".join(v).strip("\n") for k, v in out.items()}


def parse_nvram_kv(text: str) -> dict[str, str]:
    """Lines of ``key=value`` (from ``nvram get`` in the batch script)."""
    out = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def which_tools(text: str) -> set[str]:
    """Lines of ``tool: /path`` or ``tool: MISSING``."""
    present = set()
    for line in text.splitlines():
        if ":" in line:
            tool, path = line.split(":", 1)
            if path.strip() and path.strip() != "MISSING":
                present.add(tool.strip())
    return present


def chip_family(tools: set[str]) -> str:
    if "mwctl" in tools:
        return "mediatek"
    if "wlanconfig" in tools or "cfg80211tool" in tools:
        return "qualcomm"
    if "wl" in tools:
        return "broadcom"
    return "unknown"


# ---------------------------------------------------------------- interfaces

_BAND_BY_PREFIX = [("rax", "6"), ("rai", "5"), ("ra", "2.4"), ("apclix", "6"), ("apclii", "5"), ("apcli", "2.4")]


def band_of_iface(iface: str) -> str | None:
    """Band of an interface name: MediaTek ra*/rai*/rax*, Qualcomm ath0*/ath1*."""
    for prefix, band in _BAND_BY_PREFIX:
        if iface.startswith(prefix):
            return band
    m = re.match(r"ath([0-9])", iface)
    if m:
        return {"0": "2.4", "1": "5", "2": "6"}.get(m.group(1))
    return None


def parse_iw_dev(text: str) -> list[dict[str, Any]]:
    """``iw dev``: interfaces with type, ssid, channel, width, tx power."""
    out: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    for line in text.splitlines():
        s = line.strip()
        m = re.match(r"Interface (\S+)", s)
        if m:
            cur = {"iface": m.group(1), "band": band_of_iface(m.group(1))}
            out.append(cur)
            continue
        if cur is None:
            continue
        if s.startswith("addr "):
            cur["addr"] = norm_mac(s.split()[1])
        elif s.startswith("ssid "):
            cur["ssid"] = s[5:]
        elif s.startswith("type "):
            cur["type"] = s[5:]
        elif s.startswith("channel "):
            m = re.match(r"channel (\d+) \((\d+) MHz\), width: (\d+) MHz", s)
            if m:
                cur["channel"], cur["freq"], cur["width"] = int(m.group(1)), int(m.group(2)), int(m.group(3))
        elif s.startswith("txpower "):
            cur["txpower_dbm"] = float(s.split()[1])
    return out


# ---------------------------------------------------------------- mesh and clients (router state files)

def parse_cfg_device_list(text: str) -> list[dict[str, Any]]:
    """``nvram get cfg_device_list``: ``<Model>IP>MAC>is_main<Model>...``."""
    out = []
    for part in text.strip().split("<"):
        f = part.split(">")
        if len(f) >= 4 and re.fullmatch(MAC_RE, f[2] or ""):
            out.append({"model": f[0], "ip": f[1], "mac": norm_mac(f[2]), "main": f[3].strip() == "1"})
    return out


def _json(text: str) -> Any:
    text = text.strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


def parse_clientlist(text: str) -> list[dict[str, Any]]:
    """``/tmp/clientlist.json``: {unit_mac: {band_key: {client_mac: {ip, rssi}}, wired_mac: {...}}}."""
    out = []
    for unit, bands in (_json(text) or {}).items():
        if not isinstance(bands, dict):
            continue
        for band_key, clients in bands.items():
            if not isinstance(clients, dict):
                continue
            for mac, info in clients.items():
                info = info if isinstance(info, dict) else {}
                rssi = info.get("rssi")
                out.append({
                    "unit": norm_mac(unit),
                    "band_key": band_key,
                    "band": None if band_key == "wired_mac" else {"2": "2.4"}.get(
                        band_key.split("_")[0].replace("G", ""), band_key.split("_")[0].replace("G", "")),
                    "wired": band_key == "wired_mac",
                    "mac": norm_mac(mac),
                    "ip": info.get("ip") or None,
                    "rssi": int(rssi) if rssi not in (None, "") else None,
                })
    return out


def parse_allwclientlist(text: str) -> dict[str, dict[str, Any]]:
    """``/tmp/allwclientlist.json``: per client interface, Wi-Fi security, MLO."""
    out = {}
    for unit, bands in (_json(text) or {}).items():
        for band_key, clients in (bands or {}).items():
            for mac, info in (clients or {}).items():
                out[norm_mac(mac)] = {
                    "unit": norm_mac(unit), "band_key": band_key,
                    "iface": info.get("ifname"), "auth": info.get("wifi_auth"),
                    "mlo": bool(info.get("mlo_active")), "ts": info.get("ts"),
                    # Wi-Fi 7 devices: per-link MAC here, the MLD MAC is the one with the IP and DHCP name.
                    "mld": (norm_mac(info["mld_mac"]) if info.get("mld_mac") and
                            norm_mac(info["mld_mac"]) not in ("00:00:00:00:00:00", norm_mac(mac)) else None),
                }
    return out


def parse_wiredclientlist(text: str) -> list[dict[str, str]]:
    out = []
    for unit, clients in (_json(text) or {}).items():
        for mac in (clients or {}):
            out.append({"unit": norm_mac(unit), "mac": norm_mac(mac)})
    return out


def parse_nmp_clients(text: str) -> dict[str, dict[str, Any]]:
    """``/jffs/nmp_cl_json.js``: the ASUS app's device database (names, vendor, type...)."""
    out = {}
    for mac, d in (_json(text) or {}).items():
        if not isinstance(d, dict):
            continue
        out[norm_mac(mac)] = {
            "name": d.get("name") or None,
            "name_src": d.get("name_src"),
            "vendor": d.get("vendor") or None,
            "vendorclass": d.get("vendorclass") or None,
            "type": d.get("type"),
            "online": str(d.get("online")) == "1",
            "wireless": d.get("wireless"),
            "conn_ts": d.get("conn_ts"),
        }
    return out


def parse_relist(text: str) -> dict[str, dict[str, str]]:
    """``/tmp/relist.json``: backhaul station MACs per mesh node."""
    return {norm_mac(k): {b: norm_mac(v) for b, v in (vals or {}).items() if v} for k, vals in (_json(text) or {}).items()}


def parse_node_settings(text: str) -> dict[str, Any]:
    """``/tmp/<node-mac>.json``: alias, preferred parent, channel plan."""
    d = _json(text) or {}
    misc = d.get("misc") or {}
    prefer = d.get("prefer_ap") or {}
    return {"alias": misc.get("cfg_alias"), "prefer_parent": (prefer.get("amas_wlc_target_bssid") or "").strip("<") or None}


def parse_leases(text: str) -> list[dict[str, Any]]:
    """dnsmasq leases: ``expiry mac ip hostname clientid``."""
    out = []
    for line in text.splitlines():
        f = line.split()
        if len(f) >= 4 and re.fullmatch(MAC_RE, f[1]):
            out.append({"expiry": int(f[0]) if f[0].isdigit() else None, "mac": norm_mac(f[1]), "ip": f[2],
                        "hostname": None if f[3] == "*" else f[3]})
    return out


# ---------------------------------------------------------------- per-client radio stats

def parse_hostapd_all_sta(text: str, iface: str) -> list[dict[str, Any]]:
    """``hostapd_cli -i <if> all_sta``: blocks starting with a MAC line."""
    out: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    for line in text.splitlines():
        s = line.strip()
        if re.fullmatch(MAC_RE, s):
            cur = {"mac": norm_mac(s), "iface": iface, "band": band_of_iface(iface)}
            out.append(cur)
        elif cur is not None and "=" in s:
            k, v = s.split("=", 1)
            if k in ("rx_packets", "tx_packets", "rx_bytes", "tx_bytes", "inactive_msec", "connected_time"):
                cur[k] = int(v) if v.lstrip("-").isdigit() else None
            elif k == "signal":
                cur["rssi"] = int(v) if v.lstrip("-").isdigit() else None
            elif k in ("rx_rate_info", "tx_rate_info"):
                parts = v.split()
                cur[k.replace("_info", "")] = float(parts[0]) / 10 if parts and parts[0].isdigit() else None
                cur[k] = v
            elif k == "flags":
                cur["flags"] = v
    return out


def parse_wlanconfig_list_sta(text: str, iface: str) -> list[dict[str, Any]]:
    """Qualcomm ``wlanconfig <if> list sta`` table."""
    out = []
    for line in text.splitlines():
        f = line.split()
        if not f or not re.fullmatch(MAC_RE, f[0]) or len(f) < 9:
            continue
        def rate(x: str) -> float | None:
            m = re.match(r"(\d+(?:\.\d+)?)M", x)
            return float(m.group(1)) if m else None
        def num(x: str) -> int | None:
            return int(x) if x.lstrip("-").isdigit() else None
        out.append({"mac": norm_mac(f[0]), "iface": iface, "band": band_of_iface(iface),
                    "channel": num(f[2]), "tx_rate": rate(f[3]), "rx_rate": rate(f[4]),
                    "rssi": num(f[5]), "rssi_min": num(f[6]), "rssi_max": num(f[7]), "idle_s": num(f[8]),
                    "assoc_time": next((x for x in f if re.fullmatch(r"\d{2}:\d{2}:\d{2}", x)), None)})
    return out


# ---------------------------------------------------------------- radio health

def parse_mwctl_stat(text: str) -> dict[str, Any]:
    """MediaTek ``mwctl dev <if> stat``."""
    out: dict[str, Any] = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        k, v = (x.strip() for x in line.split("=", 1))
        k = re.sub(r"\s+", " ", k)
        if k == "CurrentTemperature":
            out["temperature"] = float(v) if v.replace(".", "").isdigit() else None
        elif k == "Tx success":
            out["tx_success"] = int(v)
        elif k == "Tx fail count":
            m = re.match(r"(\d+), PER=([\d.]+)%", v)
            if m:
                out["tx_fail"], out["tx_fail_pct"] = int(m.group(1)), float(m.group(2))
        elif k == "Rx success":
            out["rx_success"] = int(v)
        elif k == "Rx with CRC":
            m = re.match(r"(\d+), PER=([\d.]+)%", v)
            if m:
                out["rx_crc"], out["rx_crc_pct"] = int(m.group(1)), float(m.group(2))
        elif k == "Last TX Rate":
            out["last_tx_rate"] = v
        elif k == "Last RX Rate":
            out["last_rx_rate"] = v
        elif k == "proxy arp enable":
            out["proxy_arp"] = v == "1"
        elif k == "WNM BSS Transition Management enable":
            out["bss_transition"] = v == "1"
    return out


def parse_iwconfig(text: str) -> list[dict[str, Any]]:
    """``iwconfig``: per interface signal / noise (Qualcomm backhaul and radios)."""
    out: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    for line in text.splitlines():
        if line and not line[0].isspace():
            name = line.split()[0]
            cur = {"iface": name, "band": band_of_iface(name)}
            out.append(cur)
            m = re.search(r'ESSID:"([^"]*)"', line)
            if m:
                cur["ssid"] = m.group(1)
        if cur is None:
            continue
        m = re.search(r"Signal level=(-?\d+) dBm\s+Noise level=(-?\d+) dBm", line)
        if m:
            cur["signal"], cur["noise"] = int(m.group(1)), int(m.group(2))
        m = re.search(r"Bit Rate[:=]([\d.]+) (\w)b/s", line)
        if m:
            cur["bitrate_mbps"] = float(m.group(1)) * (1000 if m.group(2) == "G" else 1)
    return [i for i in out if len(i) > 2]


def parse_proc_net_dev(text: str) -> dict[str, dict[str, int]]:
    out = {}
    for line in text.splitlines():
        m = re.match(r"\s*([\w.@-]+):\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+\d+\s+\d+\s+\d+\s+\d+\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)", line)
        if m:
            out[m.group(1)] = {"rx_bytes": int(m.group(2)), "rx_packets": int(m.group(3)), "rx_errs": int(m.group(4)),
                               "rx_drop": int(m.group(5)), "tx_bytes": int(m.group(6)), "tx_packets": int(m.group(7)),
                               "tx_errs": int(m.group(8)), "tx_drop": int(m.group(9))}
    return out


# ---------------------------------------------------------------- log events

_MONTHS = {m: i for i, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}

_EVENT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("connect", re.compile(rf"hapdevent: hapdevent_proc_event\(\d+\): (?P<iface>[\w.]+): Assoc (?P<mac>{MAC_RE})")),
    ("disconnect", re.compile(rf"hapdevent: hapdevent_proc_event\(\d+\): (?P<iface>[\w.=]+): Disassoc (?P<mac>{MAC_RE})")),
    ("kick", re.compile(rf"roamast: (?P<iface>[\w.]+): disconnect weak signal strength station \[(?P<mac>{MAC_RE})\]")),
    ("roam_candidate", re.compile(
        rf"roamast: determine candidate node \[(?P<target>{MAC_RE})\]\(rssi: (?P<target_rssi>-?\d+)dbm\) for client "
        rf"\[(?P<mac>{MAC_RE})\]\(rssi: (?P<rssi>-?\d+)dbm from client\)")),
    ("roam", re.compile(rf"roamast: Roam a client \[(?P<mac>{MAC_RE})\], status \[(?P<status>\d+)\]")),
    ("moved_away", re.compile(
        rf"roamast: \[EXAP\]Deauth old sta in (?P<iface>[\w.]+): (?P<mac>{MAC_RE}), because sta is already connected to "
        rf"(?P<new_unit>{MAC_RE})")),
    # Qualcomm driver lines (BD4)
    ("leave", re.compile(rf"vap-\d+\((?P<iface>\w+)\): \[(?P<mac>{MAC_RE})\]_ieee80211_node_leave: station with aid \d+ , "
                         rf"rssi=(?P<rssi>-?\d+)")),
    ("assoc_failed", re.compile(r"vap-\d+\((?P<iface>\w+)\):wlan_mlme_assoc_resp assoc failed with status (?P<status>\d+)")),
]


def _event_time(line: str, now: datetime) -> float | None:
    m = re.match(r"([A-Z][a-z]{2}) +(\d+) (\d{2}):(\d{2}):(\d{2})", line)
    if not m or m.group(1) not in _MONTHS:
        return None
    month = _MONTHS[m.group(1)]
    year = now.year - (1 if month > now.month else 0)  # Syslog has no year; December lines read in January.
    dt = datetime(year, month, int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5)))
    return time.mktime(dt.timetuple())


def parse_log_events(text: str, unit: str, now: datetime | None = None) -> list[dict[str, Any]]:
    """Wi-Fi events from syslog lines (MediaTek and Qualcomm units share hapdevent/roamast)."""
    now = now or datetime.now()
    out = []
    for line in text.splitlines():
        for kind, pat in _EVENT_PATTERNS:
            m = pat.search(line)
            if not m:
                continue
            ts = _event_time(line, now)
            if ts is None:
                break
            g = {k: v for k, v in m.groupdict().items() if v is not None}
            detail = {}
            for k in ("target", "new_unit"):
                if k in g:
                    detail[k] = norm_mac(g.pop(k))
            for k in ("target_rssi", "rssi", "status"):
                if k in g:
                    detail[k] = int(g.pop(k))
            iface = g.pop("iface", None)
            if iface and iface.startswith("initiator="):
                detail["initiator"] = iface.split("=", 1)[1]
                iface = None
            out.append({
                "ts": ts, "unit": unit, "kind": kind,
                "mac": norm_mac(g["mac"]) if "mac" in g else None,
                "iface": iface, "band": band_of_iface(iface) if iface else None,
                "detail": detail,
                "raw_hash": hashlib.sha1(line.strip().encode()).hexdigest(),
            })
            break
    return out
