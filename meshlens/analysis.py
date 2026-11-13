"""Analysis: turns raw collections into issues with a cause and a fix, plus health scores (DIA-01..05).

Pure functions over plain dicts so every rule can be tested without a database.
Each issue says what was seen (evidence), why it probably happens (cause) and what
to try (fixes), and links to the matching Help section.
"""
from __future__ import annotations

import statistics
import time
from collections import defaultdict
from typing import Any, Iterable

from .config import AnalysisConfig
from .journeys import _bursts

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}

# Settings keys on ASUS units: wl0 = 2.4 GHz, wl1 = 5 GHz, wl2 = second 5 GHz or 6 GHz, wl3 = 6 GHz.
_WL_BAND = {"wl0": "2.4 GHz", "wl1": "5 GHz", "wl2": "5/6 GHz", "wl3": "6 GHz"}
_IFACE_WL = {"2.4": "wl0", "5": "wl1", "6": "wl2"}

USB_ADAPTER_HINT = ("If this is a Windows PC with a USB Wi-Fi adapter: plug it into a USB extension cable away from "
                    "the PC case and USB 3 ports, install the maker's latest driver, and in Device Manager → adapter → "
                    "Advanced set \"Roaming aggressiveness\" to Lowest or Medium-low and turn off power saving.")


def _label(score: float | None) -> str | None:
    if score is None:
        return None
    return "good" if score >= 80 else "fair" if score >= 60 else "poor"


def device_score(kicks: int, roam_attempts: int, disconnects: int, typical_rssi: float | None,
                 slow: bool = False) -> int:
    """0-100, 100 = no problems. Shared by live analysis and the daily summaries."""
    s = 100.0
    s -= min(50, kicks * 10)
    s -= min(20, roam_attempts * 2)
    s -= min(25, disconnects * 4)
    if typical_rssi is not None and typical_rssi < -65:
        s -= min(30, (-65 - typical_rssi) * 2)
    if slow:
        s -= 10
    return max(0, round(s))


def _issue(rule: str, severity: str, scope: str, subject: str | None, title: str, evidence: str, cause: str,
           fixes: list[str], ts_from: float | None = None, ts_to: float | None = None, **extra: Any) -> dict[str, Any]:
    return {"rule": rule, "severity": severity, "scope": scope, "subject": subject, "title": title,
            "evidence": evidence, "cause": cause, "fixes": fixes, "from": ts_from, "to": ts_to,
            "help": f"issue-{rule}", **extra}


def _thresholds(settings: dict[str, dict[str, str]], units: list[dict[str, Any]]) -> dict[str, int]:
    """Roaming-assistant thresholds per band from the main router's settings."""
    main = next((u["mac"] for u in units if u.get("role") == "router"), None)
    s = settings.get(main or "", {}) if main else (next(iter(settings.values()), {}) if settings else {})
    out = {}
    for wl in _WL_BAND:
        v = s.get(f"{wl}_user_rssi")
        try:
            if v not in (None, "", "0"):
                out[wl] = int(v)
        except ValueError:
            pass
    return out


