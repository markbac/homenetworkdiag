"""Configuration: paths, defaults and the user's settings file.

Settings live in ``%LOCALAPPDATA%\\MeshLens\\config.yaml`` (or ``$MESHLENS_HOME``).
Secrets (passwords, tokens) are never stored here: see :mod:`meshlens.secrets`.
Everything in this file is editable from the web UI (Settings).
"""
from __future__ import annotations

import ipaddress
import os
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

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
class NetworkConfig:
    """The home network (D-11). Used by discovery and to tell local from internet addresses."""

    subnets: list[str] = field(default_factory=lambda: ["10.0.10.0/23"])
    exclude: list[str] = field(default_factory=list)       # addresses or ranges never to scan
    dns_servers: list[str] = field(default_factory=list)   # empty = use the router's


@dataclass
class WebConfig:
    """Web control interface (D-05, D-18): LAN access with a login. Changes need a restart."""

    bind: str = "0.0.0.0"
    port: int = 8787


@dataclass
class RetentionConfig:
    """D-20 / D-25."""

    raw_days: int = 31
    summary_days: int = 366


@dataclass
class PiholeConfig:
    """Pi-hole plug-in (D-21). Secret: the app password (v6) or API token (v5)."""

    enabled: bool = False
    url: str = ""                   # e.g. http://10.0.10.53
    version: str = "6"              # "6" or "5"
    verify_tls: bool = True


@dataclass
class HomeAssistantConfig:
    """Home Assistant plug-in (D-21). Secret: a long-lived access token."""

    enabled: bool = False
    url: str = ""                   # e.g. http://homeassistant.local:8123
    verify_tls: bool = True


@dataclass
class UnifiConfig:
    """UniFi Network controller, read-only View Only admin (D-27). Secret: its password."""

    enabled: bool = False
    url: str = "https://127.0.0.1:8443"
    username: str = "meshlens"
    site: str = "default"
    verify_tls: bool = False        # the local controller uses a self-signed certificate


@dataclass
class DevoloConfig:
    """devolo Magic (G.hn) powerline via the devolo device API (SW-04). Any one adapter reports the whole network."""

    enabled: bool = True
    adapters: list[str] = field(default_factory=list)   # addresses to ask; empty = devolo devices seen on the LAN
    poll_seconds: int = 300
    slow_mbps: int = 50               # powerline link slower than this (either direction) is reported
    bad_mbps: int = 20


@dataclass
class DiscoveryConfig:
    """LAN discovery (DEV-01, SVC-01..03) and port scans (PRT-01..05). Paced so it never floods the network."""

    enabled: bool = True
    sweep_minutes: int = 15           # find devices: ping/ARP sweep, names
    services_minutes: int = 60        # mDNS, SSDP, NetBIOS
    port_scan_hours: int = 24         # top ports on every device (0 = only on demand)
    port_set: str = "top100"          # top100 or top1000
    max_rate: int = 200               # packets / connection attempts per second at most


@dataclass
class MonitorConfig:
    """Ping, latency, jitter and loss monitors (TST-01, TST-05) and speed / bufferbloat tests (TST-02, TST-03)."""

    enabled: bool = True
    interval_seconds: float = 2.0
    internet_targets: list[str] = field(default_factory=lambda: ["1.1.1.1", "8.8.8.8"])
    extra_targets: list[str] = field(default_factory=list)   # anything else to watch, e.g. a games server
    include_nodes: bool = True
    include_isp_hop: bool = False      # first ISP router (tracert-style); routers answer at low priority, so noisy
    include_watched: bool = True       # watched devices with an address
    speedtest_hours: float = 6.0       # 0 = only on demand (test from this PC)
    router_speedtest: bool = False     # run Ookla on the router; switching it on accepts Ookla's licence and GDPR terms
    router_speedtest_hours: float = 12.0
    speedtest_seconds: int = 10        # per direction
    speedtest_server: str = "https://speed.cloudflare.com"
    expected_down_mbps: float = 0.0    # your plan; 0 = unknown
    expected_up_mbps: float = 0.0


@dataclass
class HostConfig:
    """The MeshLens PC as a sensor: air survey with its Wi-Fi card, broadcast/multicast on its wired adapter."""

    enabled: bool = True


@dataclass
class UpdateConfig:
    """In-app updates from the project's GitHub releases."""

    check_automatically: bool = True
    check_hours: int = 24
    include_prereleases: bool = False


DEVICE_KINDS = ("unifi_switch", "tplink_easy_smart", "devolo", "managed_switch", "access_point", "other")


@dataclass
class DeviceLogin:
    """Login for a switch, powerline adapter or other network device. Secret: its password."""

    name: str = ""
    kind: str = "other"
    host: str = ""
    username: str = ""
    notes: str = ""


@dataclass
class AnalysisConfig:
    """Thresholds for issue detection (DIA-04). Defaults suit a typical home mesh."""

    kick_loop_count: int = 3
    kick_loop_minutes: int = 60
    roam_storm_count: int = 4
    roam_storm_seconds: int = 120
    flapping_count: int = 6
    flapping_minutes: int = 60
    weak_rssi_dbm: int = -72          # typical signal below this = edge of coverage
    sticky_margin_db: int = 8         # another unit this much stronger but the device stays
    slow_rate_mbps_24: float = 20.0   # typical link rate below this = slow link
    slow_rate_mbps_5: float = 80.0
    radio_tx_fail_warn: float = 5.0
    radio_tx_fail_bad: float = 10.0
    radio_rx_err_warn: float = 5.0
    radio_rx_err_bad: float = 10.0
    radio_temp_warn: float = 80.0
    mass_drop_devices: int = 4        # this many devices dropping off one unit within a minute
    crowded_clients_24: int = 20      # clients on one 2.4 GHz radio
    watched_offline_hours: float = 2.0
    flood_pps: float = 100.0          # broadcast + multicast packets per second reaching the MeshLens PC
    channel_busy_pct: float = 50.0    # an access point's own channel-utilisation report
    crowded_neighbours: int = 3       # other networks heard on the same channel


