"""LAN discovery orchestration: sweep, names, services, ports, classification.

Runs on its own schedule in a background thread; every step is paced and stays inside the
configured subnets (NFR-02). Results are merged into the device table keyed by MAC.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import threading
import time
from typing import Any

from .. import oui
from ..collectors.asus.parsers import is_randomised
from ..store import db, jsonl
from . import ports as ports_mod
from . import services, sweep

log = logging.getLogger("meshlens.discovery")


def classify(d: dict[str, Any], open_ports: set[int]) -> str | None:
    """Best-guess device type (SVC-07) from maker, services, ports and names. The user's type always wins."""
    text = " ".join(str(d.get(k) or "") for k in ("vendor", "name", "model", "names")).lower()
    svc = " ".join(json.loads(d.get("services") or "[]")).lower()
    rules = [
        ("powerline", ("devolo",), ()), ("network", ("ubiquiti", "tp-link switch", "netgear switch", "asus router"), ()),
        ("printer", ("printer", "epson", "brother", "canon", "hp inc", "laserjet"), ("printer", "ipp")),
        ("camera", ("camera", "arlo", "hikvision", "dahua", "reolink", "ring", "wyze", "axis"), ("rtsp", "axis")),
        ("speaker", ("sonos", "echo", "bose", "homepod"), ("sonos", "spotify connect", "airplay audio")),
        ("tv / streamer", ("tv", "roku", "fire tv", "chromecast", "shield", "apple tv", "bravia"), ("google cast", "android tv", "fire tv")),
        ("console", ("xbox", "playstation", "nintendo", "sony interactive"), ("xbox",)),
        ("nas / server", ("synology", "qnap", "nas", "raspberry", "unraid", "truenas"), ("file share", "time machine")),
        ("smart home", ("espressif", "tuya", "shelly", "sonoff", "hue", "ikea", "nest", "meross", "tapo", "kasa", "home assistant"),
         ("homekit", "matter", "esphome", "hue", "home assistant", "thread")),
        ("phone / tablet", ("iphone", "ipad", "galaxy", "pixel", "android", "oneplus", "xiaomi"), ("apple device",)),
        ("computer", ("desktop", "laptop", "-pc", "macbook", "imac", "workstation", "intel corporate", "dell", "lenovo",
                      "microsoft", "alfa"), ("workstation", "ssh")),
    ]
    for kind, words, svcs in rules:
        if any(w in text for w in words) or any(s in svc for s in svcs):
            return kind
    if {3389, 445, 135} & open_ports:
        return "computer"
    if {9100, 631, 515} & open_ports:
        return "printer"
    if {554, 8554} & open_ports:
        return "camera"
    if d.get("randomised"):
        return "phone / tablet"
    return None


_OPAQUE = __import__("re").compile(r"^(?:[0-9a-fA-F]{12}|[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,})$")


def mdns_name(s: dict[str, Any]) -> str | None:
    """Best human name from an mDNS record: TXT friendly name, then a readable instance or host name."""
    if s.get("friendly"):
        return s["friendly"]
    for n in [*(s.get("names") or []), s.get("host")]:
        if n and not _OPAQUE.match(n.split("-")[-1] if n.count("-") == 1 else n):
            return n
    return None


def split_proxied(arp: dict[str, str], units: list[dict[str, Any]]) -> tuple[dict[str, str], list[str]]:
    """Separate real ARP entries from ones a mesh unit answers for its clients.

    A mesh node with a wireless backhaul translates its clients' MACs, so from the PC many addresses
    show the node's (byte-0-varied) MAC. Those, and any MAC answering for 3+ addresses, are not used
    to identify devices. Returns ({ip: mac} usable, [proxied ips]).
    """
    tails = {m[3:] for u in units for m in [u["mac"], *(u.get("macs") or [])]}
    count: dict[str, int] = {}
    for mac in arp.values():
        count[mac] = count.get(mac, 0) + 1
    usable, proxied = {}, []
    for ip, mac in arp.items():
        unit_like = mac[3:] in tails
        if count[mac] >= 3 or unit_like:
            if not (unit_like and count[mac] == 1):   # a unit's own address is just the unit: skip silently
                proxied.append(ip)
            continue
        usable[ip] = mac
    return usable, proxied