def analyse(events: Iterable[dict[str, Any]], samples: Iterable[dict[str, Any]], radios: list[dict[str, Any]],
            units: list[dict[str, Any]], devices: list[dict[str, Any]], settings: dict[str, dict[str, str]],
            cfg: AnalysisConfig | None = None, now: float | None = None) -> dict[str, Any]:
    """Run every rule. Returns issues (most severe first) and device / unit / network health."""
    cfg = cfg or AnalysisConfig()
    now = now or time.time()
    events = sorted(events, key=lambda e: e["ts"])
    devs = {d["mac"]: d for d in devices}
    unit_name = {}
    for u in units:
        for m in [u["mac"], *(u.get("macs") or [])]:
            unit_name[m] = u.get("alias") or u.get("model") or u["mac"]

    def uname(mac: str | None) -> str:
        return unit_name.get(mac or "", mac or "?")

    def dname(mac: str) -> str:
        d = devs.get(mac) or {}
        return d.get("user_name") or d.get("name") or mac

    thresholds = _thresholds(settings, units)
    issues: list[dict[str, Any]] = []

    # ------------------------------------------------ per-device facts
    per_kind: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    rssi_ev: dict[str, list[tuple[float, int]]] = defaultdict(list)
    for e in events:
        if e.get("mac"):
            per_kind[e["mac"]][e["kind"]].append(e)
            r = (e.get("detail") or {}).get("rssi")
            if r is not None:
                rssi_ev[e["mac"]].append((e["ts"], r))

    smp: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in samples:
        smp[s["mac"]].append(s)

    facts: dict[str, dict[str, Any]] = {}
    for mac in set(per_kind) | set(smp):
        rows = smp.get(mac, [])
        rssis = [r["rssi"] for r in rows if r.get("rssi") is not None]
        rates = [r["tx_rate"] for r in rows if r.get("tx_rate")]
        last = rows[-1] if rows else {}
        band = last.get("band")
        typical = statistics.median(rssis) if len(rssis) >= 3 else None
        rate = statistics.median(rates) if len(rates) >= 5 else None
        slow_limit = cfg.slow_rate_mbps_24 if band == "2.4" else cfg.slow_rate_mbps_5
        slow = rate is not None and rate < slow_limit
        k = per_kind.get(mac, {})
        facts[mac] = {"typical_rssi": typical, "typical_rate": rate, "band": band, "unit": last.get("unit"),
                      "samples": len(rows), "slow": slow, "kicks": len(k.get("kick", [])),
                      "roam_attempts": len(k.get("roam_candidate", [])), "disconnects": len(k.get("disconnect", []))}

    # ------------------------------------------------ device rules
    for mac, kinds in per_kind.items():
        f = facts[mac]
        name = dname(mac)

        kicks = kinds.get("kick", [])
        for start, end, n in _bursts([e["ts"] for e in kicks], cfg.kick_loop_count, cfg.kick_loop_minutes * 60):
            near = [r for t, r in rssi_ev[mac] if start - 600 <= t <= end]
            near += [s["rssi"] for s in smp.get(mac, []) if start - 600 <= s["ts"] <= end and s.get("rssi") is not None]
            band = next((e.get("band") for e in kicks if start <= e["ts"] <= end and e.get("band")), None)
            limit = thresholds.get(_IFACE_WL.get(band or "", ""), None)
            fixes = ["Move the device closer to a mesh unit, or a unit closer to where the device is used.",
                     "If it supports 5 GHz, make sure it uses it; if it never moves, use a network cable."]
            if limit is not None and limit > -78:
                fixes.append(f"Lower the router's Roaming assistant threshold for {band} GHz from {limit} dBm to about "
                             f"-75 dBm (Wireless → Professional) so it kicks less eagerly.")
            fixes.append(USB_ADAPTER_HINT)
            issues.append(_issue(
                "kick_loop", "high", "device", mac, f"{name} keeps getting kicked off Wi-Fi",
                f"Kicked {n} times between {_hm(start)} and {_hm(end)}"
                + (f"; signal was down to {min(near)} dBm" if near else "") + ".",
                "The mesh's roaming assistant disconnects a device whose signal drops below "
                + (f"its threshold ({limit} dBm on {band} GHz)" if limit is not None else "its threshold")
                + ", hoping it reconnects to a closer unit. If it rejoins the same unit with the same weak signal, "
                  "it loops: each kick is a few seconds offline, which feels like lag or dropouts.",
                fixes, start, end, count=n))

        cands = kinds.get("roam_candidate", [])
        for start, end, n in _bursts([e["ts"] for e in cands], cfg.roam_storm_count, cfg.roam_storm_seconds):
            issues.append(_issue(
                "roam_storm", "high", "device", mac, f"{name} is bounced between mesh units",
                f"{n} roam attempts within {int(end - start) or 1} s around {_hm(start)}.",
                "The device sits where two units are about equally strong (or both weak), so the mesh keeps "
                "trying to move it. Every attempt can briefly stall traffic.",
                ["Move the device a little towards one unit, or move a unit so one is clearly stronger.",
                 "Lower the Roaming assistant threshold so it only steps in when the signal is really poor."],
                start, end, count=n))

        # Sticky: the router found a clearly better unit but the device did not move.
        sticky = []
        moved = [e["ts"] for e in kinds.get("moved_away", [])] + [e["ts"] for e in kinds.get("connect", [])]
        for e in cands:
            d = e.get("detail") or {}
            if d.get("target_rssi") is None or d.get("rssi") is None:
                continue
            if d["target_rssi"] - d["rssi"] >= cfg.sticky_margin_db and not any(e["ts"] <= t <= e["ts"] + 120 for t in moved):
                sticky.append(e)
        if len(sticky) >= 2:
            d = sticky[-1]["detail"]
            issues.append(_issue(
                "sticky_client", "medium", "device", mac, f"{name} sticks to a weaker unit",
                f"{len(sticky)} times the mesh saw a unit {d['target_rssi'] - d['rssi']} dB stronger "
                f"({uname(d.get('target'))}, {d['target_rssi']} dBm vs {d['rssi']} dBm) but the device stayed.",
                "The device decides when to roam. Some (often older laptops, USB adapters and IoT devices) ignore "
                "the mesh's suggestions and hold on to the first unit they joined.",
                ["Turn Wi-Fi off and on when you move, to make it join the nearest unit.",
                 "Update the Wi-Fi driver; on Windows raise \"Roaming aggressiveness\" one step.",
                 "For devices that never move, a cable or a unit closer to them is the real fix."],
                sticky[0]["ts"], sticky[-1]["ts"], count=len(sticky)))

        drops = kinds.get("disconnect", [])
        for start, end, n in _bursts([e["ts"] for e in drops], cfg.flapping_count, cfg.flapping_minutes * 60):
            good_signal = f["typical_rssi"] is not None and f["typical_rssi"] >= -67
            issues.append(_issue(
                "flapping", "medium", "device", mac, f"{name} keeps disconnecting",
                f"Disconnected {n} times between {_hm(start)} and {_hm(end)}"
                + (f"; its typical signal is {f['typical_rssi']:.0f} dBm" if f["typical_rssi"] is not None else "") + ".",
                ("Its signal is good, so the device itself is leaving: usually Wi-Fi power saving, a driver "
                 "problem, or the device sleeping and waking." if good_signal else
                 "It is disconnecting at a weak or unknown signal: coverage, interference or power saving."),
                (["Turn off Wi-Fi power saving on the device (Windows: Power options and the adapter's Advanced "
                  "settings; phones: battery optimisation for the apps that matter).",
                  "Update the Wi-Fi driver or firmware.", "Forget the network on the device and join again."]
                 + ([USB_ADAPTER_HINT] if not good_signal else [])),
                start, end, count=n))

    for mac, f in facts.items():
        name = dname(mac)
        if f["typical_rssi"] is not None and f["typical_rssi"] < cfg.weak_rssi_dbm and f["samples"] >= 5:
            issues.append(_issue(
                "weak_signal", "medium", "device", mac, f"{name} has a weak signal",
                f"Typical signal {f['typical_rssi']:.0f} dBm on {f['band'] or '?'} GHz via {uname(f['unit'])} "
                f"({f['samples']} readings).",
                "Below about -70 dBm the link drops to slow rates and retries a lot; the device is at the edge "
                "of coverage, behind walls, floors or furniture, or its antenna is weak.",
                ["Move the device or the nearest unit so there are fewer walls between them.",
                 "Use 5 GHz where possible at short range; 2.4 GHz only reaches further.",
                 "For a fixed device (TV, console, desktop) use a cable or a powerline adapter."]))
        if f["slow"] and f["samples"] >= 5:
            issues.append(_issue(
                "slow_link", "low", "device", mac, f"{name} has a slow Wi-Fi link",
                f"Typical link rate {f['typical_rate']:.0f} Mbit/s on {f['band'] or '?'} GHz.",
                "The link rate is the speed the radio negotiated. It is low with a weak signal, an old Wi-Fi "
                "standard, or simply because the device was idle (phones drop their rate when asleep).",
                ["Check it while the device is in use; if still low, improve the signal (see Weak signal).",
                 "Older 2.4 GHz-only devices cannot go faster; that is normal for them."]))

    # ------------------------------------------------ unit / radio rules
    for r in radios:
        band = r.get("band")
        uname_r = uname(r.get("unit"))
        tx, rx = r.get("tx_fail_pct"), r.get("rx_crc_pct")
        worst = max(x for x in (tx, rx, 0) if x is not None)
        bad = (tx or 0) >= cfg.radio_tx_fail_bad or (rx or 0) >= cfg.radio_rx_err_bad
        warn = (tx or 0) >= cfg.radio_tx_fail_warn or (rx or 0) >= cfg.radio_rx_err_warn
        if bad or warn:
            if band == "2.4":
                fixes = ["Set 2.4 GHz to 20 MHz channel width and a fixed channel of 1, 6 or 11 (whichever neighbours "
                         "use least; the probes' Wi-Fi survey shows this).",
                         "Move devices that can use 5 GHz off 2.4 GHz.",
                         "Keep units away from microwaves, baby monitors, cordless phones, USB 3 hubs and TVs."]
            else:
                fixes = ["Use a fixed channel instead of Auto if neighbours keep moving onto yours.",
                         "Try a narrower channel width (80 instead of 160 MHz) if the unit is far from its devices."]
            issues.append(_issue(
                "radio_errors", "high" if bad else "medium", "unit", r.get("unit"),
                f"{uname_r} {band or '?'} GHz radio has many failed transmissions",
                f"Transmit failures {tx if tx is not None else '?'}%, receive errors {rx if rx is not None else '?'}% "
                "(counted since the unit last restarted).",
                "Frames that fail or arrive corrupted must be resent. Usually interference from neighbouring "
                "networks or other equipment, or devices at the edge of coverage pulling the radio down.",
                fixes, worst=worst))
        temp = r.get("temperature")
        if temp is not None and temp >= cfg.radio_temp_warn:
            issues.append(_issue(
                "radio_hot", "medium", "unit", r.get("unit"), f"{uname_r} is running hot",
                f"Radio temperature {temp:.0f} °C.",
                "Hot radios lower their power or become unstable.",
                ["Give the unit space and airflow; keep it out of cupboards and direct sun."]))

    for u in units:
        if u.get("reachable"):
            continue
        err = u.get("last_error") or "no answer"
        unmanaged = "cfg_device_list" in err
        name = u.get("alias") or u.get("model") or u["mac"]
        issues.append(_issue(
            "unit_unreachable", "info" if unmanaged else "medium", "unit", u["mac"],
            f"MeshLens cannot read {name}" if not unmanaged else f"{name} is not in the router's mesh list",
            err,
            ("The router reports clients on this unit but does not list it as a managed AiMesh node, so MeshLens "
             "has no address to read it at. Its clients still show up via the router." if unmanaged else
             "The unit did not answer over SSH: it may be off, its backhaul down, or SSH not enabled on it."),
            (["In the ASUS app check the unit is a mesh node (not a separate router or repeater)."] if unmanaged else
             ["Check the unit is powered and shows as connected in the ASUS app.",
              "SSH is set on the main router and applies to nodes after they sync; reboot the node if needed."])))

    # Many devices dropping off one unit at once points at the unit, not the devices.
    by_unit: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        if e["kind"] in ("disconnect", "kick", "leave") and e.get("unit") and e.get("mac"):
            by_unit[e["unit"]].append(e)
    for unit, evs in by_unit.items():
        i, last_end = 0, 0.0
        for j in range(len(evs)):
            while evs[j]["ts"] - evs[i]["ts"] > 60:
                i += 1
            macs = {e["mac"] for e in evs[i:j + 1]}
            if len(macs) >= cfg.mass_drop_devices and evs[i]["ts"] > last_end:
                last_end = evs[j]["ts"] + 60
                issues.append(_issue(
                    "mass_drop", "high", "unit", unit, f"Many devices dropped off {uname(unit)} at once",
                    f"{len(macs)} devices disconnected within a minute at {_hm(evs[i]['ts'])}.",
                    "When several devices drop together the cause is the unit, not the devices: a radio restart, "
                    "a lost backhaul link, a power blip, or a burst of interference.",
                    ["Check the unit's uptime and backhaul in the ASUS app around that time.",
                     "If it repeats, update the mesh firmware and check the unit's power supply.",
                     "A wired (Ethernet) backhaul removes the wireless backhaul as a cause."],
                    evs[i]["ts"], evs[j]["ts"], count=len(macs)))

    # Crowded 2.4 GHz radios (devices connected in the last 10 minutes).
    recent: dict[tuple[str, str], set[str]] = defaultdict(set)
    for mac, rows in smp.items():
        if rows and rows[-1]["ts"] >= now - 600 and rows[-1].get("band") == "2.4" and rows[-1].get("unit"):
            recent[(rows[-1]["unit"], "2.4")].add(mac)
    for (unit, _band), macs in recent.items():
        if len(macs) >= cfg.crowded_clients_24:
            issues.append(_issue(
                "crowded_radio", "low", "unit", unit, f"{uname(unit)} 2.4 GHz has {len(macs)} devices",
                f"{len(macs)} devices on one 2.4 GHz radio.",
                "Devices share airtime; many slow 2.4 GHz devices (smart plugs, cameras) leave less for the rest.",
                ["Move devices that support 5 GHz off 2.4 GHz.", "Spread smart-home devices over units, or use a "
                 "separate IoT network on 2.4 GHz only."]))

    # ------------------------------------------------ patterns across devices
    scores = {mac: device_score(f["kicks"], f["roam_attempts"], f["disconnects"], f["typical_rssi"], f["slow"])
              for mac, f in facts.items()}
    by_vendor: dict[str, list[str]] = defaultdict(list)
    for mac in facts:
        v = (devs.get(mac) or {}).get("vendor")
        if v:
            by_vendor[v].append(mac)
    for vendor, macs in by_vendor.items():
        poor = [m for m in macs if scores[m] < 60]
        if len(poor) >= 2 and len(poor) * 2 >= len(macs):
            issues.append(_issue(
                "vendor_pattern", "medium", "network", vendor, f"Devices with {vendor} Wi-Fi are unstable as a group",
                f"{len(poor)} of {len(macs)} {vendor} devices are unstable: " + ", ".join(dname(m) for m in poor[:6]) + ".",
                "When devices of one maker misbehave together, it is usually their Wi-Fi chip or driver, not where "
                "they are.",
                ["Update drivers or firmware for these devices.", "Turn off Wi-Fi power saving on them.",
                 USB_ADAPTER_HINT], devices=poor))

    # ------------------------------------------------ settings review (main router, read-only)
    main = next((u for u in units if u.get("role") == "router"), None)
    s = settings.get(main["mac"], {}) if main else {}
    for wl, label in _WL_BAND.items():
        v = thresholds.get(wl)
        if v is not None and v > -70:
            issues.append(_issue(
                "aggressive_roaming", "medium", "network", wl, f"Roaming assistant on {label} is aggressive",
                f"Threshold {v} dBm.", "Devices are disconnected while their signal is still usable, which causes "
                "kick loops for devices that cannot find a better unit.",
                ["Set it to about -70 to -75 dBm (Wireless → Professional → Roaming assistant)."]))
    if s.get("wps_enable") == "1":
        issues.append(_issue("wps_on", "low", "network", "wps", "WPS is switched on", "wps_enable = 1.",
                             "WPS push-button/PIN joining is a known weak point and rarely needed.",
                             ["Turn WPS off (Wireless → WPS) unless you use it."]))
    if s.get("misc_http_x") == "1":
        issues.append(_issue("remote_admin", "high", "network", "misc_http_x", "Router admin page is reachable from the internet",
                             "Web access from WAN is enabled.", "Anyone on the internet can try to log in to the router.",
                             ["Turn off Administration → System → Enable Web Access from WAN, or limit it to known addresses."]))
    if s.get("dmz_ip"):
        issues.append(_issue("dmz", "medium", "network", "dmz_ip", "A device is in the DMZ",
                             f"DMZ points at {s['dmz_ip']}.", "Every incoming connection from the internet goes to that device.",
                             ["Use specific port forwards instead, or turn the DMZ off."]))
    if s.get("upnp_enable") == "1":
        issues.append(_issue("upnp_on", "info", "network", "upnp", "UPnP is on",
                             "Devices can open ports on the router by themselves.",
                             "Handy for consoles and video calls; a risk if a device is compromised.",
                             ["Leave it on if consoles or games need it; otherwise turn it off (WAN → Virtual Server)."]))
    if s.get("emf_enable") == "0":
        issues.append(_issue("igmp_snooping_off", "info", "network", "emf", "IGMP snooping is off",
                             "Multicast (casting, IPTV, Sonos, smart-home discovery) is sent like broadcast to every device.",
                             "On Wi-Fi this uses airtime at the slowest rate and can add lag for everyone.",
                             ["If you use IPTV or a lot of casting, try turning on IGMP snooping (LAN → IPTV) and check "
                              "casting still works."]))

    # ------------------------------------------------ watched and new devices
    for d in devices:
        if d.get("watched") and d.get("last_seen") and now - d["last_seen"] > cfg.watched_offline_hours * 3600:
            issues.append(_issue("watched_offline", "medium", "device", d["mac"], f"{dname(d['mac'])} has not been seen",
                                 f"Last seen {_ago(now - d['last_seen'])} ago.",
                                 "A device you are watching is off the network.", ["Check it is powered and connected."]))
        if d.get("first_seen") and now - d["first_seen"] < 86400:
            issues.append(_issue("new_device", "info", "device", d["mac"], f"New device: {dname(d['mac'])}",
                                 f"First seen {_ago(now - d['first_seen'])} ago"
                                 + (f", made by {d['vendor']}" if d.get("vendor") else "") + ".",
                                 "A device joined the network for the first time (private-address phones also appear "
                                 "new when they change address).",
                                 ["If you don't recognise it, give it a name in Devices or change the Wi-Fi password."]))

    issues.sort(key=lambda i: (SEVERITY_ORDER[i["severity"]], -(i.get("to") or 0)))

    # ------------------------------------------------ health
    dev_health = {mac: {"score": scores[mac], "label": _label(scores[mac]), **facts[mac]} for mac in facts}
    unit_health: dict[str, dict[str, Any]] = {}
    for u in units:
        managed = "cfg_device_list" not in (u.get("last_error") or "")
        if not managed:
            unit_health[u["mac"]] = {"score": None, "label": None, "note": "not a managed node"}
            continue
        sc = 0.0 if not u.get("reachable") else 100.0
        notes = []
        if u.get("reachable"):
            for r in radios:
                if r.get("unit") != u["mac"]:
                    continue
                for val, warn, bad in ((r.get("tx_fail_pct"), cfg.radio_tx_fail_warn, cfg.radio_tx_fail_bad),
                                       (r.get("rx_crc_pct"), cfg.radio_rx_err_warn, cfg.radio_rx_err_bad)):
                    if val is not None and val >= bad:
                        sc -= 20
                    elif val is not None and val >= warn:
                        sc -= 8
            sc -= 15 * sum(1 for i in issues if i["rule"] == "mass_drop" and i["subject"] == u["mac"])
            clients = [m for m, f in facts.items() if f["unit"] in [u["mac"], *(u.get("macs") or [])]]
            if clients:
                share = sum(1 for m in clients if scores[m] < 60) / len(clients)
                sc -= min(20, 40 * share)
                notes.append(f"{len(clients)} Wi-Fi devices")
        sc = max(0, round(sc))
        unit_health[u["mac"]] = {"score": sc, "label": _label(sc), "note": ", ".join(notes)}

    u_scores = [h["score"] for h in unit_health.values() if h["score"] is not None]
    d_scores = list(scores.values())
    net = None
    if u_scores or d_scores:
        parts = []
        if u_scores:
            parts.append((0.4, sum(u_scores) / len(u_scores)))
        if d_scores:
            parts.append((0.6, sum(d_scores) / len(d_scores)))
        w = sum(p[0] for p in parts)
        net = sum(p[0] * p[1] for p in parts) / w
        net -= min(25, 5 * sum(1 for i in issues if i["severity"] == "high"))
        net = max(0, round(net))
    counts = {k: sum(1 for i in issues if i["severity"] == k) for k in SEVERITY_ORDER}
    return {
        "generated": now,
        "issues": issues,
        "devices": dev_health,
        "units": unit_health,
        "network": {"score": net, "label": _label(net), "issue_counts": counts,
                    "devices_poor": sum(1 for s in d_scores if s < 60), "devices_seen": len(d_scores),
                    "summary": _summary(net, counts, issues)},
    }


