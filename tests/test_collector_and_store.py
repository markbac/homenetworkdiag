import time

from meshlens.collectors.asus.collector import AsusCollector
from meshlens.collectors.asus.ssh import SshError
from meshlens.config import RouterConfig
from meshlens.store import db


def fake_runner(texts):
    calls = []

    def run(host, port, user, password, script, timeout):
        calls.append((host, script))
        if host not in texts:
            raise SshError(f"{host}: unreachable")
        return texts[host]
    run.calls = calls
    return run


def test_poll_stores_mesh(tmp_home, fixture_text):
    runner = fake_runner({"10.0.10.1": fixture_text("asus/router_mediatek.txt"),
                          "10.0.11.14": fixture_text("asus/node_qualcomm.txt")})
    c = AsusCollector(RouterConfig(), tmp_home, runner=runner)
    st = c.poll("pw", full=True)
    assert st["error"] is None
    # Router script asks for the rotated log on first poll; the second poll does not.
    assert "/tmp/syslog.log-1" in runner.calls[0][1] and "nvram show" not in runner.calls[0][1]

    units = {u["mac"]: u for u in db.units()}
    assert units["02:00:00:00:01:64"]["role"] == "router" and units["02:00:00:00:01:64"]["chip"] == "mediatek"
    assert units["02:00:00:00:02:44"]["chip"] == "qualcomm" and units["02:00:00:00:02:44"]["reachable"] == 1
    assert units["02:00:00:00:01:60"]["reachable"] == 0          # 10.0.11.29 not answering in this test
    assert units["02:00:00:00:01:60"]["alias"] == "First Floor"
    # Radio addresses are recorded so log BSSIDs (e.g. a roam target) resolve to a unit.
    assert {"02:00:00:00:01:64", "02:00:00:00:01:66"} <= set(units["02:00:00:00:01:64"]["macs"])
    assert "06:00:00:00:02:45" in units["02:00:00:00:02:44"]["macs"]          # from the backhaul table (relist)
    garden = units["02:00:00:00:03:e8"]                            # in client data but not a managed node
    assert garden["alias"] == "Garden" and garden["reachable"] == 0 and "cfg_device_list" in garden["last_error"]

    devs = {d["mac"]: d for d in db.devices()}
    assert devs["02:aa:00:00:00:02"]["name"] == "Test-Laptop" and devs["02:aa:00:00:00:02"]["wifi_auth"] == "WPA3-SAE"
    assert devs["02:aa:00:00:00:03"]["name"] == "printer" and devs["02:aa:00:00:00:03"]["name_src"] == "dhcp"
    assert devs["02:bb:00:00:00:01"]["wired"] == 1
    assert devs["0a:00:00:00:00:01"]["randomised"] == 1
    assert "02:00:00:00:01:60" not in devs                          # units are not devices

    assert len(db.latest_stations()) == 3                           # 2 MediaTek + 1 Qualcomm
    radios = {(r["unit"], r["iface"]): r for r in db.latest_radio()}
    assert radios[("02:00:00:00:01:64", "ra0")]["tx_fail_pct"] == 15.7
    evs = db.events(0)
    assert len(evs) == 6 + 5

    # Second poll: same log lines are not stored twice; rotated log not requested again.
    c.poll("pw", full=False)
    assert len(db.events(0)) == 11
    assert "/tmp/syslog.log-1" not in runner.calls[3][1]


def test_router_unreachable(tmp_home):
    c = AsusCollector(RouterConfig(), tmp_home, runner=fake_runner({}))
    st = c.poll(None)
    assert "unreachable" in st["error"] and db.units() == []


def test_enrichment_and_prune(tmp_home):
    now = time.time()
    db.upsert_device("02:AA:00:00:00:02", now, name="X", vendor="V")
    assert db.update_enrichment("02:aa:00:00:00:02", user_name="Ollie's PC", watched=1, bogus="ignored")
    d = db.device("02:aa:00:00:00:02")
    assert d["user_name"] == "Ollie's PC" and d["watched"] == 1 and d["name"] == "X"
    db.upsert_device("02:aa:00:00:00:02", now, name="Y")             # detection never overwrites user data
    assert db.device("02:aa:00:00:00:02")["user_name"] == "Ollie's PC"
    db.insert_many("station_samples", [{"ts": now - 40 * 86400, "unit": "u", "mac": "m"}, {"ts": now, "unit": "u", "mac": "m"}])
    db.prune(31, 366, now)
    assert len(db.station_history("m", 0)) == 1


def test_migration_adds_columns(tmp_path):
    import sqlite3
    old = tmp_path / "old.db"
    c = sqlite3.connect(old)
    c.execute("CREATE TABLE units (mac TEXT PRIMARY KEY, model TEXT)")
    c.execute("INSERT INTO units(mac, model) VALUES ('aa', 'BT8')")
    c.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    c.execute("CREATE TABLE devices (mac TEXT PRIMARY KEY, name TEXT)")
    c.commit()
    c.close()
    db.init(old)
    cols = {r[1] for r in db.conn().execute("PRAGMA table_info(units)")}
    assert {"macs", "chip", "alias", "parent_hint"} <= cols and db.units()[0]["model"] == "BT8"
    dcols = {r[1] for r in db.conn().execute("PRAGMA table_info(devices)")}
    assert {"last_alive", "names", "dtype", "watched", "user_name"} <= dcols
    db.close()
