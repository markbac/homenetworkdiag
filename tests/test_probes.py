"""ESP32 probe discovery, pairing, polling, storage, channel plan and issues (fake probe; invented addresses)."""
import json

import pytest

from meshlens import channels
from meshlens.analysis_net import net_issues
from meshlens.collectors import probes as P
from meshlens.config import AnalysisConfig, ProbesConfig

PID, IP = "c5-0a0b0c", "10.0.0.34"
ROUTER = "02:28:aa:00:00:64"
NOW = 1_800_000_000.0


class FakeProbe:
    """Answers like the c5-probe firmware (docs/api/openapi.yaml)."""

    def __init__(self):
        self.adopted, self.token, self.code, self.calls = False, "0123456789abcdef0123456789abcdef", "482913", []
        self.placement = {}
        self.now = NOW          # the probe reports alert ages relative to the time it is asked

    def __call__(self, method, ip, path, body=None, token=None, timeout=5):
        self.calls.append((method, ip, path, token))
        if ip != IP:
            raise OSError("no route")
        env = {"probe": PID, "time": "", **({"placement": self.placement} if self.placement else {})}
        protected = path not in ("/api/info", "/api/adopt", "/api/adopt/confirm")
        if self.adopted and protected and token != self.token:
            return 401, {"error": "unauthorised"}
        if path == "/api/info":
            return 200, {**env, "chip": "ESP32-C5", "firmware": "0.2.0", "bands": ["2.4", "5"], "adopted": self.adopted,
                         "mode": "Both", "uptime_s": 99,
                         "wifi": {"connected": True, "ssid": "Home-Net", "channel": 48, "band": "5", "rssi": -55, "ip": IP}}
        if path == "/api/adopt":
            return 200, {**env, "code_shown": True, "expires_s": 300}
        if path == "/api/adopt/confirm":
            if body.get("code") != self.code:
                return 403, {"error": "wrong code"}
            self.adopted = True
            return 200, {**env, "token": self.token}
        if path == "/api/placement":
            self.placement = body
            return 200, env
        if path == "/api/wifi.json":
            return 200, {**env, "age_s": 20, "aps": [
                {"ssid": "Home-Net", "bssid": "06:28:AA:1C:00:64", "channel": 48, "band": "5", "rssi": -52, "pmf": "capable",
                 "own": True, "joined": True, "seen_s_ago": 10, "width_mhz": 80},
                {"ssid": "Next-Door", "bssid": "02:bb:00:00:00:01", "channel": 40, "band": "5", "rssi": -61, "pmf": "off", "seen_s_ago": 10},
                {"ssid": "Old", "bssid": "02:bb:00:00:00:02", "channel": 6, "band": "2.4", "rssi": -80, "seen_s_ago": 5000}]}
        if path == "/api/air.json":
            return 200, {**env, "frames": 1200, "hopping": False, "deauths_last_minute": 0,
                         "clients": [{"mac": "02:00:00:00:99:01", "bssid": "06:28:aa:1c:00:64", "rssi": -60, "channel": 48,
                                      "frames": 900, "bytes": 5_000_000, "seen_s_ago": 3}],
                         "probes": [], "access_points": [
                             {"bssid": "06:28:aa:1c:00:64", "ssid": "Home-Net", "pmf": "capable"},
                             {"bssid": "06:28:aa:1c:00:48", "ssid": "Home-Net", "pmf": "off"}]}
        if path == "/api/alerts.json":
            return 200, {**env, "alerts": [{"severity": "warn", "text": "Deauth flood: 40 frames from 02:bb:00:00:00:01",
                                          "s_ago": int(self.now - (NOW - 60))}]}
        if path == "/api/env":
            return 200, {**env, "temp_c": 21.5, "rh": 44, "battery_v": 4.9}
        return 404, {"error": "not found"}


@pytest.fixture
def vault(monkeypatch):
    store = {}
    monkeypatch.setattr(P.secrets, "set_secret", lambda k, i, v: store.__setitem__((k, i), v))
    monkeypatch.setattr(P.secrets, "get_secret", lambda k, i: store.get((k, i)))
    monkeypatch.setattr(P.secrets, "clear_secret", lambda k, i: store.pop((k, i), None))
    return store


def test_beacon_parsing_uses_sender_address():
    b = P.parse_beacon(json.dumps({"type": "hndprobe", "id": PID, "ip": "10.9.9.9", "bands": ["2.4", "5"], "fw": "0.2.0",
                                   "adopted": False}).encode(), IP)
    assert b["id"] == PID and b["ip"] == IP and b["bands"] == "2.4,5"
    assert P.parse_beacon(b'{"type": "other"}', IP) is None and P.parse_beacon(b"junk", IP) is None
    assert P.parse_beacon(json.dumps({"type": "hndprobe", "id": "../etc"}).encode(), IP) is None
    assert P.looks_like_probe({"name": "esp32c5-0A0B0C"}) and P.looks_like_probe({"name": "c5-0a0b0c"})
    assert not P.looks_like_probe({"name": "ESP_631004"})


