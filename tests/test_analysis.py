import time

from meshlens import analysis, config
from meshlens.analysis import analyse, device_score

NOW = 1_800_000_000.0
ROUTER = "02:00:00:00:01:64"
NODE = "02:00:00:00:02:44"
UNITS = [{"mac": ROUTER, "role": "router", "alias": "Main", "reachable": 1, "macs": []},
         {"mac": NODE, "role": "node", "alias": "Upstairs", "reachable": 1, "macs": ["0e:00:00:00:02:45"]}]


def ev(ts, kind, mac, unit=ROUTER, iface="ra1", **detail):
    return {"ts": NOW - ts, "kind": kind, "mac": mac, "unit": unit, "iface": iface,
            "band": "2.4" if iface.startswith("ra") and not iface.startswith("rai") else "5", "detail": detail}


def samples(mac, rssi, rate=50.0, unit=ROUTER, band="2.4", n=10):
    return [{"ts": NOW - 60 * i, "unit": unit, "mac": mac, "band": band, "rssi": rssi, "tx_rate": rate}
            for i in range(n, 0, -1)]


def rules(res):
    return {(i["rule"], i["subject"]) for i in res["issues"]}


def test_kick_loop_explains_threshold_and_fix():
    mac = "02:aa:00:00:00:02"
    evs = [ev(3000, "kick", mac), ev(2000, "kick", mac), ev(1000, "kick", mac),
           ev(1500, "roam_candidate", mac, target="0e:00:00:00:02:45", target_rssi=-74, rssi=-76)]
    res = analyse(evs, samples(mac, -75), [], UNITS, [{"mac": mac, "name": "PC"}],
                  {ROUTER: {"wl0_user_rssi": "-70"}}, now=NOW)
    kick = next(i for i in res["issues"] if i["rule"] == "kick_loop")
    assert kick["severity"] == "high" and kick["title"] == "PC keeps getting kicked off Wi-Fi"
    assert "-70 dBm on 2.4 GHz" in kick["cause"] and "down to -76 dBm" in kick["evidence"]
    assert any("-75 dBm" in f for f in kick["fixes"]) and kick["help"] == "issue-kick_loop"
    assert ("weak_signal", mac) in rules(res)
    assert res["devices"][mac]["label"] == "poor"


def test_sticky_client_and_flapping_with_good_signal():
    mac = "02:aa:00:00:00:03"
    evs = [ev(900, "roam_candidate", mac, target="0e:00:00:00:02:45", target_rssi=-55, rssi=-70),
           ev(500, "roam_candidate", mac, target="0e:00:00:00:02:45", target_rssi=-56, rssi=-71)]
    evs += [ev(3000 - i * 400, "disconnect", mac) for i in range(6)]
    res = analyse(evs, samples(mac, -60), [], UNITS, [], {}, now=NOW)
    sticky = next(i for i in res["issues"] if i["rule"] == "sticky_client")
    assert "Upstairs" in sticky["evidence"] and "15 dB stronger" in sticky["evidence"]
    flap = next(i for i in res["issues"] if i["rule"] == "flapping")
    assert "signal is good" in flap["cause"]


def test_sticky_not_reported_when_device_moved():
    mac = "02:aa:00:00:00:04"
    evs = [ev(900, "roam_candidate", mac, target="x", target_rssi=-55, rssi=-70), ev(880, "moved_away", mac),
           ev(500, "roam_candidate", mac, target="x", target_rssi=-56, rssi=-71), ev(450, "connect", mac)]
    assert "sticky_client" not in {r for r, _ in rules(analyse(evs, [], [], UNITS, [], {}, now=NOW))}


def test_radio_errors_unit_health_and_mass_drop():
    radios = [{"unit": ROUTER, "iface": "ra0", "band": "2.4", "tx_fail_pct": 15.7, "rx_crc_pct": 22.6, "temperature": 53}]
    evs = [ev(100 + i, "disconnect", f"02:aa:00:00:01:0{i}") for i in range(5)]
    res = analyse(evs, [], radios, UNITS, [], {}, now=NOW)
    radio = next(i for i in res["issues"] if i["rule"] == "radio_errors")
    assert radio["severity"] == "high" and any("20 MHz" in f for f in radio["fixes"])
    assert ("mass_drop", ROUTER) in rules(res)
    assert res["units"][ROUTER]["score"] < res["units"][NODE]["score"] == 100


def test_unmanaged_unit_is_info_and_unscored():
    units = UNITS + [{"mac": "02:00:00:00:03:e8", "alias": "Garden", "reachable": 0,
                      "last_error": "not in the router's cfg_device_list (not a managed node)"}]
    res = analyse([], [], [], units, [], {}, now=NOW)
    g = next(i for i in res["issues"] if i["subject"] == "02:00:00:00:03:e8")
    assert g["severity"] == "info" and res["units"]["02:00:00:00:03:e8"]["score"] is None


