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

from .. import config as config_mod
from .. import integrations, journeys, secrets
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
    r.add_post("/api/auth/password", auth_password)
    r.add_get("/api/settings", settings_get)
    r.add_post("/api/settings/devices", settings_devices)
    r.add_post("/api/settings/test/{section}", settings_test)
    r.add_post("/api/settings/{section}", settings_section)
    r.add_get("/api/analysis", analysis_get)
    r.add_get("/api/trends", trends)
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


async def auth_password(request: web.Request) -> web.Response:
    """Change the dashboard password (needs the current one); logs out every other session."""
    auth: Auth = request.app[AUTH]
    if not auth.valid(request.cookies.get(COOKIE)):
        return web.json_response({"error": "login required"}, status=401)
    body = await request.json()
    client = _client(request)
    if auth.locked(client):
        return web.json_response({"error": "too many attempts, wait 5 minutes"}, status=429)
    if not auth.check_password(body.get("current") or ""):
        auth.failed(client)
        return web.json_response({"error": "current password is wrong"}, status=403)
    new = body.get("new") or ""
    if len(new) < 10:
        return web.json_response({"error": "use at least 10 characters"}, status=400)
    auth.set_password(new)
    auth.end_all()
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
    a = await asyncio.to_thread(request.app[ENGINE].analysis, 24)
    d["health"] = a["devices"].get(mac)
    d["issues"] = [i for i in a["issues"] if i["subject"] == mac or mac in (i.get("devices") or [])]
    d["daily"] = db.daily_summary(_day(-30), mac)
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


# ---------------------------------------------------------------- analysis

def _day(offset: int = 0) -> str:
    import datetime as dt
    return (dt.date.today() + dt.timedelta(days=offset)).isoformat()


async def analysis_get(request: web.Request) -> web.Response:
    try:
        hours = max(1.0, min(float(request.query.get("hours", 24)), 24 * 31))
    except ValueError:
        hours = 24.0
    return web.json_response(await asyncio.to_thread(request.app[ENGINE].analysis, hours))


async def trends(request: web.Request) -> web.Response:
    from .. import analysis
    try:
        days = max(7, min(int(request.query.get("days", 30)), 366))
    except ValueError:
        days = 30
    rows = db.daily_summary(_day(-(days - 1)))
    cmp_rows = db.daily_summary(_day(-13))
    return web.json_response({"days": analysis.trend(rows), "compare": analysis.compare_periods(cmp_rows, 7, _day())})


# ---------------------------------------------------------------- settings

SECRET_SECTIONS = ("router", "pihole", "homeassistant", "unifi")
INFRA_VENDORS = ("ubiquiti", "tp-link", "tp link", "devolo", "netgear", "cisco", "zyxel", "d-link", "mikrotik",
                 "aruba", "hewlett", "linksys", "draytek")


def _settings_payload(engine: Engine) -> dict[str, Any]:
    cfg = engine.cfg
    out = cfg.as_dict()
    out["secret_set"] = {s: secrets.has_secret(secrets.SERVICE_KINDS[s], secrets.service_ident(s, cfg))
                         for s in SECRET_SECTIONS if s == "router" or getattr(cfg, s).url}
    for d in out["devices"]:
        d["ident"] = secrets.device_ident(d["host"], d["username"])
        d["password_set"] = bool(d["host"]) and secrets.has_secret("device", d["ident"])
    out["device_kinds"] = list(config_mod.DEVICE_KINDS)
    known = {d["host"] for d in out["devices"]}
    out["suggested_devices"] = [
        {"name": d.get("user_name") or d.get("name") or d["mac"], "host": d["ip"], "vendor": d["vendor"], "mac": d["mac"]}
        for d in db.devices()
        if d.get("ip") and d["ip"] not in known and d.get("vendor")
        and any(v in d["vendor"].lower() for v in INFRA_VENDORS) and (d.get("wired") or "devolo" in d["vendor"].lower())]
    out["config_file"] = str(cfg.path)
    return out


async def settings_get(request: web.Request) -> web.Response:
    return web.json_response(_settings_payload(request.app[ENGINE]))


def _apply(engine: Engine, new_cfg: config_mod.Config) -> web.Response | None:
    errs = config_mod.validate(new_cfg)
    if errs:
        return web.json_response({"error": "; ".join(errs)}, status=400)
    new_cfg.save()
    engine.apply_config(new_cfg)
    return None


