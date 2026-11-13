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
