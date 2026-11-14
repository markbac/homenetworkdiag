"""ASUS AiMesh collector: poll the router and every reachable node, normalise, store.

One SSH session per unit per poll (``commands.unit_script``). The main router
supplies the mesh-wide view (node list, every client with node/band/RSSI, wired
clients, device database); each reachable node adds its own per-client radio
stats, radio health and log events.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ... import oui
from ...config import RouterConfig
from ...store import db, jsonl
from . import commands
from . import parsers as P
from .ssh import SshError, run_script

log = logging.getLogger("meshlens.asus")

Runner = Callable[[str, int, str, "str | None", str, int], str]


@dataclass
class UnitResult:
    mac: str
    ip: str
    main: bool
    ok: bool = False
    error: str | None = None
    chip: str = "unknown"
    identity: dict[str, str] = field(default_factory=dict)
    stations: list[dict[str, Any]] = field(default_factory=list)
    radios: list[dict[str, Any]] = field(default_factory=list)
    counters: dict[str, dict[str, int]] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    settings: dict[str, str] | None = None
    info: dict[str, Any] = field(default_factory=dict)     # snapshot shown on the Router tab
    sections: dict[str, str] = field(default_factory=dict)


def parse_unit(text: str, mac: str, ip: str, main: bool) -> UnitResult:
    """Turn one unit's batch output into a UnitResult (pure, testable)."""
    s = P.split_sections(text)
    r = UnitResult(mac=mac, ip=ip, main=main, ok=True, sections=s)
    r.identity = P.parse_nvram_kv(s.get("identity", ""))
    r.chip = P.chip_family(P.which_tools(s.get("tools", "")))
    for key, body in s.items():
        if key.startswith("sta_hostapd:"):
            r.stations += P.parse_hostapd_all_sta(body, key.split(":", 1)[1])
        elif key.startswith("sta_wlanconfig:"):
            r.stations += P.parse_wlanconfig_list_sta(body, key.split(":", 1)[1])
        elif key.startswith("mwctl_stat:"):
            iface = key.split(":", 1)[1]
            st = P.parse_mwctl_stat(body)
            if st:
                r.radios.append({"iface": iface, "band": P.band_of_iface(iface), **st})
    if r.chip == "qualcomm":
        for i in P.parse_iwconfig(s.get("iwconfig", "")):
            if i["iface"] in ("ath0", "ath1", "ath2"):
                r.radios.append({"iface": i["iface"], "band": i["band"], "noise": i.get("noise")})
    r.counters = P.parse_proc_net_dev(s.get("net_dev", ""))
    r.events = P.parse_log_events(s.get("log", ""), unit=mac)
    if "settings" in s:
        r.settings = P.parse_nvram_kv(s["settings"])
    r.info = {"identity": r.identity, **P.parse_uptime(s.get("uptime", "")), "ifaces": P.parse_iw_dev(s.get("iw_dev", "")),
              "iwconfig": P.parse_iwconfig(s.get("iwconfig", "")), "chip": r.chip}
    r.info["links"] = P.parse_links(s.get("links", ""))
    if s.get("port_status", "").strip():
        r.info["ports"] = P._json(s["port_status"])
    return r


