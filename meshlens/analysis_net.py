"""Issue rules for powerline, latency monitors, speed tests and open ports.

Inputs (all optional) in ``net``: ``pings`` (ping_minutes rows), ``speeds`` (speed_tests rows),
``plc_adapters``, ``plc_rates``, ``open_ports``, ``inventory`` (inventory events),
``monitor_cfg`` (MonitorConfig), ``devolo_cfg`` (DevoloConfig).
"""
from __future__ import annotations

import statistics
import time
from collections import defaultdict
from typing import Any

from .analysis import _issue
from .collectors.devolo import links

LOCAL_P95_MS = 25        # this PC to the router; wired is ~1 ms, good Wi-Fi under 10 ms
LOSS_PCT = 1.0
SPIKE_MS = 150


def _agg(rows: list[dict[str, Any]]) -> dict[str, Any]:
    sent = sum(r["sent"] or 0 for r in rows)
    lost = sum(r["lost"] or 0 for r in rows)
    avgs = [r["avg_ms"] for r in rows if r.get("avg_ms") is not None]
    p95s = [r["p95_ms"] for r in rows if r.get("p95_ms") is not None]
    jit = [r["jitter_ms"] for r in rows if r.get("jitter_ms") is not None]
    return {"sent": sent, "lost": lost, "loss_pct": round(100 * lost / sent, 2) if sent else None,
            "avg_ms": round(statistics.fmean(avgs), 1) if avgs else None,
            "p95_ms": round(statistics.median(p95s), 1) if p95s else None,
            "jitter_ms": round(statistics.fmean(jit), 1) if jit else None,
            "spike_minutes": [r["ts"] for r in rows if (r.get("max_ms") or 0) >= SPIKE_MS],
            "label": rows[-1].get("label") if rows else None, "kind": rows[-1].get("kind") if rows else None}


def _hm(ts: float) -> str:
    return time.strftime("%d %b %H:%M", time.localtime(ts))


