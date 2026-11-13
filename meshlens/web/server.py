"""aiohttp web server: static UI, login, JSON API.

All ``/api`` routes except the auth ones need a valid session cookie. State-
changing requests must be JSON (with SameSite=Strict cookies this blocks CSRF).
Secrets are accepted (router password) but never returned.
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from aiohttp import web

from .. import journeys, secrets
from ..engine import Engine
from ..store import db
from .auth import Auth

log = logging.getLogger("meshlens.web")
STATIC = Path(__file__).parent / "static"
COOKIE = "meshlens_session"
ENGINE = web.AppKey("engine", object)
AUTH = web.AppKey("auth", Auth)


def _client(request: web.Request) -> str:
    return request.remote or "?"


def build_app(engine: Engine, auth: Auth) -> web.Application:
    app = web.Application(middlewares=[_security])
    app[ENGINE], app[AUTH] = engine, auth
    r = app.router
    r.add_get("/", index)
    r.add_static("/static", STATIC)
    r.add_get("/api/auth/state", auth_state)
    r.add_post("/api/auth/setup", auth_setup)
    r.add_post("/api/auth/login", auth_login)
    r.add_post("/api/auth/logout", auth_logout)
    r.add_get("/api/status", status)
    r.add_get("/api/units", units)
    r.add_get("/api/devices", devices)
    r.add_get("/api/devices/{mac}", device)
    r.add_patch("/api/devices/{mac}", device_update)
    r.add_get("/api/events", events)
    r.add_get("/api/stability", stability)
    r.add_get("/api/findings", findings)
    r.add_get("/api/radios", radios)
    r.add_post("/api/collect", collect)
    r.add_post("/api/settings/router", router_settings)
    return app


async def index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC / "index.html")


@web.middleware
async def _security(request: web.Request, handler):
    path = request.path
    if path.startswith("/api/") and not path.startswith("/api/auth/"):
        if not request.app[AUTH].valid(request.cookies.get(COOKIE)):
            return web.json_response({"error": "login required"}, status=401)
    if request.method in ("POST", "PATCH", "PUT", "DELETE") and request.content_type != "application/json":
        return web.json_response({"error": "JSON body required"}, status=415)
    resp = await handler(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:"
    if path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


def _login_response(request: web.Request, payload: dict[str, Any]) -> web.Response:
    token = request.app[AUTH].new_session()
    resp = web.json_response(payload)
    resp.set_cookie(COOKIE, token, httponly=True, samesite="Strict", max_age=7 * 86400,
                    secure=request.secure)
    return resp


# ---------------------------------------------------------------- auth

async def auth_state(request: web.Request) -> web.Response:
    auth: Auth = request.app[AUTH]
    return web.json_response({"setup_needed": not auth.configured, "logged_in": auth.valid(request.cookies.get(COOKIE))})


async def auth_setup(request: web.Request) -> web.Response:
    auth: Auth = request.app[AUTH]
    if auth.configured:
        return web.json_response({"error": "already set up"}, status=409)
    pw = (await request.json()).get("password") or ""
    if len(pw) < 10:
        return web.json_response({"error": "use at least 10 characters"}, status=400)
    auth.set_password(pw)
    return _login_response(request, {"ok": True})


async def auth_login(request: web.Request) -> web.Response:
    auth: Auth = request.app[AUTH]
    client = _client(request)
    if auth.locked(client):
        return web.json_response({"error": "too many attempts, wait 5 minutes"}, status=429)
    pw = (await request.json()).get("password") or ""
    if not auth.check_password(pw):
        auth.failed(client)
        await asyncio.sleep(1)
        return web.json_response({"error": "wrong password"}, status=401)
    return _login_response(request, {"ok": True})


async def auth_logout(request: web.Request) -> web.Response:
    request.app[AUTH].end(request.cookies.get(COOKIE))
    resp = web.json_response({"ok": True})
    resp.del_cookie(COOKIE)
    return resp


# ---------------------------------------------------------------- data

def _since(request: web.Request, default_hours: float = 24) -> float:
    try:
        hours = float(request.query.get("hours", default_hours))
    except ValueError:
        hours = default_hours
    return time.time() - max(0.1, min(hours, 24 * 31)) * 3600


async def status(request: web.Request) -> web.Response:
    return web.json_response(request.app[ENGINE].status())


async def units(request: web.Request) -> web.Response:
    return web.json_response(db.units())


async def devices(request: web.Request) -> web.Response:
    devs = db.devices()
    now_at = {s["mac"]: s for s in db.latest_stations()}
    for d in devs:
        s = now_at.get(d["mac"])
        if s and time.time() - s["ts"] < 600:
            d["live"] = {k: s[k] for k in ("unit", "iface", "band", "rssi", "tx_rate", "rx_rate", "ts")}
    return web.json_response(devs)


async def device(request: web.Request) -> web.Response:
    mac = request.match_info["mac"].lower()
    d = db.device(mac)
    if not d:
        return web.json_response({"error": "unknown device"}, status=404)
    since = _since(request)
    evs = db.events(since, mac=mac, limit=5000)
    d["history"] = db.station_history(mac, since)
    d["journey"] = journeys.journey(evs, mac)
    st = journeys.stability(evs)
    d["stability"] = _stab_json(st[0]) if st else None
    d["findings"] = [f for f in journeys.findings(evs)]
    return web.json_response(d)


async def device_update(request: web.Request) -> web.Response:
    mac = request.match_info["mac"].lower()
    body = await request.json()
    if "watched" in body:
        body["watched"] = 1 if body["watched"] else 0
    ok = db.update_enrichment(mac, **{k: (v if v != "" else None) for k, v in body.items()})
    return web.json_response({"ok": ok}, status=200 if ok else 404)


async def events(request: web.Request) -> web.Response:
    kinds = [k for k in request.query.get("kind", "").split(",") if k] or None
    return web.json_response(db.events(_since(request), mac=request.query.get("mac"), kinds=kinds,
                                       limit=min(int(request.query.get("limit", 500)), 5000)))


def _stab_json(s: journeys.DeviceStability) -> dict[str, Any]:
    return {"mac": s.mac, "connects": s.connects, "disconnects": s.disconnects, "kicks": s.kicks,
            "roam_attempts": s.roam_attempts, "roams": s.roams, "moved_away": s.moved_away,
            "units": sorted(s.units), "weakest_rssi": s.weakest_rssi, "score": s.score, "last_event": s.last_event}


async def stability(request: web.Request) -> web.Response:
    st = journeys.stability(db.events(_since(request), limit=100000))
    return web.json_response([_stab_json(s) for s in st if s.score > 0])


async def findings(request: web.Request) -> web.Response:
    return web.json_response(journeys.findings(db.events(_since(request), limit=100000)))


async def radios(request: web.Request) -> web.Response:
    return web.json_response(db.latest_radio())


async def collect(request: web.Request) -> web.Response:
    request.app[ENGINE].collect_now(full=True)
    return web.json_response({"ok": True})


async def router_settings(request: web.Request) -> web.Response:
    engine: Engine = request.app[ENGINE]
    body = await request.json()
    r = engine.cfg.router
    old_ident = secrets.router_ident(r.username, r.host, r.port)
    try:
        if "host" in body:
            r.host = str(body["host"]).strip()
        if "port" in body:
            r.port = int(body["port"])
        if "username" in body:
            r.username = str(body["username"]).strip()
    except (TypeError, ValueError):
        return web.json_response({"error": "invalid value"}, status=400)
    engine.cfg.save()
    new_ident = secrets.router_ident(r.username, r.host, r.port)
    if body.get("clear_password"):
        secrets.clear_secret("asus", new_ident)
    elif body.get("password"):
        secrets.set_secret("asus", new_ident, body["password"])
    elif new_ident != old_ident and secrets.has_secret("asus", old_ident):
        secrets.set_secret("asus", new_ident, secrets.get_secret("asus", old_ident) or "")
    engine.collect_now(full=True)
    return web.json_response({"ok": True, "password_set": secrets.has_secret("asus", new_ident)})


# ---------------------------------------------------------------- running

def run_in_thread(engine: Engine, auth: Auth, host: str, port: int) -> tuple[asyncio.AbstractEventLoop, Any]:
    """Start the server on its own event loop thread (used by the tray app)."""
    import threading

    loop = asyncio.new_event_loop()
    started = threading.Event()

    def _run() -> None:
        asyncio.set_event_loop(loop)
        runner = web.AppRunner(build_app(engine, auth), access_log=None)
        loop.run_until_complete(runner.setup())
        loop.run_until_complete(web.TCPSite(runner, host, port).start())
        log.info("web UI on http://%s:%s", "localhost" if host in ("0.0.0.0", "127.0.0.1") else host, port)
        started.set()
        loop.run_forever()
        loop.run_until_complete(runner.cleanup())

    t = threading.Thread(target=_run, name="meshlens-web", daemon=True)
    t.start()
    started.wait(10)
    return loop, t
