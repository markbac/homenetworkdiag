"""devolo Magic (G.hn) powerline collector (SW-04), read-only.

Uses the devolo device API (``devolo_plc_api``, the library Home Assistant uses). Any one adapter
returns the whole powerline network: every adapter, which one is wired to the router, the devices
bridged behind each adapter, and the PHY data rate between every pair of adapters.
No password is needed for this read; the API's write calls (pairing, LEDs, restart) are never used.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

from ..store import db, jsonl

log = logging.getLogger("meshlens.devolo")

Fetcher = Callable[[str], Awaitable[dict[str, Any]]]


def _mac(raw: str) -> str:
    raw = (raw or "").replace(":", "").replace("-", "").lower()
    return ":".join(raw[i:i + 2] for i in range(0, 12, 2)) if len(raw) == 12 else raw


async def fetch_overview(ip: str) -> dict[str, Any]:
    """Ask one adapter for the network overview. Returns plain dicts."""
    from devolo_plc_api import Device

    async with Device(ip=ip) as d:
        if not d.plcnet:
            raise RuntimeError(f"{ip} has no powerline API")
        ov = await d.plcnet.async_get_network_overview()
        return {
            "asked": ip,
            "devices": [{
                "mac": _mac(x.mac_address), "ip": x.ipv4_address or None, "name": x.user_device_name or None,
                "product": x.product_name, "product_id": x.product_id, "firmware": x.friendly_version,
                "attached_to_router": bool(x.attached_to_router), "local": int(x.topology) == 1,
                "bridged": sorted({_mac(b) for b in x.bridged_devices}),
            } for x in ov.devices],
            "rates": [{"src": _mac(r.mac_address_from), "dst": _mac(r.mac_address_to),
                       "tx_mbps": int(r.tx_rate), "rx_mbps": int(r.rx_rate)} for r in ov.data_rates],
        }


class DevoloCollector:
    def __init__(self, cfg, data_root, fetcher: Fetcher = fetch_overview):
        self.cfg = cfg
        self.data_root = data_root
        self.fetcher = fetcher
        self.status: dict[str, Any] = {"last_poll": None, "error": None, "adapters": 0, "asked": None}
        self.last_poll = 0.0

    def candidates(self) -> list[str]:
        """Addresses to ask: configured ones first, then adapters seen before, then devolo devices on the LAN."""
        out: list[str] = list(self.cfg.adapters)
        out += [a["ip"] for a in db.plc_adapters() if a.get("ip")]
        out += [d["ip"] for d in db.devices() if d.get("ip") and "devolo" in (d.get("vendor") or "").lower()]
        seen, uniq = set(), []
        for ip in out:
            if ip not in seen:
                seen.add(ip)
                uniq.append(ip)
        return uniq

    def due(self, now: float) -> bool:
        return self.cfg.enabled and now - self.last_poll >= self.cfg.poll_seconds

    def poll(self, now: float | None = None) -> dict[str, Any]:
        now = now or time.time()
        self.last_poll = now
        cands = self.candidates()
        if not cands:
            self.last_poll = now - self.cfg.poll_seconds + 60   # try again in a minute (discovery may find them)
            self.status.update(last_poll=now, error="no devolo adapters known yet (add an address in Settings → Powerline)")
            return self.status
        errors = []
        for ip in cands[:4]:
            try:
                ov = asyncio.run(asyncio.wait_for(self.fetcher(ip), 30))
            except Exception as e:  # unreachable adapter, timeout, API change
                errors.append(f"{ip}: {type(e).__name__}: {e}")
                continue
            self.store(ov, now)
            self.status.update(last_poll=now, error=None, adapters=len(ov["devices"]), asked=ip)
            return self.status
        self.status.update(last_poll=now, error="; ".join(errors))
        return self.status

    def store(self, ov: dict[str, Any], now: float) -> None:
        db.store_plc(ov["devices"], ov["rates"], now)
        try:
            clients = {s["mac"]: s["unit"] for s in db.latest_stations() if now - s["ts"] <= 600}
            found = loop_evidence(ov["devices"], clients, db.units(), now)
            if found:
                import hashlib
                import json
                db.insert_events([{"ts": now, "unit": f["adapter"], "kind": "l2_loop", "mac": None, "iface": None,
                                   "detail": f, "raw_hash": hashlib.sha1(json.dumps([now, f], sort_keys=True).encode()).hexdigest()}
                                  for f in found])
                log.warning("possible network loop behind powerline: %s", found)
        except Exception:  # never let the check break powerline collection
            log.exception("loop check failed")
        for a in ov["devices"]:
            # The adapters are LAN devices too; give them their devolo names.
            if db.device(a["mac"]):
                db.set_device_fields(a["mac"], model=a.get("product"), dtype="powerline")
                if a.get("name"):
                    db.merge_names(a["mac"], {"devolo": a["name"]})
        jsonl.append(self.data_root, {"type": "devolo", "ts": now, **ov})


def loop_evidence(adapters: list[dict[str, Any]], wifi_clients: dict[str, str], units: list[dict[str, Any]],
                  now: float | None = None) -> list[dict[str, Any]]:
    """Signs of a second path (a loop) behind a powerline adapter that isn't the one wired to the router.

    - the main router's own addresses seen behind it (the router is upstream, so they should only appear behind
      the router-side adapter);
    - devices that are Wi-Fi clients of a unit *not* behind that adapter seen behind it (their traffic is coming
      back round through the wired side);
    - a mesh node behind it that is also linked to the mesh over Wi-Fi (two paths unless AiMesh has switched its
      wireless backhaul off).
    """
    now = now or time.time()
    router = next((u for u in units if u.get("role") == "router"), None)
    router_macs = set([router["mac"], *(router.get("macs") or [])]) if router else set()
    unit_of: dict[str, str] = {}
    for u in units:
        for m in [u["mac"], *(u.get("macs") or [])]:
            unit_of[m] = u["mac"]
    names = {u["mac"]: u.get("alias") or u.get("model") or u["mac"] for u in units}
    out = []
    for a in adapters:
        if a.get("attached_to_router"):
            continue
        bridged = {_mac(b) for b in a.get("bridged") or []}
        # Units legitimately behind this adapter (their own clients' traffic passes it). Never the router: it is
        # upstream of every non-router adapter, so its addresses here are themselves the sign of a loop.
        units_here = {unit_of[m] for m in bridged if m in unit_of and m not in router_macs}
        r = sorted(bridged & router_macs)
        w = sorted(m for m in bridged if m in wifi_clients and wifi_clients[m] not in units_here and m not in unit_of)
        nodes = []
        for u in units:
            if u["mac"] in units_here and u.get("role") != "router":
                live = [b for b in u.get("backhaul") or [] if now - (b.get("ts") or 0) <= 900]
                if live:
                    nodes.append({"unit": u["mac"], "name": names[u["mac"]],
                                  "wifi_backhaul": [f"{b.get('band')} GHz" for b in live]})
        if r or w or nodes:
            out.append({"adapter": a["mac"], "adapter_name": a.get("name") or a.get("product") or a["mac"],
                        "router_macs": r, "wifi_clients": w[:20], "wifi_client_units": sorted({names.get(wifi_clients[m], wifi_clients[m]) for m in w}),
                        "nodes": nodes})
    return out


def links(adapters: list[dict[str, Any]], rates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per adapter pair: rate each way (Mbit/s) and the slower of the two."""
    by = {(r["src"], r["dst"]): r for r in rates}
    names = {a["mac"]: a.get("name") or a.get("product") or a["mac"] for a in adapters}
    out, done = [], set()
    for (s, d), r in by.items():
        key = tuple(sorted((s, d)))
        if key in done:
            continue
        done.add(key)
        back = by.get((d, s))
        a_to_b = r["tx_mbps"]
        b_to_a = back["tx_mbps"] if back else r["rx_mbps"]
        out.append({"a": s, "b": d, "a_name": names.get(s, s), "b_name": names.get(d, d),
                    "a_to_b": a_to_b, "b_to_a": b_to_a, "slowest": min(a_to_b, b_to_a)})
    return sorted(out, key=lambda x: x["slowest"])
