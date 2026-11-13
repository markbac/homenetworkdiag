import asyncio
import time

from aiohttp.test_utils import TestClient, TestServer

from meshlens import config
from meshlens.store import db
from meshlens.web.auth import Auth
from meshlens.web.server import build_app


class FakeEngine:
    def __init__(self):
        self.cfg = config.Config()
        self.collected = 0

    def status(self):
        return {"asus": {"last_poll": None}, "router": {"host": "10.0.10.1", "port": 1111, "username": "u", "password_set": False}}

    def collect_now(self, full=True):
        self.collected += 1


def run(coro):
    return asyncio.run(coro)


def test_auth_flow_and_api(tmp_home, monkeypatch):
    monkeypatch.setattr("meshlens.config.Config.save", lambda self: None)
    db.upsert_device("02:aa:00:00:00:02", time.time(), name="Test-Laptop")
    db.insert_events([{"ts": time.time(), "unit": "u1", "kind": "kick", "mac": "02:aa:00:00:00:02", "iface": "ra1",
                       "detail": {"rssi": -72}, "raw_hash": "h1"}])
    engine = FakeEngine()

    async def scenario():
        app = build_app(engine, Auth(tmp_home / "web_auth.json"))
        async with TestClient(TestServer(app)) as c:
            assert (await c.get("/api/devices")).status == 401
            st = await (await c.get("/api/auth/state")).json()
            assert st == {"setup_needed": True, "logged_in": False}
            assert (await c.post("/api/auth/setup", json={"password": "short"})).status == 400
            assert (await c.post("/api/auth/setup", json={"password": "a-long-password"})).status == 200
            # Setup logs in; data endpoints now work.
            devs = await (await c.get("/api/devices")).json()
            assert devs[0]["name"] == "Test-Laptop"
            d = await (await c.get("/api/devices/02:AA:00:00:00:02")).json()
            assert d["stability"]["kicks"] == 1 and d["journey"][0]["kind"] == "kick"
            r = await c.patch("/api/devices/02:aa:00:00:00:02", json={"user_name": "Gaming PC", "watched": True})
            assert r.status == 200 and db.device("02:aa:00:00:00:02")["watched"] == 1
            # Non-JSON state changes are refused (CSRF guard).
            assert (await c.post("/api/collect", data="x")).status == 415
            assert (await c.post("/api/collect", json={})).status == 200 and engine.collected == 1
            # Security headers.
            r = await c.get("/api/status")
            assert r.headers["Cache-Control"] == "no-store" and "default-src 'self'" in r.headers["Content-Security-Policy"]
            await c.post("/api/auth/logout", json={})
            assert (await c.get("/api/devices")).status == 401
            # Second setup is refused; wrong password rate-limited.
            assert (await c.post("/api/auth/setup", json={"password": "another-long-one"})).status == 409
            assert (await c.post("/api/auth/login", json={"password": "a-long-password"})).status == 200
    run(scenario())


def test_router_password_never_returned(tmp_home, monkeypatch):
    store = {}
    monkeypatch.setattr("meshlens.secrets.keyring.set_password", lambda s, k, v: store.__setitem__(k, v))
    monkeypatch.setattr("meshlens.secrets.keyring.get_password", lambda s, k: store.get(k))
    monkeypatch.setattr("meshlens.config.Config.save", lambda self: None)
    engine = FakeEngine()

    async def scenario():
        auth = Auth(tmp_home / "web_auth.json")
        auth.set_password("a-long-password")
        async with TestClient(TestServer(build_app(engine, auth))) as c:
            await c.post("/api/auth/login", json={"password": "a-long-password"})
            r = await c.post("/api/settings/router", json={"password": "router-secret"})
            body = await r.text()
            assert r.status == 200 and "router-secret" not in body and '"password_set": true' in body
    run(scenario())
    assert "router-secret" in store.values()