def _summary(score: int | None, counts: dict[str, int], issues: list[dict[str, Any]]) -> str:
    if score is None:
        return "Not enough data yet: MeshLens needs a few router polls before it can judge the network."
    head = f"Network health {score}/100 ({_label(score)})."
    act = counts["high"] + counts["medium"]
    if not act:
        return head + " Nothing needs attention."
    top = [i["title"] for i in issues if i["severity"] in ("high", "medium")][:3]
    return head + f" {act} problem{'s' if act != 1 else ''} need attention, starting with: " + "; ".join(top) + "."


def compare_periods(rows: list[dict[str, Any]], days: int, today: str) -> dict[str, Any]:
    """Totals for the last ``days`` days vs the ``days`` before (before/after a change)."""
    import datetime as dt
    t = dt.date.fromisoformat(today)
    cur_from = (t - dt.timedelta(days=days - 1)).isoformat()
    prev_from = (t - dt.timedelta(days=2 * days - 1)).isoformat()

    def agg(rs: list[dict[str, Any]]) -> dict[str, Any]:
        n_days = max(1, len({r["day"] for r in rs}))
        scored = [r["score"] for r in rs if r.get("score") is not None]
        return {"days_with_data": len({r["day"] for r in rs}),
                "kicks_per_day": round(sum(r["kicks"] or 0 for r in rs) / n_days, 1),
                "roam_attempts_per_day": round(sum(r["roams"] or 0 for r in rs) / n_days, 1),
                "disconnects_per_day": round(sum(r["disconnects"] or 0 for r in rs) / n_days, 1),
                "avg_device_score": round(sum(scored) / len(scored)) if scored else None}

    cur = [r for r in rows if r["day"] >= cur_from]
    prev = [r for r in rows if prev_from <= r["day"] < cur_from]
    return {"days": days, "current": agg(cur), "previous": agg(prev)}


def trend(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Network totals per day from the daily device summaries."""
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by[r["day"]].append(r)
    out = []
    for day in sorted(by):
        rs = by[day]
        scored = [r["score"] for r in rs if r.get("score") is not None]
        out.append({"day": day, "devices": len(rs), "kicks": sum(r["kicks"] or 0 for r in rs),
                    "roam_attempts": sum(r["roams"] or 0 for r in rs),
                    "disconnects": sum(r["disconnects"] or 0 for r in rs),
                    "poor_devices": sum(1 for s in scored if s < 60),
                    "avg_score": round(sum(scored) / len(scored)) if scored else None})
    return out


def _hm(ts: float) -> str:
    return time.strftime("%d %b %H:%M", time.localtime(ts))


def _ago(s: float) -> str:
    return f"{s / 60:.0f} min" if s < 5400 else f"{s / 3600:.0f} h" if s < 172800 else f"{s / 86400:.0f} days"