def test_unpaired_probe_polled_paired_then_token_used(tmp_home, vault):
    from meshlens.store import db
    fake = FakeProbe()
    c = P.ProbeCollector(ProbesConfig(), tmp_home, http=fake)
    c.seen({"id": PID, "ip": IP, "adopted": False}, NOW)
    c.poll(NOW)
    p = db.probe(PID)
    assert p["chip"] == "ESP32-C5" and not p["adopted"] and p["info"]["wifi"]["channel"] == 48
    aps = db.probe_aps_since(0)
    assert {a["bssid"] for a in aps} == {"06:28:aa:1c:00:64", "02:bb:00:00:00:01"}      # stale one skipped
    assert db.probe_clients_since(0)[0]["bytes"] == 5_000_000
    assert [e["kind"] for e in db.events(0, kinds=["probe_alert"])] == ["probe_alert"]
    assert p["env"]["temp_c"] == 21.5 and p["air"]["beacons"][1]["pmf"] == "off"

    assert c.start_pairing(PID)["ok"]
    assert c.confirm_pairing(PID, "000000")["ok"] is False and c.confirm_pairing(PID, "12")["ok"] is False
    assert c.confirm_pairing(PID, "482913")["ok"] and vault[("probe", PID)] == fake.token
    assert c.set_placement(PID, "Lounge", "TV shelf")["ok"] and fake.placement == {"room": "Lounge", "spot": "TV shelf"}
    fake.calls.clear()
    fake.now = NOW + 400
    c.poll(NOW + 400)
    assert all(t == fake.token for m, ip, path, t in fake.calls if path not in ("/api/info",))
    p = db.probe(PID)
    assert p["paired"] and p["room"] == "Lounge"
    assert len(db.events(0, kinds=["probe_alert"])) == 1      # same alert again: not stored twice

    fake.token = "ffffffffffffffffffffffffffffffff"            # probe re-paired elsewhere
    c.poll(NOW + 800)
    assert not db.probe(PID)["paired"] and "pair it again" in db.probe(PID)["last_error"]


def test_paired_probe_does_not_follow_a_stray_announcement(tmp_home, vault):
    from meshlens.store import db
    fake = FakeProbe()
    c = P.ProbeCollector(ProbesConfig(), tmp_home, http=fake)
    c.seen({"id": PID, "ip": IP}, NOW)
    db.upsert_probe(PID, paired=1)
    c.seen({"id": PID, "ip": "10.0.0.66"}, NOW + 5)            # someone else claims the id
    assert db.probe(PID)["ip"] == IP


def test_view_and_forget(tmp_home, vault):
    from meshlens.store import db
    c = P.ProbeCollector(ProbesConfig(), tmp_home, http=FakeProbe())
    c.seen({"id": PID, "ip": IP}, NOW)
    c.poll(NOW)
    units = [{"mac": ROUTER, "alias": "Main router", "role": "router", "macs": []}]
    v = P.view(units, [{"mac": "02:00:00:00:99:01", "name": "Laptop"}], now=NOW + 60)[0]
    assert v["online"] and v["units_heard"][0]["unit"] == "Main router" and v["neighbours"]["5"] == 1
    assert v["top_talkers"][0]["name"] == "Laptop" and v["alerts"][0]["severity"] == "warn"
    c.forget(PID)
    assert db.probes() == []


def test_plan_merges_probe_aps_with_strongest_signal():
    air = [{"ts": NOW - 60, "bssid": "02:bb:00:00:00:01", "ssid": "Next-Door", "band": "5", "channel": 40, "rssi": -85,
            "util_pct": None, "own_unit": None}]
    pa = [{"ts": NOW - 30, "probe": PID, "bssid": "02:bb:00:00:00:01", "ssid": "Next-Door", "band": "5", "channel": 40, "rssi": -61},
          {"ts": NOW - 30, "probe": PID, "bssid": "02:bb:00:00:00:03", "ssid": "Upstairs-Flat", "band": "5", "channel": 100, "rssi": -66}]
    units = [{"mac": ROUTER, "role": "router", "macs": [], "info": {"ifaces": [{"iface": "rai0", "band": "5", "type": "AP", "channel": 44}]}}]
    p = channels.plan(air, units, [], NOW, probe_aps=pa, probes=[{"id": PID, "room": "Lounge"}])
    five = {f["block"]: f for f in p["five"]}
    assert five["36-48"]["strong_neighbours"] == 1          # -85 from the PC, -61 from the probe: strongest counts
    assert five["100-112"]["neighbours"] == 1 and p["sensors"] == ["this PC", "probe Lounge"]


def test_probe_issues():
    units = [{"mac": ROUTER, "role": "router", "macs": [ROUTER]}, {"mac": "02:28:aa:00:00:40", "role": "node", "macs": []}]
    plist = [{"id": PID, "ip": IP, "adopted": 0, "paired": 0, "last_seen": NOW - 60, "chip": "ESP32-C5"},
             {"id": "c5-111111", "ip": "10.0.0.35", "adopted": 1, "paired": 1, "room": "Loft", "last_seen": NOW - 5 * 3600,
              "air": {"beacons": [{"bssid": "06:28:aa:1c:00:64", "ssid": "Home-Net", "pmf": "capable"},
                                  {"bssid": "06:28:aa:1c:00:40", "ssid": "Home-Net", "pmf": "off"}]}}]
    alerts = [{"ts": NOW - k * 600, "unit": PID, "kind": "probe_alert", "mac": None, "iface": None,
               "detail": {"severity": "danger", "text": f"Evil twin: Home-Net from 02:bb:00:00:00:0{k}"}} for k in range(3)]
    net = {"probes": plist, "units": units, "unit_events": alerts, "analysis_cfg": AnalysisConfig(),
           "unit_names": {ROUTER: "Main router", "02:28:aa:00:00:40": "Box room"}}
    out = {}
    for i in net_issues(net, {}, NOW):
        out.setdefault(i["rule"], []).append(i)
    assert out["probe_new"][0]["title"] == f"New probe found: {PID}"
    assert "hasn't reported" in out["probe_offline"][0]["title"] and "Loft" in out["probe_offline"][0]["title"]
    assert len(out["probe_alert"]) == 1 and out["probe_alert"][0]["severity"] == "high" and out["probe_alert"][0]["count"] == 3
    assert "Main router: capable" in out["pmf_mismatch_air"][0]["evidence"] and "Box room: off" in out["pmf_mismatch_air"][0]["evidence"]
