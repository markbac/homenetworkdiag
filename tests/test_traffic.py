"""Traffic features: internet port counters (#17), per-device internet use (#18), Qualcomm station bytes (#19),
ASUS Traffic Analyzer (#20) and managed-switch port traffic (#21). Fixtures use made-up addresses only."""
import json
import shutil
import subprocess

import pytest

from meshlens import graphs
from meshlens.analysis_net import net_issues
from meshlens.collectors import switches
from meshlens.collectors.asus import commands
from meshlens.collectors.asus import parsers as P
from meshlens.collectors.asus.collector import AsusCollector, parse_unit
from meshlens.config import Config, DeviceLogin, RouterConfig
from meshlens.store import db

WAN = "203.0.113.7"

# /proc/net/nf_conntrack in the router's format: a NAT'd internet connection, a UDP one, one to the router itself
# (not internet: reply goes to the device), an ICMP one, the router's own internet connection and an IPv6 one.
CONNTRACK = f"""\
ipv4     2 tcp      6 484 ESTABLISHED src=10.0.11.5 dst=198.51.100.10 sport=36370 dport=443 packets=2437 bytes=1000 src=198.51.100.10 dst={WAN} sport=443 dport=36370 packets=929 bytes=50000 [ASSURED] mark=0 zone=0 use=2
ipv4     2 udp      17 51 src=10.0.11.6 dst=198.51.100.53 sport=5000 dport=443 packets=4 bytes=284 src=198.51.100.53 dst={WAN} sport=443 dport=5000 packets=4 bytes=540 [ASSURED] mark=0 zone=0 use=2
ipv4     2 udp      17 23 src=10.0.11.6 dst=10.0.10.1 sport=39428 dport=53 packets=1 bytes=60 src=10.0.10.1 dst=10.0.11.6 sport=53 dport=39428 packets=2 bytes=472 mark=0 zone=0 use=2
ipv4     2 icmp     1 29 src=10.0.11.5 dst=198.51.100.1 type=8 code=0 id=1 packets=1 bytes=84 src=198.51.100.1 dst={WAN} type=0 code=0 id=1 packets=1 bytes=84 mark=0 zone=0 use=2
ipv4     2 tcp      6 100 ESTABLISHED src={WAN} dst=198.51.100.20 sport=40000 dport=443 packets=3 bytes=300 src=198.51.100.20 dst={WAN} sport=443 dport=40000 packets=3 bytes=900 [ASSURED] mark=0 zone=0 use=2
ipv6     10 tcp      6 300 ESTABLISHED src=2001:db8::5 dst=2001:db8:1::1 sport=1000 dport=443 packets=1 bytes=80 src=2001:db8:1::1 dst=2001:db8::5 sport=443 dport=1000 packets=1 bytes=80 [ASSURED] mark=0 zone=0 use=2
"""


# ---------------------------------------------------------------- #18 internet use per device

@pytest.mark.skipif(not shutil.which("awk"), reason="needs an awk to run the router's script")
def test_inet_flows_awk_keeps_only_internet_connections(tmp_path):
    f = tmp_path / "nf_conntrack"
    f.write_text(CONNTRACK)
    script = commands.INET_FLOWS_AWK.replace('"$(nvram get wan0_ipaddr)"', WAN).replace("/proc/net/nf_conntrack", str(f).replace("\\", "/"))
    out = subprocess.run(["sh", "-c", script], capture_output=True, text=True, check=True).stdout
    flows = P.parse_inet_flows(out)
    assert flows == {
        "tcp|10.0.11.5|198.51.100.10|36370|443": ("10.0.11.5", 1000, 50000),
        "udp|10.0.11.6|198.51.100.53|5000|443": ("10.0.11.6", 284, 540),
        "icmp|10.0.11.5|198.51.100.1||": ("10.0.11.5", 84, 84),          # no ports: the previous line's must not leak
        f"tcp|{WAN}|198.51.100.20|40000|443": (WAN, 300, 900),           # the router's own traffic
    }


def test_inet_flows_only_on_main_router_every_poll():
    assert "@@@ inet_flows" in commands.unit_script(main=True, first_poll=False, full=False)
    assert "@@@ inet_flows" not in commands.unit_script(main=False, first_poll=False, full=True)


