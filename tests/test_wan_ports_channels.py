from datetime import datetime

from meshlens import config
from meshlens.analysis import analyse
from meshlens.collectors.asus import parsers as P
from meshlens.collectors.asus.collector import AsusCollector, UnitResult
from meshlens.config import RouterConfig
from meshlens.store import db

LOG = """Nov 14 00:16:41 kernel: [15404.02] wlan: [0:D:dfs] WLAN_DEBUG_DFS_ALWAYS : dfs_process_radar_ind_on_home_chan: Radar found on channel=104, freq=5520 MHz.
Nov 14 00:16:43 kernel: [15406.07] wlan: [0:I:ANY] ieee80211_dfs_action: Changing to DFS chan 116 (5580 MHZ) of mode (11AEHT80)
Nov 14 10:17:56 MTK WL: trigger acs word(rai0) channel(56) bw(80) unavbl_ch(52,56,60,64)
Nov 13 19:59:31 kernel: [493129.6] __do_sys_kill: send signal [15] to init, current [reboot] parent [sh],[ahs]!
Jan  1 00:00:34 kernel: [    0.0]  crash_log: __do_sys_kill: send signal [15] to init, current [reboot] parent [sh],[ahs]!
"""


def test_unit_level_log_events():
    ev = P.parse_log_events(LOG, unit="u", now=datetime(2026, 11, 14, 12, 0))
    kinds = [e["kind"] for e in ev]
    assert kinds == ["radar", "channel_change", "channel_replan", "unit_reboot"]      # crash_log copy skipped
    assert ev[0]["detail"] == {"channel": 104} and ev[0]["mac"] is None
    assert ev[1]["detail"] == {"to_channel": 116}
    assert ev[2]["detail"] == {"channel": 56, "bw": 80, "unavailable": "52,56,60,64"} and ev[2]["iface"] == "rai0"
    assert ev[3]["detail"] == {"by": "ahs"}


def test_links_parse_and_wired():
    links = P.parse_links("eth1 4 up 1000\nlan2 0 lowerlayerdown -1\nppp0 0 unknown\nbad x\n")
    assert links["eth1"] == {"changes": 4, "state": "up", "speed": 1000}
    assert links["lan2"]["speed"] is None and "bad" not in links
    assert P.is_wired("eth1") and P.is_wired("lan1") and not P.is_wired("lan1.52") and not P.is_wired("rai0")


def test_derived_events(tmp_home):
    c = AsusCollector(RouterConfig(), tmp_home, runner=lambda *a: "")

    def unit(chan, links_text, wan=None):
        r = UnitResult(mac="02:00:00:00:01:64", ip="10.0.10.1", main=True, ok=True)
        r.sections = {"iw_dev": f"phy#0\n\tInterface rai0\n\t\taddr 02:00:00:00:01:65\n\t\ttype AP\n\t\tchannel {chan} (5280 MHz), width: 80 MHz, center1: 5290 MHz\n"}
        if wan:
            r.settings = {"wan0_ipaddr": wan}
        return r, P.parse_links(links_text)
    r, l = unit(56, "eth1 4 up 1000\nrai0 0 up\n", "203.0.113.5")
    assert c._derived_events(r, l, 1000.0) == []                 # first poll: nothing to compare with
    db.insert_many("unit_settings", [{"ts": 1000.0, "unit": r.mac, "data": '{"wan0_ipaddr": "203.0.113.5"}'}])
    r, l = unit(100, "eth1 6 up 1000\nrai0 3 up\n", "203.0.113.9")
    ev = {e["kind"]: e for e in c._derived_events(r, l, 1060.0)}
    assert ev["link_change"]["iface"] == "eth1" and ev["link_change"]["detail"]["changes"] == 2   # Wi-Fi iface ignored
    assert ev["channel_change"]["detail"] == {"from_channel": 56, "to_channel": 100, "from_width": 80, "width": 80, "band": "5"}
    assert ev["wan_ip_change"]["detail"] == {"from": "203.0.113.5", "to": "203.0.113.9"}


def test_new_rules():
    now = 1_800_000_000.0
    u = "02:00:00:00:02:44"
    uev = [{"ts": now - 3000, "unit": u, "kind": "radar", "mac": None, "iface": None, "detail": {"channel": 104}},
           {"ts": now - 2998, "unit": u, "kind": "channel_change", "mac": None, "iface": None, "detail": {"to_channel": 116}},
           {"ts": now - 500, "unit": u, "kind": "link_change", "mac": None, "iface": "eth1", "detail": {"changes": 5, "state": "up", "speed": 1000}},
           {"ts": now - 400, "unit": u, "kind": "wan_ip_change", "mac": None, "iface": None, "detail": {}},
           {"ts": now - 300, "unit": u, "kind": "unit_reboot", "mac": None, "iface": None, "detail": {"by": "ahs"}}]
    counters = [{"ts": now - 3600, "unit": u, "iface": "eth1", "rx_errs": 0, "tx_errs": 0, "rx_packets": 0, "tx_packets": 0},
                {"ts": now, "unit": u, "iface": "eth1", "rx_errs": 300, "tx_errs": 0, "rx_packets": 20000, "tx_packets": 10000}]
    net = {"analysis_cfg": config.AnalysisConfig(), "unit_names": {u: "Second Floor"}, "unit_events": uev,
           "iface_counters": counters, "units": []}
    res = analyse([], [], [], [], [], {}, now=now, net=net)
    r = {i["rule"]: i for i in res["issues"]}
    assert "Second Floor on channel 104 → moved to 116" in r["radar_dfs"]["evidence"]
    assert r["link_flap"]["severity"] == "medium" and "5 link changes" in r["link_flap"]["evidence"]
    assert "wan_reconnect" in r and "'ahs'" in r["unit_restarted_log"]["evidence"]
    assert r["iface_errors"]["severity"] == "medium" and "300 errors" in r["iface_errors"]["evidence"]
    units = [{"mac": u, "info": {"ifaces": [{"iface": "ath1", "band": "5", "channel": 100}]}}]
    res = analyse([], [], [], [], [], {}, now=now, net={"analysis_cfg": config.AnalysisConfig(), "units": units, "unit_names": {}})
    assert any(i["rule"] == "dfs_channel" for i in res["issues"])