class AsusCollector:
    """Polls the mesh. ``password`` is fetched per poll from the Credential Manager by the caller."""

    def __init__(self, cfg: RouterConfig, data_root: Path, runner: Runner = run_script):
        self.cfg = cfg
        self.data_root = data_root
        self.runner = runner
        self.seen_units: set[str] = set()   # units already polled once (rotated log read on first poll only)
        self.last_full = 0.0
        self.prev_cpu: dict[str, dict[str, list[int]]] = {}   # last /proc/stat per unit, for CPU %
        self.prev_links: dict[str, dict[str, dict[str, Any]]] = {}   # carrier_changes per unit/iface
        self.prev_chan: dict[str, dict[str, tuple]] = {}          # channel/width per unit/band
        self.status: dict[str, Any] = {"last_poll": None, "units": {}, "error": None}

    # ------------------------------------------------------------ polling

    def _run(self, host: str, password: str | None, main: bool, mac: str, full: bool) -> str:
        script = commands.unit_script(main=main, first_poll=mac not in self.seen_units, full=full)
        return self.runner(host, self.cfg.port, self.cfg.username, password, script, self.cfg.timeout_seconds)

    def poll(self, password: str | None, full: bool | None = None) -> dict[str, Any]:
        now = time.time()
        if full is None:
            full = now - self.last_full >= self.cfg.full_poll_seconds
        try:
            main_text = self._run(self.cfg.host, password, True, "main", full)
        except SshError as e:
            self.status.update(last_poll=now, error=str(e))
            log.warning("router poll failed: %s", e)
            return self.status
        sections = P.split_sections(main_text)
        mesh = P.parse_cfg_device_list(sections.get("cfg_device_list", ""))
        main_mac = next((u["mac"] for u in mesh if u["main"]), "main")
        # Nodes the mesh controller reports online but cfg_device_list leaves out: try them too.
        if "cfgmnt" in sections:
            self.cfgmnt = P.parse_cfgmnt(sections["cfgmnt"])
        listed = {u["mac"] for u in mesh}
        for mac, c in (getattr(self, "cfgmnt", None) or {}).items():
            if mac not in listed and c.get("online") and c.get("ip"):
                mesh.append({"model": c.get("model"), "ip": c["ip"], "mac": mac, "main": False, "unlisted": True})
        results = [parse_unit(main_text, main_mac, self.cfg.host, True)]
        self.seen_units.add("main")
        for u in mesh:
            if u["main"]:
                continue
            try:
                text = self._run(u["ip"], password, False, u["mac"], full)
                results.append(parse_unit(text, u["mac"], u["ip"], False))
            except SshError as e:
                err = str(e)
                if u.get("unlisted"):
                    err = ("online in the mesh, but SSH isn't open on it, so its settings from the main router "
                           f"(including SSH) haven't been applied ({err})")
                results.append(UnitResult(mac=u["mac"], ip=u["ip"], main=False, ok=False, error=err))
            self.seen_units.add(u["mac"])
        self.store(results, mesh, now, full)
        if full:
            self.last_full = now
        self.status.update(last_poll=now, error=None,
                           units={r.mac: {"ok": r.ok, "error": r.error, "chip": r.chip, "stations": len(r.stations),
                                          "events": len(r.events)} for r in results})
        return self.status

    # ------------------------------------------------------------ storing

    def store(self, results: list[UnitResult], mesh: list[dict[str, Any]], now: float, full: bool) -> None:
        main = results[0]
        s = main.sections
        aliases = {k.split(":", 1)[1].lower(): P.parse_node_settings(v) for k, v in s.items() if k.startswith("node:")}
        models = {u["mac"]: u for u in mesh}
        backhaul = P.parse_relist(s.get("relist", ""))

        for r in results:
            ident = r.identity
            db.upsert_unit(
                r.mac, ip=r.ip, role="router" if r.main else "node",
                model=(models.get(r.mac) or {}).get("model") or ident.get("odmpid"),
                productid=ident.get("productid") or None, chip=r.chip if r.ok else None,
                firmware=f"{ident.get('firmver')}.{ident.get('buildno')}_{ident.get('extendno')}" if ident.get("firmver") else None,
                alias="Main router" if r.main else (aliases.get(r.mac) or {}).get("alias"),
                parent_hint=None if r.main else (aliases.get(r.mac) or {}).get("prefer_parent"),
                reachable=1 if r.ok else 0, last_seen=now if r.ok else None, last_error=r.error or "",
                info=json.dumps({**r.info, "ts": now, "system": self._system(r, now)}, default=str) if r.ok else None,
                macs=json.dumps(sorted({r.mac, *(i["addr"] for i in P.parse_iw_dev(r.sections.get("iw_dev", "")) if i.get("addr")),
                                        *backhaul.get(r.mac, {}).values()})) if r.ok else None)
            if not r.ok:
                continue
            db.insert_many("station_samples", [{
                "ts": now, "unit": r.mac, "mac": st["mac"], "iface": st.get("iface"), "band": st.get("band"),
                "ssid": None, "rssi": st.get("rssi"), "tx_rate": st.get("tx_rate"), "rx_rate": st.get("rx_rate"),
                "tx_bytes": st.get("tx_bytes"), "rx_bytes": st.get("rx_bytes"), "inactive_ms": st.get("inactive_msec"),
                "connected_s": st.get("connected_time")} for st in r.stations])
            db.insert_many("radio_samples", [{
                "ts": now, "unit": r.mac, "iface": rd["iface"], "band": rd.get("band"),
                "tx_fail_pct": rd.get("tx_fail_pct"), "rx_crc_pct": rd.get("rx_crc_pct"),
                "temperature": rd.get("temperature"), "noise": rd.get("noise"), "data": json.dumps(rd)} for rd in r.radios])
            links = P.parse_links(r.sections.get("links", ""))
            db.insert_many("iface_counters", [{
                "ts": now, "unit": r.mac, "iface": k, "rx_bytes": v["rx_bytes"], "tx_bytes": v["tx_bytes"],
                "rx_packets": v.get("rx_packets"), "tx_packets": v.get("tx_packets"), "rx_errs": v.get("rx_errs"),
                "rx_drop": v.get("rx_drop"), "tx_errs": v.get("tx_errs"), "tx_drop": v.get("tx_drop"),
                "carrier_changes": (links.get(k) or {}).get("changes")}
                for k, v in r.counters.items()])
            db.insert_events(self._derived_events(r, links, now))
            if r.settings is not None:
                db.insert_many("unit_settings", [{"ts": now, "unit": r.mac, "data": json.dumps(r.settings)}])
            new = db.insert_events(r.events)
            jsonl.append(self.data_root, {"type": "asus_unit", "unit": r.mac, "chip": r.chip, "stations": r.stations,
                                          "radios": r.radios, "new_events": new})

        # Units that appear in the client data but are not managed nodes (e.g. Office, Garden).
        # Model, firmware, address and the controller's signal reading for every node it knows.
        # (Only for nodes the live list leaves out: the controller's record can be out of date.)
        for mac, c in (getattr(self, "cfgmnt", None) or {}).items():
            if mac == main.mac or (mac in models and not models[mac].get("unlisted")):
                continue
            db.upsert_unit(mac, model=c.get("model"), productid=c.get("productid"), firmware=c.get("firmware"),
                           ip=c.get("ip"), alias=c.get("alias"))
        for unit_mac, a in aliases.items():
            if unit_mac not in models:
                db.upsert_unit(unit_mac, role="node", alias=a.get("alias"), reachable=0, parent_hint=a.get("prefer_parent"),
                               last_error="serves devices, but the router doesn't list it as a synced mesh node (cfg_device_list)",
                               macs=json.dumps(sorted({unit_mac, *backhaul.get(unit_mac, {}).values()})))

        if "ookla_history" in s:
            from .speedtest import parse_history
            db.add_speed_tests([{**r, "trigger": "router (ASUS app)", "location": "router"}
                                for r in parse_history(s["ookla_history"])])

        # Units whose address is answered by another unit's backhaul radio are connected through that unit.
        if "arp" in s:
            arp = P.parse_proc_arp(s["arp"])
            sta_to_node = {sta: node for node, stas in backhaul.items() for sta in stas.values() if sta}
            for u in db.units():
                via = sta_to_node.get(arp.get(u.get("ip") or ""))
                if via and via != u["mac"]:
                    db.upsert_unit(u["mac"], via_unit=via)

        # Mesh-wide client view from the router.
        table = oui.load(download=False) if full else None
        nmp = P.parse_nmp_clients(s.get("nmp_clients", "")) if "nmp_clients" in s else {}
        leases = {l["mac"]: l for l in P.parse_leases(s.get("leases", ""))}
        auth = P.parse_allwclientlist(s.get("allwclientlist", ""))
        unit_macs = set(models) | set(aliases)
        clients = P.parse_clientlist(s.get("clientlist", ""))
        # Backhaul: each node's backhaul radios (relist.json) appear as Wi-Fi clients of their parent unit.
        sta_owner = {sta: node for node, stas in backhaul.items() for sta in stas.values()}
        links: dict[str, list[dict[str, Any]]] = {}
        for c in clients:
            node = sta_owner.get(c["mac"])
            if node and c.get("band"):
                links.setdefault(node, []).append({"parent": c["unit"], "band": c["band"], "rssi": c["rssi"], "ts": now})
        for node in backhaul:
            db.upsert_unit(node, backhaul=json.dumps(links.get(node, [])))
        for c in clients:
            if c["mac"] in unit_macs or c["mac"] in sta_owner:
                continue
            fields: dict[str, Any] = {"ip": c["ip"] or (leases.get(c["mac"]) or {}).get("ip"),
                                      "wired": 1 if c["wired"] else 0, "unit": c["unit"],
                                      "band": c["band"], "randomised": 1 if P.is_randomised(c["mac"]) else 0}
            mld = None
            if c["mac"] in auth:
                fields["wifi_auth"] = auth[c["mac"]]["auth"]
                mld = auth[c["mac"]].get("mld")
            self._enrich(fields, c["mac"], nmp, leases, table)
            if mld:
                # Name and address live on the MLD MAC (DHCP); show them on the link MAC the logs use.
                linked: dict[str, Any] = {}
                self._enrich(linked, mld, nmp, leases, table)
                if linked.get("name") and fields.get("name_src") != "dhcp":
                    fields["name"], fields["name_src"] = linked["name"], f"{linked['name_src']} (Wi-Fi 7 link of {mld})"
                fields["ip"] = fields.get("ip") or (leases.get(mld) or {}).get("ip")
            db.upsert_device(c["mac"], now, **fields)
        for mac, d in nmp.items():   # devices known to the router but not currently connected
            if not db.device(mac):
                fields = {"randomised": 1 if P.is_randomised(mac) else 0}
                self._enrich(fields, mac, nmp, leases, table)
                db.upsert_device(mac, d.get("conn_ts") or now, **fields)

        # Wi-Fi standard / streams / width seen on each device's link (kept on the device record).
        for r in results:
            for st in r.stations if r.ok else []:
                fields = {k: st.get(src) for k, src in (("wifi_gen", "wifi_gen"), ("wifi_nss", "nss"), ("wifi_width", "width"))
                          if st.get(src) is not None}
                dev = db.device(st["mac"]) if fields else None
                if not dev:
                    continue
                # The link's current mode drops when a device idles or is far away: keep the best it has shown.
                rank = {"legacy": 0, "Wi-Fi 4": 4, "Wi-Fi 5": 5, "Wi-Fi 6": 6, "Wi-Fi 7": 7}
                if dev.get("wifi_gen") and rank.get(fields.get("wifi_gen"), -1) < rank.get(dev["wifi_gen"], -1):
                    fields.pop("wifi_gen", None)
                for k in ("wifi_nss", "wifi_width"):
                    if k in fields and dev.get(k) and fields[k] < dev[k]:
                        fields.pop(k)
                if fields:
                    db.set_device_fields(st["mac"], **fields)

        # Address pool and connections per device (main router, full polls).
        if full and "conn_counts" in s:
            counts = P.parse_conn_counts(s["conn_counts"])
            by_ip = {d["ip"]: d["mac"] for d in db.devices() if d.get("ip")}
            db.insert_many("conn_samples", [{"ts": now, "ip": c["ip"], "mac": by_ip.get(c["ip"]), "count": c["count"]}
                                            for c in counts[:40]])
            main.info["conn_top"] = [{**c, "mac": by_ip.get(c["ip"])} for c in counts[:15]]
            main.info["conn_total"] = sum(c["count"] for c in counts)
        if full and main.settings and "leases" in s:
            pool = P.dhcp_pool(main.settings, len(P.parse_leases(s["leases"])))
            if pool:
                main.info["dhcp"] = pool
        if full and (main.info.get("conn_top") is not None or main.info.get("dhcp")):
            db.upsert_unit(main.mac, info=json.dumps({**main.info, "ts": now, "system": (db.units() and next(
                (u.get("info") or {}).get("system") for u in db.units() if u["mac"] == main.mac)) or {}}, default=str))

    def _derived_events(self, r: UnitResult, links: dict[str, dict[str, Any]], now: float) -> list[dict[str, Any]]:
        """Events MeshLens works out by comparing polls: wired link up/down, channel changes, new internet address."""
        import hashlib
        out: list[dict[str, Any]] = []

        def ev(kind: str, iface: str | None, detail: dict[str, Any]) -> None:
            out.append({"ts": now, "unit": r.mac, "kind": kind, "mac": None, "iface": iface, "detail": detail,
                        "raw_hash": hashlib.sha1(f"{r.mac}|{kind}|{iface}|{now}".encode()).hexdigest()})

        prev = self.prev_links.get(r.mac)
        if prev is not None:
            for iface, l in links.items():
                p = prev.get(iface)
                if P.is_wired(iface) and p and l["changes"] > p["changes"]:
                    ev("link_change", iface, {"changes": l["changes"] - p["changes"], "state": l["state"], "speed": l["speed"]})
        if links:
            self.prev_links[r.mac] = links
        # Channel per band (first interface of each band carries the radio's channel).
        chans: dict[str, tuple] = {}
        for i in P.parse_iw_dev(r.sections.get("iw_dev", "")):
            if i.get("band") and i.get("channel") and i["band"] not in chans and not i["iface"].startswith("apcli"):
                chans[i["band"]] = (i["channel"], i.get("width"), i["iface"])
        pc = self.prev_chan.get(r.mac)
        if pc is not None:
            for band, (ch, w, iface) in chans.items():
                if band in pc and (pc[band][0], pc[band][1]) != (ch, w):
                    ev("channel_change", iface, {"from_channel": pc[band][0], "to_channel": ch, "from_width": pc[band][1],
                                                 "width": w, "band": band})
        if chans:
            self.prev_chan[r.mac] = chans
        # Internet address change on the main router (read at full polls): the line reconnected.
        if r.main and r.settings and r.settings.get("wan0_ipaddr"):
            last = db.latest_settings().get(r.mac) or {}
            old, new = last.get("wan0_ipaddr"), r.settings["wan0_ipaddr"]
            if old and new and old != new:
                ev("wan_ip_change", None, {"from": old, "to": new})
        return out

    def _system(self, r: UnitResult, now: float) -> dict[str, Any]:
        """CPU (from the change since the last poll), memory, temperature, conntrack, storage; stored per poll."""
        s = r.sections
        cur = P.parse_proc_stat(s.get("cpu_stat", ""))
        sysd: dict[str, Any] = {**P.parse_meminfo(s.get("meminfo", "")), **P.parse_conntrack(s.get("conntrack", "")),
                                **P.parse_df(s.get("df", ""))}
        th = P.parse_thermal(s.get("thermal", ""))
        if th:
            sysd["temp_c"] = th["temp_c"]
        prev = self.prev_cpu.get(r.mac)
        if cur:
            if prev:
                u = P.cpu_usage(prev, cur)
                if u:
                    sysd.update(u)
            self.prev_cpu[r.mac] = cur
        load = r.info.get("load")
        if load:
            sysd["load1"] = load[0]
        if sysd:
            db.insert_many("unit_samples", [{"ts": now, "unit": r.mac, **{k: sysd.get(k) for k in (
                "cpu_pct", "max_core_pct", "softirq_pct", "load1", "mem_used_pct", "mem_avail_mb", "mem_total_mb",
                "temp_c", "conntrack", "conntrack_max", "jffs_pct", "tmp_pct")}, "uptime_s": r.info.get("uptime_s")}])
        return sysd

    @staticmethod
    def _enrich(fields: dict[str, Any], mac: str, nmp: dict[str, Any], leases: dict[str, Any],
                table: dict[str, str] | None) -> None:
        d = nmp.get(mac)
        lease_name = (leases.get(mac) or {}).get("hostname")
        # ASUS fills "name" with the DHCP vendor class (e.g. "android-dhcp-17", "MSFT 5.0") when it knows
        # nothing better; the DHCP host name ("Pixel-9a") is more useful, and the vendor class is an OS hint.
        vc_only = bool(d) and (d.get("name_src") or "").startswith("nmp_vc") or (
            bool(d) and d.get("name") and d.get("name") == d.get("vendorclass"))
        if d and d.get("name") and not vc_only:
            fields["name"], fields["name_src"] = d["name"], f"asus:{d.get('name_src') or 'db'}"
        elif lease_name:
            fields["name"], fields["name_src"] = lease_name, "dhcp"
        if d and d.get("vendorclass"):
            fields["os_hint"] = d["vendorclass"]
        v = oui.vendor(mac, table) if table else None
        if v:
            fields["vendor"], fields["vendor_src"] = v, "ieee"
        elif d and d.get("vendor") and d.get("vendor") != d.get("vendorclass"):
            fields["vendor"], fields["vendor_src"] = d["vendor"], "asus"