def test_flow_deltas():
    prev = {"a": ("10.0.11.5", 1000, 50000), "b": ("10.0.11.6", 100, 100), "gone": ("10.0.11.7", 5, 5)}
    cur = {"a": ("10.0.11.5", 1500, 80000),        # grew
           "b": ("10.0.11.6", 10, 20),              # went down: 5-tuple reused by a new connection, counts in full
           "new": ("10.0.11.6", 200, 4000)}         # new connection counts in full; "gone" closed
    assert P.flow_deltas(prev, cur) == {"10.0.11.5": [30000, 500], "10.0.11.6": [4020, 210]}
    assert P.flow_deltas(None, cur) == {}          # first poll: nothing to compare with


def test_collector_stores_internet_use_per_device(tmp_home):
    db.upsert_device("02:aa:00:00:00:05", 1, ip="10.0.11.5")
    c = AsusCollector(RouterConfig(), tmp_home, runner=None)
    c._store_inet({"k": ("10.0.11.5", 1000, 50000)}, 1000)
    assert db.device_traffic_history(0) == []                                 # baseline only
    c._store_inet({"k": ("10.0.11.5", 2000, 150000), "k2": ("10.0.11.9", 10, 20)}, 1060)
    rows = {r["ip"]: r for r in db.device_traffic_history(0)}
    assert rows["10.0.11.5"]["mac"] == "02:aa:00:00:00:05" and rows["10.0.11.5"]["bytes_in"] == 100000
    assert rows["10.0.11.5"]["bytes_out"] == 1000 and rows["10.0.11.5"]["secs"] == 60
    assert rows["10.0.11.9"]["mac"] is None
    c._store_inet({}, 1120)                     # empty read: next poll is a new baseline, not "everything is new"
    c._store_inet({"k": ("10.0.11.5", 9_000_000, 9_000_000)}, 1180)
    assert len(db.device_traffic_history(0)) == 2
    c._store_inet({"k": ("10.0.11.5", 9_000_100, 9_000_100)}, 5000)   # long gap: baseline again
    assert len(db.device_traffic_history(0)) == 2


def test_top_users_rollup_export_and_device_graph(tmp_home):
    import time
    now = time.time()
    db.upsert_device("02:aa:00:00:00:05", now, ip="10.0.11.5", name="Speaker")
    db.upsert_device("02:aa:00:00:00:06", now, ip="10.0.11.6", name="Laptop")
    db.insert_many("device_traffic", [
        {"ts": now - 120, "secs": 60, "ip": "10.0.11.5", "mac": "02:aa:00:00:00:05", "bytes_in": 30_000_000, "bytes_out": 1_000_000},
        {"ts": now - 60, "secs": 60, "ip": "10.0.11.5", "mac": "02:aa:00:00:00:05", "bytes_in": 30_000_000, "bytes_out": 1_000_000},
        {"ts": now - 60, "secs": 60, "ip": "10.0.11.6", "mac": "02:aa:00:00:00:06", "bytes_in": 5_000_000, "bytes_out": 3_000_000},
        {"ts": now - 60, "secs": 60, "ip": WAN, "mac": None, "bytes_in": 1000, "bytes_out": 1000}])
    t = graphs.top_users(db.device_traffic_totals(now - 3600), {d["mac"]: d for d in db.devices()}, WAN)
    assert [r["name"] for r in t["rows"]] == ["Speaker", "Laptop", "Router itself"]
    assert t["rows"][0]["down_bytes"] == 60_000_000 and t["rows"][0]["share_pct"] == pytest.approx(88.6, abs=0.1)
    g = graphs.device("02:aa:00:00:00:05", 1)
    assert g["internet"]["down_bytes"] == 60_000_000 and g["internet"]["rates"]["Download"][0][1] == 4.0
    import datetime as dt
    day = dt.date.today()
    start = __import__("time").mktime(day.timetuple())
    assert db.rollup_traffic_day(day.isoformat(), start, start + 86400) == 3
    daily = {(r["mac"], r["ip"]): r for r in db.daily_traffic(day.isoformat())}
    assert daily[("02:aa:00:00:00:05", "")]["bytes_in"] == 60_000_000 and ("", WAN) in daily
    from meshlens import export
    assert {"internet", "internet_daily", "switch"} <= set(export.DATASETS)
    assert len(list(export.rows("internet", now - 3600, now + 1))) == 4
    db.prune(raw_days=0, summary_days=400, now=now + 10)
    assert db.device_traffic_history(0) == [] and db.daily_traffic(day.isoformat())


