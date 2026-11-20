"""Faster history queries, one analysis at a time, and probe alerts that name your own units (invented data)."""
import threading
import time

from meshlens import config
from meshlens.analysis_net import net_issues, own_unit_lookup
from meshlens.config import AnalysisConfig

NOW = 1_800_000_000.0
UNIT = {"mac": "02:28:aa:00:00:60", "alias": "Landing", "role": "node",
        "macs": ["02:28:aa:00:00:61", "06:28:aa:50:00:61"]}


def test_own_unit_lookup_matches_any_radio_address():
    find = own_unit_lookup([UNIT])
    assert find("06:28:AA:50:00:61") == "Landing"
    assert find("0a:28:aa:50:00:61") == "Landing"     # byte 0 varies per SSID on ASUS
    assert find("02:00:00:00:00:99") is None


def _alert(text, sev="danger"):
    return {"ts": NOW - 60, "unit": "p1", "kind": "probe_alert", "mac": None, "iface": None,
            "detail": {"text": text, "severity": sev}}


def test_deauth_from_own_unit_is_steering_not_attack():
    net = {"units": [UNIT], "probes": [{"id": "p1", "room": "Hall", "paired": 1, "last_seen": NOW}],
           "analysis_cfg": AnalysisConfig(),
           "unit_events": [_alert("Deauth flood: 150 in 5 s from 06:28:AA:50:00:61 to 02:00:00:00:00:4a")]}
    i = next(x for x in net_issues(net, {}, NOW) if x["rule"] == "probe_alert")
    assert i["severity"] == "low" and "your own unit Landing" in i["evidence"]
    net["unit_events"] = [_alert("Access point 06:28:AA:50:00:61 disconnected devices 30 times in 5 s", "warn")]
    i = next(x for x in net_issues(net, {}, NOW) if x["rule"] == "probe_alert")
    assert i["severity"] == "low" and "Landing" in i["evidence"]
    net["unit_events"] = [_alert("Deauth flood: 150 in 5 s from 02:00:00:00:00:77 to 02:00:00:00:00:4a")]
    i = next(x for x in net_issues(net, {}, NOW) if x["rule"] == "probe_alert")
    assert i["severity"] == "high" and "own unit" not in i["evidence"]


def test_iface_first_last_and_unit_filter(tmp_home):
    from meshlens.store import db
    rows = []
    for k in range(5):
        for unit, iface in (("u1", "eth1"), ("u1", "eth2"), ("u2", "eth1")):
            rows.append({"ts": NOW + k * 60, "unit": unit, "iface": iface, "rx_bytes": k, "tx_bytes": k,
                         "rx_packets": k, "tx_packets": k, "rx_errs": k, "tx_errs": 0, "rx_drop": 0, "tx_drop": 0})
    db.insert_many("iface_counters", rows)
    fl = db.iface_first_last(NOW + 30)
    assert len(fl) == 6 and {r["ts"] for r in fl} == {NOW + 60, NOW + 240}
    assert {r["unit"] for r in db.iface_counters_since(NOW, unit="u2")} == {"u2"}
    assert all(r["iface"] in ("eth0", "ppp0") or r["iface"].startswith(("ra", "at")) for r in db.throughput_counters(NOW))


def test_analysis_runs_once_for_concurrent_callers(tmp_home, monkeypatch):
    from meshlens.engine import Engine
    e = Engine.__new__(Engine)
    e.cfg, e._analysis, e._channels, e._lock = config.Config(), {}, None, threading.Lock()
    calls = []

    def slow(hours):            # like the real one: compute, then store in the cache
        calls.append(hours)
        time.sleep(0.3)
        res = {"issues": []}
        with e._lock:
            e._analysis[hours] = (time.time(), res)
        return res
    monkeypatch.setattr(e, "_analyse", slow)
    ts = [threading.Thread(target=e.analysis) for _ in range(5)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert calls == [24]
