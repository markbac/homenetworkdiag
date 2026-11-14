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
from .names import display_name

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
    all_speeds = [s for s in net.get("speeds") or [] if not s.get("error")]
    router_speeds = [s for s in all_speeds if s.get("location") == "router"]
    pc_speeds = [s for s in all_speeds if (s.get("location") or "pc") == "pc"]
    # The line itself is best measured on the router; fall back to the PC's tests.
    speeds = router_speeds if router_speeds and now - router_speeds[-1]["ts"] < 7 * 86400 else pc_speeds
    graded = [s for s in all_speeds if s.get("grade")]
    if speeds:
        last = graded[-1] if graded else speeds[-1]
        g = last.get("grade")
        where = "on the router" if last.get("location") == "router" else "from the MeshLens PC"
        if g in ("C", "D", "F"):
            out.append(_issue(
                "bufferbloat", "high" if g in ("D", "F") else "medium", "network", "bufferbloat",
                f"Bufferbloat: grade {g}",
                f"Measured {where}: idle latency {last['idle_ms']} ms, during download {last['down_loaded_ms']} ms, "
                f"during upload {last['up_loaded_ms']} ms ({_hm(last['ts'])}).",
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
    if router_speeds and pc_speeds and now - pc_speeds[-1]["ts"] < 2 * 86400 and now - router_speeds[-1]["ts"] < 30 * 86400:
        line, pc = router_speeds[-1], pc_speeds[-1]
        if line.get("down_mbps") and pc.get("down_mbps") is not None and pc["down_mbps"] < 0.5 * line["down_mbps"]:
            out.append(_issue(
                "pc_path_slow", "medium", "network", "pc_path",
                "The MeshLens PC gets much less than the internet line",
                f"Router test: {line['down_mbps']:.0f}/{line['up_mbps'] or 0:.0f} Mbit/s; test from this PC: "
                f"{pc['down_mbps']:.0f}/{pc['up_mbps'] or 0:.0f} Mbit/s.",
                "The line is fine; the path between the PC and the router (its Wi-Fi, a powerline link or a slow switch "
                "port) is the limit. Other devices on the same path are limited the same way.",
                ["Check the PC's path on the Topology tab: a powerline link shows its speed on the map.",
                 "A direct cable to the router (or a mesh unit with a wired backhaul) removes the bottleneck.",
                 "Treat speed tests from this PC as a test of its path, and the router test as the line."]))
    for s in (net.get("speeds") or [])[-1:]:
        if s.get("error"):
            out.append(_issue("speedtest_failed", "low", "network", "speedtest", "The last speed test failed",
                              s["error"], "The test server could not be reached or the connection dropped mid-test.",
                              ["Run it again from the Tests tab; check the internet is up."], s["ts"], s["ts"]))

    # ------------------------------------------------ the MeshLens PC as a sensor
    acfg = net.get("analysis_cfg")
    lan = net.get("host_lan") or []
    if acfg and len(lan) >= 5:
        by_ad: dict[str, list[float]] = defaultdict(list)
        for r in lan:
            by_ad[r["adapter"]].append((r["bcast_pps"] or 0) + (r["mcast_pps"] or 0))
        for ad, vals in by_ad.items():
            typical = statistics.median(vals)
            if typical >= acfg.flood_pps:
                worst = max(vals)
                out.append(_issue(
                    "lan_flood", "high" if typical >= 3 * acfg.flood_pps else "medium", "network", ad,
                    "Lots of broadcast and multicast traffic on the network",
                    f"The MeshLens PC ({ad}) receives about {typical:.0f} broadcast/multicast packets a second "
                    f"(peak {worst:.0f}). A quiet home network is usually under 30.",
                    "Every device has to receive and look at broadcast and multicast packets, and on Wi-Fi they go out at the "
                    "slowest rate, using airtime for everyone. A single misbehaving device (a network scanner, a camera, a "
                    "looping cable) can cause this, or IGMP snooping being off.",
                    ["Unplug or power off suspect devices one at a time and watch the graph (Graphs → Broadcast and "
                     "multicast); network scanners such as Fing/Domotz boxes are common culprits.",
                     "Turn on IGMP snooping (LAN → IPTV) if casting and IPTV still work afterwards.",
                     "Check for a network loop: two cables between the same switches, or powerline adapters bridged twice."]))
    air = net.get("air") or []
    if acfg and air:
        latest_ts = max(r["ts"] for r in air)
        util: dict[tuple, list[int]] = defaultdict(list)
        for r in air:
            if r.get("own_unit") and r.get("util_pct") is not None:
                util[(r["own_unit"], r["band"], r["channel"])].append(r["util_pct"])
        unit_names = net.get("unit_names") or {}
        for (unit, band, ch), vals in util.items():
            typical = statistics.median(vals)
            if typical >= acfg.channel_busy_pct and len(vals) >= 2:
                out.append(_issue(
                    "channel_busy", "high" if typical >= 75 else "medium", "unit", unit,
                    f"{unit_names.get(unit, unit)} {band} GHz channel {ch} is {typical:.0f}% busy",
                    f"The access point itself reports its channel busy {typical:.0f}% of the time (peak {max(vals)}%), "
                    "measured by the MeshLens PC's air survey.",
                    "The air is shared by every device and every nearby network on the channel. Above about 50% devices "
                    "wait to transmit, which shows as lag and slow speeds; slow or distant devices use the most airtime.",
                    (["Move devices that can use 5 GHz off 2.4 GHz; 2.4 GHz should carry only the things that need it.",
                      "Use 20 MHz width on 2.4 GHz and the least-used of channels 1, 6 and 11.",
                      "Smart-home devices on a separate IoT network don't reduce airtime on their own; fewer chatty "
                      "devices does."] if band == "2.4" else
                     ["Try another channel (the air survey shows which ones neighbours use).",
                      "Check which devices are connected at low rates (Analysis → device health): they use the most airtime."])))
        latest = [r for r in air if r["ts"] == latest_ts]
        neigh: dict[tuple, list[str]] = defaultdict(list)
        own_ch = {(r["band"], r["channel"]) for r in latest if r.get("own_unit")}
        for r in latest:
            if not r.get("own_unit") and (r.get("signal_pct") or 0) >= 40 and (r["band"], r["channel"]) in own_ch:
                neigh[(r["band"], r["channel"])].append(r.get("ssid") or r["bssid"])
        for (band, ch), names in neigh.items():
            if len(names) >= acfg.crowded_neighbours:
                out.append(_issue(
                    "crowded_channel", "low", "network", f"{band}-{ch}", f"{len(names)} neighbouring networks on your {band} GHz channel {ch}",
                    "Heard strongly by the MeshLens PC: " + ", ".join(sorted(set(names))[:8]) + ".",
                    "Networks on the same channel take turns with yours.",
                    ["Pick a channel with fewer strong neighbours (Router → This PC lists them)."]))

    # ------------------------------------------------ unit CPU, memory, temperature, connections, storage, restarts
    us = net.get("unit_samples") or []
    names = net.get("unit_names") or {}
    if acfg and us:
        per: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in us:
            per[r["unit"]].append(r)
        for unit, rs in per.items():
            name = names.get(unit, unit)

            def med(k: str) -> float | None:
                v = [r[k] for r in rs if r.get(k) is not None]
                return statistics.median(v) if len(v) >= 3 else None
            cpu, core, mem, temp = med("cpu_pct"), med("max_core_pct"), med("mem_used_pct"), med("temp_c")
            if (cpu is not None and cpu >= acfg.unit_cpu_pct) or (core is not None and core >= 95):
                one = cpu is not None and cpu < acfg.unit_cpu_pct
                out.append(_issue(
                    "unit_cpu_high", "medium", "unit", unit, f"{name}'s processor is very busy",
                    f"Typical CPU {cpu:.0f}%" + (f", busiest core {core:.0f}%" if core is not None else "") + ".",
                    ("One core is maxed out: on these units that is usually the core handling network packets, so "
                     "traffic through the unit queues." if one else
                     "A busy processor slows routing and Wi-Fi handling for everything on the unit.") +
                    " Traffic Analyzer, AiProtection and QoS add load on the main router.",
                    ["Restart the unit if it has been up for weeks.", "Update the firmware (Router tab → settings).",
                     "If it stays high, turn off router features you don't use (Traffic Analyzer, AiProtection)."]))
            last = rs[-1]
            if mem is not None and mem >= acfg.unit_mem_pct:
                out.append(_issue(
                    "unit_mem_high", "medium", "unit", unit, f"{name} is low on memory",
                    f"Typically {mem:.0f}% of memory in use ({last.get('mem_avail_mb')} MB free).",
                    "When memory runs out the router drops connections or restarts services.",
                    ["Restart the unit; if it fills up again within days, update the firmware."]))
            if temp is not None and temp >= acfg.unit_temp_c:
                out.append(_issue(
                    "unit_hot", "medium", "unit", unit, f"{name}'s chip is hot ({temp:.0f} °C)",
                    f"Hottest sensor typically {temp:.0f} °C.", "Hot units throttle and can become unstable.",
                    ["Give it space and airflow; keep it out of cupboards and direct sun."]))
            if last.get("conntrack") and last.get("conntrack_max"):
                pct = 100 * last["conntrack"] / last["conntrack_max"]
                if pct >= acfg.conntrack_pct:
                    out.append(_issue(
                        "conntrack_full", "high", "unit", unit, f"{name}'s connection table is nearly full",
                        f"{last['conntrack']} of {last['conntrack_max']} connections ({pct:.0f}%).",
                        "Every connection through the router needs an entry; when the table is full new connections "
                        "fail (pages hang, games can't connect). A device making huge numbers of connections is the usual cause.",
                        ["Find the device with the most traffic or connections (file sharing, a misbehaving camera).",
                         "Restart the router to clear it in the short term."]))
            for k, label in (("jffs_pct", "settings storage (/jffs)"), ("tmp_pct", "temporary storage (/tmp)")):
                if (last.get(k) or 0) >= 90:
                    out.append(_issue(
                        "unit_storage_full", "low", "unit", f"{unit}-{k}", f"{name}'s {label} is {last[k]}% full",
                        f"{label}: {last[k]}% used.", "When it fills, logs and settings can't be written.",
                        ["Restart the unit (clears /tmp); if /jffs stays full, check add-on scripts."]))
            ups = [(r["ts"], r["uptime_s"]) for r in rs if r.get("uptime_s") is not None]
            restarts = [t for (t0, u0), (t, u) in zip(ups, ups[1:]) if u < u0]
            if restarts:
                out.append(_issue(
                    "unit_restarted", "medium" if len(restarts) > 1 else "info", "unit", unit,
                    f"{name} restarted" + (f" {len(restarts)} times" if len(restarts) > 1 else ""),
                    "Restarted at " + ", ".join(_hm(t) for t in restarts[-5:]) + ".",
                    "A restart drops every device on the unit for a minute or two. Unplanned restarts point at power, "
                    "heat, firmware or memory problems.",
                    ["If you didn't restart it, check its power supply and temperature, and update the firmware."],
                    restarts[0], restarts[-1], count=len(restarts)))

    # ------------------------------------------------ radar, channel changes, wired links, internet reconnects
    uev = net.get("unit_events") or []
    by_kind: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in uev:
        by_kind[e["kind"]].append(e)
    radar = sorted(by_kind.get("radar", []), key=lambda e: e["ts"])
    if radar:
        moves = {e["unit"]: e for e in by_kind.get("channel_change", [])}
        lines = []
        for e in radar[-5:]:
            mv = next((c for c in by_kind.get("channel_change", []) if c["unit"] == e["unit"] and 0 <= c["ts"] - e["ts"] < 120), None)
            lines.append(f"{_hm(e['ts'])} {names.get(e['unit'], e['unit'])} on channel {e['detail'].get('channel')}"
                         + (f" → moved to {mv['detail'].get('to_channel')}" if mv else ""))
        out.append(_issue(
            "radar_dfs", "medium", "network", "radar", f"Radar forced 5 GHz off its channel {len(radar)} time{'s' if len(radar) > 1 else ''}",
            "; ".join(lines) + ".",
            "5 GHz channels 52-144 are shared with weather and other radar (DFS channels). When a unit detects radar it must leave "
            "the channel at once and check the new one for a minute before using it, so every 5 GHz device on it drops or "
            "stalls, and a 5 GHz mesh backhaul moves too.",
            ["Set 5 GHz to a channel from 36-48 (no radar checks) in the ASUS app or web UI (Wireless → General → 5 GHz → "
             "Control channel), so radar can't knock it off.",
             "If the units' 5 GHz backhaul is also on a DFS channel, prefer 6 GHz or Ethernet backhaul where possible."],
            radar[0]["ts"], radar[-1]["ts"], count=len(radar)))
    for u in net.get("units") or []:
        for i in (u.get("info") or {}).get("ifaces") or []:
            ch = i.get("channel") or 0
            if i.get("band") == "5" and 52 <= ch <= 144 and not i["iface"].startswith("apcli") and not radar:
                out.append(_issue(
                    "dfs_channel", "low", "unit", f"{u['mac']}-dfs", f"{names.get(u['mac'], u['mac'])} uses DFS channel {ch} on 5 GHz",
                    f"Channel {ch} is a radar (DFS) channel.",
                    "If radar is detected nearby, the unit must leave the channel immediately and every 5 GHz device drops for "
                    "about a minute.", ["Prefer channels 36-48 on 5 GHz unless you need the extra width."]))
                break
    changes = [e for e in by_kind.get("channel_change", []) if e["detail"].get("from_channel") is not None]
    by_unit_ch: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in changes:
        by_unit_ch[e["unit"]].append(e)
    for unit, es in by_unit_ch.items():
        if len(es) >= 3:
            out.append(_issue(
                "channel_hopping", "medium", "unit", unit, f"{names.get(unit, unit)} keeps changing channel",
                f"{len(es)} channel changes, last {_hm(es[-1]['ts'])}: "
                + ", ".join(f"{e['detail'].get('band')} GHz {e['detail'].get('from_channel')}→{e['detail'].get('to_channel')}" for e in es[-4:]) + ".",
                "Each change briefly drops or stalls the devices on that band. Auto channel selection reacting to "
                "interference, or radar on DFS channels, causes it.",
                ["Pick a fixed, quiet channel (the air survey shows how busy each is)."], es[0]["ts"], es[-1]["ts"], count=len(es)))
    flaps: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for e in by_kind.get("link_change", []):
        flaps[(e["unit"], e.get("iface"))].append(e)
    for (unit, iface), es in flaps.items():
        n = sum(e["detail"].get("changes", 1) for e in es)
        out.append(_issue(
            "link_flap", "medium" if n >= 4 else "low", "unit", f"{unit}-{iface}",
            f"Wired port {iface} on {names.get(unit, unit)} dropped and reconnected",
            f"{n} link change{'s' if n > 1 else ''}, last {_hm(es[-1]['ts'])}" +
            (f" (now {es[-1]['detail'].get('state')}, {es[-1]['detail'].get('speed')} Mbit/s)" if es[-1]["detail"].get("speed") else "") + ".",
            "A wired link going down and up means a loose or damaged cable, a device or switch restarting or losing power, "
            "or a powerline adapter resetting. Everything behind that port drops each time.",
            ["Reseat or swap the cable; check what is plugged into that port (and whether it restarted).",
             "If it is the internet (WAN) port, the modem/ONT may be restarting: check its lights and power."],
            es[0]["ts"], es[-1]["ts"], count=n))
    wan = by_kind.get("wan_ip_change", [])
    if wan:
        out.append(_issue(
            "wan_reconnect", "medium" if len(wan) > 1 else "info", "network", "wan",
            "The internet connection reconnected" + (f" {len(wan)} times" if len(wan) > 1 else ""),
            "Your internet address changed at " + ", ".join(_hm(e["ts"]) for e in wan[-5:]) + ".",
            "A new address means the line or the router's connection to the ISP dropped and came back: every call, game and "
            "download is cut at that moment.",
            ["If it happens more than occasionally, note the times and report them to your ISP.",
             "Check the internet (WAN) port for link changes on the Router tab."], wan[0]["ts"], wan[-1]["ts"], count=len(wan)))
    for e in by_kind.get("unit_reboot", []):
        who = e["detail"].get("by")
        out.append(_issue(
            "unit_restarted_log", "info", "unit", f"{e['unit']}-reboot-{int(e['ts'])}", f"{names.get(e['unit'], e['unit'])} was restarted",
            f"Restart requested at {_hm(e['ts'])}" + (f" by the firmware's '{who}' service" if who else "") + ".",
            "A deliberate restart (by the firmware, the ASUS app or a schedule), not a crash.",
            ["Nothing to do unless it happens often; ASUS's automatic restarts can be scheduled at night."], e["ts"], e["ts"]))
    # Errors and drops on wired interfaces over the period.
    rows_by: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for c in net.get("iface_counters") or []:
        if c.get("rx_errs") is not None:
            rows_by[(c["unit"], c["iface"])].append(c)
    from .collectors.asus.parsers import is_wired
    for (unit, iface), rs in rows_by.items():
        if not is_wired(iface) or len(rs) < 2:
            continue
        a, b = rs[0], rs[-1]
        errs = sum(max(0, (b.get(k) or 0) - (a.get(k) or 0)) for k in ("rx_errs", "tx_errs"))
        pkts = max(1, (b.get("rx_packets") or 0) - (a.get("rx_packets") or 0) + (b.get("tx_packets") or 0) - (a.get("tx_packets") or 0))
        if errs >= 50 and errs / pkts >= 0.0005:
            out.append(_issue(
                "iface_errors", "medium" if errs / pkts >= 0.005 else "low", "unit", f"{unit}-{iface}-errs",
                f"Errors on wired port {iface} ({names.get(unit, unit)})",
                f"{errs} errors in {pkts} packets ({100 * errs / pkts:.2f}%) over the period.",
                "Errors on a wired link mean damaged frames: usually a poor cable or connector, a cable that is too long, or "
                "electrical interference; on the WAN port it can be the modem.",
                ["Swap the cable for a known-good one (Cat 5e or better) and check both connectors."]))

    # ------------------------------------------------ settings that matter for speed (main router)
    main = next((u for u, n in names.items() if n == "Main router"), None)
    s = (net.get("settings") or {}).get(main or "", {})
    if s.get("webs_state_flag") in ("1", "2"):
        out.append(_issue("firmware_update", "medium" if s["webs_state_flag"] == "2" else "low", "network", "firmware",
                          "A router firmware update is available",
                          f"The router reports a newer firmware{(': ' + s['webs_state_info']) if s.get('webs_state_info') else ''}.",
                          "Updates fix stability and security problems.",
                          ["Update from the ASUS app or web UI (Administration → Firmware Upgrade); nodes update with it."]))
    last_grade = next((x.get("grade") for x in reversed(all_speeds) if x.get("grade")), None)
    if s and s.get("qos_enable") == "0" and last_grade in ("C", "D", "F"):
        out.append(_issue("qos_off", "medium", "network", "qos", "QoS is off while bufferbloat is high",
                          f"Bufferbloat grade {last_grade}; QoS is switched off on the router.",
                          "QoS is the router feature that keeps latency low when the line is full.",
                          ["Turn on Adaptive QoS → QoS, type Adaptive or Traditional, and set the bandwidth to about 90% "
                           "of the router speed test's results."]))

    # ------------------------------------------------ open ports
    risky: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in net.get("open_ports") or []:
        if p.get("risk"):
            risky[p["mac"]].append(p)
    for mac, ps in risky.items():
        d = devs.get(mac) or {}
        name = display_name(d, mac)
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
                f"New open port on {display_name(d, e['mac'])}",
                f"Port {det.get('port')} ({det.get('service') or 'unknown service'}) opened, first seen {_hm(e['ts'])}.",
                "A device started offering a new service: a software update, a setting turned on, or new software.",
                ["If you didn't expect it, check the device's settings or recent updates."], e["ts"], e["ts"]))
    return out