def test_internet_hog_rule():
    now = 10_000
    rows = [{"mac": "02:aa:00:00:00:05", "ip": None, "bytes_in": 4_000_000_000, "bytes_out": 100_000_000,
             "first": now - 3500, "last": now},
            {"mac": "02:aa:00:00:00:06", "ip": None, "bytes_in": 300_000_000, "bytes_out": 10_000_000,
             "first": now - 3500, "last": now}]
    devs = [{"mac": "02:aa:00:00:00:05", "name": "NAS"}]
    issues = [i for i in net_issues({"inet_hour": rows, "devices": devs}, {}, now) if i["rule"] == "internet_hog"]
    assert len(issues) == 1 and issues[0]["severity"] == "info" and "NAS" in issues[0]["title"]
    rows[1]["bytes_in"] = 4_000_000_000               # nobody dominates
    assert not [i for i in net_issues({"inet_hour": rows, "devices": devs}, {}, now) if i["rule"] == "internet_hog"]


# ---------------------------------------------------------------- #17 internet port counters

def test_throughput_prefers_physical_internet_port():
    c = [{"ts": 0, "unit": "r", "iface": "ppp0", "rx_bytes": 0, "tx_bytes": 0, "rx_packets": 0, "tx_packets": 0},
         {"ts": 60, "unit": "r", "iface": "ppp0", "rx_bytes": 7_500_000, "tx_bytes": 750_000, "rx_packets": 0, "tx_packets": 0},
         {"ts": 0, "unit": "r", "iface": "eth1", "rx_bytes": 0, "tx_bytes": 0, "rx_packets": 0, "tx_packets": 0},
         {"ts": 60, "unit": "r", "iface": "eth1", "rx_bytes": 75_000_000 + 22 * 10_000, "tx_bytes": 7_500_000 + 22 * 5_000,
          "rx_packets": 10_000, "tx_packets": 5_000}]
    t = graphs.throughput(c, {}, "r", {"phys": "eth1", "logical": "ppp0", "proto": "pppoe"})
    assert t["wan_iface"] == "eth1" and t["wan"]["Download"] == [[60, 10.0]] and t["wan"]["Upload"] == [[60, 1.0]]
    assert "hardware acceleration" in t["wan_note"] and "PPPoE" in t["wan_note"]
    old = graphs.throughput(c, {}, "r")                           # nothing known about the internet port yet
    assert old["wan_iface"] == "ppp0" and old["wan"]["Download"] == [[60, 1.0]]
    dhcp = graphs.throughput([dict(r, rx_bytes=r["rx_bytes"] - 8 * r["rx_packets"], tx_bytes=r["tx_bytes"] - 8 * r["tx_packets"])
                              for r in c], {}, "r", {"phys": "eth1", "proto": "dhcp"})
    assert dhcp["wan"]["Download"] == [[60, 10.0]]


def test_wan_source_and_counter_query(tmp_home):
    db.upsert_unit("r", role="router", info=json.dumps({"identity": {"wan0_ifname": "eth1", "wan0_proto": "pppoe",
                                                                     "wan0_gw_ifname": "ppp0"}}))
    assert graphs.wan_source(db.units()) == {"phys": "eth1", "logical": "ppp0", "proto": "pppoe"}
    db.insert_many("iface_counters", [{"ts": 1, "unit": "r", "iface": i, "rx_bytes": 1, "tx_bytes": 1}
                                      for i in ("eth1", "ppp0", "eth2", "br0")])
    assert {r["iface"] for r in db.throughput_counters(0, ["eth1"])} == {"eth1", "ppp0"}
    assert "wan0_ifname" in commands.IDENTITY_KEYS


# ---------------------------------------------------------------- #19 Qualcomm station bytes

def test_apstats_bytes_reach_qualcomm_stations(fixture_text):
    r = parse_unit(fixture_text("asus/node_qualcomm.txt"), "02:00:00:00:02:44", "10.0.11.14", False)
    st = next(s for s in r.stations if s["mac"] == "02:aa:00:00:00:05")
    assert st["tx_bytes"] == 276122973 and st["rx_bytes"] == 7912804 and st["tx_packets"] == 232883


