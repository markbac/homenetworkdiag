"""Engine: schedules collectors in a background thread and owns shared state.

The tray app and the web server both talk to one Engine instance.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from . import config as config_mod
from . import analysis, channels, oui, secrets, topology, updater
from .collectors.asus.collector import AsusCollector
from .collectors.devolo import DevoloCollector
from .collectors.probes import ProbeCollector
from .discovery import Discovery
from .hostpc import HostMonitor
from .monitor import PingMonitor
from .net import icmp, speedtest
from .store import db, jsonl

log = logging.getLogger("meshlens.engine")
log_ = log


class Engine:
    def __init__(self, cfg: config_mod.Config):
        self.cfg = cfg
        self.root = config_mod.home()
        db.init(self.root / "meshlens.db")
        self.asus = AsusCollector(cfg.router, self.root)
        self.devolo = DevoloCollector(cfg.devolo, self.root)
        self.probes = ProbeCollector(cfg.probes, self.root)
        self.discovery = Discovery(lambda: self.cfg, self.root)
        self.monitor = PingMonitor(cfg.monitor, self._ping_targets)
        self.host = HostMonitor()
        self.speed_status: dict[str, Any] = {"running": False, "last": None, "router_running": False}
        self.update_info: dict[str, Any] = {"available": False, "current": updater.__version__, "checked": None,
                                            "state": None, "error": None, "self_update": updater.can_self_update()}
        self.advertiser = None  # mDNS announcement (meshlens.local), started with the web server
        self.on_quit = None   # set by the tray: called after the installer has been started
        self._isp_hop: tuple[float, tuple[str, int] | None] = (0.0, None)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self.paused = False
        self.started = time.time()
        self._analysis: dict[float, tuple[float, dict[str, Any]]] = {}
        self._channels: tuple[float, dict[str, Any]] | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------ control

    def start(self) -> None:
        try:
            from . import __version__
            db.note_app_version(__version__)
        except Exception:
            log.exception("could not record the MeshLens version")
        self._thread = threading.Thread(target=self._loop, name="meshlens-engine", daemon=True)
        self._thread.start()
        threading.Thread(target=oui.load, name="oui-load", daemon=True).start()
        self.monitor.start()
        self.discovery.start()
        self.probes.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self.monitor.stop()
        self.discovery.stop()
        self.probes.stop()
        if getattr(self, "advertiser", None):
            self.advertiser.stop()
        if self._thread:
            self._thread.join(timeout=10)

    def collect_now(self, full: bool = True) -> None:
        self._force_full = full
        self._wake.set()

    def apply_config(self, cfg: config_mod.Config) -> None:
        """Use a new configuration (already validated and saved)."""
        with self._lock:
            self.cfg = cfg
            self.asus.cfg = cfg.router
            self.devolo.cfg = cfg.devolo
            self.probes.cfg = cfg.probes
            self.monitor.cfg = cfg.monitor
            self._analysis.clear()

    def analysis(self, hours: float = 24, max_age: float = 60) -> dict[str, Any]:
        """Issues and health for the last ``hours`` (cached for ``max_age`` seconds).

        Only one analysis runs at a time: callers that arrive while one is running wait and share its result
        (the Overview, device list, map and tray all ask at once)."""
        with self._lock:
            hit = self._analysis.get(hours)
            if hit and time.time() - hit[0] < max_age:
                return hit[1]
        with self.__dict__.setdefault("_compute_lock", threading.Lock()):
            with self._lock:
                hit = self._analysis.get(hours)
                if hit and time.time() - hit[0] < max_age:
                    return hit[1]
            return self._analyse(hours)

    def _analyse(self, hours: float) -> dict[str, Any]:
        now = time.time()
        since = now - hours * 3600
        net = {"unit_events": db.events(since, kinds=["radar", "channel_change", "channel_replan", "unit_reboot", "link_change",
                                                     "wan_ip_change", "router_warning", "l2_loop", "probe_alert",
                                                     "probe_deauths"], limit=5000),
               "channel_plan": self.channels(), "probes": db.probes(),
               "probe_clients": db.probe_clients_since(max(since, now - 3600)), "latest_stations": (latest := db.latest_stations()),
               "band_history": db.band_history(now - 7 * 86400, [s["mac"] for s in latest
                                                                 if s.get("band") == "2.4" and now - s["ts"] < 900]),
               "devices": db.devices(),
               "iface_counters": db.iface_first_last(since), "units": db.units(),
               "unit_samples": db.unit_samples_since(since), "settings": db.latest_settings(), "host_lan": db.host_lan_since(since), "air": db.air_scan_since(max(since, now - 6 * 3600)),
               "analysis_cfg": self.cfg.analysis, "pings": db.ping_minutes(since), "speeds": db.speed_tests(now - 7 * 86400),
               "unit_names": {u["mac"]: u.get("alias") or u.get("model") or u["mac"] for u in db.units()},
               "plc_adapters": db.plc_adapters(), "plc_rates": db.plc_latest_rates(), "open_ports": db.open_ports(),
               "inventory": db.inventory_events(since), "monitor_cfg": self.cfg.monitor, "devolo_cfg": self.cfg.devolo}
        res = analysis.analyse(db.events(since, limit=200000), db.station_samples_since(since), db.latest_radio(),
                               db.units(), db.devices(), db.latest_settings(), self.cfg.analysis, now, net=net,
                               hidden=[(h["rule"], h["subject"]) for h in db.hidden_issues()])
        # Issue history: new / ongoing, and what was resolved (only from the standard 24 h view).
        try:
            log = db.log_issues(res["issues"], now) if hours == 24 else {}
            for i in res["issues"]:
                e = log.get((i["rule"], str(i.get("subject"))))
                if e:
                    i["first_seen"] = e["first_seen"]
                    i["status"] = "new" if now - e["first_seen"] < 86400 else "ongoing"
            res["resolved"] = [r for r in db.resolved_issues(now - 7 * 86400)]
        except Exception:
            log_.exception("issue history failed")
        with self._lock:
            self._analysis[hours] = (now, res)
        return res

    # ------------------------------------------------------------ changes the user marked, before / after
    def change_impact(self, ts: float, now: float | None = None) -> dict[str, Any]:
        """The same measures before and after a moment: equal windows, up to 24 h each side."""
        import statistics
        now = now or time.time()
        hrs = max(0.25, min(24.0, (now - ts) / 3600))
        w = hrs * 3600

        def measures(a: float, b: float) -> dict[str, Any]:
            pings = [p for p in db.ping_minutes(a) if p["ts"] < b]
            def loss(label: str):
                rs = [p for p in pings if (p.get("label") or "").lower() == label or p.get("target") == label]
                sent = sum(r["sent"] or 0 for r in rs)
                spikes = sum(1 for r in rs if (r.get("max_ms") or 0) >= 200)
                return (round(100 * sum(r["lost"] or 0 for r in rs) / sent, 2) if sent else None,
                        round(spikes / max(1, len(rs)) * 60, 1) if rs else None)
            rl, rs_ = loss("router")
            il, is_ = loss("1.1.1.1")
            disc = db.events_count(a, b, ["disconnect", "kick"])
            smp = db.station_samples_between(a, b)
            polls: dict[float, set] = {}
            for x in smp:
                if x.get("band") == "2.4":
                    polls.setdefault(x["ts"], set()).add(x["mac"])
            on24 = round(statistics.mean(len(v) for v in polls.values()), 1) if polls else None
            sp = [x for x in db.speed_tests(a) if x["ts"] < b and not x.get("error") and (x.get("location") or "pc") == "pc"]
            return {"router_loss_pct": rl, "router_spikes_per_h": rs_, "internet_loss_pct": il, "internet_spikes_per_h": is_,
                    "disconnects_per_h": round(disc / hrs, 1), "devices_on_24": on24,
                    "speed_down": round(statistics.median(x["down_mbps"] for x in sp)) if sp else None,
                    "speed_up": round(statistics.median(x["up_mbps"] for x in sp if x.get("up_mbps") is not None)) if sp else None,
                    "grade": sp[-1].get("grade") if sp else None, "speed_tests": len(sp)}
        return {"hours": round(hrs, 1), "before": measures(ts - w, ts), "after": measures(ts, min(now, ts + w))}

    def checklist(self) -> list[dict[str, Any]]:
        """Setup steps and whether each is done (None = optional / not applicable)."""
        now = time.time()
        r, st = self.cfg.router, self.asus.status
        units = db.units()
        devs = db.devices()
        probes = db.probes()
        from .analysis_net import key_device_suggestions
        sugg = key_device_suggestions(devs, db.open_ports(), units=units)
        items = [
            {"id": "router_password", "label": "Router login stored", "done": secrets.has_secret("asus", secrets.router_ident(r.username, r.host, r.port)),
             "detail": f"{r.username}@{r.host}:{r.port}", "goto": "settings:router"},
            {"id": "router_poll", "label": "Router read successfully", "done": bool(st.get("last_poll")) and not st.get("error"),
             "detail": st.get("error") or (f"last read {round((now - st['last_poll']) / 60)} min ago" if st.get("last_poll") else "not yet"),
             "goto": "settings:router"},
            {"id": "nodes", "label": "Every mesh unit readable", "done": bool(units) and all(u.get("reachable") for u in units),
             "detail": ", ".join(u.get("alias") or u["mac"] for u in units if not u.get("reachable")) or f"{len(units)} units",
             "goto": "router"},
            {"id": "speeds", "label": "Your internet plan's speed entered", "done": bool(self.cfg.monitor.expected_down_mbps),
             "detail": (f"{self.cfg.monitor.expected_down_mbps:.0f}/{self.cfg.monitor.expected_up_mbps:.0f} Mbit/s"
                        if self.cfg.monitor.expected_down_mbps else "so MeshLens can tell when the line is slower than it should be"),
             "goto": "settings:monitor"},
            {"id": "watched", "label": "Key devices watched", "done": not sugg and any(d.get("watched") for d in devs),
             "detail": (f"{sum(1 for d in devs if d.get('watched'))} watched" + (f"; suggested: {', '.join(x['name'] for x in sugg[:6])}" if sugg else "")),
             "goto": "watch-suggested", "suggested": [x["mac"] for x in sugg]},
            {"id": "powerline", "label": "Powerline adapters found", "done": (bool(db.plc_adapters()) if self.cfg.devolo.enabled else None),
             "detail": f"{len(db.plc_adapters())} adapters" if self.cfg.devolo.enabled else "turned off", "goto": "settings:devolo"},
            {"id": "probes", "label": "Probes paired and placed",
             "done": (all(p.get("paired") and p.get("room") for p in probes) if probes else None),
             "detail": (", ".join(f"{p['id']}: {'paired' if p.get('paired') else 'not paired'}{', ' + p['room'] if p.get('room') else ', no room'}" for p in probes)
                        or "optional: none found yet"), "goto": "probes"},
            {"id": "hostpc", "label": "This PC used as a sensor", "done": self.cfg.hostpc.enabled, "detail": "air survey and LAN broadcast counts",
             "goto": "settings:hostpc"},
            {"id": "updates", "label": "Automatic update checks", "done": self.cfg.updates.check_automatically, "detail": "",
             "goto": "settings:updates"},
        ]
        return items

    def set_issue_hidden(self, rule: str, subject: str, hidden: bool, title: str | None = None) -> None:
        db.set_issue_hidden(rule, subject, hidden, title)
        with self._lock:
            self._analysis.clear()

    def channels(self) -> dict[str, Any]:
        """Channel plan (5 GHz blocks, 2.4 GHz 1/6/11) and radar incidents over 30 days (cached for 60 s)."""
        now = time.time()
        with self._lock:
            if self._channels and now - self._channels[0] < 60:
                return self._channels[1]
        units = db.units()
        unames: dict[str, str] = {}
        skip: set[str] = set()
        for u in units:
            for m in [u["mac"], *(u.get("macs") or [])]:
                unames[m] = u.get("alias") or u.get("model") or u["mac"]
                skip.add(m)
        from .names import display_name
        dnames = {d["mac"]: display_name(d, d["mac"]) for d in db.devices()}
        ue = db.events(now - 30 * 86400, kinds=["radar", "channel_change", "channel_replan"], limit=5000)
        plan = channels.plan(db.air_scan_since(now - 24 * 3600), units, ue, now,
                             probe_aps=db.probe_aps_since(now - 6 * 3600), probes=db.probes())
        first = channels.radar_incidents(ue, [], None, unames)
        latest = {s["mac"]: s for s in db.latest_stations()}
        drops, moves = [], {}
        for inc in first["incidents"][-20:]:
            t0, t1 = inc["ts"], inc["until"]
            drops += db.events(t0 - 10, kinds=["disconnect", "leave", "kick"], limit=2000, until=t1 + channels.AFTER_S)
            moves[t0] = channels.band_moves_after(t0, db.station_samples_between(t0 - 600, t1 + 600), latest, skip, dnames,
                                                 until=t1)
        plan["radar"] = channels.radar_incidents(ue, drops, moves, unames)
        with self._lock:
            self._channels = (now, plan)
        return plan

    def topology(self) -> dict[str, Any]:
        now = time.time()
        t = topology.build(db.units(), db.devices(), db.station_samples_since(now - 900), db.plc_adapters(),
                           db.plc_latest_rates(), self.analysis(24), now)
        # Extra facts drawn on the diagram.
        from . import graphs
        speeds = [x for x in db.speed_tests(now - 7 * 86400) if not x.get("error")]
        radios: dict[str, list] = {}
        for r in db.latest_radio():
            radios.setdefault(r["unit"], []).append({"band": r.get("band"), "tx_fail_pct": r.get("tx_fail_pct"),
                                                     "temperature": r.get("temperature")})
        units = db.units()
        router = next((u["mac"] for u in units if u.get("role") == "router"), None)
        wan = graphs.throughput(db.throughput_counters(now - 600), {}, router)["wan"]
        t["summary"] = {
            "speed": speeds[-1] if speeds else None,
            "latency": [{"label": x["label"], "kind": x["kind"], "avg_ms": x["avg_ms"],
                         "loss_pct": round(100 * x["lost"] / x["sent"], 1) if x["sent"] else None}
                        for x in self.monitor.live() if x["kind"] in ("router", "internet")],
            "radios": radios,
            "wan_now": {k: (v[-1][1] if v else None) for k, v in wan.items()},
            "system": {u["mac"]: (u.get("info") or {}).get("system") or {} for u in units},
        }
        return t

    # ------------------------------------------------------------ monitors and tests

    def _ping_targets(self) -> list[dict[str, str]]:
        m = self.cfg.monitor
        out = [{"target": self.cfg.router.host, "kind": "router", "label": "Router"}]
        units = db.units()
        if m.include_nodes:
            out += [{"target": u["ip"], "kind": "node", "label": u.get("alias") or u.get("model") or u["ip"]}
                    for u in units if u.get("role") == "node" and u.get("ip") and u.get("reachable")]
        if m.include_isp_hop:
            ts, hop = self._isp_hop
            dest = m.internet_targets[0] if m.internet_targets else "1.1.1.1"
            if time.time() - ts > 3600:
                try:
                    hop = icmp.isp_hop(dest, self.cfg.router.host)
                except Exception:
                    hop = None
                self._isp_hop = (time.time(), hop)
            if hop:
                out.append({"target": f"hop:{hop[1]}:{dest}", "kind": "isp", "label": f"ISP first hop ({hop[0]})"})
        out += [{"target": t, "kind": "internet", "label": t} for t in m.internet_targets]
        out += [{"target": t, "kind": "extra", "label": t} for t in m.extra_targets]
        if m.include_watched:
            out += [{"target": d["ip"], "kind": "device", "label": d.get("user_name") or d.get("name") or d["ip"]}
                    for d in db.devices() if d.get("watched") and d.get("ip")]
        seen, uniq = set(), []
        for t in out:
            if t["target"] not in seen:
                seen.add(t["target"])
                uniq.append(t)
        return uniq

    def run_router_speedtest(self, trigger: str = "manual") -> dict[str, Any]:
        """Ookla on the main router (the internet line itself). Only when switched on in Settings."""
        from .collectors.asus import speedtest as rst
        from .collectors.asus.ssh import run_script
        m, r = self.cfg.monitor, self.cfg.router
        if not m.router_speedtest:
            return {"error": "router speed tests are off (Settings → Monitors and speed tests)"}
        if self.speed_status["router_running"]:
            return {"error": "a router speed test is already running"}
        self.speed_status["router_running"] = True
        try:
            from .collectors.asus import parsers as P
            out = run_script(r.host, r.port, r.username, self.router_password(),
                             rst.run_script(m.internet_targets[0] if m.internet_targets else "1.1.1.1"), timeout=45)
            s = P.split_sections(out)
            res = rst.parse_run(s.get("ookla", ""), s.get("ping", ""))
        except Exception as e:
            res = {"error": f"{type(e).__name__}: {e}"}
        finally:
            self.speed_status["router_running"] = False
        row = {"ts": res.get("ts") or time.time(), "trigger": trigger, "location": "router", "server": res.get("server") or "router",
               **{k: res.get(k) for k in ("down_mbps", "up_mbps", "idle_ms", "down_loaded_ms", "up_loaded_ms", "grade", "error")}}
        db.add_speed_tests([row])
        with self._lock:
            self._analysis.clear()
        return row

    def run_speedtest(self, trigger: str = "manual") -> dict[str, Any]:
        """Blocking; the web server runs it in a thread. The ping monitor pauses meanwhile."""
        if self.speed_status["running"]:
            return {"error": "a speed test is already running"}
        m = self.cfg.monitor
        self.speed_status["running"] = True
        self.monitor.paused = True
        try:
            res = speedtest.run(m.speedtest_server, m.speedtest_seconds,
                                m.internet_targets[0] if m.internet_targets else "1.1.1.1")
        finally:
            self.monitor.paused = False
            self.speed_status["running"] = False
        row = {"ts": time.time(), "trigger": trigger, "location": "pc", **res}
        db.add_speed_tests([row])
        self.speed_status["last"] = row
        with self._lock:
            self._analysis.clear()
        return row

    # ------------------------------------------------------------ updates

    def check_update(self) -> dict[str, Any]:
        try:
            info = updater.check(include_prereleases=self.cfg.updates.include_prereleases)
            self.update_info.update(info, checked=time.time(), error=None)
        except Exception as e:
            self.update_info.update(checked=time.time(), error=f"could not check: {type(e).__name__}: {e}")
        return self.update_info

    def install_update(self) -> None:
        """Download, verify and start the installer; MeshLens then quits so files can be replaced."""
        u = self.update_info
        if not u.get("available"):
            u["error"] = "no update available"
            return
        if not updater.can_self_update():
            u["error"] = "this copy runs from source: update it with git pull"
            return
        try:
            u.update(state="downloading", error=None)
            path = updater.download(u, self.root / "updates")
            u["state"] = "starting installer (approve the Windows prompt)"
            updater.launch_installer(path)
            u["state"] = "installing: MeshLens will restart"
        except Exception as e:
            u.update(state=None, error=str(e))
            return
        threading.Timer(3.0, self._quit_for_update).start()

    def _quit_for_update(self) -> None:
        self.stop()
        if self.on_quit:
            self.on_quit()
        else:
            import os
            os._exit(0)

    def rollup(self, now: float | None = None) -> None:
        """Daily summaries for today and yesterday (kept 12 months, D-25)."""
        import datetime as dt
        now = now or time.time()
        today = dt.date.fromtimestamp(now)
        for d in (today - dt.timedelta(days=1), today):
            start = time.mktime(d.timetuple())
            end = time.mktime((d + dt.timedelta(days=1)).timetuple())
            db.rollup_day(d.isoformat(), start, end,
                          lambda k, r, dc, rssi: analysis.device_score(k, r, dc, rssi))

    def router_password(self) -> str | None:
        r = self.cfg.router
        return secrets.get_secret("asus", secrets.router_ident(r.username, r.host, r.port))

    def status(self) -> dict[str, Any]:
        r = self.cfg.router
        return {
            "version": __import__("meshlens").__version__,
            "uptime_s": round(time.time() - self.started),
            "paused": self.paused,
            "router": {"host": r.host, "port": r.port, "username": r.username,
                       "password_set": secrets.has_secret("asus", secrets.router_ident(r.username, r.host, r.port))},
            "asus": self.asus.status,
            "devolo": self.devolo.status,
            "probes": self.probes.status,
            "discovery": self.discovery.status,
            "speedtest": {"running": self.speed_status["running"], "router_running": self.speed_status["router_running"],
                          "router_enabled": self.cfg.monitor.router_speedtest},
            "update": self.update_info,
            "hostpc": self.host.status,
            "integrations": {s: {"enabled": getattr(self.cfg, s).enabled,
                                 "secret_set": bool(getattr(self.cfg, s).url) and
                                 secrets.has_secret(secrets.SERVICE_KINDS[s], secrets.service_ident(s, self.cfg)),
                                 "collector": "not yet implemented"}
                             for s in ("pihole", "homeassistant", "unifi")},
        }

    # ------------------------------------------------------------ loop

    _force_full = False

    @staticmethod
    def _last_speedtest_ts(location: str = "pc") -> float:
        rows = [x for x in db.speed_tests(time.time() - 30 * 86400) if (x.get("location") or "pc") == location
                and not str(x.get("trigger") or "").startswith("router (ASUS")]
        return rows[-1]["ts"] if rows else 0.0

    def _loop(self) -> None:
        last_prune = 0.0
        last_update_check = time.time() - self.cfg.updates.check_hours * 3600 + 120   # first check 2 min after start
        # With no test yet, the first scheduled one runs 10 minutes after start.
        last_speed = self._last_speedtest_ts() or time.time() - self.cfg.monitor.speedtest_hours * 3600 + 600
        last_router_speed = self._last_speedtest_ts("router") or time.time() - self.cfg.monitor.router_speedtest_hours * 3600 + 900
        while not self._stop.is_set():
            if not self.paused:
                try:
                    full = True if self._force_full else None
                    self._force_full = False
                    self.asus.poll(self.router_password(), full=full)
                except Exception:  # A collector bug must not stop the engine.
                    log.exception("ASUS poll crashed")
                if self.cfg.hostpc.enabled:
                    try:
                        self.host.poll(db.units())
                    except Exception:
                        log.exception("host PC sensor failed")
                if self.devolo.due(time.time()):
                    try:
                        self.devolo.poll()
                    except Exception:
                        log.exception("devolo poll crashed")
                if self.probes.due(time.time()):
                    try:
                        self.probes.poll()
                    except Exception:
                        log.exception("probe poll crashed")
                m = self.cfg.monitor
                if m.speedtest_hours and time.time() - last_speed > m.speedtest_hours * 3600:
                    last_speed = time.time()
                    threading.Thread(target=self.run_speedtest, args=("scheduled",), daemon=True).start()
                if m.router_speedtest and m.router_speedtest_hours and time.time() - last_router_speed > m.router_speedtest_hours * 3600:
                    last_router_speed = time.time()
                    threading.Thread(target=self.run_router_speedtest, args=("scheduled",), daemon=True).start()
                u = self.cfg.updates
                if u.check_automatically and time.time() - last_update_check > u.check_hours * 3600:
                    last_update_check = time.time()
                    threading.Thread(target=self.check_update, daemon=True).start()
                if time.time() - last_prune > 3600:
                    try:
                        self.rollup()
                    except Exception:
                        log.exception("daily rollup failed")
                    db.prune(self.cfg.retention.raw_days, self.cfg.retention.summary_days)
                    jsonl.prune(self.root, self.cfg.retention.raw_days)
                    last_prune = time.time()
            self._wake.wait(self.cfg.router.poll_seconds)
            self._wake.clear()