class Discovery:
    def __init__(self, cfg_fn, data_root):
        self.cfg_fn = cfg_fn            # -> Config (live, follows settings changes)
        self.data_root = data_root
        self.status: dict[str, Any] = {"last_sweep": None, "last_services": None, "last_ports": None,
                                       "running": None, "found": 0, "error": None}
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._requests: list[tuple[str, Any]] = []
        self._lock = threading.Lock()

    # ------------------------------------------------------------ control
    def start(self) -> None:
        threading.Thread(target=self._loop, name="meshlens-discovery", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def request(self, what: str, arg: Any = None) -> None:
        """Run now: 'sweep', 'services', 'ports' (all) or ('ports', mac) for one device."""
        with self._lock:
            self._requests.append((what, arg))
        self._wake.set()

    # ------------------------------------------------------------ steps
    def run_sweep(self, now: float | None = None) -> int:
        cfg = self.cfg_fn()
        now = now or time.time()
        self.status["running"] = "sweep"
        ips = sweep.addresses(cfg.network.subnets, cfg.network.exclude)
        alive = sweep.sweep(ips, cfg.discovery.max_rate)
        arp = sweep.arp_table()
        nets = [ipaddress.ip_network(s, strict=False) for s in cfg.network.subnets]
        arp = {ip: mac for ip, mac in arp.items() if any(ipaddress.ip_address(ip) in n for n in nets)}
        table = oui.load(download=False)
        arp, proxied = split_proxied(arp, db.units())
        # Addresses answered by a mesh unit on behalf of its clients: the router's client list knows their real MAC.
        for ip in proxied:
            d = db.device_by_ip(ip)
            if d and alive.get(ip) is not None:
                db.set_device_fields(d["mac"], last_alive=now)
        n = 0
        for ip, mac in arp.items():
            known = db.device(mac)
            fields: dict[str, Any] = {"ip": ip, "randomised": 1 if is_randomised(mac) else 0}
            if not known or not known.get("vendor"):
                v = oui.vendor(mac, table) if table else None
                if v:
                    fields["vendor"], fields["vendor_src"] = v, "ieee"
            if not known:
                db.add_inventory_event(now, mac, "new_device", {"ip": ip, "via": "sweep"})
                fields["wired"] = None
            db.upsert_device(mac, now, **fields)
            if alive.get(ip) is not None:
                db.set_device_fields(mac, last_alive=now)
            n += 1
        # Names: reverse DNS and NetBIOS for everything that answered (proxied addresses via the router's IP→MAC).
        mac_of = dict(arp)
        for ip in proxied:
            d = db.device_by_ip(ip)
            if d:
                mac_of[ip] = d["mac"]
        for ip, nb in sweep.netbios_names(list(mac_of)).items():
            db.merge_names(mac_of[ip], {"netbios": nb["netbios"]})
        for ip, name in sweep.reverse_dns_many(list(mac_of)).items():
            if name:
                db.merge_names(mac_of[ip], {"dns": name})
        self.status.update(last_sweep=now, found=n, proxied=len(proxied), running=None, error=None)
        jsonl.append(self.data_root, {"type": "sweep", "ts": now, "alive": sum(v is not None for v in alive.values()),
                                      "arp": len(arp), "proxied": len(proxied)})
        return n

    def run_services(self, now: float | None = None) -> int:
        cfg = self.cfg_fn()
        now = now or time.time()
        self.status["running"] = "services"
        n = 0
        try:
            found = services.mdns_browse()
        except Exception as e:
            log.warning("mDNS browse failed: %s", e)
            found = {}
        for ip, s in found.items():
            d = db.device_by_ip(ip)
            if not d:
                continue
            old = set(json.loads(d.get("services") or "[]"))
            new = old | set(s["services"])
            for svc in sorted(set(s["services"]) - old):
                if old:
                    db.add_inventory_event(now, d["mac"], "new_service", {"service": svc})
            db.set_device_fields(d["mac"], services=json.dumps(sorted(new)), model=d.get("model") or s.get("model"))
            db.merge_names(d["mac"], {"mdns": mdns_name(s)})
            n += 1
        try:
            upnp = services.ssdp_discover(cfg.network.subnets)
        except Exception as e:
            log.warning("SSDP failed: %s", e)
            upnp = {}
        for ip, u in upnp.items():
            d = db.device_by_ip(ip)
            if not d:
                continue
            svcs = set(json.loads(d.get("services") or "[]")) | {f"UPnP {u.get('device_type') or 'device'}"}
            db.set_device_fields(d["mac"], services=json.dumps(sorted(svcs)),
                                 model=d.get("model") or " ".join(x for x in (u.get("manufacturer"), u.get("model")) if x) or None)
            db.merge_names(d["mac"], {"ssdp": u.get("friendly_name")})
            n += 1
        self._classify_all()
        self.status.update(last_services=now, running=None)
        return n

    def run_ports(self, macs: list[str] | None = None, now: float | None = None) -> int:
        cfg = self.cfg_fn()
        now = now or time.time()
        nets = [ipaddress.ip_network(s, strict=False) for s in cfg.network.subnets]
        devs = [d for d in db.devices() if d.get("ip") and (macs is None or d["mac"] in macs)
                and any(ipaddress.ip_address(d["ip"]) in n for n in nets)]
        if macs is None:  # scheduled scan: only devices seen in the last day
            devs = [d for d in devs if now - (d.get("last_seen") or 0) < 86400]
        n = 0
        for d in devs:
            if self._stop.is_set():
                break
            self.status["running"] = f"ports {d['ip']}"
            try:
                found, scanned = ports_mod.scan(d["ip"], cfg.discovery.port_set, cfg.discovery.max_rate)
            except Exception as e:
                log.warning("port scan of %s failed: %s", d["ip"], e)
                continue
            db.record_ports(d["mac"], d["ip"], found, scanned, "tcp", now)
            n += 1
        self._classify_all()
        self.status.update(last_ports=now, running=None)
        return n

    def _classify_all(self) -> None:
        by_mac: dict[str, set[int]] = {}
        for p in db.open_ports():
            by_mac.setdefault(p["mac"], set()).add(p["port"])
        for d in db.devices():
            t = classify(d, by_mac.get(d["mac"], set()))
            if t and t != d.get("dtype"):
                db.set_device_fields(d["mac"], dtype=t)

    # ------------------------------------------------------------ loop
    def _loop(self) -> None:
        # Sweep and service lookups start shortly after launch; the first full port scan waits 10 minutes.
        start = time.time()
        last = {"sweep": 0.0, "services": 0.0,
                "ports": start - self.cfg_fn().discovery.port_scan_hours * 3600 + 600}
        self._stop.wait(20)   # let the router poll fill the device table first
        while not self._stop.is_set():
            cfg = self.cfg_fn().discovery
            now = time.time()
            with self._lock:
                reqs, self._requests = self._requests, []
            try:
                for what, arg in reqs:
                    if what == "sweep":
                        self.run_sweep()
                        last["sweep"] = time.time()
                    elif what == "services":
                        self.run_services()
                        last["services"] = time.time()
                    elif what == "ports":
                        self.run_ports([arg] if arg else None)
                        if not arg:
                            last["ports"] = time.time()
                if cfg.enabled:
                    if now - last["sweep"] >= cfg.sweep_minutes * 60:
                        self.run_sweep()
                        last["sweep"] = time.time()
                    if now - last["services"] >= cfg.services_minutes * 60:
                        self.run_services()
                        last["services"] = time.time()
                    if cfg.port_scan_hours and now - last["ports"] >= cfg.port_scan_hours * 3600:
                        self.run_ports()
                        last["ports"] = time.time()
            except Exception as e:
                log.exception("discovery step failed")
                self.status.update(error=f"{type(e).__name__}: {e}", running=None)
            self._wake.wait(30)
            self._wake.clear()
