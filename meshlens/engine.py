"""Engine: schedules collectors in a background thread and owns shared state.

The tray app and the web server both talk to one Engine instance.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from . import config as config_mod
from . import oui, secrets
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
                    db.prune(self.cfg.retention.raw_days, self.cfg.retention.summary_days)
                    jsonl.prune(self.root, self.cfg.retention.raw_days)
                    last_prune = time.time()
            self._wake.wait(self.cfg.router.poll_seconds)
            self._wake.clear()
