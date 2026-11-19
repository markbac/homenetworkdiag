"""MeshLens ESP32 radio probes (docs/04-esp32-probes.md; firmware: markbac/esp32 ``c5-probe``).

Discovery: a UDP broadcast to port 47800 every 30 s (``{"type": "hndprobe", "id", "ip", "port", ...}``), mDNS
``_hndprobe._tcp``, addresses set in Settings, and LAN devices whose name looks like a probe.

Pairing ("adoption"): ``POST /api/adopt`` shows a 6-digit code on the probe's screen; the user types it into
MeshLens, which sends ``POST /api/adopt/confirm`` and keeps the returned token in the Windows Credential Manager.
After that every data call carries ``Authorization: Bearer <token>``.

Reading (request / response, the probe scans on its own schedule): ``/api/info`` every poll; ``/api/wifi.json``,
``/api/air.json``, ``/api/alerts.json`` and ``/api/env`` at ``scan_seconds``. Nothing is ever changed on the probe
except pairing and its room, and only when the user asks.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from .. import secrets
from ..store import db, jsonl

log = logging.getLogger("meshlens.probes")

BEACON_PORT = 47800
SECRET_KIND = "probe"
Http = Callable[[str, str, str, "dict[str, Any] | None", "str | None", float], "tuple[int, dict[str, Any]]"]


def http_json(method: str, ip: str, path: str, body: dict[str, Any] | None = None, token: str | None = None,
              timeout: float = 6.0) -> tuple[int, dict[str, Any]]:
    """One JSON request to a probe on the LAN. Returns (status, body); network errors raise OSError."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://{ip}{path}", data=data, method=method,
                                 headers={"Accept": "application/json", **({"Content-Type": "application/json"} if data else {}),
                                          **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310 - LAN address of a known probe
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {"error": str(e)}


def parse_beacon(payload: bytes, sender_ip: str) -> dict[str, Any] | None:
    """The probe's UDP announcement; ``None`` for anything else."""
    try:
        d = json.loads(payload.decode("utf-8", "replace"))
    except ValueError:
        return None
    if not isinstance(d, dict) or d.get("type") != "hndprobe" or not re.fullmatch(r"[\w.-]{1,40}", str(d.get("id") or "")):
        return None
    # Use the packet's source address, not the address it claims (nothing on the LAN can point MeshLens elsewhere).
    return {"id": d["id"], "ip": sender_ip, "port": int(d.get("port") or 80), "chip": d.get("chip"),
            "bands": ",".join(d.get("bands") or []), "firmware": d.get("fw"), "adopted": bool(d.get("adopted"))}


def looks_like_probe(dev: dict[str, Any]) -> bool:
    """LAN devices worth asking for /api/info (probe host names are c5-xxxxxx / esp32c5-XXXXXX)."""
    n = (dev.get("name") or "").lower()
    return bool(re.match(r"(esp32)?c[56]-?[0-9a-f]{6}$", n))


class ProbeCollector:
    def __init__(self, cfg, data_root, http: Http = http_json):
        self.cfg = cfg
        self.data_root = data_root
        self.http = http
        self.status: dict[str, Any] = {"last_poll": None, "probes": 0, "error": None, "listening": False}
        self._last_scan: dict[str, float] = {}
        self._stop = threading.Event()
        self._sock: socket.socket | None = None
        self._zc = None

    # ------------------------------------------------------------ discovery

    def start(self) -> None:
        if not self.cfg.enabled:
            return
        threading.Thread(target=self._listen, name="probe-beacons", daemon=True).start()
        try:
            from zeroconf import ServiceBrowser, Zeroconf

            class _L:
                def add_service(_s, zc, type_, name):
                    info = zc.get_service_info(type_, name, 2000)
                    if info and info.parsed_addresses():
                        props = {k.decode(): (v or b"").decode() for k, v in (info.properties or {}).items()}
                        self.seen({"id": props.get("id") or name.split(".")[0], "ip": info.parsed_addresses()[0],
                                   "port": info.port or 80, "chip": props.get("chip"), "bands": props.get("bands"),
                                   "firmware": props.get("fw"), "adopted": props.get("adopted") == "1"})
                update_service = add_service

                def remove_service(_s, *a):
                    pass
            self._zc = Zeroconf()
            ServiceBrowser(self._zc, "_hndprobe._tcp.local.", _L())
        except Exception as e:  # zeroconf missing or the interface refused: UDP beacons still work
            log.info("mDNS browse for probes not available: %s", e)

    def stop(self) -> None:
        self._stop.set()
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
        if self._zc:
            try:
                self._zc.close()
            except Exception:
                pass

    def _listen(self) -> None:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("", BEACON_PORT))
            s.settimeout(2)
            self._sock = s
            self.status["listening"] = True
        except OSError as e:
            self.status.update(listening=False, error=f"can't listen for probe announcements on UDP {BEACON_PORT}: {e}")
            return
        while not self._stop.is_set():
            try:
                payload, (ip, _port) = s.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            b = parse_beacon(payload, ip)
            if b:
                self.seen(b)

    def seen(self, b: dict[str, Any], now: float | None = None) -> None:
        """Record an announcement (UDP or mDNS)."""
        now = now or time.time()
        prev = db.probe(b["id"])
        if prev and prev.get("paired") and prev.get("ip") and prev["ip"] != b["ip"]:
            # A paired probe "moving" address: only follow it if the old address no longer answers as that probe,
            # so an announcement from another device on the LAN can't make MeshLens send it the token.
            try:
                st, info = self.http("GET", prev["ip"], "/api/info", None, None, 3)
                if st == 200 and info.get("probe") == b["id"]:
                    return
            except OSError:
                pass
        db.upsert_probe(b["id"], ip=b["ip"], chip=b.get("chip"), bands=b.get("bands"), firmware=b.get("firmware"),
                        adopted=1 if b.get("adopted") else 0, last_seen=now,
                        **({} if prev else {"first_seen": now}))

    def candidates(self) -> list[dict[str, Any]]:
        """Known probes, plus addresses from Settings and LAN devices that look like probes (asked once)."""
        known = {p["ip"] for p in db.probes() if p.get("ip")}
        extra = [ip for ip in self.cfg.addresses if ip not in known]
        extra += [d["ip"] for d in db.devices() if d.get("ip") and d["ip"] not in known and looks_like_probe(d)]
        return extra

    # ------------------------------------------------------------ polling

    def token(self, pid: str) -> str | None:
        return secrets.get_secret(SECRET_KIND, pid)

    def due(self, now: float) -> bool:
        return self.cfg.enabled and now - (self.status.get("last_poll") or 0) >= self.cfg.poll_seconds

    def poll(self, now: float | None = None) -> dict[str, Any]:
        now = now or time.time()
        self.status["last_poll"] = now
        for ip in self.candidates():
            try:
                st, info = self.http("GET", ip, "/api/info", None, None, 4)
            except OSError:
                continue
            if st == 200 and info.get("probe"):
                self.seen({"id": info["probe"], "ip": ip, "chip": info.get("chip"), "bands": ",".join(info.get("bands") or []),
                           "firmware": info.get("firmware"), "adopted": info.get("adopted")}, now)
        errors = []
        probes = db.probes()
        for p in probes:
            try:
                self._poll_one(p, now)
            except OSError as e:
                db.upsert_probe(p["id"], last_error=f"no answer: {e}")
                errors.append(p["id"])
        self.status.update(probes=len(probes), error=("not answering: " + ", ".join(errors)) if errors else None)
        return self.status

    def _poll_one(self, p: dict[str, Any], now: float) -> None:
        pid, ip = p["id"], p["ip"]
        st, info = self.http("GET", ip, "/api/info", None, None, 5)
        if st != 200:
            db.upsert_probe(pid, last_error=f"/api/info answered {st}")
            return
        token = self.token(pid)
        pl = info.get("placement") or {}
        db.upsert_probe(pid, last_seen=now, last_error="", chip=info.get("chip"), firmware=info.get("firmware"),
                        bands=",".join(info.get("bands") or []), adopted=1 if info.get("adopted") else 0,
                        paired=1 if token and info.get("adopted") else 0, room=pl.get("room"), spot=pl.get("spot"),
                        info=json.dumps(info))
        if info.get("adopted") and not token:
            db.upsert_probe(pid, last_error="paired with another MeshLens (or the token was lost): use Forget on the probe, then pair again")
            return
        if now - self._last_scan.get(pid, 0) < self.cfg.scan_seconds:
            return
        self._last_scan[pid] = now
        got: dict[str, Any] = {}
        for path in ("/api/wifi.json", "/api/air.json", "/api/alerts.json", "/api/env"):
            st, body = self.http("GET", ip, path, None, token, 8)
            if st == 401:
                db.upsert_probe(pid, paired=0, last_error="the probe no longer accepts MeshLens's token: pair it again")
                return
            if st == 200:
                got[path] = body
        self.store(pid, got, now, info)

    def store(self, pid: str, got: dict[str, Any], now: float, info: dict[str, Any] | None = None) -> None:
        wifi = got.get("/api/wifi.json") or {}
        aps = wifi.get("aps") or []
        db.insert_many("probe_aps", [{
            "ts": now, "probe": pid, "bssid": (a.get("bssid") or "").lower(), "ssid": a.get("ssid") or a.get("revealed_ssid") or "",
            "band": a.get("band"), "channel": a.get("channel"), "rssi": a.get("rssi"), "width": a.get("width_mhz"),
            "pmf": a.get("pmf"), "akm": a.get("akm"), "clients": a.get("clients"), "own": 1 if a.get("own") else 0,
            "joined": 1 if a.get("joined") else 0} for a in aps if a.get("bssid") and (a.get("seen_s_ago") or 0) < 900])
        air = got.get("/api/air.json") or {}
        ch = ((info or {}).get("wifi") or {}).get("channel")
        db.insert_many("probe_clients", [{
            "ts": now, "probe": pid, "mac": (c.get("mac") or "").lower(), "bssid": (c.get("bssid") or "").lower(),
            "channel": c.get("channel") or ch, "rssi": c.get("rssi"), "frames": c.get("frames"), "bytes": c.get("bytes")}
            for c in air.get("clients") or [] if c.get("mac") and (c.get("seen_s_ago") or 0) < 900])
        evs = []
        alerts = (got.get("/api/alerts.json") or {}).get("alerts") or []
        known = db.events(now - 7 * 86400 - 600, kinds=["probe_alert"], limit=5000) if alerts else []
        for a in alerts:
            t = now - (a.get("s_ago") or 0)
            # The probe keeps returning its recent alerts: the same text from the same probe within 3 minutes is the same alert.
            if any(k["unit"] == pid and k["detail"].get("text") == a.get("text") and abs(k["ts"] - t) <= 180 for k in known):
                continue
            key = f"{pid}|{a.get('severity')}|{a.get('text')}|{round(t)}"
            evs.append({"ts": t, "unit": pid, "kind": "probe_alert", "mac": None, "iface": None,
                        "detail": {"severity": a.get("severity"), "text": a.get("text")},
                        "raw_hash": hashlib.sha1(key.encode()).hexdigest()})
        if air.get("deauths_last_minute"):
            evs.append({"ts": now, "unit": pid, "kind": "probe_deauths", "mac": None, "iface": None,
                        "detail": {"count": air["deauths_last_minute"]}, "raw_hash": hashlib.sha1(f"{pid}|deauth|{now}".encode()).hexdigest()})
        db.insert_events(evs)
        env = got.get("/api/env") or {}
        db.upsert_probe(pid, env=json.dumps({k: env.get(k) for k in ("temp_c", "rh", "battery_v", "moving")}) if env else None,
                        last_scan=now, air=json.dumps({"frames": air.get("frames"), "hopping": air.get("hopping"),
                                                        "deauths_last_minute": air.get("deauths_last_minute"),
                                                        "probers": len(air.get("probes") or []),
                                                        "beacons": air.get("access_points") or []}) if air else None)
        jsonl.append(self.data_root, {"type": "probe", "probe": pid, "ts": now, "aps": len(aps),
                                      "clients": len(air.get("clients") or []), "alerts": len((got.get("/api/alerts.json") or {}).get("alerts") or [])})

    # ------------------------------------------------------------ user actions

    def _probe(self, pid: str) -> dict[str, Any]:
        p = db.probe(pid)
        if not p:
            raise KeyError(pid)
        return p

    def start_pairing(self, pid: str) -> dict[str, Any]:
        p = self._probe(pid)
        st, body = self.http("POST", p["ip"], "/api/adopt", {}, None, 6)
        if st != 200:
            return {"ok": False, "error": body.get("error") or f"the probe answered {st}"}
        return {"ok": True, "expires_s": body.get("expires_s", 300)}

    def confirm_pairing(self, pid: str, code: str) -> dict[str, Any]:
        if not re.fullmatch(r"\d{6}", code or ""):
            return {"ok": False, "error": "the code is the 6 digits shown on the probe"}
        p = self._probe(pid)
        st, body = self.http("POST", p["ip"], "/api/adopt/confirm", {"code": code}, None, 6)
        if st != 200 or not re.fullmatch(r"[0-9a-fA-F]{16,128}", str(body.get("token") or "")):
            return {"ok": False, "error": body.get("error") or f"the probe answered {st}"}
        secrets.set_secret(SECRET_KIND, pid, body["token"])
        db.upsert_probe(pid, adopted=1, paired=1, last_error="")
        self._last_scan.pop(pid, None)
        return {"ok": True}

    def set_placement(self, pid: str, room: str, spot: str = "") -> dict[str, Any]:
        p = self._probe(pid)
        room, spot = room.strip()[:39], spot.strip()[:39]
        st, body = self.http("POST", p["ip"], "/api/placement", {"room": room, "spot": spot}, self.token(pid), 6)
        if st != 200:
            return {"ok": False, "error": body.get("error") or f"the probe answered {st}"}
        db.upsert_probe(pid, room=room, spot=spot)
        return {"ok": True}

    def forget(self, pid: str) -> dict[str, Any]:
        """Forget it on this side (the token and the record). The probe keeps its pairing until Forget on its screen."""
        secrets.clear_secret(SECRET_KIND, pid)
        db.delete_probe(pid)
        return {"ok": True}


