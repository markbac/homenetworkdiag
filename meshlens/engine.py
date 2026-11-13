"""Engine: schedules collectors in a background thread and owns shared state.

The tray app and the web server both talk to one Engine instance.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from . import config as config_mod
from . import analysis, oui, secrets, topology, updater
from .collectors.asus.collector import AsusCollector
from .collectors.devolo import DevoloCollector
from .discovery import Discovery
from .monitor import PingMonitor
from .net import icmp, speedtest
from .store import db, jsonl

log = logging.getLogger("meshlens.engine")


class Engine:
    def __init__(self, cfg: config_mod.Config):
        self.cfg = cfg
        self.root = config_mod.home()
        db.init(self.root / "meshlens.db")
        self.asus = AsusCollector(cfg.router, self.root)
        self.devolo = DevoloCollector(cfg.devolo, self.root)
        self.discovery = Discovery(lambda: self.cfg, self.root)
        self.monitor = PingMonitor(cfg.monitor, self._ping_targets)
        self.speed_status: dict[str, Any] = {"running": False, "last": None}
        self.update_info: dict[str, Any] = {"available": False, "current": updater.__version__, "checked": None,
                                            "state": None, "error": None, "self_update": updater.can_self_update()}
        self.on_quit = None   # set by the tray: called after the installer has been started
        self._isp_hop: tuple[float, tuple[str, int] | None] = (0.0, None)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self.paused = False
        self.started = time.time()
        self._analysis: dict[float, tuple[float, dict[str, Any]]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------ control

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="meshlens-engine", daemon=True)
        self._thread.start()
        threading.Thread(target=oui.load, name="oui-load", daemon=True).start()
        self.monitor.start()
        self.discovery.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self.monitor.stop()
        self.discovery.stop()
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
            self.monitor.cfg = cfg.monitor
            self._analysis.clear()

    def analysis(self, hours: float = 24) -> dict[str, Any]:
        """Issues and health for the last ``hours`` (cached for 30 s)."""
        now = time.time()
        with self._lock:
            hit = self._analysis.get(hours)
            if hit and now - hit[0] < 30:
                return hit[1]
        since = now - hours * 3600
        net = {"pings": db.ping_minutes(since), "speeds": db.speed_tests(now - 7 * 86400),
               "plc_adapters": db.plc_adapters(), "plc_rates": db.plc_latest_rates(), "open_ports": db.open_ports(),
               "inventory": db.inventory_events(since), "monitor_cfg": self.cfg.monitor, "devolo_cfg": self.cfg.devolo}
        res = analysis.analyse(db.events(since, limit=200000), db.station_samples_since(since), db.latest_radio(),
                               db.units(), db.devices(), db.latest_settings(), self.cfg.analysis, now, net=net)
        with self._lock:
            self._analysis[hours] = (now, res)
        return res

    def topology(self) -> dict[str, Any]:
        now = time.time()
        return topology.build(db.units(), db.devices(), db.station_samples_since(now - 900), db.plc_adapters(),
                              db.plc_latest_rates(), self.analysis(24), now)

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
        row = {"ts": time.time(), "trigger": trigger, **res}
        db.insert_many("speed_tests", [row])
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
            "discovery": self.discovery.status,
            "speedtest": {"running": self.speed_status["running"]},
            "update": self.update_info,
            "integrations": {s: {"enabled": getattr(self.cfg, s).enabled,
                                 "secret_set": bool(getattr(self.cfg, s).url) and
                                 secrets.has_secret(secrets.SERVICE_KINDS[s], secrets.service_ident(s, self.cfg)),
                                 "collector": "not yet implemented"}
                             for s in ("pihole", "homeassistant", "unifi")},
        }

    # ------------------------------------------------------------ loop

    _force_full = False

    @staticmethod
    def _last_speedtest_ts() -> float:
        rows = db.speed_tests(time.time() - 30 * 86400)
        return rows[-1]["ts"] if rows else 0.0

    def _loop(self) -> None:
        last_prune = 0.0
        last_update_check = time.time() - self.cfg.updates.check_hours * 3600 + 120   # first check 2 min after start
        # With no test yet, the first scheduled one runs 10 minutes after start.
        last_speed = self._last_speedtest_ts() or time.time() - self.cfg.monitor.speedtest_hours * 3600 + 600
        while not self._stop.is_set():
            if not self.paused:
                try:
                    full = True if self._force_full else None
                    self._force_full = False
                    self.asus.poll(self.router_password(), full=full)
                except Exception:  # A collector bug must not stop the engine.
                    log.exception("ASUS poll crashed")
                if self.devolo.due(time.time()):
                    try:
                        self.devolo.poll()
                    except Exception:
                        log.exception("devolo poll crashed")
                m = self.cfg.monitor
                if m.speedtest_hours and time.time() - last_speed > m.speedtest_hours * 3600:
                    last_speed = time.time()
                    threading.Thread(target=self.run_speedtest, args=("scheduled",), daemon=True).start()
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