@dataclass
class Config:
    router: RouterConfig = field(default_factory=RouterConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    web: WebConfig = field(default_factory=WebConfig)
    retention: RetentionConfig = field(default_factory=RetentionConfig)
    pihole: PiholeConfig = field(default_factory=PiholeConfig)
    homeassistant: HomeAssistantConfig = field(default_factory=HomeAssistantConfig)
    unifi: UnifiConfig = field(default_factory=UnifiConfig)
    devices: list[DeviceLogin] = field(default_factory=list)
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    devolo: DevoloConfig = field(default_factory=DevoloConfig)
    discovery: DiscoveryConfig = field(default_factory=DiscoveryConfig)
    monitor: MonitorConfig = field(default_factory=MonitorConfig)
    updates: UpdateConfig = field(default_factory=UpdateConfig)
    hostpc: HostConfig = field(default_factory=HostConfig)

    @property
    def path(self) -> Path:
        return home() / "config.yaml"

    def save(self) -> None:
        self.path.write_text(yaml.safe_dump(asdict(self), sort_keys=False), encoding="utf-8")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


SECTIONS = {f.name: f.default_factory for f in fields(Config) if f.name != "devices"}  # type: ignore[misc]


def _coerce(cls: type, values: dict[str, Any]) -> Any:
    """Build a section dataclass from a dict, ignoring unknown keys and converting types.

    Raises ValueError for values that cannot be converted, so the web UI can refuse them.
    """
    obj = cls()
    for f in fields(cls):
        if f.name not in values or values[f.name] is None:
            continue
        cur, v = getattr(obj, f.name), values[f.name]
        if isinstance(cur, bool):
            v = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
        elif isinstance(cur, int):
            v = int(v)
        elif isinstance(cur, float):
            v = float(v)
        elif isinstance(cur, list):
            if isinstance(v, str):
                v = [x.strip() for x in v.replace("\n", ",").split(",") if x.strip()]
            v = [str(x).strip() for x in v]
        else:
            v = str(v).strip()
        setattr(obj, f.name, v)
    return obj


def validate(cfg: Config) -> list[str]:
    """Return problems with a configuration (empty when it is fine)."""
    errs = []
    for s in cfg.network.subnets:
        try:
            ipaddress.ip_network(s, strict=False)
        except ValueError:
            errs.append(f"'{s}' is not a subnet (use e.g. 10.0.10.0/23)")
    for s in cfg.network.exclude + cfg.network.dns_servers:
        try:
            ipaddress.ip_network(s, strict=False)
        except ValueError:
            errs.append(f"'{s}' is not an address or range")
    if not cfg.network.subnets:
        errs.append("at least one subnet is needed")
    for name, port in (("router", cfg.router.port), ("web", cfg.web.port)):
        if not 1 <= port <= 65535:
            errs.append(f"{name} port must be 1-65535")
    if cfg.router.poll_seconds < 15:
        errs.append("router poll interval must be at least 15 seconds")
    if cfg.retention.raw_days < 1 or cfg.retention.summary_days < cfg.retention.raw_days:
        errs.append("keep summaries at least as long as raw data")
    for svc in (cfg.pihole, cfg.homeassistant, cfg.unifi):
        if svc.enabled and not svc.url.startswith(("http://", "https://")):
            errs.append(f"{type(svc).__name__.replace('Config', '')} address must start with http:// or https://")
    for s in cfg.devolo.adapters + cfg.monitor.extra_targets + cfg.monitor.internet_targets:
        if not s or " " in s:
            errs.append(f"'{s}' is not an address or host name")
    if cfg.discovery.port_set not in ("top100", "top1000"):
        errs.append("port set must be top100 or top1000")
    if not 10 <= cfg.discovery.max_rate <= 2000:
        errs.append("discovery rate must be 10-2000 per second")
    if cfg.monitor.interval_seconds < 0.5:
        errs.append("ping interval must be at least 0.5 seconds")
    if not 3 <= cfg.monitor.speedtest_seconds <= 60:
        errs.append("speed test length must be 3-60 seconds")
    if cfg.monitor.speedtest_server and not cfg.monitor.speedtest_server.startswith("https://"):
        errs.append("speed test server must start with https://")
    for d in cfg.devices:
        if not d.host:
            errs.append(f"device '{d.name or '?'}' needs an address")
        if d.kind not in DEVICE_KINDS:
            errs.append(f"device '{d.name or d.host}': unknown kind '{d.kind}'")
    return errs


def from_dict(raw: dict[str, Any]) -> Config:
    cfg = Config()
    for name, factory in SECTIONS.items():
        cls = type(factory())
        if is_dataclass(cls) and isinstance(raw.get(name), dict):
            setattr(cfg, name, _coerce(cls, raw[name]))
    cfg.devices = [_coerce(DeviceLogin, d) for d in raw.get("devices") or [] if isinstance(d, dict)]
    # Settings files written before NetworkConfig existed had a single top-level subnet.
    if "network" not in raw and raw.get("subnet"):
        cfg.network.subnets = [raw["subnet"]]
    return cfg


def load() -> Config:
    """Load config.yaml, filling in defaults for anything missing."""
    cfg = Config()
    p = cfg.path
    if p.exists():
        cfg = from_dict(yaml.safe_load(p.read_text(encoding="utf-8")) or {})
    else:
        cfg.save()
    return cfg