async def settings_section(request: web.Request) -> web.Response:
    """Save one settings section. Secrets (password/token) go to the Credential Manager, never to the file."""
    engine: Engine = request.app[ENGINE]
    section = request.match_info["section"]
    if section not in config_mod.SECTIONS:
        return web.json_response({"error": "unknown section"}, status=404)
    body = await request.json()
    if not isinstance(body, dict):
        return web.json_response({"error": "object required"}, status=400)
    secret = body.pop("secret", None) or body.pop("password", None)
    clear = bool(body.pop("clear_secret", None) or body.pop("clear_password", None))
    old_cfg = engine.cfg
    old_ident = secrets.service_ident(section, old_cfg) if section in SECRET_SECTIONS else None
    raw = old_cfg.as_dict()
    raw[section] = {**raw[section], **body}
    try:
        new_cfg = config_mod.from_dict(raw)
    except (TypeError, ValueError) as e:
        return web.json_response({"error": f"invalid value: {e}"}, status=400)
    err = _apply(engine, new_cfg)
    if err:
        return err
    if section in SECRET_SECTIONS:
        kind, ident = secrets.SERVICE_KINDS[section], secrets.service_ident(section, new_cfg)
        if clear:
            secrets.clear_secret(kind, ident)
        elif secret:
            secrets.set_secret(kind, ident, str(secret))
        elif (section == "router" and ident != old_ident and new_cfg.router.host == old_cfg.router.host
              and secrets.has_secret(kind, old_ident)):
            # Same router, new port or username: keep the password. A new address needs it entered again.
            secrets.set_secret(kind, ident, secrets.get_secret(kind, old_ident) or "")
    if section == "router":
        engine.collect_now(full=True)
    return web.json_response(_settings_payload(engine))


async def settings_devices(request: web.Request) -> web.Response:
    """Replace the list of switch / powerline / other device logins."""
    engine: Engine = request.app[ENGINE]
    items = (await request.json()).get("devices")
    if not isinstance(items, list) or not all(isinstance(d, dict) for d in items):
        return web.json_response({"error": "devices list required"}, status=400)
    live = [d for d in items if not d.get("removed")]
    raw = engine.cfg.as_dict()
    raw["devices"] = [{k: v for k, v in d.items() if k in ("name", "kind", "host", "username", "notes")} for d in live]
    try:
        new_cfg = config_mod.from_dict(raw)
    except (TypeError, ValueError) as e:
        return web.json_response({"error": f"invalid value: {e}"}, status=400)
    err = _apply(engine, new_cfg)
    if err:
        return err
    keep = set()
    for d, item in zip(new_cfg.devices, live):
        ident = secrets.device_ident(d.host, d.username)
        keep.add(ident)
        orig = item.get("orig_ident")
        if item.get("clear_password"):
            secrets.clear_secret("device", ident)
        elif item.get("password"):
            secrets.set_secret("device", ident, str(item["password"]))
        elif orig and orig != ident and orig.split("@")[-1] == d.host.lower() and secrets.has_secret("device", orig):
            secrets.set_secret("device", ident, secrets.get_secret("device", orig) or "")
    for item in items:  # Removed or renamed entries: drop passwords nothing uses any more.
        orig = item.get("orig_ident")
        if orig and orig not in keep:
            secrets.clear_secret("device", orig)
    return web.json_response(_settings_payload(engine))


async def settings_test(request: web.Request) -> web.Response:
    """Read-only connection test with the stored (or just typed, unsaved) secret."""
    engine: Engine = request.app[ENGINE]
    section = request.match_info["section"]
    if section not in ("pihole", "homeassistant", "unifi"):
        return web.json_response({"error": "nothing to test for this section"}, status=404)
    cfg = engine.cfg
    svc = getattr(cfg, section)
    if not svc.url:
        return web.json_response({"ok": False, "detail": "Enter and save the address first."})
    body = await request.json()
    secret = (body or {}).get("secret") or secrets.get_secret(secrets.SERVICE_KINDS[section],
                                                              secrets.service_ident(section, cfg))
    if not secret:
        return web.json_response({"ok": False, "detail": "No password or token stored yet."})
    try:
        if section == "pihole":
            res = await integrations.test_pihole(svc.url, svc.version, secret, svc.verify_tls)
        elif section == "homeassistant":
            res = await integrations.test_homeassistant(svc.url, secret, svc.verify_tls)
        else:
            res = await integrations.test_unifi(svc.url, svc.username, secret, svc.site, svc.verify_tls)
    except Exception as e:  # network errors, bad certificates, non-JSON answers
        res = {"ok": False, "detail": f"Could not connect: {type(e).__name__}: {e}"}
    return web.json_response(res)


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