def net_issues(net: dict[str, Any], devs: dict[str, dict[str, Any]], now: float) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    # ------------------------------------------------ powerline
    dcfg = net.get("devolo_cfg")
    if net.get("plc_adapters") and net.get("plc_rates") and dcfg:
        for ln in links(net["plc_adapters"], net["plc_rates"]):
            if ln["slowest"] >= dcfg.slow_mbps:
                continue
            bad = ln["slowest"] < dcfg.bad_mbps
            out.append(_issue(
                "powerline_slow", "high" if bad else "medium", "network", f"{ln['a']}|{ln['b']}",
                f"Slow powerline link: {ln['a_name']} ↔ {ln['b_name']}",
                f"{ln['a_name']} → {ln['b_name']}: {ln['a_to_b']} Mbit/s, {ln['b_name']} → {ln['a_name']}: "
                f"{ln['b_to_a']} Mbit/s (radio rate; real throughput is roughly half).",
                "Powerline speed depends on the house wiring between the two sockets: different circuits, long runs, "
                "extension leads and surge protectors, and electrical noise from chargers, LED drivers and appliances.",
                ["Plug both adapters straight into wall sockets, not into extension leads or power strips "
                 "(use the adapter's own pass-through socket for other plugs).",
                 "Move them away from phone/laptop chargers, LED lamps, fridges and washing machines, or try another socket.",
                 "If one side is always slow, that path cannot carry much: use a cable, or let the mesh serve that spot.",
                 "Update the adapters with the devolo Home Network app."]))

    # ------------------------------------------------ latency monitors
    mcfg = net.get("monitor_cfg")
    by_target: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in net.get("pings") or []:
        by_target[r["target"]].append(r)
    agg = {t: _agg(rows) for t, rows in by_target.items() if rows}
    router = next((a for a in agg.values() if a["kind"] == "router"), None)
    router_ok = router is not None and (router["loss_pct"] or 0) < LOSS_PCT and (router["p95_ms"] or 0) < LOCAL_P95_MS
    if router and not router_ok and router["sent"] >= 30:
        out.append(_issue(
            "local_latency", "medium", "network", "router", "Lag between this PC and the router",
            f"Loss {router['loss_pct']}%, typical worst-case (p95) {router['p95_ms']} ms, jitter {router['jitter_ms']} ms.",
            "These pings never leave your home, so the delay is on the way from the MeshLens PC to the router: "
            "its own Wi-Fi link, a mesh hop, a busy radio or a cable/switch problem.",
            ["If the MeshLens PC is on Wi-Fi, a cable to the router makes its measurements cleaner.",
             "Check the Wi-Fi issues and radio health above for the unit this PC uses."]))
    for target, a in agg.items():
        if a["sent"] < 30:
            continue
        kind, label = a["kind"], a["label"] or target
        loss = a["loss_pct"] or 0
        if kind in ("internet", "extra") and router_ok and (loss >= LOSS_PCT or len(a["spike_minutes"]) >= 5):
            isp = kind == "isp"
            out.append(_issue(
                "isp_problem", "high" if loss >= 3 else "medium", "network", target,
                f"{'Your line to the ISP' if isp else 'The internet'} drops or delays packets ({label})",
                f"Loss {loss}%, average {a['avg_ms']} ms, jitter {a['jitter_ms']} ms"
                + (f", {len(a['spike_minutes'])} minutes with spikes over {SPIKE_MS} ms (last {_hm(a['spike_minutes'][-1])})"
                   if a["spike_minutes"] else "") + ". Inside your home the same pings were clean.",
                "Your home network is fine at the same time, so the problem is on the broadband line or at the ISP"
                + (" (this is the first router on the ISP's side)." if isp else "."),
                ["Check whether it lines up with heavy use at home (a large upload or download makes this worse: "
                 "see Bufferbloat).",
                 "If not, note the times and report them to your ISP; the ISP-hop target shows it is on their side.",
                 "Restarting the router can clear a bad line sync."]))
        if kind == "node" and loss >= 2:
            out.append(_issue(
                "node_loss", "medium", "unit", target, f"Pings to mesh unit {label} are lost",
                f"Loss {loss}%, average {a['avg_ms']} ms.",
                "The unit itself or its backhaul is struggling; its clients will feel it as lag.",
                ["Check the unit's backhaul signal on the Topology tab; move it closer or wire it."]))
        if kind == "device" and loss >= 10 and router_ok:
            out.append(_issue(
                "device_loss", "low", "device", next((m for m, d in devs.items() if d.get("ip") == target), target),
                f"{label} misses pings", f"Loss {loss}%, average {a['avg_ms']} ms.",
                "Usually the device's Wi-Fi power saving (phones, tablets, consoles in rest mode). If it is a PC or "
                "console in use at the time, it is a real dropout.",
                ["Compare with when it is being used; see its Wi-Fi journey for kicks or roams at the same times."]))

    # ------------------------------------------------ speed tests and bufferbloat
    speeds = [s for s in net.get("speeds") or [] if not s.get("error")]
    if speeds:
        last = speeds[-1]
        g = last.get("grade")
        if g in ("C", "D", "F"):
            out.append(_issue(
                "bufferbloat", "high" if g in ("D", "F") else "medium", "network", "bufferbloat",
                f"Bufferbloat: grade {g}",
                f"Idle latency {last['idle_ms']} ms, during download {last['down_loaded_ms']} ms, during upload "
                f"{last['up_loaded_ms']} ms ({_hm(last['ts'])}).",
                "When the line is full, the router or modem queues packets, so games and calls lag whenever someone "
                "downloads, uploads (cloud backup, photo sync) or streams.",
                ["Turn on the router's QoS so it keeps latency low under load: ASUS Adaptive QoS → QoS → enable, type "
                 "Adaptive or Traditional, with the bandwidth set to about 90% of your measured speeds.",
                 "Schedule big uploads (backups, photo sync) for the night."], last["ts"], last["ts"]))
        if mcfg and (mcfg.expected_down_mbps or mcfg.expected_up_mbps):
            recent = speeds[-3:]
            dn = statistics.median([s["down_mbps"] for s in recent if s.get("down_mbps") is not None] or [0])
            up = statistics.median([s["up_mbps"] for s in recent if s.get("up_mbps") is not None] or [0])
            low = []
            if mcfg.expected_down_mbps and dn < 0.6 * mcfg.expected_down_mbps:
                low.append(f"download {dn:.0f} of {mcfg.expected_down_mbps:.0f} Mbit/s")
            if mcfg.expected_up_mbps and up < 0.6 * mcfg.expected_up_mbps:
                low.append(f"upload {up:.0f} of {mcfg.expected_up_mbps:.0f} Mbit/s")
            if low:
                out.append(_issue(
                    "speed_low", "medium", "network", "speed", "Internet slower than your plan",
                    "Typical of the last tests: " + ", ".join(low) + ".",
                    "Either the line delivers less than the plan, or the MeshLens PC's own connection limits the test.",
                    ["Run a test with the MeshLens PC on a cable if it is on Wi-Fi.",
                     "If wired tests are also slow at several times of day, report it to your ISP."]))
    for s in (net.get("speeds") or [])[-1:]:
        if s.get("error"):
            out.append(_issue("speedtest_failed", "low", "network", "speedtest", "The last speed test failed",
                              s["error"], "The test server could not be reached or the connection dropped mid-test.",
                              ["Run it again from the Tests tab; check the internet is up."], s["ts"], s["ts"]))

    # ------------------------------------------------ open ports
    risky: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in net.get("open_ports") or []:
        if p.get("risk"):
            risky[p["mac"]].append(p)
    for mac, ps in risky.items():
        d = devs.get(mac) or {}
        name = d.get("user_name") or d.get("name") or mac
        out.append(_issue(
            "risky_port", "low", "device", mac, f"{name} has a service worth checking",
            "; ".join(f"port {p['port']} {p.get('service') or ''}: {p['risk']}" for p in ps) + ".",
            "These services are fine on the right device with a password set, but are a common way in on gadgets "
            "with default settings. This only looks at your home network, not the internet.",
            ["If you don't use it, turn the service off in the device's settings.",
             "If you do, make sure it has its own strong password and is not forwarded on the router."]))
    for e in net.get("inventory") or []:
        det = e.get("detail") or {}
        if e["kind"] == "port_opened" and not det.get("first_scan"):
            d = devs.get(e["mac"]) or {}
            out.append(_issue(
                "new_open_port", "info", "device", e["mac"],
                f"New open port on {d.get('user_name') or d.get('name') or e['mac']}",
                f"Port {det.get('port')} ({det.get('service') or 'unknown service'}) opened, first seen {_hm(e['ts'])}.",
                "A device started offering a new service: a software update, a setting turned on, or new software.",
                ["If you didn't expect it, check the device's settings or recent updates."], e["ts"], e["ts"]))
    return out
