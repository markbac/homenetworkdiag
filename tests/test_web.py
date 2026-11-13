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

    def apply_config(self, cfg):
        self.cfg = cfg

    def analysis(self, hours=24):
        return _analyse(self, hours)


def _analyse(engine, hours):
    from meshlens import analysis
    since = time.time() - hours * 3600
    return analysis.analyse(db.events(since, limit=100000), db.station_samples_since(since), db.latest_radio(),
                            db.units(), db.devices(), db.latest_settings(), engine.cfg.analysis)


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
            assert r.status == 200 and "router-secret" not in body and '"router": true' in body
    run(scenario())
    assert "router-secret" in store.values()


def _fake_keyring(monkeypatch):
    store = {}
    monkeypatch.setattr("meshlens.secrets.keyring.set_password", lambda s, k, v: store.__setitem__(k, v))
    monkeypatch.setattr("meshlens.secrets.keyring.get_password", lambda s, k: store.get(k))

    def delete(s, k):
        store.pop(k, None)
    monkeypatch.setattr("meshlens.secrets.keyring.delete_password", delete)
    return store


def test_settings_sections_secrets_and_devices(tmp_home, monkeypatch):
    store = _fake_keyring(monkeypatch)
    engine = FakeEngine()
    db.upsert_device("02:bb:00:00:00:09", time.time(), ip="10.0.10.195", vendor="TP-LINK TECHNOLOGIES", wired=1)

    async def scenario():
        auth = Auth(tmp_home / "web_auth.json")
        auth.set_password("a-long-password")
        async with TestClient(TestServer(build_app(engine, auth))) as c:
            await c.post("/api/auth/login", json={"password": "a-long-password"})
            s = await (await c.get("/api/settings")).json()
            assert s["network"]["subnets"] == ["10.0.10.0/23"] and s["suggested_devices"][0]["host"] == "10.0.10.195"
            # Invalid values are refused and nothing is saved.
            r = await c.post("/api/settings/network", json={"subnets": "10.0.10.0/23, rubbish"})
            assert r.status == 400 and "rubbish" in (await r.json())["error"]
            r = await c.post("/api/settings/network", json={"subnets": "10.0.10.0/23\n192.168.50.0/24"})
            assert (await r.json())["network"]["subnets"] == ["10.0.10.0/23", "192.168.50.0/24"]
            # Pi-hole token stored in the credential store, never in the file or the response.
            r = await c.post("/api/settings/pihole", json={"enabled": True, "url": "http://10.0.10.53", "secret": "tok-123"})
            body = await r.text()
            assert "tok-123" not in body and '"pihole": true' in body
            assert "tok-123" not in (tmp_home / "config.yaml").read_text()
            assert store["pihole:http://10.0.10.53"] == "tok-123"
            assert (await c.post("/api/settings/nonsense", json={})).status == 404
            # Device logins: add, keep password across a rename of username, remove.
            r = await c.post("/api/settings/devices", json={"devices": [
                {"name": "Office switch", "kind": "tplink_easy_smart", "host": "10.0.10.195", "username": "admin",
                 "password": "sw-pass"}]})
            d = (await r.json())["devices"][0]
            assert d["password_set"] and "sw-pass" not in await r.text() and d["ident"] == "admin@10.0.10.195"
            r = await c.post("/api/settings/devices", json={"devices": [
                {"name": "Office switch", "kind": "tplink_easy_smart", "host": "10.0.10.195", "username": "root",
                 "orig_ident": "admin@10.0.10.195"}]})
            assert (await r.json())["devices"][0]["password_set"] and "device:admin@10.0.10.195" not in store
            r = await c.post("/api/settings/devices", json={"devices": [{"orig_ident": "root@10.0.10.195", "removed": True}]})
            assert (await r.json())["devices"] == [] and not any(k.startswith("device:") for k in store)
            # Analysis thresholds are editable and used.
            r = await c.post("/api/settings/analysis", json={"kick_loop_count": "2"})
            assert (await r.json())["analysis"]["kick_loop_count"] == 2 and engine.cfg.analysis.kick_loop_count == 2
            a = await (await c.get("/api/analysis?hours=24")).json()
            assert {"issues", "devices", "units", "network"} <= set(a)
            t = await (await c.get("/api/trends?days=30")).json()
            assert t["compare"]["days"] == 7
    run(scenario())


def test_change_dashboard_password(tmp_home):
    async def scenario():
        auth = Auth(tmp_home / "web_auth.json")
        auth.set_password("a-long-password")
        async with TestClient(TestServer(build_app(FakeEngine(), auth))) as c:
            assert (await c.post("/api/auth/password", json={"current": "x", "new": "another-long-one"})).status == 401
            await c.post("/api/auth/login", json={"password": "a-long-password"})
            r = await c.post("/api/auth/password", json={"current": "wrong", "new": "another-long-one"})
            assert r.status == 403
            assert (await c.post("/api/auth/password", json={"current": "a-long-password", "new": "short"})).status == 400
            r = await c.post("/api/auth/password", json={"current": "a-long-password", "new": "another-long-one"})
            assert r.status == 200 and (await c.get("/api/status")).status == 200   # this browser stays logged in
        assert auth.check_password("another-long-one") and not auth.check_password("a-long-password")
    run(scenario())
