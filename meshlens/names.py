"""Human label for a device when the user hasn't named it (used in issue titles, the map and the UI)."""
from __future__ import annotations

from typing import Any

_OS = (("msft", "Windows PC"), ("android", "Android device"), ("dhcpcd", "Linux device"), ("udhcp", "Linux device"),
       ("ubnt", "Ubiquiti device"))


def os_label(hint: str | None) -> str | None:
    h = (hint or "").lower()
    return next((label for key, label in _OS if key in h), None)


def display_name(d: dict[str, Any] | None, mac: str = "") -> str:
    d = d or {}
    if d.get("user_name") or d.get("name"):
        return d.get("user_name") or d.get("name")
    vendor = (d.get("vendor") or "").split(",")[0].replace(" Inc.", "").replace(" INC.", "").strip()
    os_ = os_label(d.get("os_hint"))
    if os_ and vendor:
        return f"{os_} ({vendor})"
    if os_ or vendor:
        return f"{os_ or vendor} {(d.get('mac') or mac)[-5:]}"
    return d.get("mac") or mac
