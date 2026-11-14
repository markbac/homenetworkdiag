import asyncio
import csv
import io
import json
import time
import zipfile

from aiohttp.test_utils import TestClient, TestServer

from meshlens import export
from meshlens.store import db


def seed():
    now = time.time()
    db.upsert_unit("02:00:00:00:01:64", alias="Main", role="router", macs=json.dumps(["02:00:00:00:01:64"]))
    db.upsert_device("02:aa:00:00:00:02", now, name="Laptop")
    db.insert_events([{"ts": now - 100, "unit": "02:00:00:00:01:64", "kind": "kick", "mac": "02:aa:00:00:00:02",
                       "iface": "ra1", "detail": {"rssi": -77}, "raw_hash": "a"},
                      {"ts": now - 90000, "unit": "02:00:00:00:01:64", "kind": "kick", "mac": "02:aa:00:00:00:02",
                       "iface": "ra1", "detail": {}, "raw_hash": "b"}])
    return now


def test_rows_add_names_and_time(tmp_home):
    now = seed()
    rows = list(export.rows("events", now - 3600, now + 1))
    assert len(rows) == 1                                   # the older event is outside the range
    r = rows[0]
    assert r["mac_name"] == "Laptop" and r["unit_name"] == "Main" and r["detail_rssi"] == -77 and len(r["time"]) == 19
    assert [d["name"] for d in export.rows("devices", 0, now)] == ["Laptop"]


def test_csv_json_zip(tmp_home):
    now = seed()
    text = b"".join(export.csv_chunks("events", now - 3600, now + 1)).decode("utf-8-sig")
    rec = list(csv.DictReader(io.StringIO(text)))
    assert rec[0]["mac_name"] == "Laptop" and rec[0]["kind"] == "kick"
    assert b"no data" in b"".join(export.csv_chunks("speed", now - 3600, now))
    js = json.loads(b"".join(export.json_chunks("events", now - 3600, now + 1)))
    assert js["dataset"] == "events" and len(js["rows"]) == 1
    z = zipfile.ZipFile(io.BytesIO(export.zip_file(["events", "devices"], now - 3600, now + 1)))
    assert set(z.namelist()) == {"README.txt", "events.csv", "devices.csv"}


def test_parse_time():
    assert export.parse_time("1800000000", 0) == 1800000000
    t = export.parse_time("2026-11-14T08:30", 0)
    assert time.localtime(t).tm_hour == 8 and time.localtime(t).tm_min == 30
    assert export.parse_time(None, 5) == 5


def test_export_endpoint(tmp_home):
    from tests.test_web import FakeEngine
    from meshlens.web.auth import Auth
    from meshlens.web.server import build_app
    now = seed()

    async def scenario():
        auth = Auth(tmp_home / "web_auth.json")
        auth.set_password("a-long-password")
        async with TestClient(TestServer(build_app(FakeEngine(), auth))) as c:
            assert (await c.get("/api/export?d=events")).status == 401
            await c.post("/api/auth/login", json={"password": "a-long-password"})
            r = await c.get(f"/api/export?d=events&from={now - 3600}&to={now + 1}")
            assert r.status == 200 and "attachment" in r.headers["Content-Disposition"]
            assert "Laptop" in (await r.read()).decode("utf-8-sig")
            r = await c.get(f"/api/export?d=events,devices&from={now - 3600}&to={now + 1}")
            assert r.headers["Content-Type"] == "application/zip"
            assert (await c.get("/api/export?d=nope")).status == 400
            assert (await c.get(f"/api/export?d=events&from={now}&to={now - 10}")).status == 400
    asyncio.run(scenario())


def test_csv_columns_cover_optional_details(tmp_home):
    now = seed()          # newest event has detail_rssi, the first row in range may not
    db.insert_events([{"ts": now - 200, "unit": "02:00:00:00:01:64", "kind": "connect", "mac": "02:aa:00:00:00:02",
                       "iface": "ra1", "detail": {}, "raw_hash": "c"}])
    text = b"".join(export.csv_chunks("events", now - 3600, now + 1)).decode("utf-8-sig")
    rec = list(csv.DictReader(io.StringIO(text)))
    assert rec[0]["kind"] == "connect" and rec[1]["detail_rssi"] == "-77"


def test_export_json_several_datasets_is_one_json_file(tmp_home):
    from tests.test_web import FakeEngine
    from meshlens.web.auth import Auth
    from meshlens.web.server import build_app
    now = seed()

    async def scenario():
        auth = Auth(tmp_home / "web_auth.json")
        auth.set_password("a-long-password")
        async with TestClient(TestServer(build_app(FakeEngine(), auth))) as c:
            await c.post("/api/auth/login", json={"password": "a-long-password"})
            r = await c.get(f"/api/export?d=events,devices&from={now - 3600}&to={now + 1}&format=json")
            assert r.headers["Content-Type"].startswith("application/json") and ".json" in r.headers["Content-Disposition"]
            doc = json.loads(await r.read())
            assert set(doc["datasets"]) == {"events", "devices"}
    asyncio.run(scenario())
