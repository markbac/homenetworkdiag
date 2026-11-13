"""Engine: schedules collectors in a background thread and owns shared state.

The tray app and the web server both talk to one Engine instance.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from . import config as config_mod
from . import analysis, oui, secrets
from .collectors.asus.collector import AsusCollector
from .store import db, jsonl

log = logging.getLogger("meshlens.engine")


class Engine:
    def __init__(self, cfg: config_mod.Config):
        self.cfg = cfg
        self.root = config_mod.home()
        db.init(self.root / "meshlens.db")
        self.asus = AsusCollector(cfg.router, self.root)
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

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
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
            self._analysis.clear()

    def analysis(self, hours: float = 24) -> dict[str, Any]:
        """Issues and health for the last ``hours`` (cached for 30 s)."""
        now = time.time()
        with self._lock:
            hit = self._analysis.get(hours)
            if hit and now - hit[0] < 30:
                return hit[1]
        since = now - hours * 3600
        res = analysis.analyse(db.events(since, limit=200000), db.station_samples_since(since), db.latest_radio(),
                               db.units(), db.devices(), db.latest_settings(), self.cfg.analysis, now)
        with self._lock:
            self._analysis[hours] = (now, res)
        return res

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
            "integrations": {s: {"enabled": getattr(self.cfg, s).enabled,
                                 "secret_set": bool(getattr(self.cfg, s).url) and
                                 secrets.has_secret(secrets.SERVICE_KINDS[s], secrets.service_ident(s, self.cfg)),
                                 "collector": "not yet implemented"}
                             for s in ("pihole", "homeassistant", "unifi")},
        }

    # ------------------------------------------------------------ loop

    _force_full = False

    def _loop(self) -> None:
        last_prune = 0.0
        while not self._stop.is_set():
            if not self.paused:
                try:
                    full = True if self._force_full else None
                    self._force_full = False
                    self.asus.poll(self.router_password(), full=full)
                except Exception:  # A collector bug must not stop the engine.
                    log.exception("ASUS poll crashed")
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
