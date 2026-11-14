import json
import time

from meshlens import config, export, graphs
from meshlens.analysis import analyse
from meshlens.collectors.asus import parsers as P
from meshlens.store import db

STAT1 = "cpu  100 0 100 800 0 0 0 0 0 0\ncpu0 50 0 50 400 0 0 0 0 0 0\ncpu1 50 0 50 400 0 0 0 0 0 0\n"
STAT2 = "cpu  200 0 200 900 0 0 100 0 0 0\ncpu0 150 0 120 400 0 0 100 0 0 0\ncpu1 50 0 80 500 0 0 0 0 0 0\n"


def test_cpu_memory_thermal_conntrack_df():
    a, b = P.parse_proc_stat(STAT1), P.parse_proc_stat(STAT2)
    u = P.cpu_usage(a, b)
    # overall: total 400 jiffies, idle 100 -> 75 % busy; core0: 270 total, 0 idle -> 100 %; softirq 100/400
    assert u["cpu_pct"] == 75.0 and u["cores"] == [100.0, 23.1] and u["max_core_pct"] == 100.0 and u["softirq_pct"] == 25.0
    assert P.cpu_usage(b, a) is None                      # counters went backwards (reboot)
    m = P.parse_meminfo("MemTotal:  1006796 kB\nMemFree:  387772 kB\nMemAvailable:  431140 kB\n")
    assert m == {"mem_total_mb": 983, "mem_avail_mb": 421, "mem_used_pct": 57.2}
    assert P.parse_meminfo("MemTotal: 1000 kB\nMemFree: 100 kB\nBuffers: 100 kB\nCached: 300 kB\n")["mem_used_pct"] == 50.0
    t = P.parse_thermal("soc_thermal 55506\nmcusys_thermal0 53720\nbroken \n")
    assert t["temp_c"] == 55.5
    assert P.parse_conntrack("684\n300000\n") == {"conntrack": 684, "conntrack_max": 300000}
    df = P.parse_df("Filesystem 1K-blocks Used Available Use% Mounted on\n/dev/ubi0_4 45068 6500 36232 15% /jffs\n"
                    "tmpfs 503396 8824 494572 2% /tmp\n")
    assert df == {"jffs_pct": 15, "tmp_pct": 2}


def test_collector_stores_unit_samples(tmp_home, fixture_text):
    from meshlens.collectors.asus.collector import AsusCollector
    from meshlens.config import RouterConfig
    base = fixture_text("asus/router_mediatek.txt")
    sysbits = ("\n@@@ meminfo\nMemTotal: 1000 kB\nMemAvailable: 400 kB\n@@@ thermal\nsoc_thermal 61000\n"
               "@@@ conntrack\n10\n100\n@@@ uptime\n500.0 1.0\n0.5 0.4 0.3 1/100 1\n")
    texts = iter([base + "\n@@@ cpu_stat\n" + STAT1 + sysbits, base + "\n@@@ cpu_stat\n" + STAT2 + sysbits])

    def runner(host, port, user, pw, script, timeout):
        if host != "10.0.10.1":
            raise __import__("meshlens.collectors.asus.ssh", fromlist=["SshError"]).SshError("unreachable")
        return next(texts)
    c = AsusCollector(RouterConfig(), tmp_home, runner=runner)
    c.poll("pw", full=False)
    c.poll("pw", full=False)
    rows = db.unit_samples_since(0)
    assert len(rows) == 2 and rows[0]["cpu_pct"] is None and rows[1]["cpu_pct"] == 75.0
    assert rows[1]["mem_used_pct"] == 60.0 and rows[1]["temp_c"] == 61.0 and rows[1]["conntrack"] == 10 and rows[1]["uptime_s"] == 500
    main = next(u for u in db.units() if u["role"] == "router")
    assert main["info"]["system"]["max_core_pct"] == 100.0


def test_unit_rules():
    now = 1_800_000_000.0
    u = "02:00:00:00:01:64"
    samples = [{"ts": now - 600 + i * 60, "unit": u, "cpu_pct": 40, "max_core_pct": 98, "mem_used_pct": 95, "temp_c": 90,
                "conntrack": 250_000, "conntrack_max": 300_000, "jffs_pct": 15, "tmp_pct": 95, "uptime_s": 1000 + i * 60}
               for i in range(5)]
    samples[3]["uptime_s"] = 30                             # restarted
    net = {"analysis_cfg": config.AnalysisConfig(), "unit_names": {u: "Main router"}, "unit_samples": samples,
           "settings": {u: {"qos_enable": "0", "webs_state_flag": "1", "webs_state_info": "3004_102_9"}},
           "speeds": [{"ts": now - 60, "grade": "D", "idle_ms": 9, "down_loaded_ms": 40, "up_loaded_ms": 300, "location": "router"}]}
    res = analyse([], [], [], [{"mac": u, "role": "router", "alias": "Main router", "reachable": 1, "macs": []}], [], {},
                  now=now, net=net)
    rules = {i["rule"]: i for i in res["issues"]}
    assert "one core is maxed out" in rules["unit_cpu_high"]["cause"].lower()
    assert {"unit_mem_high", "unit_hot", "conntrack_full", "unit_storage_full", "unit_restarted", "firmware_update", "qos_off"} <= set(rules)
    assert "3004_102_9" in rules["firmware_update"]["evidence"]


def test_unit_series_and_json_multi_export(tmp_home):
    s = graphs.unit_series([{"ts": 1, "unit": "u", "cpu_pct": 10, "max_core_pct": 50, "mem_used_pct": 60, "temp_c": 55}], {"u": "Main"})
    assert s["core"] == {"Main": [[1, 50]]} and s["temp"] == {"Main": [[1, 55]]}
    now = time.time()
    db.insert_many("unit_samples", [{"ts": now - 10, "unit": "u", "cpu_pct": 12.5}])
    db.insert_events([{"ts": now - 5, "unit": "u", "kind": "kick", "mac": "m", "raw_hash": "x"}])
    doc = json.loads(b"".join(export.json_multi_chunks(["system", "events"], now - 60, now + 1)))
    assert set(doc["datasets"]) == {"system", "events"}
    assert doc["datasets"]["system"][0]["cpu_pct"] == 12.5 and doc["datasets"]["events"][0]["kind"] == "kick"
