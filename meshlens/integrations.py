"""Connection tests for the optional services (Pi-hole, Home Assistant, UniFi).

Read-only: each test logs in, reads a version/status value and logs out again.
The full collectors for these services are still to come; these tests let the
user check the address and credentials now.
"""
from __future__ import annotations

from typing import Any

import aiohttp

TIMEOUT = aiohttp.ClientTimeout(total=10)


def _ssl(verify: bool):
    return None if verify else False


async def test_pihole(url: str, version: str, secret: str, verify: bool) -> dict[str, Any]:
    url = url.rstrip("/")
    async with aiohttp.ClientSession(timeout=TIMEOUT) as s:
        if str(version) == "5":
            async with s.get(f"{url}/admin/api.php", params={"summaryRaw": "", "auth": secret}, ssl=_ssl(verify)) as r:
                data = await r.json(content_type=None)
                if not isinstance(data, dict) or "dns_queries_today" not in data:
                    return {"ok": False, "detail": "Pi-hole answered but the API token was not accepted."}
                return {"ok": True, "detail": f"Pi-hole v5: {data['dns_queries_today']} DNS queries today."}
        async with s.post(f"{url}/api/auth", json={"password": secret}, ssl=_ssl(verify)) as r:
            data = await r.json(content_type=None)
        sess = (data or {}).get("session") or {}
        if not sess.get("valid"):
            return {"ok": False, "detail": "Pi-hole rejected the password (use an app password from Settings → Web interface / API)."}
        headers = {"X-FTL-SID": sess.get("sid", "")}
        try:
            async with s.get(f"{url}/api/stats/summary", headers=headers, ssl=_ssl(verify)) as r:
                summ = await r.json(content_type=None)
            total = ((summ or {}).get("queries") or {}).get("total")
            return {"ok": True, "detail": f"Pi-hole v6: {total} DNS queries in the last 24 h." if total is not None
                    else "Pi-hole v6: logged in."}
        finally:
            async with s.delete(f"{url}/api/auth", headers=headers, ssl=_ssl(verify)):
                pass


async def test_homeassistant(url: str, token: str, verify: bool) -> dict[str, Any]:
    async with aiohttp.ClientSession(timeout=TIMEOUT) as s:
        async with s.get(f"{url.rstrip('/')}/api/", headers={"Authorization": f"Bearer {token}"}, ssl=_ssl(verify)) as r:
            if r.status == 401:
                return {"ok": False, "detail": "Home Assistant rejected the token."}
            data = await r.json(content_type=None)
            return {"ok": r.status == 200, "detail": (data or {}).get("message") or f"HTTP {r.status}"}


async def test_unifi(url: str, username: str, password: str, site: str, verify: bool) -> dict[str, Any]:
    url = url.rstrip("/")
    async with aiohttp.ClientSession(timeout=TIMEOUT, cookie_jar=aiohttp.CookieJar(unsafe=True)) as s:
        # Self-hosted Network application: /api/login. UniFi OS consoles: /api/auth/login + /proxy/network.
        for login, prefix in (("/api/login", ""), ("/api/auth/login", "/proxy/network")):
            async with s.post(url + login, json={"username": username, "password": password}, ssl=_ssl(verify)) as r:
                if r.status == 404:
                    continue
                if r.status != 200:
                    return {"ok": False, "detail": f"UniFi rejected the login (HTTP {r.status}). "
                                                   "Use a local admin without 2FA (View Only is enough)."}
                csrf = r.headers.get("X-CSRF-Token")
            headers = {"X-CSRF-Token": csrf} if csrf else {}
            try:
                async with s.get(f"{url}{prefix}/api/s/{site}/stat/sysinfo", headers=headers, ssl=_ssl(verify)) as r:
                    data = await r.json(content_type=None)
                info = ((data or {}).get("data") or [{}])[0]
                return {"ok": r.status == 200, "detail": f"UniFi Network {info.get('version', '?')}, site '{site}'."}
            finally:
                logout = "/api/logout" if not prefix else "/api/auth/logout"
                async with s.post(url + logout, headers=headers, ssl=_ssl(verify)):
                    pass
        return {"ok": False, "detail": "No UniFi login page at that address."}
