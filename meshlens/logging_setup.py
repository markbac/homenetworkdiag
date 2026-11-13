"""Rotating debug log in the data directory (NFR-07)."""
from __future__ import annotations

import logging
import logging.handlers

from . import config


def setup(level: int = logging.INFO, console: bool = True) -> None:
    logdir = config.home() / "logs"
    logdir.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(level)
    fh = logging.handlers.RotatingFileHandler(logdir / "meshlens.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if console:
        ch = logging.StreamHandler()
        ch.setFormatter(fmt)
        root.addHandler(ch)
    # paramiko is very chatty at INFO.
    logging.getLogger("paramiko").setLevel(logging.WARNING)
