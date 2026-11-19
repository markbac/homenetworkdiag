"""Issue history (new / resolved), marked changes with before/after, key-device suggestions (invented data)."""
import threading

from meshlens import config
from meshlens.analysis_net import key_device_suggestions, net_issues
from meshlens.config import AnalysisConfig

NOW = 1_800_000_000.0


def test_issue_log_new_ongoing_resolved(tmp_home):
    from meshlens.store import db
    a = {"rule": "flapping", "subject": "02:00:00:00:00:01", "title": "Plug keeps disconnecting", "severity": "medium"}
    b = {"rule": "radar_dfs", "subject": "radar", "title": "Radar", "severity": "high"}
    info = {"rule": "new_device", "subject": "02:00:00:00:00:09", "title": "New", "severity": "info"}
    log = db.log_issues([a, b, info], NOW)
    assert ("new_device", "02:00:00:00:00:09") not in log           # info isn't tracked
    assert log[("flapping", "02:00:00:00:00:01")]["first_seen"] == NOW
    db.log_issues([a], NOW + 3600)                                   # b gone
    res = db.resolved_issues(NOW)
    assert [r["rule"] for r in res] == ["radar_dfs"] and res[0]["resolved_at"] == NOW + 3600
    log = db.log_issues([a, b], NOW + 7200)                          # b back: new again
    assert log[("radar_dfs", "radar")]["first_seen"] == NOW + 7200 and log[("radar_dfs", "radar")]["resolved_at"] is None
    assert log[("flapping", "02:00:00:00:00:01")]["first_seen"] == NOW   # still ongoing since the start


def test_change_impact_compares_equal_windows(tmp_home):
    from meshlens.engine import Engine
    from meshlens.store import db
    t = NOW
    rows = []
    for k in range(60):     # an hour before: 10% loss and spikes; an hour after: clean
        rows.append({"ts": t - 3600 + k * 60, "target": "10.0.0.1", "kind": "router", "label": "Router", "sent": 30, "lost": 3,
                     "avg_ms": 10, "min_ms": 5, "max_ms": 300, "p95_ms": 50, "jitter_ms": 4})
        rows.append({"ts": t + k * 60, "target": "10.0.0.1", "kind": "router", "label": "Router", "sent": 30, "lost": 0,
                     "avg_ms": 5, "min_ms": 3, "max_ms": 20, "p95_ms": 9, "jitter_ms": 1})
    db.insert_many("ping_minutes", rows)
    db.insert_events([{"ts": t - 1800 + k, "unit": "02:00:00:00:00:64", "kind": "disconnect", "mac": "02:00:00:00:00:01",
                       "iface": None, "detail": {}, "raw_hash": f"d{k}"} for k in range(6)])
    e = Engine.__new__(Engine)
    e.cfg, e._analysis, e._channels, e._lock = config.Config(), {}, None, threading.Lock()
    im = e.change_impact(t, now=t + 3600)
    assert im["hours"] == 1.0
    assert im["before"]["router_loss_pct"] == 10.0 and im["after"]["router_loss_pct"] == 0.0
    assert im["before"]["router_spikes_per_h"] == 60.0 and im["after"]["router_spikes_per_h"] == 0.0
    assert im["before"]["disconnects_per_h"] == 6.0 and im["after"]["disconnects_per_h"] == 0.0
    cid = db.add_change(t, "Unplugged the extra cable")
    assert db.changes()[0]["note"] == "Unplugged the extra cable"
    db.delete_change(cid)
    assert db.changes() == []


def test_key_device_suggestions_and_issue():
    devs = [{"mac": "02:00:00:00:00:53", "name": "dns-pi", "wired": 1, "last_seen": NOW - 60},
            {"mac": "02:00:00:00:00:54", "name": "dns-pi", "wired": 0, "last_seen": NOW - 60},       # same host on Wi-Fi: once
            {"mac": "02:00:00:00:00:55", "name": "nas", "wired": 1, "dtype": "nas / server", "last_seen": NOW - 60},
            {"mac": "02:00:00:00:00:56", "name": "switch", "dtype": "network", "wired": 1, "last_seen": NOW - 60, "watched": 1},
            {"mac": "02:00:00:00:00:57", "name": "phone", "dtype": "phone / tablet", "last_seen": NOW - 60},
            {"mac": "06:00:00:00:00:58", "name": "random", "randomised": 1, "last_seen": NOW - 60},
            {"mac": "02:00:00:00:00:59", "name": "desk-pc", "dtype": "computer", "wired": 1, "last_seen": NOW - 60},
            {"mac": "02:00:00:00:00:5a", "name": "wall-display", "dtype": "nas / server", "wired": 0, "last_seen": NOW - 60}]
    ports = [{"mac": "02:00:00:00:00:53", "port": 53}, {"mac": "02:00:00:00:00:54", "port": 53},
             {"mac": "02:00:00:00:00:59", "port": 445}]
    s = key_device_suggestions(devs, ports, NOW)
    assert [(x["name"], x["why"]) for x in s] == [("dns-pi", "DNS server"), ("nas", "nas / server")]
    assert s[0]["mac"] == "02:00:00:00:00:53"                       # the wired address
    out = {i["rule"]: i for i in net_issues({"devices": devs, "open_ports": ports, "analysis_cfg": AnalysisConfig()}, {}, NOW)}
    assert out["watch_suggest"]["title"] == "Watch 2 key devices" and "dns-pi (DNS server)" in out["watch_suggest"]["evidence"]
