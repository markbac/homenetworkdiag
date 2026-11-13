"""MAC vendor lookup from the IEEE registry, downloaded once and searched locally (NFR-03).

Replaces ``homelab-unified``'s macvendors.com lookup so no device address leaves the LAN.
"""
from __future__ import annotations

import csv
import logging
import threading
import time
import urllib.request
from pathlib import Path

from . import config

log = logging.getLogger("meshlens.oui")

REGISTRIES = {  # name: (url, hex prefix length)
    "MA-S": ("https://standards-oui.ieee.org/oui36/oui36.csv", 9),
    "MA-M": ("https://standards-oui.ieee.org/oui28/mam.csv", 7),
    "MA-L": ("https://standards-oui.ieee.org/oui/oui.csv", 6),
}
MAX_AGE_DAYS = 30

_table: dict[str, str] | None = None
_lock = threading.Lock()


def _cache_dir() -> Path:
    d = config.home() / "oui"
    d.mkdir(exist_ok=True)
    return d


def load(download: bool = True) -> dict[str, str]:
    global _table
    with _lock:
        if _table is not None:
            return _table
        table: dict[str, str] = {}
        for name, (url, _) in REGISTRIES.items():
            path = _cache_dir() / f"{name}.csv"
            stale = not path.exists() or time.time() - path.stat().st_mtime > MAX_AGE_DAYS * 86400
            if stale and download:
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "MeshLens/0.1"})
                    with urllib.request.urlopen(req, timeout=60) as r:
                        path.write_bytes(r.read())
                except OSError as e:
                    log.warning("IEEE %s registry download failed: %s", name, e)
            if path.exists():
                with open(path, encoding="utf-8", errors="replace") as f:
                    for row in csv.DictReader(f):
                        table[row["Assignment"].upper()] = row["Organization Name"].strip()
        _table = table
        return table


def vendor(mac: str, table: dict[str, str] | None = None) -> str | None:
    """Vendor for a MAC, or None (randomised addresses have none by design)."""
    if int(mac[:2], 16) & 0x02:
        return None
    t = table if table is not None else (_table or {})
    h = mac.replace(":", "").replace("-", "").upper()
    for _, n in REGISTRIES.values():
        if h[:n] in t:
            return t[h[:n]]
    return None