def test_parse_apstats_ignores_per_ac_lines():
    text = ("Node Level Stats: 02:AA:00:00:00:07 (under VAP ath101)\nTx Data Packets                 = 3\n"
            "Tx Data Packets per AC:\nTx Data Bytes                   = 499\nRx Data Bytes                   = 12\n")
    assert P.parse_apstats_sta(text) == {"02:aa:00:00:00:07": {"iface": "ath101", "tx_packets": 3, "tx_bytes": 499, "rx_bytes": 12}}


def test_apstats_command_is_bounded():
    s = commands.unit_script(main=False, first_poll=False, full=False)
    assert "@@@ apstats_sta" in s and f"-gt {commands.APSTATS_MAX} ] && break 2" in s and "apstats -s -m $m" in s


# ---------------------------------------------------------------- #20 ASUS Traffic Analyzer

def test_traffic_analyzer_read_only_when_present():
    s = commands.unit_script(main=True, first_poll=False, full=True)
    assert f"if [ -f {commands.TA_DB} ]" in s and "sqlite3 -readonly" in s
    assert "@@@ ta_traffic" not in commands.unit_script(main=True, first_poll=False, full=False)


def test_traffic_analyzer_query_runs_on_the_documented_schema(tmp_path):
    import sqlite3
    import time
    ta = tmp_path / "TrafficAnalyzer.db"
    c = sqlite3.connect(ta)
    c.execute("CREATE TABLE traffic(mac VARCHAR(18), app_name VARCHAR(50), cat_name VARCHAR(50), "
              "timestamp UNSIGNED BIG INT, tx UNSIGNED BIG INT, rx UNSIGNED BIG INT)")
    now = int(time.time()) // 3600 * 3600
    c.executemany("INSERT INTO traffic VALUES (?, ?, ?, ?, ?, ?)", [
        ("02:AA:00:00:00:05", "YouTube", "Media streaming", now + 10, 100, 9000),
        ("02:AA:00:00:00:05", "YouTube", "Media streaming", now + 20, 50, 1000),
        ("02:AA:00:00:00:05", "Old", "x", now - 200000, 1, 1)])
    c.commit()
    script = commands.unit_script(main=True, first_poll=False, full=True)
    sql = script.split('sqlite3 -readonly -separator "|" ' + commands.TA_DB + ' "')[1].split('"')[0]
    schema = "\n".join("|".join(str(x) for x in r) for r in c.execute("PRAGMA table_info(traffic)"))
    out = "\n".join("|".join(str(x) for x in r) for r in c.execute(sql))
    assert P.ta_schema_ok(schema) and not P.ta_schema_ok("0|mac|TEXT|0||0")
    assert P.parse_ta_traffic(out) == [{"hour": now, "mac": "02:aa:00:00:00:05", "app": "YouTube",
                                        "category": "Media streaming", "bytes_out": 150, "bytes_in": 10000}]


def test_traffic_analyzer_rows_stored_and_shown(tmp_home):
    import time
    h = time.time() // 3600 * 3600
    rows = [{"hour": h, "mac": "02:aa:00:00:00:05", "app": "YouTube", "category": "Media", "bytes_in": 10, "bytes_out": 1}]
    db.store_app_traffic(rows)
    db.store_app_traffic([dict(rows[0], bytes_in=20)])          # same hour read again: replaced, not added
    apps = db.app_traffic(time.time() - 3600, "02:aa:00:00:00:05")
    assert len(apps) == 1 and apps[0]["bytes_in"] == 20
    assert graphs.device("02:aa:00:00:00:05", 2)["apps"][0]["app"] == "YouTube"


# ---------------------------------------------------------------- #21 managed switches

UNIFI_DEVICES = [{"type": "usw", "name": "USW-Flex-Mini", "mac": "02:bb:00:00:00:01", "port_table": [
    {"port_idx": 1, "name": "Port 1", "up": True, "speed": 1000, "rx_bytes": 1000, "tx_bytes": 2000, "rx_packets": 10,
     "tx_packets": 20, "rx_errors": 0, "tx_errors": 0, "rx_dropped": 0, "tx_dropped": 0},
    {"port_idx": 2, "name": "Port 2", "up": False, "speed": 0}]},
    {"type": "uap", "name": "AP", "mac": "02:bb:00:00:00:02"}]
