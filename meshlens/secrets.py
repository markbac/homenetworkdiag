"""Secrets in the Windows Credential Manager (DPAPI-protected) via ``keyring``.

On a headless Linux server (the Docker image) there is no credential manager, so secrets go to
``secrets.json`` in the data directory, readable only by the MeshLens user (mode 0600). This is
used when ``MESHLENS_SECRET_STORE=file`` or when keyring has no usable backend.

Nothing secret is written to config files, logs, the database or API responses.
The web UI can set or clear a secret and see whether it is set, never read it.
"""
from __future__ import annotations

import json
import os
import threading

import keyring
from keyring.errors import PasswordDeleteError

SERVICE = "MeshLens"
_LOCK = threading.Lock()


def _use_file() -> bool:
    mode = os.environ.get("MESHLENS_SECRET_STORE", "").lower()
    if mode in ("file", "keyring"):
        return mode == "file"
    try:
        from keyring.backends import fail
        return isinstance(keyring.get_keyring(), fail.Keyring)
    except Exception:
        return True


def _file():
    from .config import home
    return home() / "secrets.json"


def _read_file() -> dict[str, str]:
    try:
        return json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_file(data: dict[str, str]) -> None:
    path = _file()
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _key(kind: str, ident: str) -> str:
    return f"{kind}:{ident}"


def set_secret(kind: str, ident: str, value: str) -> None:
    if _use_file():
        with _LOCK:
            data = _read_file()
            data[_key(kind, ident)] = value
            _write_file(data)
        return
    keyring.set_password(SERVICE, _key(kind, ident), value)


def get_secret(kind: str, ident: str) -> str | None:
    if _use_file():
        with _LOCK:
            return _read_file().get(_key(kind, ident))
    return keyring.get_password(SERVICE, _key(kind, ident))


def has_secret(kind: str, ident: str) -> bool:
    return get_secret(kind, ident) is not None


def clear_secret(kind: str, ident: str) -> None:
    if _use_file():
        with _LOCK:
            data = _read_file()
            if data.pop(_key(kind, ident), None) is not None:
                _write_file(data)
        return
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