def test_arp_and_relayed_unit_parent():
    arp = P.parse_proc_arp("IP address HW type Flags HW address Mask Device\n"
                           "10.0.11.133 0x1 0x2 46:00:00:00:02:49 * br0\n10.0.11.99 0x1 0x0 00:00:00:00:00:00 * br0\n")
    assert arp == {"10.0.11.133": "46:00:00:00:02:49"}
    from meshlens import topology
    units = [{"mac": "02:00:00:00:01:64", "role": "router", "alias": "Main", "reachable": 1, "macs": []},
             {"mac": "02:00:00:00:02:48", "role": "node", "alias": "Office", "reachable": 1, "macs": ["46:00:00:00:02:49"]},
             {"mac": "02:00:00:00:03:e8", "role": "node", "alias": "Garden", "reachable": 0, "macs": [],
              "via_unit": "02:00:00:00:02:48"}]
    n = {x["id"]: x for x in topology.build(units, [], [], [], [], now=1.0)["nodes"]}
    assert n["02:00:00:00:03:e8"]["parent"] == "02:00:00:00:02:48"
    assert n["02:00:00:00:03:e8"]["link"]["type"] == "wireless (through this unit)"


CFGMNT = ('{"role":"master","mac":"02:00:00:00:01:64","client_list":['
          '{"alias":"Main","model_name":"ZenWiFi_BT8","ip":"10.0.10.1","mac":"02:00:00:00:01:64","online":1,"level":0},'
          '{"alias":"Office","model_name":"ZenWiFi_BD4","ip":"10.0.11.18","mac":"02:00:00:00:02:48","online":0,"level":0},'
          '{"alias":"Garden","model_name":"ZenWiFi_BD4_Outdoor","product_id":"BD4_OD","fwver":"3.0.0.6.102_58451",'
          '"ip":"10.0.11.133","mac":"02:00:00:00:03:E8","online":1,"rssi2g":"-60","rssi5g":"-62","level":-1}]}')


def test_cfgmnt_parse():
    c = P.parse_cfgmnt("@junk\n" + CFGMNT)
    g = c["02:00:00:00:03:e8"]
    assert g["alias"] == "Garden" and g["online"] and g["level"] == -1 and g["rssi5g"] == -62 and g["productid"] == "BD4_OD"
    assert c["02:00:00:00:02:48"]["online"] is False and P.parse_cfgmnt("not json") == {}


def test_unlisted_online_node_is_tried_and_explained(tmp_home, fixture_text):
    from meshlens.collectors.asus.ssh import SshError
    router = fixture_text("asus/router_mediatek.txt").replace("@@@ cfg_device_list\n<ZenWiFi_BT8>10.0.10.1>02:00:00:00:01:64>1",
                                                              "@@@ cfg_device_list\n<ZenWiFi_BT8>10.0.10.1>02:00:00:00:01:64>1")
    router += "\n@@@ cfgmnt\n" + CFGMNT + "\n"
    tried = []

    def runner(host, port, user, pw, script, timeout):
        tried.append(host)
        if host == "10.0.10.1":
            return router
        raise SshError(f"{host}:{port}: Unable to connect to port {port}")
    c = AsusCollector(RouterConfig(), tmp_home, runner=runner)
    c.poll("pw", full=True)
    assert "10.0.11.133" in tried                              # the unlisted but online node is tried
    garden = next(u for u in db.units() if u["mac"] == "02:00:00:00:03:e8")
    assert garden["reachable"] == 0 and "SSH isn't open" in garden["last_error"] and garden["firmware"] == "3.0.0.6.102_58451"
    assert garden["ip"] == "10.0.11.133" and garden["model"] == "ZenWiFi_BD4_Outdoor"
    res = analyse([], [], [], db.units(), [], {}, now=1.0)
    i = next(x for x in res["issues"] if x["subject"] == "02:00:00:00:03:e8")
    assert i["rule"] == "unit_ssh_closed" and any("Enable SSH" in f for f in i["fixes"])
    assert res["units"]["02:00:00:00:03:e8"]["score"] is None