def test_vendor_pattern_and_settings_review():
    devs = [{"mac": f"02:aa:00:00:02:0{i}", "name": f"PC{i}", "vendor": "ALFA, Inc."} for i in range(3)]
    evs = [ev(3000 - j * 300, "kick", d["mac"]) for d in devs[:2] for j in range(5)]
    settings = {ROUTER: {"wl1_user_rssi": "-65", "wps_enable": "1", "emf_enable": "0", "misc_http_x": "1"}}
    res = analyse(evs, [], [], UNITS, devs, settings, now=NOW)
    r = rules(res)
    assert ("vendor_pattern", "ALFA, Inc.") in r
    assert {("aggressive_roaming", "wl1"), ("wps_on", "wps"), ("igmp_snooping_off", "emf"), ("remote_admin", "misc_http_x")} <= r
    assert res["issues"][0]["severity"] == "high"   # most severe first


def test_new_and_watched_devices():
    devs = [{"mac": "02:aa:00:00:03:01", "name": "New", "first_seen": NOW - 600, "last_seen": NOW},
            {"mac": "02:aa:00:00:03:02", "name": "Cam", "watched": 1, "first_seen": NOW - 9e5, "last_seen": NOW - 4 * 3600}]
    r = rules(analyse([], [], [], UNITS, devs, {}, now=NOW))
    assert ("new_device", "02:aa:00:00:03:01") in r and ("watched_offline", "02:aa:00:00:03:02") in r


def test_thresholds_are_configurable():
    mac = "02:aa:00:00:00:05"
    evs = [ev(3000, "kick", mac), ev(1000, "kick", mac)]
    assert "kick_loop" not in {x for x, _ in rules(analyse(evs, [], [], UNITS, [], {}, now=NOW))}
    cfg = config.AnalysisConfig(kick_loop_count=2)
    assert "kick_loop" in {x for x, _ in rules(analyse(evs, [], [], UNITS, [], {}, cfg, now=NOW))}


def test_network_summary_and_scores():
    assert device_score(0, 0, 0, -50) == 100 and device_score(3, 0, 0, -80) == 40
    empty = analyse([], [], [], [], [], {}, now=NOW)
    assert empty["network"]["score"] is None and "Not enough data" in empty["network"]["summary"]
    ok = analyse([], samples("02:aa:00:00:00:06", -50), [], UNITS, [], {}, now=NOW)
    assert ok["network"]["score"] == 100 and "Nothing needs attention" in ok["network"]["summary"]


def test_trend_and_compare():
    rows = [{"day": "2026-11-01", "mac": "a", "kicks": 4, "roams": 2, "connects": 1, "disconnects": 1, "score": 50},
            {"day": "2026-11-01", "mac": "b", "kicks": 0, "roams": 0, "connects": 1, "disconnects": 0, "score": 100},
            {"day": "2026-11-10", "mac": "a", "kicks": 1, "roams": 0, "connects": 1, "disconnects": 0, "score": 90}]
    t = analysis.trend(rows)
    assert t[0] == {"day": "2026-11-01", "devices": 2, "kicks": 4, "roam_attempts": 2, "disconnects": 1,
                    "poor_devices": 1, "avg_score": 75}
    c = analysis.compare_periods(rows, 7, "2026-11-13")
    assert c["current"]["kicks_per_day"] == 1.0 and c["previous"]["kicks_per_day"] == 4.0


def test_rollup_fills_daily_summary(tmp_home):
    from meshlens.store import db
    now = time.time()
    db.insert_events([{"ts": now - 10, "unit": "u", "kind": "kick", "mac": "m1", "raw_hash": "a"},
                      {"ts": now - 9, "unit": "u", "kind": "roam_candidate", "mac": "m1", "raw_hash": "b"}])
    db.insert_many("station_samples", [{"ts": now - 5, "unit": "u", "mac": "m1", "rssi": -60, "tx_rate": 100.0}])
    db.rollup_day("d", now - 100, now + 1, lambda k, r, d, rssi: device_score(k, r, d, rssi))
    row = db.daily_summary("d")[0]
    assert row["kicks"] == 1 and row["roams"] == 1 and row["avg_rssi"] == -60 and row["score"] == 88


def test_config_round_trip_and_validation(tmp_home):
    cfg = config.from_dict({"subnet": "192.168.1.0/24", "devices": [{"name": "sw", "kind": "tplink_easy_smart",
                                                                     "host": "10.0.10.195", "bogus": 1}],
                            "analysis": {"weak_rssi_dbm": "-75"}, "pihole": {"enabled": "true", "url": "http://p"}})
    assert cfg.network.subnets == ["192.168.1.0/24"] and cfg.devices[0].host == "10.0.10.195"
    assert cfg.analysis.weak_rssi_dbm == -75 and cfg.pihole.enabled is True
    assert config.validate(cfg) == []
    cfg.save()
    assert config.load().devices[0].kind == "tplink_easy_smart"
    bad = config.from_dict({"network": {"subnets": "10.0.10.0/23, nonsense"}, "devices": [{"kind": "x"}]})
    errs = " ".join(config.validate(bad))
    assert "nonsense" in errs and "needs an address" in errs and "unknown kind" in errs


def test_unit_score_not_zeroed_by_many_mass_drops():
    # 6 separate "many devices dropped" bursts on one unit: penalty is capped, the unit still scores.
    evs = [ev(5000 - b * 600 + i, "disconnect", f"02:aa:00:00:0{b}:0{i}") for b in range(6) for i in range(5)]
    res = analyse(evs, [], [], UNITS, [], {}, now=NOW)
    assert sum(1 for i in res["issues"] if i["rule"] == "mass_drop") == 6
    assert res["units"][ROUTER]["score"] >= 70
