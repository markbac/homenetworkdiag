"""Managed switches: traffic between devices inside the house (it never reaches the router), read-only.

- **UniFi** switches through the local UniFi Network controller (View Only admin, D-26/D-27): bytes, packets and errors
  per port (``stat/device``) and which devices are on each port (``stat/sta``: ``sw_mac`` / ``sw_port``).
- **TP-Link Easy Smart** switches (TL-SG105E/108E/116E, SG608E...) through their own web page with the stored login:
  good/bad packet counts per port and link speed (``PortStatisticsRpm.htm``). They don't report bytes or which
  devices are on a port.

Only reads: login, read the statistics page / API, log out. Nothing is changed, counters are never cleared.
Counters are stored as read; the Graphs tab turns them into rates per poll.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Awaitable, Callable

import aiohttp

from .. import secrets
from ..store import db, jsonl

log = logging.getLogger("meshlens.switches")

TIMEOUT = aiohttp.ClientTimeout(total=20)

# Easy Smart link_status codes (as the switch's own page shows them).
EASY_SMART_SPEED = {0: None, 1: None, 2: 10, 3: 10, 4: 100, 5: 100, 6: 1000, 7: 1000}


def _mac(raw: str | None) -> str:
    raw = (raw or "").replace(":", "").replace("-", "").lower()
    return ":".join(raw[i:i + 2] for i in range(0, 12, 2)) if len(raw) == 12 else raw


# ---------------------------------------------------------------- parsers (pure)

def parse_unifi(devices: list[dict[str, Any]], clients: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """UniFi ``stat/device`` + ``stat/sta`` -> one row per switch port."""
    on_port: dict[tuple[str, int], list[str]] = {}
    for c in clients or []:
        if c.get("sw_mac") and c.get("sw_port") is not None and c.get("mac"):
            on_port.setdefault((_mac(c["sw_mac"]), int(c["sw_port"])), []).append(_mac(c["mac"]))
    out = []
    for d in devices or []:
        if d.get("type") != "usw" and not d.get("port_table"):
            continue
        sw_mac = _mac(d.get("mac"))
        name = d.get("name") or d.get("model") or sw_mac
        for p in d.get("port_table") or []:
            idx = p.get("port_idx")
            if idx is None:
                continue
            out.append({"switch": name, "switch_mac": sw_mac, "port": int(idx), "name": p.get("name") or None,
                        "up": 1 if p.get("up") else 0, "speed": p.get("speed") or None,
                        "rx_bytes": p.get("rx_bytes"), "tx_bytes": p.get("tx_bytes"),
                        "rx_packets": p.get("rx_packets"), "tx_packets": p.get("tx_packets"),
                        "rx_errors": (p.get("rx_errors") or 0) + (p.get("rx_dropped") or 0) if p.get("rx_errors") is not None else None,
                        "tx_errors": (p.get("tx_errors") or 0) + (p.get("tx_dropped") or 0) if p.get("tx_errors") is not None else None,
                        "macs": sorted(on_port.get((sw_mac, int(idx)), []))})
    return out


def _js_array(text: str, key: str) -> list[int] | None:
    m = re.search(key + r"\s*:\s*\[([^\]]*)\]", text)
    if not m:
        return None
    return [int(x) for x in re.findall(r"-?\d+", m.group(1))]


def parse_easy_smart_stats(html: str, name: str) -> list[dict[str, Any]]:
    """TP-Link Easy Smart ``PortStatisticsRpm.htm``: ``all_info = {state:[..], link_status:[..], pkts:[..]}``.

    ``pkts`` holds four numbers per port: transmitted good, transmitted bad, received good, received bad.
    """
    state = _js_array(html, "state")
    link = _js_array(html, "link_status")
    pkts = _js_array(html, "pkts")
    m = re.search(r"max_port_num\s*=\s*(\d+)", html)
    if not pkts or not link:
        return []
    n = int(m.group(1)) if m else min(len(link), len(pkts) // 4)
    out = []
    for i in range(min(n, len(pkts) // 4, len(link))):
        tx_good, tx_bad, rx_good, rx_bad = pkts[4 * i:4 * i + 4]
        out.append({"switch": name, "switch_mac": None, "port": i + 1, "name": None,
                    "up": 1 if link[i] not in (0,) and (state is None or i >= len(state) or state[i]) else 0,
                    "speed": EASY_SMART_SPEED.get(link[i]), "rx_bytes": None, "tx_bytes": None,
                    "rx_packets": rx_good, "tx_packets": tx_good, "rx_errors": rx_bad, "tx_errors": tx_bad, "macs": []})
    return out


# ---------------------------------------------------------------- fetchers (network)

async def fetch_unifi(url: str, username: str, password: str, site: str, verify: bool) -> dict[str, Any]:
    url = url.rstrip("/")
    ssl = None if verify else False
    async with aiohttp.ClientSession(timeout=TIMEOUT, cookie_jar=aiohttp.CookieJar(unsafe=True)) as s:
        for login, prefix in (("/api/login", ""), ("/api/auth/login", "/proxy/network")):
            async with s.post(url + login, json={"username": username, "password": password}, ssl=ssl) as r:
                if r.status == 404:
                    continue
                if r.status != 200:
                    raise RuntimeError(f"UniFi rejected the login (HTTP {r.status})")
                csrf = r.headers.get("X-CSRF-Token")
            headers = {"X-CSRF-Token": csrf} if csrf else {}
            try:
                out = {}
                for key in ("device", "sta"):
                    async with s.get(f"{url}{prefix}/api/s/{site}/stat/{key}", headers=headers, ssl=ssl) as r:
                        out[key] = ((await r.json(content_type=None)) or {}).get("data") or []
                return out
            finally:
                async with s.post(url + ("/api/logout" if not prefix else "/api/auth/logout"), headers=headers, ssl=ssl):
                    pass
        raise RuntimeError("no UniFi login page at that address")


async def fetch_easy_smart(host: str, username: str, password: str) -> str:
    """Log in to an Easy Smart switch's web page, read the port statistics page, log out.

    These switches only speak plain HTTP and remember the login by the caller's address, so the password crosses
    the LAN unencrypted, as it does when you use their web page yourself.
    """
    base = f"http://{host}"
    headers = {"Referer": f"{base}/"}
    async with aiohttp.ClientSession(timeout=TIMEOUT, cookie_jar=aiohttp.CookieJar(unsafe=True)) as s:
        async with s.post(f"{base}/logon.cgi", data={"username": username, "password": password, "cpassword": "",
                                                     "logon": "Login"}, headers=headers) as r:
            await r.read()
        try:
            async with s.get(f"{base}/PortStatisticsRpm.htm", headers=headers) as r:
                html = await r.text(errors="replace")
            if "pkts" not in html:
                raise RuntimeError("the switch did not accept the login (or its page format is unknown)")
            return html
        finally:
            try:
                async with s.get(f"{base}/Logout.htm", headers=headers):
                    pass
            except aiohttp.ClientError:
                pass


# ---------------------------------------------------------------- collector

UnifiFetcher = Callable[..., Awaitable[dict[str, Any]]]
EasyFetcher = Callable[[str, str, str], Awaitable[str]]


class SwitchCollector:
    """Polls every configured switch source every ``POLL_SECONDS``."""

    POLL_SECONDS = 300

    def __init__(self, cfg_getter: Callable[[], Any], data_root, unifi_fetcher: UnifiFetcher = fetch_unifi,
                 easy_fetcher: EasyFetcher = fetch_easy_smart):
        self.cfg = cfg_getter
        self.data_root = data_root
        self.unifi_fetcher = unifi_fetcher
        self.easy_fetcher = easy_fetcher
        self.last_poll = 0.0
        self.status: dict[str, Any] = {"last_poll": None, "sources": {}}

    def sources(self) -> list[tuple[str, Any]]:
        cfg = self.cfg()
        out: list[tuple[str, Any]] = []
        if cfg.unifi.enabled and cfg.unifi.url:
            out.append(("unifi", cfg.unifi))
        out += [("tplink_easy_smart", d) for d in cfg.devices if d.kind == "tplink_easy_smart" and d.host]
        return out

    def due(self, now: float) -> bool:
        return bool(self.sources()) and now - self.last_poll >= self.POLL_SECONDS

    def poll(self, now: float | None = None) -> dict[str, Any]:
        now = now or time.time()
        self.last_poll = now
        cfg = self.cfg()
        rows: list[dict[str, Any]] = []
        for kind, src in self.sources():
            label = "UniFi controller" if kind == "unifi" else (src.name or src.host)
            try:
                if kind == "unifi":
                    pw = secrets.get_secret("unifi", secrets.service_ident("unifi", cfg))
                    if not pw:
                        raise RuntimeError("no password stored (Settings → UniFi)")
                    data = asyncio.run(self.unifi_fetcher(src.url, src.username, pw, src.site, src.verify_tls))
                    got = parse_unifi(data.get("device") or [], data.get("sta") or [])
                else:
                    pw = secrets.get_secret("device", secrets.device_ident(src.host, src.username))
                    if not pw:
                        raise RuntimeError("no password stored (Settings → Device logins)")
                    html = asyncio.run(self.easy_fetcher(src.host, src.username, pw))
                    got = parse_easy_smart_stats(html, src.name or src.host)
                    if not got:
                        jsonl.append(self.data_root, {"type": "switch_unparsed", "ts": now, "switch": label,
                                                      "raw": html[:4000]})
                        raise RuntimeError("logged in, but the statistics page format wasn't recognised (kept for a parser)")
                rows += got
                self.status["sources"][label] = {"ok": True, "ports": len(got), "error": None, "ts": now}
            except Exception as e:  # unreachable switch, bad login, API change
                log.warning("switch %s: %s", label, e)
                self.status["sources"][label] = {"ok": False, "ports": 0, "error": f"{type(e).__name__}: {e}", "ts": now}
        if rows:
            db.insert_many("switch_ports", [{"ts": now, **{k: r[k] for k in (
                "switch", "port", "name", "up", "speed", "rx_bytes", "tx_bytes", "rx_packets", "tx_packets",
                "rx_errors", "tx_errors")}, "macs": json.dumps(r["macs"])} for r in rows])
            jsonl.append(self.data_root, {"type": "switch_ports", "ts": now, "ports": rows})
        self.status["last_poll"] = now
        return self.status


# ---------------------------------------------------------------- graphs

def _delta(a: int | None, b: int | None) -> int | None:
    if a is None or b is None:
        return None
    return b - a if b >= a else None     # counter reset or 32-bit wrap: skip that interval


def series(rows: list[dict[str, Any]], devices: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Per-port rates and period totals. Bytes (Mbit/s) where the switch reports them, otherwise packets/s."""
    from ..names import display_name
    by: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for r in rows:
        by.setdefault((r["switch"], r["port"]), []).append(r)
    any_bytes = any(r.get("rx_bytes") is not None for r in rows)
    out: dict[str, list[list[float]]] = {}
    ports = []
    for (sw, port), rs in sorted(by.items()):
        rs.sort(key=lambda r: r["ts"])
        macs = json.loads(rs[-1].get("macs") or "[]")
        devs = [{"mac": m, "name": display_name(devices.get(m), m)} for m in macs]
        label = f"{sw} port {port}" + (f" ({devs[0]['name']}{' +' + str(len(devs) - 1) if len(devs) > 1 else ''})" if devs else "")
        tot = {"rx_bytes": 0, "tx_bytes": 0, "rx_packets": 0, "tx_packets": 0, "errors": 0}
        has = {k: False for k in tot}
        pts = []
        for a, b in zip(rs, rs[1:]):
            dt = b["ts"] - a["ts"]
            if dt <= 0 or dt > 1800:
                continue
            for k in ("rx_bytes", "tx_bytes", "rx_packets", "tx_packets"):
                d = _delta(a.get(k), b.get(k))
                if d is not None:
                    tot[k] += d
                    has[k] = True
            e = sum(x for x in (_delta(a.get("rx_errors"), b.get("rx_errors")), _delta(a.get("tx_errors"), b.get("tx_errors")))
                    if x is not None)
            tot["errors"] += e
            if any_bytes:
                d = [_delta(a.get(k), b.get(k)) for k in ("rx_bytes", "tx_bytes")]
                if None not in d:
                    pts.append([b["ts"], round(sum(d) * 8 / dt / 1e6, 3)])
            else:
                d = [_delta(a.get(k), b.get(k)) for k in ("rx_packets", "tx_packets")]
                if None not in d:
                    pts.append([b["ts"], round(sum(d) / dt, 1)])
        if any(p[1] for p in pts):
            out[label] = pts
        last = rs[-1]
        ports.append({"switch": sw, "port": port, "name": last.get("name"), "up": last.get("up"), "speed": last.get("speed"),
                      "devices": devs, "errors": tot["errors"],
                      "packets": tot["rx_packets"] + tot["tx_packets"],
                      **{k: (tot[k] if has[k] else None) for k in ("rx_bytes", "tx_bytes", "rx_packets", "tx_packets")}})
    ports.sort(key=lambda p: -((p["rx_bytes"] or 0) + (p["tx_bytes"] or 0) + (p["rx_packets"] or 0) + (p["tx_packets"] or 0)))
    note = ("Counted at the switch port, so a port with several devices behind it (another switch, a powerline adapter) "
            "shows their combined traffic. Received = from the device into the switch.")
    if any(r.get("rx_bytes") is None for r in rows):
        note += " TP-Link Easy Smart switches only count packets, not bytes, and don't say which devices are on a port."
    return {"series": out, "unit": "Mbit/s" if any_bytes else "packets/s", "ports": [p for p in ports if p["up"] or p["packets"]],
            "note": note}
