"""Topology map (TOP-01, MAP-01/02): what is connected to what, from verified sources only.

- Internet -> main router.
- Mesh nodes under their parent: a node's backhaul radio appearing as a client of another unit gives
  a wireless backhaul with its band, signal and rate; otherwise the node's preferred parent; otherwise
  it is shown as wired (or unknown) under the router.
- devolo powerline: the adapter wired to the router hangs off the router; other adapters hang off it
  with the measured powerline rates. Devices bridged behind a non-router adapter (including a mesh
  node using powerline as backhaul) are placed under that adapter.
- Every other device under the unit it is connected to (Wi-Fi band and signal, or wired).
"""
from __future__ import annotations

import time
from typing import Any

from .names import display_name


def _tail(mac: str) -> str:
    return mac[3:] if mac else ""


def build(units: list[dict[str, Any]], devices: list[dict[str, Any]], stations: list[dict[str, Any]],
          plc: list[dict[str, Any]], plc_rates: list[dict[str, Any]], health: dict[str, Any] | None = None,
          now: float | None = None) -> dict[str, Any]:
    now = now or time.time()
    health = health or {}
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []

    def add(id_: str, kind: str, label: str, parent: str | None, link: dict[str, Any] | None = None, **extra: Any):
        nodes[id_] = {"id": id_, "kind": kind, "label": label, "parent": parent, "link": link or {}, **extra}

    # Which unit owns an address (main MAC, radios, backhaul STAs; ASUS varies byte 0, so match bytes 1-5 too).
    owner: dict[str, str] = {}
    owner_tail: dict[str, str] = {}
    for u in units:
        for m in [u["mac"], *(u.get("macs") or [])]:
            owner[m] = u["mac"]
            owner_tail[_tail(m)] = u["mac"]

    def unit_of(mac: str | None) -> str | None:
        if not mac:
            return None
        return owner.get(mac) or owner_tail.get(_tail(mac))

    router = next((u for u in units if u.get("role") == "router"), None)
    add("internet", "internet", "Internet", None)
    if not router:
        return {"nodes": list(nodes.values()), "generated": now}
    add(router["mac"], "router", router.get("alias") or "Router", "internet", {"type": "wan"},
        model=router.get("model"), ip=router.get("ip"), health=(health.get("units") or {}).get(router["mac"]))

    # Latest station sample per client MAC (fresh ones only).
    latest: dict[str, dict[str, Any]] = {}
    for s in stations:
        if now - s["ts"] < 900 and (s["mac"] not in latest or s["ts"] > latest[s["mac"]]["ts"]):
            latest[s["mac"]] = s

    # Powerline adapters.
    plc_by_mac = {a["mac"]: a for a in plc}
    rate = {(r["src"], r["dst"]): r for r in plc_rates}
    head = next((a for a in plc if a.get("attached_to_router")), None)
    behind_adapter: dict[str, str] = {}      # device MAC (and tail) -> adapter MAC
    for a in plc:
        if a is head:
            continue
        for b in a.get("bridged") or []:
            behind_adapter[b] = a["mac"]
            behind_adapter["tail:" + _tail(b)] = a["mac"]

    def adapter_behind(mac: str) -> str | None:
        return behind_adapter.get(mac) or behind_adapter.get("tail:" + _tail(mac))

    if head:
        add(head["mac"], "powerline", head.get("name") or "Powerline", router["mac"], {"type": "ethernet"},
            model=head.get("product"), ip=head.get("ip"))
        for a in plc:
            if a is head:
                continue
            fwd, back = rate.get((head["mac"], a["mac"])), rate.get((a["mac"], head["mac"]))
            down = fwd["tx_mbps"] if fwd else None
            up = back["tx_mbps"] if back else (fwd["rx_mbps"] if fwd else None)
            add(a["mac"], "powerline", a.get("name") or "Powerline", head["mac"],
                {"type": "powerline", "down_mbps": down, "up_mbps": up,
                 "slowest": min(x for x in (down, up) if x is not None) if (down or up) else None},
                model=a.get("product"), ip=a.get("ip"))
        # Rates between non-head adapters are shown as extra links on the map.
        for (s, d), r in rate.items():
            if head["mac"] not in (s, d) and s < d:
                back = rate.get((d, s))
                edges.append({"a": s, "b": d, "type": "powerline", "a_to_b": r["tx_mbps"],
                              "b_to_a": back["tx_mbps"] if back else r["rx_mbps"]})

    # Mesh nodes.
    for u in units:
        if u is router:
            continue
        parent, link = router["mac"], {"type": "unknown"}
        bh = [latest[m] for m in (u.get("macs") or []) if m in latest and unit_of(latest[m]["unit"]) != u["mac"]]
        reported = [b for b in (u.get("backhaul") or []) if now - (b.get("ts") or 0) < 1800 and unit_of(b["parent"])]
        if reported:
            # Best link first: 6 > 5 > 2.4 GHz, then signal. A node may use several bands (or parents) at once.
            reported.sort(key=lambda b: (-float(b["band"] or 0), -(b["rssi"] if b["rssi"] is not None else -999)))
            parent = unit_of(reported[0]["parent"]) or router["mac"]
            link = {"type": "wireless", "band": reported[0]["band"], "rssi": reported[0]["rssi"],
                    "bands": [{"band": b["band"], "rssi": b["rssi"], "parent": unit_of(b["parent"])} for b in reported]}
        elif bh:
            s = max(bh, key=lambda x: x["ts"])
            parent = unit_of(s["unit"]) or router["mac"]
            link = {"type": "wireless", "band": s.get("band"), "rssi": s.get("rssi"), "tx_rate": s.get("tx_rate"),
                    "rx_rate": s.get("rx_rate")}
        elif adapter_behind(u["mac"]) and adapter_behind(u["mac"]) in nodes:
            parent, link = adapter_behind(u["mac"]), {"type": "ethernet", "via": "powerline"}
        elif u.get("via_unit") and unit_of(u["via_unit"]) and unit_of(u["via_unit"]) != u["mac"]:
            parent, link = unit_of(u["via_unit"]), {"type": "wireless (through this unit)"}
        elif u.get("parent_hint") and unit_of(u["parent_hint"]):
            parent, link = unit_of(u["parent_hint"]), {"type": "wireless (preferred parent)"}
        elif u.get("reachable"):
            link = {"type": "ethernet or unknown"}
        add(u["mac"], "node", u.get("alias") or u.get("model") or u["mac"], parent, link, model=u.get("model"),
            ip=u.get("ip"), reachable=u.get("reachable"), health=(health.get("units") or {}).get(u["mac"]))
    # Nodes whose parent is another node must exist; fall back to the router.
    for n in nodes.values():
        if n["parent"] and n["parent"] not in nodes:
            n["parent"] = router["mac"]

    # Clients.
    dev_health = health.get("devices") or {}
    for d in devices:
        mac = d["mac"]
        if mac in nodes or unit_of(mac) or mac in plc_by_mac:
            continue
        s = latest.get(mac)
        online = bool(s) or (d.get("last_seen") and now - d["last_seen"] < 900)
        if not online:
            continue
        label = display_name(d, mac)
        common = {"mac": mac, "ip": d.get("ip"), "dtype": d.get("user_type") or d.get("dtype"),
                  "vendor": d.get("vendor"), "watched": d.get("watched"),
                  "health": (dev_health.get(mac) or {}).get("score")}
        adapter = adapter_behind(mac)
        if s:
            add(mac, "device", label, unit_of(s["unit"]) or router["mac"],
                {"type": "wifi", "band": s.get("band"), "rssi": s.get("rssi"), "tx_rate": s.get("tx_rate")}, **common)
        elif adapter and adapter in nodes:
            add(mac, "device", label, adapter, {"type": "ethernet", "via": "powerline"}, **common)
        else:
            parent = unit_of(d.get("unit")) or router["mac"]
            add(mac, "device", label, parent, {"type": "ethernet" if d.get("wired") else (
                f"wifi {d['band']} GHz" if d.get("band") else "unknown")}, **common)

    return {"nodes": list(nodes.values()), "edges": edges, "generated": now}
