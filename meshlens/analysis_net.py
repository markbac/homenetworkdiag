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


def _ago_h(s: float) -> str:
    return f"{s / 3600:.0f} hours" if s < 172800 else f"{s / 86400:.0f} days"


def _hm(ts: float) -> str:
    return time.strftime("%d %b %H:%M", time.localtime(ts))


KEY_PORTS = {53: "DNS server", 445: "file server", 2049: "file server", 8123: "Home Assistant", 1883: "MQTT broker",
             5000: "NAS", 5001: "NAS", 32400: "media server"}
KEY_TYPES = ("nas", "server", "network", "powerline", "switch", "router", "hub", "gateway")


def key_device_suggestions(devices: list[dict[str, Any]], open_ports: list[dict[str, Any]], now: float | None = None,
                           units: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Devices the rest of the house depends on (servers, switches, hubs, powerline) that aren't watched yet.

    One entry per host name: a device on cable and Wi-Fi is suggested once, on its wired address. Computers, phones
    and TVs are never suggested (Windows file sharing doesn't make a PC a server); mesh units are watched already;
    a device only typed as a server/hub must be on a cable."""
    now = now or time.time()
    unit_macs = {m for u in units or [] for m in [u["mac"], *(u.get("macs") or [])]}
    ports: dict[str, set[int]] = defaultdict(set)
    for p in open_ports:
        ports[p["mac"]].add(p["port"])
    out, seen_names = [], set()
    for d in sorted(devices, key=lambda d: (not d.get("wired"), d["mac"])):
        if d.get("randomised") or not d.get("last_seen") or now - d["last_seen"] > 7 * 86400:
            continue
        kind = (d.get("user_type") or d.get("dtype") or "").lower()
        if d["mac"] in unit_macs or (d.get("name") or "").startswith(("ZenWiFi", "RT-", "GT-")) or any(
                k in kind for k in ("computer", "phone", "tablet", "laptop", "tv", "streamer", "console")):
            continue
        server = "nas" in kind or "server" in kind
        roles = sorted({KEY_PORTS[p] for p in ports.get(d["mac"], ()) if p in KEY_PORTS and (server or p not in (445, 2049))})
        if not roles and not (d.get("wired") and any(k in kind for k in KEY_TYPES)):
            continue
        name = display_name(d, d["mac"])
        if name.lower() in seen_names or any(x.get("watched") and display_name(x, x["mac"]).lower() == name.lower() for x in devices):
            continue
        seen_names.add(name.lower())
        if not d.get("watched"):
            out.append({"mac": d["mac"], "name": name, "why": ", ".join(roles) or kind})
    return out


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
    rank = {"A": 0, "B": 1, "C": 2, "D": 3, "F": 4}
    pcg = [x for x in pc_speeds if x.get("down_mbps") is not None]
    if len(pcg) >= 5:
        # Find the most recent big step in this PC's results (speed and bufferbloat together).
        for k in range(len(pcg) - 1, 3, -1):
            before, after = pcg[max(0, k - 8):k], pcg[k:]
            bd = statistics.median(x["down_mbps"] for x in before)
            ad = statistics.median(x["down_mbps"] for x in after)
            bg = [rank[x["grade"]] for x in before if x.get("grade") in rank]
            ag = [rank[x["grade"]] for x in after if x.get("grade") in rank]
            gstep = (statistics.median(ag) - statistics.median(bg)) if bg and ag else 0
            ratio = ad / bd if bd else 1
            big = (ratio >= 2.5 or ratio <= 0.4) and (abs(gstep) >= 2 or len(after) >= 3)
            if not big:
                continue
            better = ratio > 1
            bup = statistics.median(x["up_loaded_ms"] for x in before if x.get("up_loaded_ms") is not None) if any(
                x.get("up_loaded_ms") is not None for x in before) else None
            aup = statistics.median(x["up_loaded_ms"] for x in after if x.get("up_loaded_ms") is not None) if any(
                x.get("up_loaded_ms") is not None for x in after) else None
            gl = lambda v: "ABCDF"[round(v)] if v is not None else "?"
            ev = (f"Before {_hm(pcg[k]['ts'])} ({len(before)} tests): typically {bd:.0f} Mbit/s down"
                  + (f", {bup:.0f} ms during upload" if bup is not None else "")
                  + (f", grade {gl(statistics.median(bg))}" if bg else "")
                  + f". Since ({len(after)} test{'s' if len(after) > 1 else ''}): {ad:.0f} Mbit/s"
                  + (f", {aup:.0f} ms during upload" if aup is not None else "")
                  + (f", grade {gl(statistics.median(ag))}" if ag else "") + ".")
            cause = ("Speed and latency under load from this PC changed together in one step, so something on the PC's own "
                     "path changed (cabling, a powerline adapter, a Wi-Fi bridge, a switch port), not the internet line. ")
            if better and gstep <= -2:
                cause += ("The bufferbloat seen before came from that old path: powerline adapters and slow links queue "
                          "packets when they are full, which shows up as lag under load even though the line itself is fine.")
            elif not better:
                cause += "The new path is slower or queues more; other devices on the same path are affected the same way."
            out.append(_issue(
                "speed_path_changed", "info" if better else "medium", "network", "speed_path",
                "Speed tests from this PC " + ("improved sharply" if better else "got much worse") + f" at {_hm(pcg[k]['ts'])}",
                ev + (" Only one test since: the next few will confirm it." if len(after) == 1 else ""), cause,
                (["Older bufferbloat issues from this PC described the old path; judge the line by the router's own test."]
                 if better else ["Check what changed on the PC's path around that time (Topology tab, powerline rates)."]),
                pcg[k]["ts"], pcg[-1]["ts"]))
            break

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
    # Restarts of several units within 45 minutes are one event (a firmware update or scheduled reboot).
    all_restarts: list[tuple[float, str]] = []
    per_u: dict[str, list[tuple[float, int]]] = defaultdict(list)
    for r in us:
        if r.get("uptime_s") is not None:
            per_u[r["unit"]].append((r["ts"], r["uptime_s"]))
    for unit, ups in per_u.items():
        ups.sort()
        all_restarts += [(t, unit) for (t0, u0), (t, u) in zip(ups, ups[1:]) if u < u0]
    all_restarts.sort()
    grouped: set[tuple[float, str]] = set()
    i = 0
    while i < len(all_restarts):
        j = i
        while j + 1 < len(all_restarts) and all_restarts[j + 1][0] - all_restarts[i][0] <= 2700:
            j += 1
        grp = all_restarts[i:j + 1]
        if len({u for _, u in grp}) >= 3:
            grouped |= set(grp)
            t0, t1 = grp[0][0], grp[-1][0]
            night = all(0 <= time.localtime(t).tm_hour < 6 for t, _ in grp)
            out.append(_issue(
                "mesh_restart", "info" if night else "medium", "network", f"mesh-restart-{int(t0)}",
                f"{len({u for _, u in grp})} mesh units restarted together",
                f"Between {_hm(t0)} and {_hm(t1)}: " + ", ".join(f"{names.get(u, u)} {time.strftime('%H:%M', time.localtime(t))}" for t, u in grp) + ".",
                ("Every unit restarting within the same half hour in the night is the mesh's own housekeeping: an automatic "
                 "firmware update or a scheduled reboot, not a fault." if night else
                 "Every unit restarting together points at the main router (a firmware update, a settings change that "
                 "restarts the mesh) or a power cut."),
                (["Nothing to do if it was a firmware update; check Administration → Firmware Upgrade for the new version."]
                 if night else ["Check whether someone changed settings or updated firmware then, and whether the power dipped."]),
                t0, t1, count=len(grp)))
        i = j + 1
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
            restarts = [t for (t0, u0), (t, u) in zip(ups, ups[1:]) if u < u0 and (t, unit) not in grouped]
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
    plan = net.get("channel_plan") or {}
    rec5 = (plan.get("recommend") or {}).get("5") or {}
    if radar:
        incs = [i for i in ((plan.get("radar") or {}).get("incidents") or []) if i["ts"] >= radar[0]["ts"] - 60]
        lines = []
        if incs:
            for i in incs[-5:]:
                bits = [f"{_hm(i['ts'])} {', '.join(i['detected_by'])} heard radar on channel {', '.join(map(str, i['channels']))}"]
                if i.get("moved_to"):
                    bits.append(f"5 GHz moved to channel {i['moved_to'][0]}")
                if i.get("devices_dropped"):
                    bits.append(f"{i['devices_dropped']} devices dropped")
                if i.get("pushed_to_24"):
                    stuck = i.get("still_on_24") or []
                    bits.append(f"{len(i['pushed_to_24'])} fell back to 2.4 GHz"
                                + (f" ({len(stuck)} still there: {', '.join(m['name'] for m in stuck[:4])})" if stuck else ""))
                lines.append(", ".join(bits))
        else:
            for e in radar[-5:]:
                mv = next((c for c in by_kind.get("channel_change", []) if c["unit"] == e["unit"] and 0 <= c["ts"] - e["ts"] < 120), None)
                lines.append(f"{_hm(e['ts'])} {names.get(e['unit'], e['unit'])} on channel {e['detail'].get('channel')}"
                             + (f" → moved to {mv['detail'].get('to_channel')}" if mv else ""))
        single = (plan.get("radar") or {}).get("single_detector")
        stuck_any = any(i.get("still_on_24") for i in incs)
        cause = ("5 GHz channels 52-144 are shared with weather and other radar (DFS channels). When a unit detects radar it must "
                 "leave the channel at once and check the new one for a minute before using it, so every 5 GHz device on it drops "
                 "or stalls, and a 5 GHz mesh backhaul moves too. Dual-band devices often reconnect on 2.4 GHz while 5 GHz is "
                 "moving, and the mesh only steers them back up if their 2.4 GHz signal is very strong, so they can stay on the "
                 "slower band for hours.")
        if single:
            cause += (f" Every detection came from {single}: if no other unit ever hears it, it may be a false trigger near "
                      "that unit (some electronics produce radar-like pulses), but the channel has to be left either way.")
        fixes = [rec5.get("text") if rec5.get("block") == "36-48" else
                 "Set 5 GHz to a channel from 36-48 (no radar checks) in the ASUS app or web UI (Wireless → General → 5 GHz → "
                 "Control channel), or untick 'Auto select channel including DFS channels', so radar can't knock it off.",
                 "If the units' 5 GHz backhaul is also on a DFS channel, prefer 6 GHz or Ethernet backhaul where possible."]
        if stuck_any:
            fixes.append("Devices still on 2.4 GHz since the radar event: turn their Wi-Fi off and on to rejoin on 5 GHz.")
        out.append(_issue(
            "radar_dfs", "high" if stuck_any or len(radar) >= 3 else "medium", "network", "radar",
            f"Radar forced 5 GHz off its channel {len(radar)} time{'s' if len(radar) > 1 else ''}",
            "; ".join(lines) + ".", cause, [f for f in fixes if f],
            radar[0]["ts"], radar[-1]["ts"], count=len(radar)))
    r24 = (plan.get("recommend") or {}).get("2.4") or {}
    if rec5.get("change") or r24.get("change"):
        ev = []
        for f in plan.get("five") or []:
            if f.get("current") or f["block"] == rec5.get("block"):
                ev.append(f"5 GHz {f['block']}{' (now)' if f.get('current') else ''}: {f['strong_neighbours']} strong / "
                          f"{f['neighbours']} neighbours" + (f", neighbours busy {f['busy_pct']}%" if f['busy_pct'] is not None else '') + ", "
                          f"radar {f['radar_hits']}×")
        for c in plan.get("two4") or []:
            if c.get("current") or c["channel"] == r24.get("channel"):
                ev.append(f"2.4 GHz ch {c['channel']}{' (now)' if c.get('current') else ''}: {c['strong_neighbours']} strong / "
                          f"{c['neighbours']} overlapping neighbours")
        cur24 = ((plan.get("current") or {}).get("2.4") or {}).get("channel")
        if cur24 and cur24 not in (1, 6, 11):
            ev.append(f"2.4 GHz is on channel {cur24}, between the non-overlapping channels 1, 6 and 11")
        out.append(_issue(
            "channel_plan", "medium" if rec5.get("change") else "low", "network", "channel_plan",
            "A better Wi-Fi channel is available",
            "; ".join(ev) + ".",
            "MeshLens scores each 5 GHz 80 MHz block and the 2.4 GHz channels 1, 6 and 11 from the air survey (how many "
            "neighbouring networks this PC hears and how loud, how busy access points say the channel is) and the radar "
            "history. Fewer strong neighbours and no radar means more airtime and fewer forced channel changes.",
            [x["text"] for x in (rec5, r24) if x.get("change")]
            + ["Channels are set on the main router (Wireless → General); the nodes follow it. Router → Channel plan shows the full table."]))

    # Dual-band devices left on 2.4 GHz (often after a radar event or a 5 GHz restart).
    hist = net.get("band_history") or {}
    unit_macs: set[str] = set()
    for u in net.get("units") or []:
        unit_macs |= {u["mac"], *(u.get("macs") or [])}
    stuck_min = getattr(net.get("analysis_cfg"), "stuck_24_minutes", 60)
    trig = sorted([e for e in uev if e["kind"] in ("radar", "channel_change", "channel_replan", "unit_reboot")], key=lambda e: e["ts"])
    for s in net.get("latest_stations") or []:
        h = hist.get(s["mac"])
        if (s.get("band") != "2.4" or s["mac"] in unit_macs or not h or now - s["ts"] > 900
                or h["hi"] < 20 or not h.get("last_hi") or now - h["last_hi"] < stuck_min * 60):
            continue
        d = devs.get(s["mac"]) or {}
        name = display_name(d, s["mac"])
        why = next((e for e in trig if -60 <= h["last_hi"] - e["ts"] <= 420), None)
        out.append(_issue(
            "stuck_on_24", "medium", "device", s["mac"], f"{name} is stuck on 2.4 GHz",
            f"It used 5/6 GHz until {_hm(h['last_hi'])} (typical signal there {h['hi_rssi']} dBm) and has been on 2.4 GHz since, "
            f"now at {s.get('rssi')} dBm via {names.get(s.get('unit'), s.get('unit'))}."
            + (f" It moved at the same time as {'radar' if why['kind'] == 'radar' else why['kind'].replace('_', ' ')} on "
               f"{names.get(why['unit'], why['unit'])} ({_hm(why['ts'])})." if why else ""),
            "The device dropped off 5 GHz and rejoined on 2.4 GHz. 2.4 GHz is slower and busier, so streaming and downloads "
            "suffer. The mesh's band steering only pushes a 2.4 GHz device back to 5 GHz when its 2.4 GHz signal is very "
            "strong (Smart Connect rule), so a device a room or two away can stay on 2.4 GHz indefinitely.",
            ["Turn the device's Wi-Fi off and on (or restart it) so it rejoins on 5 GHz.",
             "Stop the trigger: keep 5 GHz on channels 36-48 so radar can't force it off (see Channel plan).",
             "In Wireless → Smart Connect Rule, check the 2.4 GHz → 5 GHz steering condition: if it needs an RSSI above "
             "about -52 dBm, lowering it to about -68 dBm lets devices like this move back up. Change it in small steps.",
             "For a TV or streamer that never moves, a cable or binding it to the nearest node (AiMesh → client) keeps it put."],
            h["last_hi"], now))

    # A network loop behind a powerline adapter (seen by the devolo poll).
    loops = sorted(by_kind.get("l2_loop", []), key=lambda e: e["ts"])
    if loops:
        per_ad: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for e in loops:
            per_ad[e["unit"]].append(e)
        router_pings = [p for p in net.get("pings") or [] if (p.get("label") or "").lower() == "router"]

        def loss(rs: list[dict[str, Any]]) -> float | None:
            sent = sum(r["sent"] or 0 for r in rs)
            return 100 * sum(r["lost"] or 0 for r in rs) / sent if sent else None
        for adapter, es in per_ad.items():
            last = es[-1]["detail"]
            seen = {int(e["ts"] // 60) for e in es}
            near = [p for p in router_pings if any(abs(int(p["ts"] // 60) - m) <= 3 for m in seen)]
            away = [p for p in router_pings if all(abs(int(p["ts"] // 60) - m) > 3 for m in seen)]
            ln, la = loss(near), loss(away)
            strong = any(e["detail"].get("router_macs") or e["detail"].get("wifi_clients") for e in es)
            ev = [f"Seen {len(es)} time{'s' if len(es) > 1 else ''} behind {last.get('adapter_name')}, last {_hm(es[-1]['ts'])}"]
            if any(e["detail"].get("router_macs") for e in es):
                ev.append("the main router's own address appeared behind it")
            wc = max((e["detail"] for e in es), key=lambda d: len(d.get("wifi_clients") or []))
            if wc.get("wifi_clients"):
                ev.append(f"{len(wc['wifi_clients'])} Wi-Fi devices of {', '.join(wc.get('wifi_client_units') or [])} appeared behind it")
            for n in last.get("nodes") or []:
                ev.append(f"{n['name']} is behind it and also linked over Wi-Fi ({', '.join(n['wifi_backhaul'])})")
            if ln is not None and la is not None:
                ev.append(f"ping loss to the router {ln:.1f}% around those times vs {la:.1f}% otherwise")
            out.append(_issue(
                "network_loop", "high" if strong else "medium", "network", f"loop-{adapter}",
                f"Possible network loop behind {last.get('adapter_name')}" if strong else
                f"A mesh node has two paths: Wi-Fi and {last.get('adapter_name')}",
                "; ".join(ev) + ".",
                "The wired devices behind this powerline adapter already reach the router through the powerline. If a mesh node "
                "(or a wireless bridge) behind it is also linked to the mesh over Wi-Fi, there are two paths and traffic can go "
                "round in a circle. Switches then keep changing their minds about where each device is, so devices drop, "
                "pings are lost and broadcasts multiply. Addresses that belong on the router side showing up behind this adapter "
                "are the tell-tale sign.",
                ["Keep one path: either unplug the cable from the node/bridge to the switch, or unplug the powerline adapter "
                 "from that switch, so the wired devices reach the router one way only.",
                 "If the node should use the powerline as its backhaul, plug it into its WAN/uplink port and set AiMesh "
                 "'Ethernet backhaul' so it stops using Wi-Fi.",
                 "Check for two cables between the same pair of switches as well."],
                es[0]["ts"], es[-1]["ts"], count=len(es)))

    # Two units with the same name make every report ambiguous.
    same: dict[str, list[str]] = defaultdict(list)
    for u in net.get("units") or []:
        if u.get("alias") and u.get("role") != "router":
            same[u["alias"].strip().lower()].append(u["alias"])
    for k, v in same.items():
        if len(v) > 1:
            out.append(_issue(
                "duplicate_unit_name", "low", "network", f"dup-{k}", f"{len(v)} mesh units are called '{v[0]}'",
                f"{len(v)} units share the name '{v[0]}'.", "Reports, events and the map can't tell them apart.",
                ["Rename one in the ASUS app (AiMesh → the node → location name)."]))

    # One address answered by two network adapters (a device on cable *and* Wi-Fi, or two devices fighting over it).
    devl = [d for d in net.get("devices") or [] if d.get("ip") and d.get("last_seen") and now - d["last_seen"] < 1800]
    by_ip: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for d in devl:
        by_ip[d["ip"]].append(d)
    for ip, ds in by_ip.items():
        if len(ds) < 2:
            continue
        wired = [d for d in ds if d.get("wired")]
        wifi = [d for d in ds if not d.get("wired")]
        nm = lambda d: display_name(d, d["mac"])
        # Same host name on both: one machine with two adapters (a stored address can be stale, so a mere shared
        # address between differently named devices is not reported).
        same_host = len({(d.get("name") or "").lower() for d in ds}) == 1 and ds[0].get("name")
        if wired and wifi and same_host:
            out.append(_issue(
                "dual_homed", "high", "device", wired[0]["mac"], f"{nm(wired[0])} is on cable and Wi-Fi with the same address",
                f"{ip} is used by {nm(wired[0])} on a cable ({wired[0]['mac']}) and by "
                + ", ".join(f"{nm(d)} on Wi-Fi ({d['mac']}" + (f", {names.get(d.get('unit'), d.get('unit'))}" if d.get('unit') else "") + ")" for d in wifi)
                + "; both seen in the last 30 minutes.",
                "Two network adapters answering for one address make the rest of the network flip between them: replies go "
                "out of one adapter and come back to the other, connections stall, and traffic takes the slow Wi-Fi path at "
                "random. On a server everyone uses (a DNS server such as Pi-hole, a NAS, Home Assistant) the whole house "
                "feels it.",
                ["Turn off the Wi-Fi on the device if it has a cable (Raspberry Pi: add 'dtoverlay=disable-wifi' to "
                 "/boot/config.txt, or 'nmcli radio wifi off').",
                 "Or give the Wi-Fi side its own address if you want it as a backup."],
                devices=[d["mac"] for d in ds]))

    # Settings that should match on every unit.
    sets = net.get("settings") or {}
    for key, label in (("wl0_mfp", "2.4 GHz"), ("wl1_mfp", "5 GHz")):
        vals = {u: v.get(key) for u, v in sets.items() if v.get(key) not in (None, "")}
        if len(set(vals.values())) > 1:
            meaning = {"0": "off", "1": "capable", "2": "required"}
            out.append(_issue(
                "pmf_mismatch", "medium", "network", f"pmf-{key}", f"Protected management frames differ between units ({label})",
                "; ".join(f"{names.get(u, u)}: {meaning.get(v, v)}" for u, v in sorted(vals.items(), key=lambda x: names.get(x[0], x[0]))) + ".",
                "PMF (part of WPA3) protects disconnect and roaming messages. If units disagree, a device moving between them "
                "meets different rules: some refuse it ('try again later', status 30) or reject its saved key (status 53), so it "
                "bounces back or drops. The values are each unit's own reading of the setting; nodes normally copy the router's.",
                ["Set Protected Management Frames once on the main router (Wireless → Professional, per band: 'Capable' "
                 "suits mixed devices) and apply; the nodes should pick it up when they sync.",
                 "If a node keeps a different value, update its firmware or restart it so it re-syncs."]))
            break
    import re as _re
    builds = {}
    for u in net.get("units") or []:
        m = _re.search(r"\.(\d{3})_(\d{5})", u.get("firmware") or "")
        if m:
            builds[u["mac"]] = (int(m.group(1)), int(m.group(2)), u.get("model") or "")
    if len({b[1] for b in builds.values()}) > 1:
        newest = max(b[1] for b in builds.values())
        old = {u: b for u, b in builds.items() if b[1] < newest}
        out.append(_issue(
            "firmware_mismatch", "low", "network", "firmware-mix", "Mesh units run different firmware builds",
            "; ".join(f"{names.get(u, u)} ({b[2]}): build {b[1]}" for u, b in sorted(builds.items(), key=lambda x: -x[1][1])) + ".",
            "Each model has its own firmware, so the numbers needn't be identical, but ASUS releases them together and fixes "
            "mesh problems (roaming, key handling, backhaul) in both. Units several builds apart are worth updating.",
            [f"In the ASUS app or Administration → Firmware Upgrade, check for updates on "
             + ", ".join(names.get(u, u) for u in old) + "; update all units, then restart the mesh once."]))

    # Names the router guessed from the maker's prefix that belong to a different maker.
    for d in net.get("devices") or []:
        n, src, vendor = d.get("name") or "", d.get("name_src") or "", (d.get("vendor") or "").lower()
        if (src.startswith("asus:") and "oui" in src.lower() and _re.match(r"(ZenWiFi|RT-|GT-|TUF|ROG|RP-)", n)
                and vendor and "asus" not in vendor and d.get("last_seen") and now - d["last_seen"] < 86400):
            out.append(_issue(
                "suspect_name", "info", "device", d["mac"], f"'{n}' is probably not an ASUS device",
                f"The router named it '{n}' from its address prefix, but that prefix belongs to {d.get('vendor')}.",
                "The router's device database guesses names from the first half of the address and sometimes picks an ASUS "
                "product name for another maker's chip (Espressif, used in many smart plugs and lights).",
                ["Give it a proper name in Devices so its issues make sense."]))

    # ------------------------------------------------ key devices nobody is watching
    sugg = key_device_suggestions(net.get("devices") or [], net.get("open_ports") or [], now, net.get("units"))
    if sugg:
        out.append(_issue(
            "watch_suggest", "info", "network", "watch-suggest", f"Watch {len(sugg)} key device{'s' if len(sugg) > 1 else ''}",
            "Not watched yet: " + "; ".join(f"{x['name']} ({x['why']})" for x in sugg[:10]) + ".",
            "These devices are ones the rest of the house relies on (DNS, file and media servers, Home Assistant, switches, "
            "powerline adapters). Watched devices are pinged every few seconds and you get an issue when one goes offline, "
            "so an outage like a DNS server dropping off is caught at once.",
            ["Settings → Setup checklist → 'Watch suggested', or open each device and tick Watch."]))

    # ------------------------------------------------ ESP32 probes
    plist = net.get("probes") or []
    pname = {p["id"]: (f"probe in {p['room']}" if p.get("room") else f"probe {p['id']}") for p in plist}
    for p in plist:
        name = pname[p["id"]]
        if not p.get("adopted") and p.get("last_seen") and now - p["last_seen"] < 3600:
            out.append(_issue(
                "probe_new", "info", "network", f"probe-{p['id']}", f"New probe found: {p['id']}",
                f"{p.get('chip') or 'ESP32'} probe at {p.get('ip')} (firmware {p.get('firmware') or '?'}), not paired yet.",
                "A MeshLens probe announced itself on the network. Until it is paired, anyone on the network can read it "
                "and MeshLens only uses what it shares openly.",
                ["Router → Probes → Pair, then type the 6-digit code shown on the probe's screen, and set its room."]))
        elif p.get("adopted") and not p.get("paired") and p.get("last_seen") and now - p["last_seen"] < 3600:
            out.append(_issue(
                "probe_unpaired", "medium", "network", f"probe-{p['id']}", f"MeshLens can't read {name}",
                p.get("last_error") or "It is paired, but not with this MeshLens (or the token was lost).",
                "The probe only answers the MeshLens it was paired with.",
                ["On the probe: SETUP → PROBE → Forget. Then Router → Probes → Pair in MeshLens."]))
        elif p.get("paired") and p.get("last_seen") and now - p["last_seen"] > 3 * 3600:
            out.append(_issue(
                "probe_offline", "low", "network", f"probe-{p['id']}", f"{name[0].upper() + name[1:]} hasn't reported for {_ago_h(now - p['last_seen'])}",
                f"Last heard {_hm(p['last_seen'])} at {p.get('ip')}" + (f": {p['last_error']}" if p.get("last_error") else "") + ".",
                "The probe is switched off, out of range, its battery is flat, or it has joined another network.",
                ["Check it is powered and shows your network on SETUP → WI-FI; MeshLens picks it up again by itself."]))
    alerts = sorted(by_kind.get("probe_alert", []), key=lambda e: e["ts"])
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for e in alerts:
        text = e["detail"].get("text") or "?"
        groups[(e["unit"], _re.sub(r"\d+", "#", text)[:80])].append(e)
    sev_map = {"danger": "high", "warn": "medium", "info": "info"}
    own = own_unit_lookup(net.get("units") or [])
    for (pid, key), es in groups.items():
        last = es[-1]["detail"]
        sev = sev_map.get(last.get("severity"), "low")
        text = last.get("text") or ""
        # The sender: "... from <mac> to <mac>", or "Access point <mac> disconnected devices ...".
        sender = (_re.search(r"\bfrom ([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})", text)
                  or _re.search(r"[Aa]ccess point ([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})", text))
        mine = own(sender.group(1)) if sender else None
        note = ""
        if mine and any(w in text.lower() for w in ("deauth", "disassoc", "disconnect")):
            # ASUS units steer and roam devices with disconnect frames: expected, not an attack.
            sev = "low" if sev in ("high", "medium") else sev
            note = (f" The sender is your own unit {mine} (one of its radio addresses), so this is the mesh moving the "
                    "device (band steering or roaming), not an attack. Many in a row mean steering keeps failing.")
        elif mine:
            note = f" The sender is your own unit {mine}."
        out.append(_issue(
            "probe_alert", sev, "network", f"{pid}|{key}", f"{pname.get(pid, pid)}: {text}",
            f"Reported {len(es)} time{'s' if len(es) > 1 else ''}, last {_hm(es[-1]['ts'])}.{note}",
            "The probe's air monitor watches the Wi-Fi channel for attacks and oddities: floods of disconnect frames, a "
            "copy of your network name from another radio (evil twin), access points answering any name (Karma), beacon "
            "floods, and Bluetooth trackers that stay near. Your own mesh steering devices away also sends disconnects, "
            "so a few of those from your own units are normal.",
            ["Open the probe's ALERTS tab for detail, or Router → Probes.",
             "If it names one of your own units, it is usually band steering or roaming; if it names a stranger's radio "
             "near you, note the time and where it was strongest."], es[0]["ts"], es[-1]["ts"], count=len(es)))
    # PMF as actually broadcast by your own access points (the probes read it from beacons).
    seen_pmf: dict[str, dict[str, str]] = defaultdict(dict)
    for p in plist:
        for b in ((p.get("air") or {}).get("beacons") or []):
            ssid, bssid, pmf = b.get("ssid") or "", (b.get("bssid") or "").lower(), b.get("pmf")
            if not ssid or not pmf or _re.fullmatch(r"[0-9A-F]{32}", ssid):
                continue
            from .hostpc import own_unit
            u = own_unit(bssid, net.get("units") or [])
            if u:
                seen_pmf[ssid][names.get(u, u)] = pmf
    for ssid, per in seen_pmf.items():
        if len(set(per.values())) > 1:
            out.append(_issue(
                "pmf_mismatch_air", "medium", "network", f"pmf-air-{ssid}", f"Your units advertise '{ssid}' with different PMF",
                "Heard by the probes in the units' beacons: " + "; ".join(f"{u}: {v}" for u, v in sorted(per.items())) + ".",
                "Measured over the air, so this is what devices really see. A device roaming between these units meets "
                "different security rules and can be refused or have its saved key rejected (status 30 / 53).",
                ["Set Protected Management Frames once on the main router and re-apply so the nodes sync; update node firmware."]))
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

    # ------------------------------------------------ address pool, connections per device, router warnings
    router_u = next((u for u in net.get("units") or [] if u.get("role") == "router"), None)
    rinfo = (router_u or {}).get("info") or {}
    dhcp = rinfo.get("dhcp")
    if dhcp and dhcp.get("pct", 0) >= 85:
        out.append(_issue(
            "dhcp_pool_full", "high" if dhcp["pct"] >= 95 else "medium", "network", "dhcp",
            "The router is running out of addresses to hand out",
            f"{dhcp['leases']} of {dhcp['size']} addresses in use ({dhcp['pct']}%), pool {dhcp['start']}-{dhcp['end']}.",
            "When the pool is full, new devices (or devices that reconnect with a new private address) can't join the network.",
            ["Make the DHCP pool bigger (LAN → DHCP Server), or shorten the lease time so old addresses are freed sooner."]))
    devs_by_mac = {d["mac"]: d for d in net.get("devices") or []}
    top = rinfo.get("conn_top") or []
    total = rinfo.get("conn_total") or 0
    if top:
        t = top[0]
        if t["count"] >= 1000 or (total >= 2000 and t["count"] >= 0.4 * total):
            name = display_name(devs_by_mac.get(t.get("mac")), t.get("mac") or t["ip"]) if t.get("mac") else t["ip"]
            out.append(_issue(
                "conn_heavy", "medium" if t["count"] >= 3000 else "low", "device", t.get("mac") or t["ip"],
                f"{name} has {t['count']} connections open",
                f"{t['count']} of the router's {total} tracked connections come from {name} ({t['ip']}).",
                "Huge numbers of connections usually mean file sharing, a backup or sync tool, or a device that is "
                "misbehaving or infected. They fill the router's connection table and use its processor.",
                ["Check what that device is doing; pause file sharing or sync and see if the count drops."]))
    warn = by_kind.get("router_warning", [])
    if warn:
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for e in warn:
            groups[e["detail"].get("what", "?")].append(e)
        serious = ("Out of memory", "oom-killer", "segfault", "Kernel panic", "Unable to handle kernel", "BUG: ",
                   "nf_conntrack: table full", "no address range available")
        for what, es in groups.items():
            out.append(_issue(
                "router_warning", "medium" if what in serious else "low", "unit", f"{es[-1]['unit']}-{what}",
                f"{names.get(es[-1]['unit'], es[-1]['unit'])} logged '{what.strip()}'" + (f" {len(es)} times" if len(es) > 1 else ""),
                f"Last at {_hm(es[-1]['ts'])}.",
                {"no address range available": "The DHCP pool is exhausted: new devices can't get an address.",
                 "DHCPNAK": "The router refused a device's address request (often a device asking for an old address).",
                 "nf_conntrack: table full": "The connection table filled up and new connections were dropped.",
                 "possible SYN flooding": "Something sent a burst of connection attempts to the router (scan or misbehaving device).",
                 }.get(what, "The router's software reported a serious problem (memory, a crash or a hung task)."),
                ["If it repeats, restart the unit and update the firmware; look at the matching time in Wi-Fi events and Graphs."],
                es[0]["ts"], es[-1]["ts"], count=len(es)))

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


def own_unit_lookup(units: list[dict[str, Any]]):
    """mac -> unit name if the address is one of your mesh units (any radio/BSSID; ASUS varies byte 0 per SSID)."""
    full: dict[str, str] = {}
    tail: dict[str, str] = {}
    for u in units:
        name = u.get("alias") or u.get("model") or u["mac"]
        for m in [u["mac"], *(u.get("macs") or [])]:
            full[m.lower()] = name
            tail[m.lower()[3:]] = name

    def find(mac: str) -> str | None:
        m = (mac or "").lower()
        return full.get(m) or tail.get(m[3:])
    return find
