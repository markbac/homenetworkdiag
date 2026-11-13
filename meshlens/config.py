"""Configuration: paths, defaults and the user's settings file.

Settings live in ``%LOCALAPPDATA%\\MeshLens\\config.yaml`` (or ``$MESHLENS_HOME``).
Secrets are never stored here: see :mod:`meshlens.secrets`.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml


def home() -> Path:
    """Data directory (created on first use)."""
    base = os.environ.get("MESHLENS_HOME")
    if base:
        p = Path(base)
    else:
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
        p = Path(local) / "MeshLens"
    p.mkdir(parents=True, exist_ok=True)
    return p


@dataclass
class RouterConfig:
    """ASUS main router; nodes are found from its mesh list (D-03, D-16)."""

    host: str = "10.0.10.1"
    port: int = 1111
    username: str = "admin1lg"
    poll_seconds: int = 60          # clients, radio health, counters, new log lines
    full_poll_seconds: int = 900    # settings, leases, device database, topology
    timeout_seconds: int = 15


@dataclass
class WebConfig:
    """Web control interface (D-05, D-18): LAN access with a login."""

    bind: str = "0.0.0.0"
    port: int = 8787


@dataclass
class RetentionConfig:
    """D-20 / D-25."""

    raw_days: int = 31
    summary_days: int = 366


@dataclass
class Config:
    router: RouterConfig = field(default_factory=RouterConfig)
    web: WebConfig = field(default_factory=WebConfig)
    retention: RetentionConfig = field(default_factory=RetentionConfig)
    subnet: str = "10.0.10.0/23"

    @property
    def path(self) -> Path:
        return home() / "config.yaml"

    def save(self) -> None:
        self.path.write_text(yaml.safe_dump(asdict(self), sort_keys=False), encoding="utf-8")


def load() -> Config:
    """Load config.yaml, filling in defaults for anything missing."""
    cfg = Config()
    p = cfg.path
    if p.exists():
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        for section, cls in (("router", RouterConfig), ("web", WebConfig), ("retention", RetentionConfig)):
            values = raw.get(section) or {}
            known = {k: v for k, v in values.items() if k in cls.__dataclass_fields__}
            setattr(cfg, section, cls(**known))
        cfg.subnet = raw.get("subnet", cfg.subnet)
    else:
        cfg.save()
    return cfg
