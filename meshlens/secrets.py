"""Secrets in the Windows Credential Manager (DPAPI-protected) via ``keyring``.

Nothing secret is written to config files, logs, the database or API responses.
The web UI can set or clear a secret and see whether it is set, never read it.
"""
from __future__ import annotations

import keyring
from keyring.errors import PasswordDeleteError

SERVICE = "MeshLens"


def _key(kind: str, ident: str) -> str:
    return f"{kind}:{ident}"


def set_secret(kind: str, ident: str, value: str) -> None:
    keyring.set_password(SERVICE, _key(kind, ident), value)


def get_secret(kind: str, ident: str) -> str | None:
    return keyring.get_password(SERVICE, _key(kind, ident))


def has_secret(kind: str, ident: str) -> bool:
    return get_secret(kind, ident) is not None


def clear_secret(kind: str, ident: str) -> None:
    try:
        keyring.delete_password(SERVICE, _key(kind, ident))
    except PasswordDeleteError:
        pass


def router_ident(username: str, host: str, port: int) -> str:
    return f"{username}@{host}:{port}"


# Where each configured service keeps its secret. The identity includes the address so a
# secret is never sent to a different host after the address is changed.
SERVICE_KINDS = {"router": "asus", "pihole": "pihole", "homeassistant": "hass", "unifi": "unifi"}


def service_ident(section: str, cfg) -> str:
    """Credential-manager identity for a config section (``cfg`` is the full Config)."""
    if section == "router":
        r = cfg.router
        return router_ident(r.username, r.host, r.port)
    if section == "unifi":
        return f"{cfg.unifi.username}@{cfg.unifi.url.rstrip('/')}"
    if section in ("pihole", "homeassistant"):
        return getattr(cfg, section).url.rstrip("/")
    raise KeyError(section)


def device_ident(host: str, username: str) -> str:
    return f"{username}@{host}".lower()