def view(units: list[dict[str, Any]], devices: list[dict[str, Any]], now: float | None = None) -> list[dict[str, Any]]:
    """Everything the Probes page shows, per probe (read from the database)."""
    from ..hostpc import own_unit
    from ..names import display_name
    now = now or time.time()
    unames = {u["mac"]: u.get("alias") or u.get("model") or u["mac"] for u in units}
    devs = {d["mac"]: d for d in devices}
    aps = db.probe_aps_since(now - 3600)
    cls = db.probe_clients_since(now - 3600)
    alerts = db.events(now - 7 * 86400, kinds=["probe_alert"], limit=500)
    out = []
    for p in db.probes():
        pid = p["id"]
        mine = [a for a in aps if a["probe"] == pid]
        last_ts = max((a["ts"] for a in mine), default=None)
        cur = [a for a in mine if a["ts"] == last_ts]
        units_heard: dict[str, dict[str, Any]] = {}
        neigh = {"2.4": 0, "5": 0}
        for a in cur:
            u = own_unit(a["bssid"], units)
            if u:
                k = f"{unames.get(u, u)} {a.get('band')} GHz"
                if k not in units_heard or (a.get("rssi") or -100) > units_heard[k]["rssi"]:
                    units_heard[k] = {"unit": unames.get(u, u), "band": a.get("band"), "rssi": a.get("rssi"),
                                      "channel": a.get("channel"), "pmf": a.get("pmf")}
            elif a.get("band") in neigh:
                neigh[a["band"]] += 1
        talk: dict[str, dict[str, Any]] = {}
        for c in cls:
            if c["probe"] != pid:
                continue
            t = talk.setdefault(c["mac"], {"mac": c["mac"], "bytes": 0, "frames": 0, "rssi": c.get("rssi"), "channel": c.get("channel"),
                                           "via": unames.get(own_unit(c.get("bssid") or "", units) or "", "")})
            t["bytes"] = max(t["bytes"], c.get("bytes") or 0)      # counters are cumulative on the probe
            t["frames"] = max(t["frames"], c.get("frames") or 0)
        top = sorted(talk.values(), key=lambda t: -t["bytes"])[:10]
        for t in top:
            t["name"] = display_name(devs.get(t["mac"]), t["mac"])
        info = p.get("info") or {}
        out.append({**{k: p.get(k) for k in ("id", "ip", "chip", "bands", "firmware", "adopted", "paired", "room", "spot",
                                             "first_seen", "last_seen", "last_scan", "last_error", "env")},
                    "online": bool(p.get("last_seen")) and now - p["last_seen"] < 3 * 60 + 90,
                    "link": info.get("wifi"), "mode": info.get("mode"), "uptime_s": info.get("uptime_s"),
                    "units_heard": sorted(units_heard.values(), key=lambda x: -(x["rssi"] or -100)),
                    "neighbours": neigh, "aps_heard": len(cur), "top_talkers": top,
                    "air": {k: v for k, v in (p.get("air") or {}).items() if k != "beacons"},
                    "alerts": [{"ts": e["ts"], **e["detail"]} for e in alerts if e["unit"] == pid][:20]})
    return out