UNIFI_STA = [{"mac": "02:AA:00:00:00:05", "sw_mac": "02:bb:00:00:00:01", "sw_port": 1, "is_wired": True},
             {"mac": "02:aa:00:00:00:09", "is_wired": False}]
EASY_SMART = """<script>
var max_port_num = 3;
var all_info = {
state:[1,1,0,0,0,0,0,0,0,0],
link_status:[6,0,5,0,0,0,0,0,0,0],
pkts:[100,0,200,1,0,0,0,0,5,0,6,0,0,0]
};
</script>"""


def test_parse_unifi_ports_and_devices():
    rows = switches.parse_unifi(UNIFI_DEVICES, UNIFI_STA)
    assert [(r["switch"], r["port"], r["up"]) for r in rows] == [("USW-Flex-Mini", 1, 1), ("USW-Flex-Mini", 2, 0)]
    assert rows[0]["macs"] == ["02:aa:00:00:00:05"] and rows[0]["rx_bytes"] == 1000 and rows[0]["rx_errors"] == 0


def test_parse_easy_smart_stats():
    rows = switches.parse_easy_smart_stats(EASY_SMART, "TL-SG608E")
    assert [(r["port"], r["up"], r["speed"]) for r in rows] == [(1, 1, 1000), (2, 0, None), (3, 0, 100)]
    assert rows[0]["tx_packets"] == 100 and rows[0]["rx_packets"] == 200 and rows[0]["rx_errors"] == 1
    assert switches.parse_easy_smart_stats("<html>login</html>", "x") == []


def test_switch_collector_and_series(tmp_home, monkeypatch):
    cfg = Config()
    cfg.unifi.enabled = True
    cfg.devices = [DeviceLogin(name="TL-SG608E", kind="tplink_easy_smart", host="10.0.10.195", username="admin")]
    monkeypatch.setattr(switches.secrets, "get_secret", lambda kind, ident: "pw")
    calls = []

    async def unifi(url, user, pw, site, verify):
        calls.append(("unifi", url, user, site))
        return {"device": UNIFI_DEVICES, "sta": UNIFI_STA}

    async def easy(host, user, pw):
        calls.append(("easy", host, user))
        return EASY_SMART

    sc = switches.SwitchCollector(lambda: cfg, tmp_home, unifi, easy)
    assert sc.due(1000)
    sc.poll(1000)
    UNIFI_DEVICES[0]["port_table"][0].update(rx_bytes=1000 + 7_500_000, tx_bytes=2000 + 0, rx_errors=60, rx_packets=10 + 50_000)
    sc.poll(1060)
    assert not sc.due(1100) and all(s["ok"] for s in sc.status["sources"].values())
    assert ("easy", "10.0.10.195", "admin") in calls
    rows = db.switch_ports_since(0)
    s = switches.series(rows, {"02:aa:00:00:00:05": {"mac": "02:aa:00:00:00:05", "name": "NAS"}})
    assert s["unit"] == "Mbit/s" and s["series"]["USW-Flex-Mini port 1 (NAS)"] == [[1060, 1.0]]
    p1 = next(p for p in s["ports"] if p["switch"] == "USW-Flex-Mini" and p["port"] == 1)
    assert p1["rx_bytes"] == 7_500_000 and p1["errors"] == 60 and p1["devices"][0]["name"] == "NAS"
    tp = next(p for p in s["ports"] if p["switch"] == "TL-SG608E" and p["port"] == 1)
    assert tp["rx_bytes"] is None and tp["rx_packets"] == 0
    errs = [i for i in net_issues({"switch_ports": s["ports"]}, {}, 2000) if i["rule"] == "iface_errors"]
    assert len(errs) == 1 and "USW-Flex-Mini port 1" in errs[0]["title"]


def test_switch_collector_reports_failures(tmp_home, monkeypatch):
    cfg = Config()
    cfg.devices = [DeviceLogin(name="sw", kind="tplink_easy_smart", host="10.0.10.195", username="admin")]
    monkeypatch.setattr(switches.secrets, "get_secret", lambda kind, ident: None)
    sc = switches.SwitchCollector(lambda: cfg, tmp_home)
    sc.poll(1000)
    assert sc.status["sources"]["sw"]["ok"] is False and "no password" in sc.status["sources"]["sw"]["error"]
    cfg.devices = []
    assert not switches.SwitchCollector(lambda: cfg, tmp_home).due(10_000)
